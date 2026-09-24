"""JWT authentication, server-side RBAC, request IDs, and audit helpers."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from functools import wraps
import hashlib
import uuid
from typing import Callable, ParamSpec, TypeVar

import jwt
from flask import current_app, g, request
from sqlalchemy import select
from werkzeug.security import check_password_hash

from .config import Settings
from .db import get_session
from .models import AuditEvent, Permission, RefreshToken, Role, RolePermission, User, UserRole, utcnow


class APIError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


@dataclass(frozen=True)
class Principal:
    user_id: uuid.UUID
    organization_id: uuid.UUID | None
    roles: frozenset[str]
    patient_id: uuid.UUID | None

    @property
    def is_super_admin(self) -> bool:
        return "SUPER_ADMIN" in self.roles


def settings() -> Settings:
    return current_app.extensions["ecg_platform_settings"]


def password_matches(user: User, password: str) -> bool:
    return user.active and check_password_hash(user.password_hash, password)


def _role_names(user_id: uuid.UUID, organization_id: uuid.UUID | None) -> frozenset[str]:
    session = get_session()
    query = select(Role.name).join(UserRole, UserRole.role_id == Role.role_id).where(UserRole.user_id == user_id)
    if organization_id is not None:
        query = query.where((UserRole.organization_id == organization_id) | (UserRole.organization_id.is_(None)))
    return frozenset(session.scalars(query).all())


def principal_for_user(user: User) -> Principal:
    return Principal(user_id=user.user_id, organization_id=user.organization_id,
                     roles=_role_names(user.user_id, user.organization_id), patient_id=user.patient_id)


def _encode(user: User, token_type: str, expires: timedelta) -> tuple[str, str]:
    now = utcnow()
    token_id = uuid.uuid4().hex
    token = jwt.encode({
        "sub": str(user.user_id), "jti": token_id, "typ": token_type,
        "iat": now, "exp": now + expires, "aud": "ecg-health-platform",
    }, settings().jwt_secret, algorithm="HS256")
    return token, token_id


def issue_token_pair(user: User) -> dict[str, object]:
    access, _ = _encode(user, "access", timedelta(minutes=settings().access_token_minutes))
    refresh, token_id = _encode(user, "refresh", timedelta(days=settings().refresh_token_days))
    session = get_session()
    session.add(RefreshToken(user_id=user.user_id, token_id=token_id,
                             expires_at=utcnow() + timedelta(days=settings().refresh_token_days)))
    session.commit()
    principal = principal_for_user(user)
    return {"access_token": access, "refresh_token": refresh, "token_type": "Bearer",
            "expires_in": settings().access_token_minutes * 60,
            "user": {"user_id": str(user.user_id), "display_name": user.display_name,
                     "organization_id": str(user.organization_id) if user.organization_id else None,
                     "roles": sorted(principal.roles)}}


def _decoded_token(expected_type: str) -> dict:
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        raise APIError("AUTH_REQUIRED", "A Bearer access token is required.", 401)
    token = header.removeprefix("Bearer ").strip()
    try:
        payload = jwt.decode(token, settings().jwt_secret, algorithms=["HS256"], audience="ecg-health-platform")
    except jwt.PyJWTError as exc:
        raise APIError("INVALID_TOKEN", "The access token is invalid or expired.", 401) from exc
    if payload.get("typ") != expected_type:
        raise APIError("INVALID_TOKEN", "The token type is not accepted for this operation.", 401)
    return payload


def current_principal() -> Principal:
    cached = getattr(g, "clinical_principal", None)
    if cached is not None:
        return cached
    payload = _decoded_token("access")
    try:
        user_id = uuid.UUID(str(payload["sub"]))
    except (ValueError, KeyError) as exc:
        raise APIError("INVALID_TOKEN", "The token subject is invalid.", 401) from exc
    session = get_session()
    user = session.get(User, user_id)
    if user is None or not user.active:
        raise APIError("AUTH_REQUIRED", "The user account is unavailable.", 401)
    g.clinical_principal = principal_for_user(user)
    return g.clinical_principal


def user_from_refresh_token(token: str) -> tuple[User, str]:
    try:
        payload = jwt.decode(token, settings().jwt_secret, algorithms=["HS256"], audience="ecg-health-platform")
        user_id = uuid.UUID(str(payload["sub"]))
    except (jwt.PyJWTError, ValueError, KeyError) as exc:
        raise APIError("INVALID_TOKEN", "The refresh token is invalid or expired.", 401) from exc
    if payload.get("typ") != "refresh":
        raise APIError("INVALID_TOKEN", "The token type is not a refresh token.", 401)
    session = get_session()
    stored = session.scalar(select(RefreshToken).where(RefreshToken.token_id == str(payload.get("jti"))))
    if stored is None or stored.revoked_at is not None or stored.expires_at <= utcnow():
        raise APIError("INVALID_TOKEN", "The refresh token has been revoked or expired.", 401)
    user = session.get(User, user_id)
    if user is None or not user.active:
        raise APIError("AUTH_REQUIRED", "The user account is unavailable.", 401)
    return user, stored.token_id


def revoke_refresh_token(token_id: str) -> None:
    session = get_session()
    stored = session.scalar(select(RefreshToken).where(RefreshToken.token_id == token_id))
    if stored is not None:
        stored.revoked_at = utcnow()
        session.commit()


def permissions_for(principal: Principal) -> frozenset[str]:
    if principal.is_super_admin:
        return frozenset({"*"})
    session = get_session()
    query = (select(Permission.name).join(RolePermission, RolePermission.permission_id == Permission.permission_id)
             .join(Role, Role.role_id == RolePermission.role_id).join(UserRole, UserRole.role_id == Role.role_id)
             .where(UserRole.user_id == principal.user_id))
    if principal.organization_id is not None:
        query = query.where((UserRole.organization_id == principal.organization_id) | (UserRole.organization_id.is_(None)))
    return frozenset(session.scalars(query).all())


P = ParamSpec("P")
R = TypeVar("R")


def require_permission(permission: str) -> Callable[[Callable[P, R]], Callable[P, R]]:
    def decorate(func: Callable[P, R]) -> Callable[P, R]:
        @wraps(func)
        def wrapped(*args: P.args, **kwargs: P.kwargs) -> R:
            principal = current_principal()
            granted = permissions_for(principal)
            if "*" not in granted and permission not in granted:
                audit("AUTHORIZATION_DENIED", "permission", permission, success=False)
                raise APIError("FORBIDDEN", "You are not authorized for this operation.", 403)
            return func(*args, **kwargs)
        return wrapped
    return decorate


def require_any_permission(*required: str) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Allow a route when at least one named server-side permission is present."""
    def decorate(func: Callable[P, R]) -> Callable[P, R]:
        @wraps(func)
        def wrapped(*args: P.args, **kwargs: P.kwargs) -> R:
            principal = current_principal()
            granted = permissions_for(principal)
            if "*" not in granted and not granted.intersection(required):
                audit("AUTHORIZATION_DENIED", "permission", ",".join(required), success=False)
                raise APIError("FORBIDDEN", "You are not authorized for this operation.", 403)
            return func(*args, **kwargs)
        return wrapped
    return decorate


def require_role(*roles: str) -> Callable[[Callable[P, R]], Callable[P, R]]:
    def decorate(func: Callable[P, R]) -> Callable[P, R]:
        @wraps(func)
        def wrapped(*args: P.args, **kwargs: P.kwargs) -> R:
            principal = current_principal()
            if not principal.roles.intersection(roles):
                audit("AUTHORIZATION_DENIED", "role", ",".join(roles), success=False)
                raise APIError("FORBIDDEN", "You are not authorized for this operation.", 403)
            return func(*args, **kwargs)
        return wrapped
    return decorate


def audit(action: str, resource_type: str, resource_id: str | None = None, *, success: bool = True,
          organization_id: uuid.UUID | None = None, metadata: dict | None = None) -> None:
    """Append a minimal audit event; callers must never pass raw PHI or ECG data."""
    principal: Principal | None = getattr(g, "clinical_principal", None)
    session = get_session()
    session.add(AuditEvent(
        user_id=principal.user_id if principal else None,
        organization_id=organization_id or (principal.organization_id if principal else None),
        action=action, resource_type=resource_type, resource_id=resource_id,
        success=success, request_id=getattr(g, "request_id", "unknown"),
        ip_address=request.remote_addr, user_agent=(request.user_agent.string or "")[:512],
        metadata_json=metadata or {},
    ))


def hash_token_for_log(value: str) -> str:
    """Allows an opaque correlation only; never log bearer/refresh token text."""
    return hashlib.sha256(value.encode()).hexdigest()[:16]
