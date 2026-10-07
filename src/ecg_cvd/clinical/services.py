"""Server-side tenant-scoped clinical workflow services."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from functools import lru_cache
from io import BytesIO
import hashlib
import hmac
from pathlib import Path
import secrets
import uuid
from typing import Any

from cryptography.exceptions import InvalidTag
from flask import current_app
import numpy as np
from PIL import Image, ImageDraw, ImageFilter
from scipy.io import savemat
from sqlalchemy import and_, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from werkzeug.security import generate_password_hash

from .ai_adapter import ECGModelAdapter
from .db import get_session
from .image_digitization import (OUTPUT_SAMPLING_RATE_HZ, DigitizedTrace,
                                 digitize_ecg_jpeg)
from .image_validation import validate_jpeg_image
from ..crypto import decrypt_password, encrypt_password
from .models import (AIAnalysis, AIModel, AIModelVersion, AIPrediction, AuditEvent, ClinicalNote,
                     Condition, DiagnosticReport, DoctorReview, ECGFile, ECGRecord, ECGVisualAccessGrant,
                     Encounter, Feature, Notification, Organization, Patient, PatientContact, PatientIdentifier,
                     PatientOrganization, Permission, Practitioner, Prescription, PrescriptionItem, ReportDocument,
                     Role, RolePermission, User, UserRole, utcnow)
from .schemas import (DiagnosisInput, EncounterRequest, FeatureRequest, PatientMatchRequest,
                      PatientRequest, PrescriptionItemInput, ReviewRequest, UserRequest)
from .security import (APIError, Principal, audit, issue_ecg_visual_access_token,
                       visual_access_token_claims)
from .storage import ObjectStorage


ROLE_PERMISSIONS: dict[str, set[str]] = {
    "SUPER_ADMIN": {"*"},
    "HOSPITAL_ADMIN": {
        "hospital.read", "hospital.update", "user.read", "user.create", "user.update",
        "patient.read", "patient.create", "patient.update", "ecg.read", "report.read",
        "audit.read", "feature.read",
    },
    "DOCTOR": {
        "patient.read", "patient.create", "patient.update", "encounter.create", "encounter.read",
        "ecg.read", "ecg.upload", "ecg.analyze", "ai.read", "ai.run", "diagnosis.create",
        "diagnosis.update", "clinical_note.create", "prescription.create", "prescription.update",
        "report.create", "report.read", "report.download", "medication.catalog.read",
    },
    "TECHNICIAN": {
        "patient.read", "patient.create", "patient.update", "encounter.create", "encounter.read",
        "ecg.read", "ecg.upload", "ecg.analyze", "ai.read", "ai.run",
    },
    "PATIENT": {"patient.self.read", "encounter.self.read", "ecg.self.read", "report.self.read",
                "prescription.self.read"},
}

DEFAULT_FEATURES = {
    "ECG_UPLOAD": "Private ECG file upload and validation",
    "ECG_AI_ANALYSIS": "Research-only AI analysis requiring clinician review",
    "GRAD_CAM": "Model-generated Grad-CAM explanation",
    "PRESCRIPTION": "Clinician-authored prescription record",
    "PDF_REPORT": "Clinical report PDF generation",
    "FHIR": "FHIR-compatible read mappings",
    "ANALYTICS": "Role-scoped dashboard analytics",
}


WAVEFORM_SUFFIXES = frozenset({".mat", ".csv"})
JPEG_SUFFIXES = frozenset({".jpg", ".jpeg"})
JPEG_CONTENT_TYPE = "image/jpeg"
DIGITIZED_FILE_PURPOSE = "DERIVED_FROM_JPEG"
DIGITIZED_DEVICE_PREFIX = "EXPERIMENTAL_JPEG_TRACE:"
DIGITIZATION_PROVENANCE = "EXPERIMENTAL_JPEG_TRACE_DIGITIZATION"
ORIGINAL_ECG_VIEW_ROLE = "DOCTOR"
VISUAL_ACCESS_PENDING = "PENDING"
VISUAL_ACCESS_APPROVED = "APPROVED"
VISUAL_ACCESS_UNLOCKED = "UNLOCKED"
VISUAL_ACCESS_REVOKED = "REVOKED"
VISUAL_ACCESS_EXPIRED = "EXPIRED"
VISUAL_ACCESS_LOCKED = "LOCKED"
VISUAL_ACCESS_TERMINAL_STATES = frozenset({
    VISUAL_ACCESS_REVOKED, VISUAL_ACCESS_EXPIRED, VISUAL_ACCESS_LOCKED,
})


@lru_cache(maxsize=1)
def protected_ecg_preview_png() -> bytes:
    """Create a generic blurred placeholder without reading any ECG asset.

    This is intentionally *not* an adversarially perturbed waveform: research
    camouflage is neither encryption nor a suitable authorization boundary.
    The fixed rendering contains no source pixels, samples, identifiers, or
    derived signal characteristics, so it is safe to return to a non-doctor
    who is otherwise permitted to know that an ECG record exists.
    """
    width, height = 1_200, 420
    canvas = Image.new("RGBA", (width, height), (245, 248, 249, 255))
    drawing = ImageDraw.Draw(canvas)
    for x in range(44, width - 44, 56):
        drawing.line((x, 40, x, height - 40), fill=(198, 215, 216, 150), width=1)
    for y in range(40, height - 40, 44):
        drawing.line((44, y, width - 44, y), fill=(198, 215, 216, 150), width=1)

    # These deliberately regular, synthetic strokes make the block visibly
    # ECG-like without being sampled from (or correlated to) a patient record.
    points: list[tuple[int, int]] = []
    for x in range(60, width - 60):
        phase = (x - 60) % 210
        if phase < 68:
            y = height // 2
        elif phase < 86:
            y = height // 2 - (phase - 68) * 6
        elif phase < 105:
            y = height // 2 + (phase - 86) * 4
        elif phase < 125:
            y = height // 2 - (125 - phase) * 2
        else:
            y = height // 2
        points.append((x, y))
    drawing.line(points, fill=(13, 113, 128, 185), width=7, joint="curve")
    blurred = canvas.filter(ImageFilter.GaussianBlur(radius=13))

    image = Image.new("RGBA", (width, height), (238, 244, 244, 255))
    image.alpha_composite(blurred)
    drawing = ImageDraw.Draw(image)
    drawing.rounded_rectangle((112, 142, width - 112, 278), radius=18,
                              fill=(7, 63, 77, 232), outline=(112, 186, 193, 255), width=2)
    drawing.text((width // 2, 181), "ECG PREVIEW REDACTED", anchor="mm",
                 fill=(255, 255, 255, 255), font_size=28)
    drawing.text((width // 2, 221), "Original waveform and image are available to a doctor only.", anchor="mm",
                 fill=(210, 237, 238, 255), font_size=17)
    output = BytesIO()
    image.convert("RGB").save(output, format="PNG", optimize=True)
    return output.getvalue()


def as_uuid(value: str | uuid.UUID, resource: str = "resource") -> uuid.UUID:
    try:
        return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
    except (TypeError, ValueError) as exc:
        raise APIError("INVALID_IDENTIFIER", f"The {resource} identifier is invalid.", 400) from exc


def model_version_for(adapter: ECGModelAdapter, session: Session) -> AIModelVersion:
    """Register the configured, real model artifact without fabricating metadata."""
    descriptor = adapter.descriptor()
    model = session.scalar(select(AIModel).where(AIModel.model_name == descriptor.model_name))
    if model is None:
        model = AIModel(model_name=descriptor.model_name, framework="PyTorch", status="ACTIVE")
        session.add(model)
        session.flush()
    existing = session.scalar(select(AIModelVersion).where(
        AIModelVersion.model_uuid == model.model_uuid, AIModelVersion.artifact_sha256 == descriptor.sha256,
    ))
    if existing is not None:
        return existing
    version = descriptor.version
    collision = session.scalar(select(AIModelVersion).where(
        AIModelVersion.model_uuid == model.model_uuid, AIModelVersion.version == version,
    ))
    if collision is not None:
        collision.status = "RETIRED"
        version = f"{version}+{descriptor.sha256[:8]}"
    created = AIModelVersion(model_uuid=model.model_uuid, version=version,
                             # Retain a stable logical artifact reference, never the host's
                             # absolute checkpoint path in a clinical database or API payload.
                             artifact_uri=f"model://ramnv2/{descriptor.sha256}", artifact_sha256=descriptor.sha256,
                             training_dataset=None, preprocessing=descriptor.preprocessing,
                             metrics=descriptor.metrics, status="ACTIVE")
    session.add(created)
    session.flush()
    return created


def initialize_reference_data(session: Session) -> None:
    """Idempotently seed role, permission, and feature definitions (not users)."""
    roles: dict[str, Role] = {}
    for name in ROLE_PERMISSIONS:
        role = session.scalar(select(Role).where(Role.name == name))
        if role is None:
            role = Role(name=name, scope="PLATFORM" if name == "SUPER_ADMIN" else "ORGANIZATION",
                        description=f"System role: {name}")
            session.add(role)
            session.flush()
        roles[name] = role
    permission_names = sorted({permission for values in ROLE_PERMISSIONS.values() for permission in values if permission != "*"})
    permissions: dict[str, Permission] = {}
    for name in permission_names:
        item = session.scalar(select(Permission).where(Permission.name == name))
        if item is None:
            item = Permission(name=name, description=f"Platform permission: {name}")
            session.add(item)
            session.flush()
        permissions[name] = item
    for role_name, values in ROLE_PERMISSIONS.items():
        if "*" in values:
            continue
        for permission_name in values:
            found = session.scalar(select(RolePermission).where(
                RolePermission.role_id == roles[role_name].role_id,
                RolePermission.permission_id == permissions[permission_name].permission_id,
            ))
            if found is None:
                session.add(RolePermission(role_id=roles[role_name].role_id,
                                           permission_id=permissions[permission_name].permission_id))
    for key, description in DEFAULT_FEATURES.items():
        if session.scalar(select(Feature).where(Feature.key == key)) is None:
            session.add(Feature(key=key, description=description, enabled=True))
    session.commit()


class ClinicalService:
    def __init__(self, principal: Principal, session: Session | None = None):
        self.principal = principal
        self.session = session or get_session()
        self.storage: ObjectStorage = current_app.extensions["ecg_platform_storage"]
        self.adapter: ECGModelAdapter | None = current_app.extensions.get("ecg_platform_adapter")

    @property
    def can_view_original_ecg(self) -> bool:
        """Whether this principal may receive original ECG bytes or visualizations.

        This is intentionally an explicit role check, rather than a permission
        wildcard check. A platform administrator can administer the platform,
        but must not receive a patient's original waveform merely through the
        SUPER_ADMIN ``*`` permission.
        """
        return ORIGINAL_ECG_VIEW_ROLE in self.principal.roles

    def _require_original_ecg_view(self, ecg: ECGRecord) -> None:
        """Block raw ECG disclosure independently of HTTP route decorators."""
        if self.can_view_original_ecg:
            return
        audit("ECG_ORIGINAL_ACCESS_DENIED", "ecg", str(ecg.ecg_uuid), success=False,
              organization_id=ecg.organization_id,
              metadata={"reason": "doctor_role_required"})
        self.session.commit()
        raise APIError(
            "ECG_DOCTOR_ONLY",
            "Only a doctor may view or download the original ECG waveform or image.",
            403,
        )

    @staticmethod
    def _utc(value: datetime) -> datetime:
        """Normalize SQLite's timezone-naive timestamps to UTC for comparison."""
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @staticmethod
    def _jti_digest(token_id: str) -> str:
        return hashlib.sha256(token_id.encode("ascii")).hexdigest()

    @staticmethod
    def _visual_access_expiry(grant: ECGVisualAccessGrant) -> datetime | None:
        if grant.status == VISUAL_ACCESS_PENDING:
            return grant.request_expires_at
        if grant.status == VISUAL_ACCESS_APPROVED:
            return grant.passcode_expires_at
        if grant.status == VISUAL_ACCESS_UNLOCKED:
            return grant.access_expires_at
        return None

    @staticmethod
    def _clear_visual_access_material(grant: ECGVisualAccessGrant) -> None:
        """Erase password-wrapped secret and browser-session correlation data."""
        grant.secret_envelope = None
        grant.unlock_token_jti_hash = None

    def _expire_visual_access_grant(self, grant: ECGVisualAccessGrant, now: datetime | None = None) -> bool:
        """Expire a mutable request/session without trusting client-side clocks."""
        if grant.status in VISUAL_ACCESS_TERMINAL_STATES:
            return False
        deadline = self._visual_access_expiry(grant)
        current = now or utcnow()
        if deadline is not None and self._utc(deadline) <= current:
            grant.status = VISUAL_ACCESS_EXPIRED
            self._clear_visual_access_material(grant)
            return True
        return False

    @staticmethod
    def _grant_deadline_for_response(grant: ECGVisualAccessGrant) -> datetime | None:
        return ClinicalService._visual_access_expiry(grant)

    def _visual_access_grant_dict(self, grant: ECGVisualAccessGrant, *, requester_name: str | None = None) -> dict[str, Any]:
        deadline = self._grant_deadline_for_response(grant)
        payload: dict[str, Any] = {
            "request_id": str(grant.visual_access_grant_uuid),
            "id": str(grant.visual_access_grant_uuid),
            "ecg_uuid": str(grant.ecg_uuid),
            "status": grant.status,
            "requested_at": grant.created_at.isoformat(),
            "expires_at": deadline.isoformat() if deadline else None,
            "passcode_expires_at": grant.passcode_expires_at.isoformat() if grant.passcode_expires_at else None,
            "access_expires_at": grant.access_expires_at.isoformat() if grant.access_expires_at else None,
            "attempts_remaining": max(0, grant.max_attempts - grant.failed_attempts),
            "visual_only": True,
            "grant_header": "X-ECG-Visual-Grant",
        }
        if requester_name is not None:
            payload["requester"] = {
                "user_id": str(grant.requester_id),
                "display_name": requester_name,
            }
        return payload

    def _require_doctor_for_visual_access_administration(self) -> uuid.UUID:
        """Return the doctor's tenant, never treating SUPER_ADMIN as a doctor."""
        if ORIGINAL_ECG_VIEW_ROLE not in self.principal.roles or self.principal.organization_id is None:
            audit("ECG_VISUAL_ACCESS_ADMIN_DENIED", "ecg_visual_access_grant", success=False,
                  metadata={"reason": "literal_doctor_role_required"})
            self.session.commit()
            raise APIError("FORBIDDEN", "Only a doctor in this hospital may approve or revoke ECG visual access.", 403)
        return self.principal.organization_id

    def _deny_visual_access(self, ecg: ECGRecord, *, reason: str) -> None:
        audit("ECG_VISUAL_ACCESS_DENIED", "ecg", str(ecg.ecg_uuid), success=False,
              organization_id=ecg.organization_id, metadata={"reason": reason})
        self.session.commit()
        raise APIError(
            "ECG_VISUAL_ACCESS_REQUIRED",
            "A doctor-approved visual-access grant is required to view this original ECG.",
            403,
        )

    def _require_ecg_visual_access(self, ecg: ECGRecord) -> None:
        """Authorize a doctor or a valid, scoped, in-browser visual grant.

        This does *not* authorize raw file/download/PDF export routes.  It is
        intentionally separate from ``_require_original_ecg_view`` so future
        service callers must choose the narrower capability explicitly.
        """
        if self.can_view_original_ecg:
            return
        claims = visual_access_token_claims()
        if claims is None:
            self._deny_visual_access(ecg, reason="grant_header_missing")
        try:
            claimant_id = uuid.UUID(str(claims["sub"]))
            claimed_ecg_id = uuid.UUID(str(claims["ecg"]))
            grant_id = uuid.UUID(str(claims["grant"]))
            token_id = str(claims["jti"])
        except (KeyError, TypeError, ValueError):
            self._deny_visual_access(ecg, reason="grant_claims_invalid")
        if claimant_id != self.principal.user_id or claimed_ecg_id != ecg.ecg_uuid:
            self._deny_visual_access(ecg, reason="grant_scope_mismatch")
        grant = self.session.get(ECGVisualAccessGrant, grant_id)
        if grant is None:
            self._deny_visual_access(ecg, reason="grant_not_found")
        if (grant.ecg_uuid != ecg.ecg_uuid or grant.organization_id != ecg.organization_id
                or grant.requester_id != self.principal.user_id):
            self._deny_visual_access(ecg, reason="grant_record_mismatch")
        expired = self._expire_visual_access_grant(grant)
        expected_digest = grant.unlock_token_jti_hash or ""
        if (expired or grant.status != VISUAL_ACCESS_UNLOCKED or grant.access_expires_at is None
                or self._utc(grant.access_expires_at) <= utcnow()
                or not hmac.compare_digest(expected_digest, self._jti_digest(token_id))):
            self._deny_visual_access(ecg, reason="grant_inactive")
        audit("ECG_VISUAL_ACCESS_GRANTED", "ecg", str(ecg.ecg_uuid), organization_id=ecg.organization_id,
              metadata={"grant_id": str(grant.visual_access_grant_uuid), "mode": "inline_visual_only"})
        self.session.commit()

    def request_visual_access(self, ecg_uuid: str) -> dict[str, Any]:
        """Create a tenant/patient-scoped request for doctor-approved viewing."""
        ecg = self._ecg(ecg_uuid)
        if self.can_view_original_ecg:
            raise APIError("VISUAL_ACCESS_NOT_REQUIRED", "Doctors already have direct ECG visual access.", 409)
        now = utcnow()
        existing = self.session.scalars(select(ECGVisualAccessGrant).where(
            ECGVisualAccessGrant.ecg_uuid == ecg.ecg_uuid,
            ECGVisualAccessGrant.requester_id == self.principal.user_id,
        ).order_by(ECGVisualAccessGrant.created_at.desc())).all()
        active: ECGVisualAccessGrant | None = None
        for grant in existing:
            self._expire_visual_access_grant(grant, now)
            if grant.status in {VISUAL_ACCESS_PENDING, VISUAL_ACCESS_APPROVED, VISUAL_ACCESS_UNLOCKED}:
                active = grant
                break
        if active is not None:
            self.session.commit()
            raise APIError("VISUAL_ACCESS_REQUEST_EXISTS",
                           "An active visual-access request already exists for this ECG.", 409)
        request_hours = max(1, int(current_app.extensions["ecg_platform_settings"].visual_access_request_hours))
        max_attempts = max(1, min(10, int(current_app.extensions["ecg_platform_settings"].visual_access_max_attempts)))
        grant = ECGVisualAccessGrant(
            ecg_uuid=ecg.ecg_uuid,
            organization_id=ecg.organization_id,
            requester_id=self.principal.user_id,
            status=VISUAL_ACCESS_PENDING,
            request_expires_at=now + timedelta(hours=request_hours),
            max_attempts=max_attempts,
        )
        self.session.add(grant)
        self.session.flush()
        doctor_users = self.session.scalars(
            select(User).join(UserRole, UserRole.user_id == User.user_id)
            .join(Role, Role.role_id == UserRole.role_id)
            .where(
                User.active.is_(True),
                User.organization_id == ecg.organization_id,
                Role.name == ORIGINAL_ECG_VIEW_ROLE,
                UserRole.organization_id == ecg.organization_id,
            )
        ).all()
        for doctor in {user.user_id: user for user in doctor_users}.values():
            self.session.add(Notification(
                user_id=doctor.user_id,
                organization_id=ecg.organization_id,
                kind="ECG_VISUAL_ACCESS_REQUEST",
                message="An ECG visual-access request is awaiting doctor review.",
            ))
        audit("ECG_VISUAL_ACCESS_REQUESTED", "ecg_visual_access_grant", str(grant.visual_access_grant_uuid),
              organization_id=ecg.organization_id,
              metadata={"ecg_id": str(ecg.ecg_uuid), "doctor_notification_count": len({u.user_id for u in doctor_users})})
        self.session.commit()
        return self._visual_access_grant_dict(grant)

    def current_visual_access_grant(self, ecg_uuid: str) -> dict[str, Any] | None:
        """Return only the signed-in requester's latest grant state for one ECG."""
        ecg = self._ecg(ecg_uuid)
        grant = self.session.scalar(select(ECGVisualAccessGrant).where(
            ECGVisualAccessGrant.ecg_uuid == ecg.ecg_uuid,
            ECGVisualAccessGrant.requester_id == self.principal.user_id,
        ).order_by(ECGVisualAccessGrant.created_at.desc()))
        if grant is None:
            return None
        changed = self._expire_visual_access_grant(grant)
        if changed:
            self.session.commit()
        return self._visual_access_grant_dict(grant)

    def list_visual_access_requests(self, status: str | None = None) -> list[dict[str, Any]]:
        """List requests only for literal doctors in their hospital tenant."""
        organization_id = self._require_doctor_for_visual_access_administration()
        normalized = status.strip().upper() if status else None
        allowed = {VISUAL_ACCESS_PENDING, VISUAL_ACCESS_APPROVED, VISUAL_ACCESS_UNLOCKED,
                   VISUAL_ACCESS_REVOKED, VISUAL_ACCESS_EXPIRED, VISUAL_ACCESS_LOCKED}
        if normalized is not None and normalized not in allowed:
            raise APIError("INVALID_VISUAL_ACCESS_STATUS", "The visual-access status filter is invalid.", 400)
        rows = self.session.execute(
            select(ECGVisualAccessGrant, User.display_name)
            .join(User, User.user_id == ECGVisualAccessGrant.requester_id)
            .where(ECGVisualAccessGrant.organization_id == organization_id)
            .order_by(ECGVisualAccessGrant.created_at.desc())
        ).all()
        result: list[dict[str, Any]] = []
        for grant, display_name in rows:
            self._expire_visual_access_grant(grant)
            if normalized is None or grant.status == normalized:
                result.append(self._visual_access_grant_dict(grant, requester_name=display_name))
        audit("ECG_VISUAL_ACCESS_REQUESTS_VIEWED", "ecg_visual_access_grant", organization_id=organization_id,
              metadata={"status": normalized, "count": len(result)})
        self.session.commit()
        return result

    def _visual_access_grant_for_doctor(self, request_id: str) -> ECGVisualAccessGrant:
        organization_id = self._require_doctor_for_visual_access_administration()
        grant_id = as_uuid(request_id, "visual-access request")
        grant = self.session.scalar(select(ECGVisualAccessGrant).where(
            ECGVisualAccessGrant.visual_access_grant_uuid == grant_id,
            ECGVisualAccessGrant.organization_id == organization_id,
        ))
        if grant is None:
            raise APIError("VISUAL_ACCESS_REQUEST_NOT_FOUND", "The visual-access request could not be found.", 404)
        return grant

    def approve_visual_access_request(self, request_id: str) -> dict[str, Any]:
        """Doctor-approve once and return the random passcode exactly once."""
        grant = self._visual_access_grant_for_doctor(request_id)
        if self._expire_visual_access_grant(grant):
            self.session.commit()
        if grant.status != VISUAL_ACCESS_PENDING:
            raise APIError("VISUAL_ACCESS_REQUEST_UNAVAILABLE",
                           "This visual-access request is no longer awaiting approval.", 409)
        now = utcnow()
        passcode_minutes = max(1, min(60, int(current_app.extensions[
            "ecg_platform_settings"].visual_access_passcode_minutes)))
        # urlsafe tokens have much more entropy than a human-chosen PIN and
        # exceed crypto.encrypt_password's minimum passphrase length.
        passcode = secrets.token_urlsafe(24)
        grant.secret_envelope = encrypt_password(secrets.token_bytes(32), passcode)
        grant.status = VISUAL_ACCESS_APPROVED
        grant.approved_by = self.principal.user_id
        grant.approved_at = now
        grant.passcode_expires_at = now + timedelta(minutes=passcode_minutes)
        grant.access_expires_at = None
        grant.unlocked_at = None
        grant.failed_attempts = 0
        grant.unlock_token_jti_hash = None
        audit("ECG_VISUAL_ACCESS_APPROVED", "ecg_visual_access_grant", str(grant.visual_access_grant_uuid),
              organization_id=grant.organization_id,
              metadata={"ecg_id": str(grant.ecg_uuid), "passcode_delivery": "response_once"})
        self.session.commit()
        return {
            **self._visual_access_grant_dict(grant),
            "passcode": passcode,
            "passcode_returned_once": True,
            "safety": "Share the passcode through an approved out-of-band channel. It is not stored and cannot be retrieved again.",
        }

    def unlock_visual_access_request(self, request_id: str, passcode: str, *,
                                     expected_ecg_uuid: str | None = None) -> dict[str, Any]:
        """Consume a doctor's passcode and issue one short-lived browser token."""
        grant_id = as_uuid(request_id, "visual-access request")
        grant = self.session.scalar(select(ECGVisualAccessGrant).where(
            ECGVisualAccessGrant.visual_access_grant_uuid == grant_id,
            ECGVisualAccessGrant.requester_id == self.principal.user_id,
        ))
        if grant is None:
            raise APIError("VISUAL_ACCESS_REQUEST_NOT_FOUND", "The visual-access request could not be found.", 404)
        if expected_ecg_uuid is not None and as_uuid(expected_ecg_uuid, "ECG") != grant.ecg_uuid:
            # The route's ECG scope is part of the anti-confusion boundary:
            # an otherwise valid request must never unlock a different record.
            raise APIError("VISUAL_ACCESS_REQUEST_NOT_FOUND", "The visual-access request could not be found.", 404)
        # The normal authenticated ECG lookup prevents a user with a stale or
        # cross-tenant account context from unlocking an unrelated record.
        ecg = self._ecg(grant.ecg_uuid)
        if ecg.organization_id != grant.organization_id:
            raise APIError("VISUAL_ACCESS_REQUEST_NOT_FOUND", "The visual-access request could not be found.", 404)
        if self._expire_visual_access_grant(grant):
            self.session.commit()
        if grant.status != VISUAL_ACCESS_APPROVED or not grant.secret_envelope:
            audit("ECG_VISUAL_ACCESS_UNLOCK_DENIED", "ecg_visual_access_grant", str(grant.visual_access_grant_uuid),
                  success=False, organization_id=grant.organization_id, metadata={"reason": "grant_unavailable"})
            self.session.commit()
            raise APIError("VISUAL_ACCESS_GRANT_UNAVAILABLE", "The visual-access passcode is no longer available.", 409)
        try:
            secret = decrypt_password(bytes(grant.secret_envelope), passcode)
            if len(secret) != 32:
                raise ValueError("Invalid per-grant secret length.")
        except (InvalidTag, ValueError, UnicodeError):
            grant.failed_attempts += 1
            attempts_remaining = max(0, grant.max_attempts - grant.failed_attempts)
            if attempts_remaining == 0:
                grant.status = VISUAL_ACCESS_LOCKED
                self._clear_visual_access_material(grant)
            audit("ECG_VISUAL_ACCESS_UNLOCK_DENIED", "ecg_visual_access_grant", str(grant.visual_access_grant_uuid),
                  success=False, organization_id=grant.organization_id,
                  metadata={"reason": "invalid_passcode", "attempts_remaining": attempts_remaining})
            self.session.commit()
            raise APIError("INVALID_VISUAL_ACCESS_PASSCODE",
                           "The passcode is invalid or no longer available.", 401)
        # The wrapped secret served its sole purpose: authenticate possession
        # of the one-time passcode. Do not keep it after a successful unlock.
        session_minutes = max(1, min(30, int(current_app.extensions[
            "ecg_platform_settings"].visual_access_session_minutes)))
        expires_at = utcnow() + timedelta(minutes=session_minutes)
        token, token_id = issue_ecg_visual_access_token(
            user_id=self.principal.user_id,
            ecg_id=ecg.ecg_uuid,
            grant_id=grant.visual_access_grant_uuid,
            expires_at=expires_at,
        )
        grant.status = VISUAL_ACCESS_UNLOCKED
        grant.unlocked_at = utcnow()
        grant.access_expires_at = expires_at
        self._clear_visual_access_material(grant)
        grant.unlock_token_jti_hash = self._jti_digest(token_id)
        audit("ECG_VISUAL_ACCESS_UNLOCKED", "ecg_visual_access_grant", str(grant.visual_access_grant_uuid),
              organization_id=grant.organization_id,
              metadata={"ecg_id": str(ecg.ecg_uuid), "mode": "inline_visual_only"})
        self.session.commit()
        return {
            "request_id": str(grant.visual_access_grant_uuid),
            "ecg_uuid": str(ecg.ecg_uuid),
            "access_token": token,
            "token_type": "ECG-Visual-Grant",
            "expires_at": expires_at.isoformat(),
            "expires_in_seconds": session_minutes * 60,
            "grant_header": "X-ECG-Visual-Grant",
            "scope": "inline_ecg_visual_only",
        }

    def revoke_visual_access_request(self, request_id: str) -> dict[str, Any]:
        """Revoke a pending, approved, or active visual session as its doctor."""
        grant = self._visual_access_grant_for_doctor(request_id)
        self._expire_visual_access_grant(grant)
        if grant.status in VISUAL_ACCESS_TERMINAL_STATES:
            self.session.commit()
            raise APIError("VISUAL_ACCESS_REQUEST_UNAVAILABLE", "This visual-access request is already closed.", 409)
        grant.status = VISUAL_ACCESS_REVOKED
        grant.revoked_at = utcnow()
        grant.revoked_by = self.principal.user_id
        self._clear_visual_access_material(grant)
        audit("ECG_VISUAL_ACCESS_REVOKED", "ecg_visual_access_grant", str(grant.visual_access_grant_uuid),
              organization_id=grant.organization_id, metadata={"ecg_id": str(grant.ecg_uuid)})
        self.session.commit()
        return self._visual_access_grant_dict(grant)

    def configured_model_info(self) -> dict[str, Any]:
        """Return safe, read-only provenance for the configured research model.

        This intentionally derives metadata from the active adapter rather than
        returning a browser-supplied label or a host filesystem path.  It is
        useful before a clinician requests analysis, but is not evidence of
        clinical validation or a treatment recommendation.
        """
        if self.adapter is None:
            raise APIError("MODEL_CONFIGURATION_ERROR", "No compatible ECG model artifact is configured.", 503)
        try:
            descriptor = self.adapter.descriptor()
        except (OSError, RuntimeError, ValueError) as exc:
            raise APIError("MODEL_CONFIGURATION_ERROR", "The configured ECG model artifact is unavailable.", 503) from exc
        return {
            "status": "CONFIGURED",
            "model_name": descriptor.model_name,
            "architecture": "RAMNV2",
            "framework": "PyTorch",
            "version": descriptor.version,
            "artifact_sha256": descriptor.sha256,
            "preprocessing": descriptor.preprocessing,
            "metrics": descriptor.metrics,
            "supported_source_formats": sorted(WAVEFORM_SUFFIXES),
            "image_source_formats": sorted(JPEG_SUFFIXES),
            "image_policy": "JPEG ECG images are retained for authorized visual review only and are not classifier inputs.",
            "research_only": True,
            "safety": "Research/clinical-decision-support model only. It is not an autonomous diagnosis or treatment recommendation.",
        }

    def _organization_id(self, explicit: str | uuid.UUID | None = None) -> uuid.UUID:
        if self.principal.organization_id is not None:
            if explicit is not None and as_uuid(explicit, "organization") != self.principal.organization_id:
                raise APIError("FORBIDDEN", "You cannot select another hospital organization.", 403)
            return self.principal.organization_id
        if not self.principal.is_super_admin:
            raise APIError("FORBIDDEN", "No hospital organization is assigned to this user.", 403)
        if explicit is None:
            raise APIError("ORGANIZATION_REQUIRED", "A super administrator must select an organization.", 400)
        organization_id = as_uuid(explicit, "organization")
        if self.session.get(Organization, organization_id) is None:
            raise APIError("ORGANIZATION_NOT_FOUND", "The organization could not be found.", 404)
        return organization_id

    def _patient(self, value: str | uuid.UUID, *, include_patient_self: bool = False) -> tuple[Patient, PatientOrganization]:
        patient_id = as_uuid(value, "patient")
        if self.principal.patient_id is not None and self.principal.patient_id != patient_id:
            raise APIError("PATIENT_NOT_FOUND", "The patient could not be found.", 404)
        if self.principal.organization_id is None:
            if not self.principal.is_super_admin:
                raise APIError("PATIENT_NOT_FOUND", "The patient could not be found.", 404)
            patient = self.session.get(Patient, patient_id)
            if patient is None:
                raise APIError("PATIENT_NOT_FOUND", "The patient could not be found.", 404)
            membership = self.session.scalar(select(PatientOrganization).where(PatientOrganization.patient_uuid == patient_id))
            if membership is None:
                raise APIError("PATIENT_NOT_FOUND", "The patient could not be found.", 404)
            return patient, membership
        row = self.session.execute(select(Patient, PatientOrganization).join(
            PatientOrganization, PatientOrganization.patient_uuid == Patient.patient_uuid
        ).where(Patient.patient_uuid == patient_id, PatientOrganization.organization_id == self.principal.organization_id,
                PatientOrganization.active.is_(True))).first()
        if row is None:
            # A 404 prevents discovery of patients in another tenant.
            raise APIError("PATIENT_NOT_FOUND", "The patient could not be found.", 404)
        return row

    def _encounter(self, value: str | uuid.UUID) -> Encounter:
        encounter_id = as_uuid(value, "encounter")
        query = select(Encounter).where(Encounter.encounter_uuid == encounter_id)
        if self.principal.organization_id is not None:
            query = query.where(Encounter.organization_id == self.principal.organization_id)
        encounter = self.session.scalar(query)
        if encounter is None:
            raise APIError("ENCOUNTER_NOT_FOUND", "The encounter could not be found.", 404)
        self._patient(encounter.patient_uuid)
        return encounter

    def _ecg(self, value: str | uuid.UUID) -> ECGRecord:
        ecg_id = as_uuid(value, "ECG")
        query = select(ECGRecord).where(ECGRecord.ecg_uuid == ecg_id)
        if self.principal.organization_id is not None:
            query = query.where(ECGRecord.organization_id == self.principal.organization_id)
        ecg = self.session.scalar(query)
        if ecg is None:
            raise APIError("ECG_NOT_FOUND", "The ECG record could not be found.", 404)
        self._patient(ecg.patient_uuid)
        return ecg

    @staticmethod
    def patient_dict(patient: Patient, membership: PatientOrganization) -> dict[str, Any]:
        return {"patient_uuid": str(patient.patient_uuid), "empi_id": patient.empi_id, "mrn": membership.mrn,
                "first_name": patient.first_name, "middle_name": patient.middle_name, "last_name": patient.last_name,
                "date_of_birth": patient.date_of_birth.isoformat(),
                "administrative_gender": patient.administrative_gender, "created_at": patient.created_at.isoformat()}

    @staticmethod
    def encounter_dict(encounter: Encounter) -> dict[str, Any]:
        return {"encounter_uuid": str(encounter.encounter_uuid), "patient_uuid": str(encounter.patient_uuid),
                "encounter_number": encounter.encounter_number, "encounter_type": encounter.encounter_type,
                "status": encounter.status, "start_time": encounter.start_time.isoformat(), "reason": encounter.reason}

    @staticmethod
    def _digitized_source_ecg_id(ecg: ECGRecord) -> str | None:
        """Return source linkage stored in the existing device field.

        There is no source-asset foreign key in the deployed schema.  The
        derivative record therefore uses a namespaced, immutable device-id
        marker rather than pretending that the reconstructed signal came from
        an acquisition device.  It is intentionally exposed to authorized
        users in every ECG response.
        """
        if not ecg.device_id or not ecg.device_id.startswith(DIGITIZED_DEVICE_PREFIX):
            return None
        source_id = ecg.device_id.removeprefix(DIGITIZED_DEVICE_PREFIX)
        try:
            return str(uuid.UUID(source_id))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def ecg_dict(ecg: ECGRecord) -> dict[str, Any]:
        image_only = ecg.status == "IMAGE_ONLY"
        source_ecg_id = ClinicalService._digitized_source_ecg_id(ecg)
        source_kind = ("ECG_IMAGE" if image_only else
                       "JPEG_DIGITIZED_WAVEFORM" if source_ecg_id else "WAVEFORM")
        result: dict[str, Any] = {
            "ecg_uuid": str(ecg.ecg_uuid), "patient_uuid": str(ecg.patient_uuid),
            "encounter_uuid": str(ecg.encounter_uuid), "accession_number": ecg.accession_number,
            "recorded_at": ecg.recorded_at.isoformat(), "device_id": ecg.device_id,
            "sampling_rate": None if image_only else ecg.sampling_rate,
            "lead_count": None if image_only else ecg.lead_count,
            "duration_seconds": None if image_only else ecg.duration_seconds,
            "source_kind": source_kind, "status": ecg.status,
        }
        if source_ecg_id:
            result["provenance"] = {
                "kind": DIGITIZATION_PROVENANCE,
                "source_ecg_id": source_ecg_id,
                "research_only": True,
            }
        return result

    def create_organization(self, name: str, code: str) -> dict[str, Any]:
        if not self.principal.is_super_admin:
            raise APIError("FORBIDDEN", "Only a super administrator can create hospitals.", 403)
        organization = Organization(name=name, code=code.upper(), status="ACTIVE")
        self.session.add(organization)
        try:
            self.session.commit()
        except IntegrityError as exc:
            self.session.rollback()
            raise APIError("ORGANIZATION_EXISTS", "A hospital with that name or code already exists.", 409) from exc
        audit("HOSPITAL_CREATED", "organization", str(organization.organization_id), organization_id=organization.organization_id)
        self.session.commit()
        return {"organization_id": str(organization.organization_id), "name": organization.name, "code": organization.code,
                "status": organization.status}

    def list_organizations(self) -> list[dict[str, Any]]:
        query = select(Organization).order_by(Organization.name)
        if not self.principal.is_super_admin:
            query = query.where(Organization.organization_id == self._organization_id())
        return [{"organization_id": str(item.organization_id), "name": item.name, "code": item.code,
                 "status": item.status} for item in self.session.scalars(query)]

    def create_user(self, payload: UserRequest) -> dict[str, Any]:
        target_org = self._organization_id(payload.organization_id) if payload.organization_id else self._organization_id()
        if payload.role == "HOSPITAL_ADMIN" and not self.principal.is_super_admin:
            raise APIError("FORBIDDEN", "Only a super administrator can assign hospital administrators.", 403)
        if payload.role == "PATIENT" and not payload.patient_uuid:
            raise APIError("PATIENT_REQUIRED", "A patient account must be linked to a patient record.", 400)
        patient_uuid = as_uuid(payload.patient_uuid, "patient") if payload.patient_uuid else None
        if patient_uuid is not None:
            self._patient(patient_uuid)
        role = self.session.scalar(select(Role).where(Role.name == payload.role))
        if role is None:
            raise APIError("ROLE_NOT_FOUND", "The requested role is unavailable.", 400)
        user = User(organization_id=target_org, patient_id=patient_uuid, email=payload.email.lower(),
                    password_hash=generate_password_hash(payload.password), display_name=payload.display_name)
        self.session.add(user)
        self.session.flush()
        self.session.add(UserRole(user_id=user.user_id, role_id=role.role_id, organization_id=target_org))
        if payload.role in {"DOCTOR", "TECHNICIAN"}:
            self.session.add(Practitioner(user_id=user.user_id, organization_id=target_org))
        try:
            self.session.commit()
        except IntegrityError as exc:
            self.session.rollback()
            raise APIError("USER_EXISTS", "A user with that email or patient association already exists.", 409) from exc
        audit("USER_CREATED", "user", str(user.user_id), organization_id=target_org)
        self.session.commit()
        return {"user_id": str(user.user_id), "email": user.email, "display_name": user.display_name,
                "role": payload.role, "organization_id": str(target_org)}

    def find_patient_matches(self, payload: PatientMatchRequest) -> list[dict[str, Any]]:
        org = self._organization_id()
        query = select(Patient, PatientOrganization).join(
            PatientOrganization, PatientOrganization.patient_uuid == Patient.patient_uuid
        ).where(PatientOrganization.organization_id == org, PatientOrganization.active.is_(True))
        if payload.mrn:
            query = query.where(PatientOrganization.mrn == payload.mrn)
        elif payload.first_name and payload.last_name and payload.date_of_birth:
            query = query.where(func.lower(Patient.first_name) == payload.first_name.lower(),
                                func.lower(Patient.last_name) == payload.last_name.lower(),
                                Patient.date_of_birth == payload.date_of_birth)
        elif payload.phone or payload.abha_id:
            identifier = select(PatientIdentifier.patient_uuid).where(PatientIdentifier.organization_id == org,
                                                                       PatientIdentifier.active.is_(True))
            contact = select(PatientContact.patient_uuid).where(PatientContact.active.is_(True))
            clauses = []
            if payload.phone:
                clauses.append(Patient.patient_uuid.in_(contact.where(PatientContact.value == payload.phone)))
            if payload.abha_id:
                clauses.append(Patient.patient_uuid.in_(identifier.where(PatientIdentifier.identifier_value == payload.abha_id)))
            if clauses:
                query = query.where(*clauses)
        else:
            raise APIError("MATCH_FIELDS_REQUIRED", "Provide MRN, name plus date of birth, phone, or ABHA ID.", 400)
        rows = self.session.execute(query.limit(10)).all()
        return [self.patient_dict(patient, membership) for patient, membership in rows]

    def create_patient(self, payload: PatientRequest) -> dict[str, Any]:
        org = self._organization_id()
        if payload.confirm_existing_patient_uuid:
            patient, membership = self._patient(payload.confirm_existing_patient_uuid)
            audit("PATIENT_REUSED", "patient", str(patient.patient_uuid), organization_id=org)
            self.session.commit()
            return {**self.patient_dict(patient, membership), "reused_existing_patient": True}
        matching_request = PatientMatchRequest(first_name=payload.first_name, last_name=payload.last_name,
                                               date_of_birth=payload.date_of_birth, phone=payload.phone,
                                               mrn=payload.mrn)
        matches = self.find_patient_matches(matching_request)
        if matches:
            raise APIError("POSSIBLE_PATIENT_MATCH", "A possible patient match exists. Review it and explicitly "
                           "select the existing patient or correct the registration details.", 409)
        patient = Patient(empi_id=f"EMPI-{uuid.uuid4().hex[:12].upper()}", first_name=payload.first_name,
                          middle_name=payload.middle_name, last_name=payload.last_name,
                          date_of_birth=payload.date_of_birth,
                          administrative_gender=payload.administrative_gender or payload.sex_at_birth)
        self.session.add(patient)
        self.session.flush()
        mrn = payload.mrn or f"{str(org).replace('-', '')[:6].upper()}-{uuid.uuid4().hex[:8].upper()}"
        membership = PatientOrganization(patient_uuid=patient.patient_uuid, organization_id=org, mrn=mrn)
        self.session.add(membership)
        self.session.add(PatientIdentifier(patient_uuid=patient.patient_uuid, organization_id=org,
                                           identifier_system="urn:ecg-health:mrn", identifier_type="MRN",
                                           identifier_value=mrn, assigning_organization=str(org)))
        if payload.phone:
            self.session.add(PatientContact(patient_uuid=patient.patient_uuid, contact_type="phone", value=payload.phone,
                                            use="mobile"))
        if payload.email:
            self.session.add(PatientContact(patient_uuid=patient.patient_uuid, contact_type="email", value=payload.email,
                                            use="home"))
        for identifier in payload.identifiers:
            self.session.add(PatientIdentifier(patient_uuid=patient.patient_uuid, organization_id=org,
                                               identifier_system=identifier.identifier_system,
                                               identifier_type=identifier.identifier_type,
                                               identifier_value=identifier.identifier_value,
                                               assigning_organization=identifier.assigning_organization))
        try:
            self.session.commit()
        except IntegrityError as exc:
            self.session.rollback()
            raise APIError("IDENTIFIER_EXISTS", "An identifier or MRN is already active for this hospital.", 409) from exc
        audit("PATIENT_CREATED", "patient", str(patient.patient_uuid), organization_id=org)
        self.session.commit()
        return self.patient_dict(patient, membership)

    def list_patients(self, search: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        org = self._organization_id()
        query = select(Patient, PatientOrganization).join(
            PatientOrganization, PatientOrganization.patient_uuid == Patient.patient_uuid
        ).where(PatientOrganization.organization_id == org, PatientOrganization.active.is_(True))
        if self.principal.patient_id is not None:
            query = query.where(Patient.patient_uuid == self.principal.patient_id)
        if search:
            term = f"%{search.lower()}%"
            query = query.where((func.lower(Patient.first_name).like(term)) | (func.lower(Patient.last_name).like(term)) |
                                (func.lower(PatientOrganization.mrn).like(term)) | (func.lower(Patient.empi_id).like(term)))
        rows = self.session.execute(query.order_by(Patient.last_name, Patient.first_name).limit(min(max(limit, 1), 100))).all()
        return [self.patient_dict(patient, membership) for patient, membership in rows]

    def get_patient(self, patient_uuid: str) -> dict[str, Any]:
        patient, membership = self._patient(patient_uuid)
        audit("PATIENT_VIEWED", "patient", str(patient.patient_uuid), organization_id=membership.organization_id)
        self.session.commit()
        return self.patient_dict(patient, membership)

    def create_encounter(self, payload: EncounterRequest) -> dict[str, Any]:
        org = self._organization_id()
        patient, _ = self._patient(payload.patient_uuid)
        encounter = Encounter(patient_uuid=patient.patient_uuid, organization_id=org,
                              encounter_number=f"ENC-{uuid.uuid4().hex[:12].upper()}",
                              encounter_type=payload.encounter_type, reason=payload.reason)
        self.session.add(encounter)
        self.session.commit()
        audit("ENCOUNTER_CREATED", "encounter", str(encounter.encounter_uuid), organization_id=org)
        self.session.commit()
        return self.encounter_dict(encounter)

    def list_encounters(self, patient_uuid: str) -> list[dict[str, Any]]:
        patient, _ = self._patient(patient_uuid)
        query = select(Encounter).where(Encounter.patient_uuid == patient.patient_uuid)
        if self.principal.organization_id is not None:
            query = query.where(Encounter.organization_id == self.principal.organization_id)
        return [self.encounter_dict(row) for row in self.session.scalars(query.order_by(Encounter.start_time.desc()))]

    def list_ecgs(self, patient_uuid: str) -> list[dict[str, Any]]:
        patient, _ = self._patient(patient_uuid)
        query = select(ECGRecord).where(ECGRecord.patient_uuid == patient.patient_uuid)
        if self.principal.organization_id is not None:
            query = query.where(ECGRecord.organization_id == self.principal.organization_id)
        records = []
        for ecg in self.session.scalars(query.order_by(ECGRecord.recorded_at.desc())):
            file = self._file_for_ecg(ecg)
            records.append({**self.ecg_dict(ecg), "filename": file.original_filename, "content_type": file.content_type,
                            "size_bytes": file.size_bytes})
        return records

    def upload_ecg(self, encounter_uuid: str, payload: bytes, filename: str, device_id: str | None = None) -> dict[str, Any]:
        suffix = Path(filename).suffix.lower()
        if suffix not in WAVEFORM_SUFFIXES | JPEG_SUFFIXES:
            raise APIError("UNSUPPORTED_ECG_FORMAT", "Only .mat, .csv, .jpg, and .jpeg ECG files are accepted.", 400)
        if not payload:
            raise APIError("EMPTY_FILE", "The ECG upload is empty.", 400)
        if len(payload) > current_app.config["MAX_CONTENT_LENGTH"]:
            raise APIError("FILE_TOO_LARGE", "The ECG upload exceeds the configured file limit.", 413)
        image_only = suffix in JPEG_SUFFIXES
        if image_only:
            validate_jpeg_image(payload)
        else:
            if self.adapter is None:
                raise APIError("MODEL_CONFIGURATION_ERROR", "No compatible ECG model is configured for file validation.", 503)
            self.adapter.validate(payload, filename)
        encounter = self._encounter(encounter_uuid)
        org = self._organization_id()
        ecg = ECGRecord(patient_uuid=encounter.patient_uuid, encounter_uuid=encounter.encounter_uuid,
                        organization_id=org, accession_number=f"ECG-{uuid.uuid4().hex[:12].upper()}",
                        device_id=device_id, status="IMAGE_ONLY" if image_only else "VALIDATING")
        self.session.add(ecg)
        self.session.flush()
        object_key = f"organizations/{org}/ecgs/{ecg.ecg_uuid}/original/{uuid.uuid4().hex}{suffix}"
        content_type = (JPEG_CONTENT_TYPE if image_only else
                        "application/x-matlab-data" if suffix == ".mat" else "text/csv")
        stored = self.storage.put_bytes(object_key, payload, content_type)
        self.session.add(ECGFile(ecg_uuid=ecg.ecg_uuid, storage_uri=stored.uri, object_key=stored.object_key,
                                 sha256=stored.sha256, content_type=stored.content_type,
                                 original_filename=Path(filename).name, size_bytes=stored.size_bytes))
        if not image_only:
            ecg.status = "READY"
        self.session.commit()
        audit("ECG_UPLOADED", "ecg", str(ecg.ecg_uuid), organization_id=org,
              metadata={"format": suffix.removeprefix("."), "size_bytes": stored.size_bytes,
                        "source_kind": "ECG_IMAGE" if image_only else "WAVEFORM"})
        self.session.commit()
        return {**self.ecg_dict(ecg), "filename": Path(filename).name, "content_type": stored.content_type,
                "size_bytes": stored.size_bytes}

    @staticmethod
    def _serialize_digitized_trace(trace: DigitizedTrace, output_format: str) -> tuple[bytes, str, str]:
        """Encode the recovered numerical trace in one supported input format."""
        if output_format == "csv":
            output = BytesIO()
            np.savetxt(output, trace.waveform, delimiter=",", fmt="%.8g")
            return output.getvalue(), ".csv", "text/csv"
        if output_format == "mat":
            output = BytesIO()
            # ``val`` matches the project parser's explicit, single-lead MAT
            # convention.  The source JPEG is always retained separately.
            savemat(output, {"val": trace.waveform.reshape(1, -1)})
            return output.getvalue(), ".mat", "application/x-matlab-data"
        raise APIError("INVALID_DIGITIZATION_FORMAT", "Choose either 'csv' or 'mat' for the derived waveform.", 400)

    def digitize_and_analyze_image(self, source_ecg_uuid: str, output_format: str) -> dict[str, Any]:
        """Create and analyse an encrypted numerical derivative of one JPEG.

        This is intentionally synchronous and explicit: callers must opt into
        a constrained research experiment, and no JPEG is silently converted
        or classified during upload.  The original source record/file is never
        modified; the derivative receives a separate accession, object key,
        checksum, audit event, and source linkage.
        """
        source = self._ecg(source_ecg_uuid)
        source_file = self._file_for_ecg(source)
        if source.status != "IMAGE_ONLY" or not self._is_image_only_file(source_file):
            raise APIError(
                "DIGITIZATION_SOURCE_REQUIRED",
                "Experimental digitization is available only for an authorized JPEG ECG image record.",
                409,
            )
        if self.adapter is None:
            raise APIError("MODEL_CONFIGURATION_ERROR", "No compatible ECG model artifact is configured.", 503)

        source_bytes = self.storage.get_bytes(source_file.object_key)
        if hashlib.sha256(source_bytes).hexdigest() != source_file.sha256:
            raise APIError("ASSET_INTEGRITY_ERROR", "The source ECG image failed an integrity check.", 500)
        trace = digitize_ecg_jpeg(source_bytes)
        derived_bytes, suffix, content_type = self._serialize_digitized_trace(trace, output_format)
        derived_filename = f"digitized-from-{source_file.ecg_file_id}{suffix}"
        try:
            # Validate the serialized output through the same parser used for
            # every normal upload.  A quality-gated image result is not trusted
            # merely because this service generated it.
            validated = self.adapter.validate(derived_bytes, derived_filename)
        except (ValueError, TypeError, OSError) as exc:
            raise APIError(
                "ECG_IMAGE_DIGITIZATION_REJECTED",
                "The recovered trace did not meet the configured numerical waveform requirements.",
                422,
            ) from exc
        if validated.size != 3600:
            raise APIError(
                "ECG_IMAGE_DIGITIZATION_REJECTED",
                "The recovered trace did not contain the required 3,600 samples.",
                422,
            )

        derived = ECGRecord(
            patient_uuid=source.patient_uuid,
            encounter_uuid=source.encounter_uuid,
            organization_id=source.organization_id,
            accession_number=f"ECG-DIGI-{uuid.uuid4().hex[:12].upper()}",
            # This field intentionally carries a namespaced provenance marker,
            # not an acquisition-device assertion. It avoids a schema change
            # while keeping source linkage visible in all record responses.
            device_id=f"{DIGITIZED_DEVICE_PREFIX}{source.ecg_uuid}",
            sampling_rate=OUTPUT_SAMPLING_RATE_HZ,
            lead_count=1,
            duration_seconds=10.0,
            status="READY",
        )
        self.session.add(derived)
        self.session.flush()
        object_key = (
            f"organizations/{source.organization_id}/ecgs/{derived.ecg_uuid}/"
            f"derived/jpeg-trace/{uuid.uuid4().hex}{suffix}"
        )
        stored = self.storage.put_bytes(object_key, derived_bytes, content_type)
        self.session.add(ECGFile(
            ecg_uuid=derived.ecg_uuid,
            storage_uri=stored.uri,
            object_key=stored.object_key,
            sha256=stored.sha256,
            content_type=stored.content_type,
            original_filename=derived_filename,
            size_bytes=stored.size_bytes,
            purpose=DIGITIZED_FILE_PURPOSE,
        ))
        self.session.commit()
        audit(
            "ECG_IMAGE_DIGITIZED",
            "ecg",
            str(derived.ecg_uuid),
            organization_id=derived.organization_id,
            metadata={
                "provenance": DIGITIZATION_PROVENANCE,
                "source_ecg_id": str(source.ecg_uuid),
                "output_format": output_format,
                "quality_gate": trace.quality["quality_gate"],
            },
        )
        self.session.commit()

        analysis = self.create_analysis(str(derived.ecg_uuid))
        completed = self.run_analysis(analysis.ai_analysis_uuid)
        # Persist origin and quality alongside the model output so later report
        # viewers cannot mistake this recovered pixel trace for raw telemetry.
        raw_output = dict(completed.raw_output or {})
        raw_output["input_provenance"] = {
            "kind": DIGITIZATION_PROVENANCE,
            "source_ecg_id": str(source.ecg_uuid),
            "source_file_sha256": source_file.sha256,
            "derived_file_sha256": stored.sha256,
            "output_format": output_format,
            "quality": trace.quality,
            "limitations": (
                "Derived from chart pixels for experimental research only. It is not a calibrated ECG acquisition, "
                "and its lead identity, amplitude units, timing, and diagnostic fidelity are unknown."
            ),
        }
        completed.raw_output = raw_output
        self.session.commit()
        audit(
            "ECG_IMAGE_DIGITIZATION_ANALYZED",
            "ai_analysis",
            str(completed.ai_analysis_uuid),
            organization_id=derived.organization_id,
            metadata={"provenance": DIGITIZATION_PROVENANCE, "source_ecg_id": str(source.ecg_uuid)},
        )
        self.session.commit()
        return {
            "source_ecg_id": str(source.ecg_uuid),
            "derived_ecg": {
                **self.ecg_dict(derived),
                "filename": derived_filename,
                "content_type": stored.content_type,
                "size_bytes": stored.size_bytes,
            },
            "analysis": self.analysis_dict(completed),
            "digitization": {
                "status": "COMPLETED",
                "provenance": DIGITIZATION_PROVENANCE,
                "source_kind": "JPEG_DIGITIZED_WAVEFORM",
                "source_format": JPEG_CONTENT_TYPE,
                "output_format": output_format,
                "source_integrity_verified": True,
                "quality": trace.quality,
                "limitations": (
                    "Experimental chart-trace digitization for research only. It is not clinically validated and "
                    "does not establish a diagnosis, treatment recommendation, or equivalent raw ECG acquisition."
                ),
            },
        }

    def _file_for_ecg(self, ecg: ECGRecord) -> ECGFile:
        # Digitized records are separate derivatives, not replacement source
        # files.  Their sole asset uses a distinct purpose so provenance stays
        # queryable without changing the existing database schema.
        file = self.session.scalar(select(ECGFile).where(
            ECGFile.ecg_uuid == ecg.ecg_uuid,
            ECGFile.purpose.in_(("ORIGINAL", DIGITIZED_FILE_PURPOSE)),
        ).order_by(ECGFile.created_at.asc()))
        if file is None:
            raise APIError("ECG_FILE_NOT_FOUND", "The ECG waveform asset is unavailable.", 404)
        return file

    @staticmethod
    def _is_image_only_file(file: ECGFile) -> bool:
        """Return true only for the server-validated JPEG source type."""
        return file.content_type == JPEG_CONTENT_TYPE

    @staticmethod
    def _image_only_error() -> APIError:
        return APIError(
            "ECG_IMAGE_ONLY",
            "This record is a JPEG ECG image. The waveform viewer and configured RAMNV2 model require a "
            "validated numerical .mat or .csv waveform, so no AI prediction was created.",
            409,
        )

    def get_ecg(self, ecg_uuid: str) -> dict[str, Any]:
        ecg = self._ecg(ecg_uuid)
        file = self._file_for_ecg(ecg)
        audit("ECG_VIEWED", "ecg", str(ecg.ecg_uuid), organization_id=ecg.organization_id)
        self.session.commit()
        return {**self.ecg_dict(ecg), "filename": file.original_filename, "content_type": file.content_type,
                "size_bytes": file.size_bytes, "file": {"filename": file.original_filename,
                "content_type": file.content_type, "size_bytes": file.size_bytes, "sha256": file.sha256}}

    def protected_preview(self, ecg_uuid: str) -> bytes:
        """Return a generic blurred image for a scoped, non-doctor viewer.

        The record lookup still enforces tenant and patient scope, but this
        method deliberately never calls ``_file_for_ecg`` or storage. It is a
        static redaction representation, not a decodable/encrypted form of the
        source ECG and not research adversarial camouflage.
        """
        ecg = self._ecg(ecg_uuid)
        audit("ECG_REDACTED_PREVIEW_VIEWED", "ecg", str(ecg.ecg_uuid),
              organization_id=ecg.organization_id,
              metadata={"representation": "static_blurred_redaction"})
        self.session.commit()
        return protected_ecg_preview_png()

    def visual_image(self, ecg_uuid: str) -> tuple[bytes, str]:
        """Return an inline-only original visual after doctor/grant approval.

        JPEG source records are rendered as their stored image bytes. Numerical
        records are rendered server-side to PNG so a grant recipient never
        receives the source MAT/CSV or the raw waveform sample array.  This is
        still sensitive visual content and is protected by the same narrow
        doctor-or-scoped-grant check as Grad-CAM.
        """
        ecg = self._ecg(ecg_uuid)
        self._require_ecg_visual_access(ecg)
        file = self._file_for_ecg(ecg)
        if self._is_image_only_file(file):
            data = self.storage.get_bytes(file.object_key)
            if hashlib.sha256(data).hexdigest() != file.sha256:
                raise APIError("ASSET_INTEGRITY_ERROR", "The stored ECG asset failed an integrity check.", 500)
            content_type = JPEG_CONTENT_TYPE
        else:
            waveform, _file = self._raw_ecg_waveform(ecg)
            assert self.adapter is not None
            data = self.adapter.render_waveform(waveform)
            content_type = "image/png"
        audit("ECG_INLINE_VISUAL_VIEWED", "ecg", str(ecg.ecg_uuid), organization_id=ecg.organization_id,
              metadata={"source_kind": "image" if self._is_image_only_file(file) else "rendered_waveform"})
        self.session.commit()
        return data, content_type

    def ecg_security(self, ecg_uuid: str) -> dict[str, Any]:
        """Return the true storage-security posture for an authorized ECG asset."""
        ecg = self._ecg(ecg_uuid)
        file = self._file_for_ecg(ecg)
        protection = self.storage.protection_status(file.object_key)
        audit("ECG_SECURITY_VIEWED", "ecg", str(ecg.ecg_uuid), organization_id=ecg.organization_id)
        self.session.commit()
        return {
            "ecg_uuid": str(ecg.ecg_uuid),
            "storage": {
                "encrypted_at_rest": protection.encrypted_at_rest,
                "algorithm": protection.algorithm,
                "authenticated_encryption": protection.authenticated_encryption,
                "key_management": protection.key_management,
                "legacy_unencrypted": protection.legacy_unencrypted,
            },
            "integrity": {
                "algorithm": "SHA-256",
                "verified_on_authorized_read": True,
                "fingerprint": file.sha256[:16],
            },
            "access": {
                "tenant_scoped": True,
                "server_side_rbac": True,
                "audited": True,
                "original_ecg_requires_role": ORIGINAL_ECG_VIEW_ROLE,
                "original_ecg_available_to_current_user": self.can_view_original_ecg,
                "protected_preview_available": True,
            },
            "research_camouflage": {
                "used": False,
                "reason": "Adversarial waveform camouflage is not encryption and is not used for clinical ECG records. "
                          "The protected preview is a non-signal redaction, not a camouflage transform.",
            },
        }

    def download_ecg(self, ecg_uuid: str) -> tuple[bytes, ECGFile]:
        ecg = self._ecg(ecg_uuid)
        self._require_original_ecg_view(ecg)
        file = self._file_for_ecg(ecg)
        data = self.storage.get_bytes(file.object_key)
        if hashlib.sha256(data).hexdigest() != file.sha256:
            raise APIError("ASSET_INTEGRITY_ERROR", "The stored ECG asset failed an integrity check.", 500)
        audit("ECG_VIEWED", "ecg_file", str(file.ecg_file_id), organization_id=ecg.organization_id)
        self.session.commit()
        return data, file

    def download_ecg_url(self, ecg_uuid: str) -> tuple[ECGRecord, str | None]:
        """Issue a direct-object URL only after the literal doctor-role check."""
        ecg = self._ecg(ecg_uuid)
        self._require_original_ecg_view(ecg)
        file = self._file_for_ecg(ecg)
        signed = self.storage.presigned_get(file.object_key)
        audit("ECG_DOWNLOAD_URL_ISSUED", "ecg_file", str(file.ecg_file_id),
              organization_id=ecg.organization_id,
              metadata={"storage_redirect": bool(signed)})
        self.session.commit()
        return ecg, signed

    def _raw_ecg_waveform(self, ecg: ECGRecord) -> tuple[Any, ECGFile]:
        file = self._file_for_ecg(ecg)
        if self._is_image_only_file(file):
            raise self._image_only_error()
        if self.adapter is None:
            raise APIError("MODEL_CONFIGURATION_ERROR", "No compatible ECG model is configured for waveform validation.", 503)
        data = self.storage.get_bytes(file.object_key)
        if hashlib.sha256(data).hexdigest() != file.sha256:
            raise APIError("ASSET_INTEGRITY_ERROR", "The stored ECG asset failed an integrity check.", 500)
        return self.adapter.validate(data, file.original_filename), file

    def waveform_data(self, ecg_uuid: str) -> dict[str, Any]:
        """Return the actual authorized single-lead data for the web viewer."""
        ecg = self._ecg(ecg_uuid)
        self._require_ecg_visual_access(ecg)
        waveform, _file = self._raw_ecg_waveform(ecg)
        audit("ECG_WAVEFORM_VIEWED", "ecg", str(ecg.ecg_uuid), organization_id=ecg.organization_id,
              metadata={"sampling_rate_hz": ecg.sampling_rate, "sample_count": int(waveform.size)})
        self.session.commit()
        return {"ecg_uuid": str(ecg.ecg_uuid), "sampling_rate_hz": ecg.sampling_rate,
                "duration_seconds": ecg.duration_seconds, "lead_count": ecg.lead_count,
                "amplitude_units": "dataset units", "samples": waveform.astype(float).tolist()}

    def waveform_png(self, ecg: ECGRecord) -> bytes:
        """Create a non-persistent waveform rendering for an authorized report."""
        self._require_original_ecg_view(ecg)
        waveform, _file = self._raw_ecg_waveform(ecg)
        assert self.adapter is not None
        return self.adapter.render_waveform(waveform)

    def create_analysis(self, ecg_uuid: str) -> AIAnalysis:
        ecg = self._ecg(ecg_uuid)
        file = self._file_for_ecg(ecg)
        if self._is_image_only_file(file):
            raise self._image_only_error()
        if self.adapter is None:
            raise APIError("MODEL_CONFIGURATION_ERROR", "No compatible ECG model artifact is configured.", 503)
        if ecg.status not in {"READY", "ANALYZED", "REVIEW_REQUIRED", "REVIEWED"}:
            raise APIError("ECG_NOT_READY", "This ECG is not ready for analysis.", 409)
        version = model_version_for(self.adapter, self.session)
        analysis = AIAnalysis(ecg_uuid=ecg.ecg_uuid, organization_id=ecg.organization_id,
                              model_version_uuid=version.model_version_uuid, status="QUEUED",
                              input_sha256=file.sha256)
        self.session.add(analysis)
        ecg.status = "PROCESSING"
        self.session.commit()
        audit("ECG_ANALYSIS_STARTED", "ai_analysis", str(analysis.ai_analysis_uuid), organization_id=ecg.organization_id,
              metadata={"model_version": version.version})
        self.session.commit()
        return analysis

    def run_analysis(self, analysis_uuid: str | uuid.UUID) -> AIAnalysis:
        analysis_id = as_uuid(analysis_uuid, "analysis")
        analysis = self.session.get(AIAnalysis, analysis_id)
        if analysis is None:
            raise APIError("ANALYSIS_NOT_FOUND", "The analysis could not be found.", 404)
        ecg = self._ecg(analysis.ecg_uuid)
        if self.adapter is None:
            raise APIError("MODEL_CONFIGURATION_ERROR", "No compatible ECG model artifact is configured.", 503)
        file = self._file_for_ecg(ecg)
        if self._is_image_only_file(file):
            raise self._image_only_error()
        analysis.status = "PROCESSING"
        self.session.commit()
        try:
            raw = self.storage.get_bytes(file.object_key)
            if hashlib.sha256(raw).hexdigest() != file.sha256:
                raise ValueError("Stored waveform integrity check failed.")
            result = self.adapter.predict(raw, file.original_filename, include_explanation=True)
            if result.explanation_png:
                key = f"organizations/{ecg.organization_id}/ecgs/{ecg.ecg_uuid}/analyses/{analysis.ai_analysis_uuid}/gradcam.png"
                explanation = self.storage.put_bytes(key, result.explanation_png, "image/png")
                analysis.explanation_uri = explanation.uri
                analysis.explanation_sha256 = explanation.sha256
            report = result.report
            analysis.predicted_label = report["label"]
            analysis.predicted_label_name = report["label_name"]
            analysis.confidence = report["confidence"]
            analysis.processing_time_ms = int(report["duration_ms"])
            analysis.inference_timestamp = utcnow()
            analysis.raw_output = report
            analysis.status = "COMPLETED"
            for rank, item in enumerate(report["top_predictions"], start=1):
                self.session.add(AIPrediction(ai_analysis_uuid=analysis.ai_analysis_uuid, rank=rank,
                                              label=item["label"], label_name=item["label_name"],
                                              score=item["probability"]))
            ecg.status = "REVIEW_REQUIRED"
            self.session.commit()
            audit("ECG_ANALYSIS_COMPLETED", "ai_analysis", str(analysis.ai_analysis_uuid),
                  organization_id=ecg.organization_id,
                  metadata={"model_version_id": str(analysis.model_version_uuid)})
            self.session.commit()
            return analysis
        except Exception:
            analysis.status = "FAILED"
            analysis.error_message = "Inference failed. The stored ECG was retained for authorized investigation."
            ecg.status = "FAILED"
            self.session.commit()
            audit("ECG_ANALYSIS_FAILED", "ai_analysis", str(analysis.ai_analysis_uuid), success=False,
                  organization_id=ecg.organization_id)
            self.session.commit()
            raise

    def analysis_dict(self, analysis: AIAnalysis) -> dict[str, Any]:
        predictions = self.session.scalars(select(AIPrediction).where(
            AIPrediction.ai_analysis_uuid == analysis.ai_analysis_uuid
        ).order_by(AIPrediction.rank)).all()
        version = self.session.get(AIModelVersion, analysis.model_version_uuid)
        return {"ai_analysis_uuid": str(analysis.ai_analysis_uuid), "ecg_uuid": str(analysis.ecg_uuid),
                "status": analysis.status, "prediction": analysis.predicted_label,
                "prediction_name": analysis.predicted_label_name, "confidence": analysis.confidence,
                "model_version": version.version if version else None,
                "model_artifact_sha256": version.artifact_sha256 if version else None,
                "inference_timestamp": analysis.inference_timestamp.isoformat() if analysis.inference_timestamp else None,
                "processing_time_ms": analysis.processing_time_ms,
                "top_predictions": [{"rank": row.rank, "label": row.label, "label_name": row.label_name,
                                     "score": row.score} for row in predictions],
                "has_explanation": bool(analysis.explanation_uri),
                "safety": "AI-generated research/clinical-decision-support output. It requires qualified clinician "
                          "review and is not an autonomous diagnosis or treatment recommendation."}

    def latest_analysis(self, ecg_uuid: str) -> dict[str, Any]:
        ecg = self._ecg(ecg_uuid)
        analysis = self.session.scalar(select(AIAnalysis).where(AIAnalysis.ecg_uuid == ecg.ecg_uuid)
                                       .order_by(AIAnalysis.created_at.desc()))
        if analysis is None:
            raise APIError("ANALYSIS_NOT_FOUND", "No AI analysis exists for this ECG.", 404)
        audit("AI_RESULT_VIEWED", "ai_analysis", str(analysis.ai_analysis_uuid), organization_id=ecg.organization_id)
        self.session.commit()
        return self.analysis_dict(analysis)

    def assessment_suggestion(self, ecg_uuid: str) -> dict[str, str]:
        """Return a clinician-editable, research-only observation draft.

        This is deliberately a faithful description of one completed model
        output, rather than an interpretation of the waveform.  It never
        creates a review, diagnosis, or prescription, and callers must keep
        the clinician-authored assessment separate from this editable draft.
        """
        ecg = self._ecg(ecg_uuid)
        analysis = self.session.scalar(select(AIAnalysis).where(
            AIAnalysis.ecg_uuid == ecg.ecg_uuid,
            AIAnalysis.status == "COMPLETED",
        ).order_by(AIAnalysis.created_at.desc()))
        if analysis is None:
            raise APIError(
                "ANALYSIS_REQUIRED",
                "A completed AI analysis is required before an assessment draft is available.",
                409,
            )

        label = (
            analysis.predicted_label_name
            or analysis.predicted_label
            or "an unavailable model label"
        ).strip()
        score_text = ""
        if (analysis.confidence is not None and np.isfinite(analysis.confidence)
                and 0.0 <= analysis.confidence <= 1.0):
            score_text = f" The stored uncalibrated model score is {analysis.confidence:.1%}."

        suggestion = (
            "Research-only editable draft — replace or delete before saving.\n\n"
            f"The completed RAMNV2 research analysis recorded the model output label as “{label}”."
            f"{score_text}\n\n"
            "Independently inspect the original ECG waveform, recording quality, and relevant clinical context before "
            "documenting any clinician-authored assessment. This text records an unverified model output only; it does "
            "not provide a clinical finding, diagnosis, triage decision, treatment plan, or prescription."
        )
        safety = (
            "Editable research-only text only. It is not a diagnosis or calibrated disease probability and must not be "
            "used to select treatment, prescribe medication, or recommend a medicine, dose, route, frequency, timing, "
            "or duration. A qualified clinician must independently author the final assessment and any prescription "
            "record."
        )
        audit("AI_ASSESSMENT_SUGGESTION_VIEWED", "ai_analysis", str(analysis.ai_analysis_uuid),
              organization_id=ecg.organization_id, metadata={"purpose": "editable_research_assessment_draft"})
        self.session.commit()
        return {"analysis_id": str(analysis.ai_analysis_uuid), "suggestion": suggestion, "safety": safety}

    def explanation_bytes(self, ecg_uuid: str) -> bytes:
        ecg = self._ecg(ecg_uuid)
        # Grad-CAM includes a plotted normalized source trace. It is not safe
        # to treat that rendering as less sensitive than the original ECG. A
        # doctor-approved visual grant may view it inline, but cannot export a
        # raw source file or waveform-bearing PDF.
        self._require_ecg_visual_access(ecg)
        analysis = self.session.scalar(select(AIAnalysis).where(AIAnalysis.ecg_uuid == ecg.ecg_uuid,
                                                                  AIAnalysis.status == "COMPLETED")
                                       .order_by(AIAnalysis.created_at.desc()))
        if analysis is None or not analysis.explanation_uri:
            raise APIError("EXPLANATION_NOT_FOUND", "No model-generated explanation exists for this ECG.", 404)
        if analysis.explanation_uri.startswith("local://"):
            key = analysis.explanation_uri.removeprefix("local://")
        elif analysis.explanation_uri.startswith("s3://"):
            key = analysis.explanation_uri.split("/", 3)[-1]
        else:
            raise APIError("EXPLANATION_NOT_FOUND", "The explanation storage reference is unsupported.", 404)
        payload = self.storage.get_bytes(key)
        if analysis.explanation_sha256 and hashlib.sha256(payload).hexdigest() != analysis.explanation_sha256:
            raise APIError("ASSET_INTEGRITY_ERROR", "The explanation asset failed an integrity check.", 500)
        audit("AI_RESULT_VIEWED", "explanation", str(analysis.ai_analysis_uuid), organization_id=ecg.organization_id)
        self.session.commit()
        return payload

    def save_review(self, ecg_uuid: str, payload: ReviewRequest) -> dict[str, Any]:
        ecg = self._ecg(ecg_uuid)
        analysis = self.session.scalar(select(AIAnalysis).where(AIAnalysis.ecg_uuid == ecg.ecg_uuid,
                                                                  AIAnalysis.status == "COMPLETED")
                                       .order_by(AIAnalysis.created_at.desc()))
        if analysis is None:
            raise APIError("ANALYSIS_REQUIRED", "A completed AI analysis is required before a clinician review.", 409)
        review = DoctorReview(ecg_uuid=ecg.ecg_uuid, ai_analysis_uuid=analysis.ai_analysis_uuid,
                              organization_id=ecg.organization_id, reviewed_by=self.principal.user_id,
                              review_status=payload.review_status, assessment=payload.doctor_assessment)
        self.session.add(review)
        condition: Condition | None = None
        if payload.diagnosis:
            condition = Condition(patient_uuid=ecg.patient_uuid, encounter_uuid=ecg.encounter_uuid,
                                  organization_id=ecg.organization_id, display=payload.diagnosis.display,
                                  code_system=payload.diagnosis.code_system, code=payload.diagnosis.code,
                                  recorded_by=self.principal.user_id)
            self.session.add(condition)
        if payload.clinical_note:
            self.session.add(ClinicalNote(patient_uuid=ecg.patient_uuid, encounter_uuid=ecg.encounter_uuid,
                                          organization_id=ecg.organization_id, author_id=self.principal.user_id,
                                          note_type="ECG_REVIEW", body=payload.clinical_note))
        prescription: Prescription | None = None
        if payload.prescription_items:
            prescription = Prescription(patient_uuid=ecg.patient_uuid, encounter_uuid=ecg.encounter_uuid,
                                        organization_id=ecg.organization_id, prescriber_id=self.principal.user_id,
                                        status="DRAFT", instructions=payload.prescription_instructions)
            self.session.add(prescription)
            self.session.flush()
            for item in payload.prescription_items:
                self.session.add(PrescriptionItem(prescription_uuid=prescription.prescription_uuid,
                                                  medicine=item.medicine, dose=item.dose, route=item.route,
                                                  frequency=item.frequency, duration=item.duration,
                                                  instructions=item.instructions))
        report = self.session.scalar(select(DiagnosticReport).where(DiagnosticReport.ecg_uuid == ecg.ecg_uuid))
        if report is None:
            report = DiagnosticReport(ecg_uuid=ecg.ecg_uuid, organization_id=ecg.organization_id,
                                      author_id=self.principal.user_id)
            self.session.add(report)
        report.conclusion = payload.doctor_assessment
        report.status = "PRELIMINARY"
        ecg.status = "REVIEWED"
        self.session.commit()
        audit("DIAGNOSIS_CREATED" if condition else "ECG_REVIEWED", "ecg", str(ecg.ecg_uuid),
              organization_id=ecg.organization_id)
        if prescription:
            audit("PRESCRIPTION_CREATED", "prescription", str(prescription.prescription_uuid),
                  organization_id=ecg.organization_id)
        self.session.commit()
        return {"doctor_review_uuid": str(review.doctor_review_uuid), "ecg_uuid": str(ecg.ecg_uuid), "status": ecg.status,
                "doctor_assessment": report.conclusion,
                "condition_uuid": str(condition.condition_uuid) if condition else None,
                "prescription_uuid": str(prescription.prescription_uuid) if prescription else None,
                "report_uuid": str(report.report_uuid),
                "safety": "This assessment and any prescription items are clinician-authored. They are not generated "
                          "by the ECG model."}

    def list_analyses(self, patient_uuid: str) -> list[dict[str, Any]]:
        patient, _ = self._patient(patient_uuid)
        query = (select(AIAnalysis).join(ECGRecord, ECGRecord.ecg_uuid == AIAnalysis.ecg_uuid)
                 .where(ECGRecord.patient_uuid == patient.patient_uuid))
        if self.principal.organization_id is not None:
            query = query.where(AIAnalysis.organization_id == self.principal.organization_id)
        return [self.analysis_dict(item) for item in self.session.scalars(query.order_by(AIAnalysis.created_at.desc()))]

    def list_reviews(self, patient_uuid: str) -> list[dict[str, Any]]:
        patient, _ = self._patient(patient_uuid)
        query = (select(DoctorReview, User).join(ECGRecord, ECGRecord.ecg_uuid == DoctorReview.ecg_uuid)
                 .join(User, User.user_id == DoctorReview.reviewed_by)
                 .where(ECGRecord.patient_uuid == patient.patient_uuid))
        if self.principal.organization_id is not None:
            query = query.where(DoctorReview.organization_id == self.principal.organization_id)
        return [{"doctor_review_uuid": str(review.doctor_review_uuid), "ai_analysis_uuid": str(review.ai_analysis_uuid),
                 "ecg_uuid": str(review.ecg_uuid), "status": review.review_status, "assessment": review.assessment,
                 "reviewed_at": review.reviewed_at.isoformat(), "reviewer": user.display_name}
                for review, user in self.session.execute(query.order_by(DoctorReview.reviewed_at.desc())).all()]

    def _report_context(self, report_uuid: str | uuid.UUID) -> tuple[DiagnosticReport, ECGRecord, Patient, PatientOrganization]:
        report_id = as_uuid(report_uuid, "report")
        query = select(DiagnosticReport).where(DiagnosticReport.report_uuid == report_id)
        if self.principal.organization_id is not None:
            query = query.where(DiagnosticReport.organization_id == self.principal.organization_id)
        report = self.session.scalar(query)
        if report is None:
            raise APIError("REPORT_NOT_FOUND", "The report could not be found.", 404)
        ecg = self._ecg(report.ecg_uuid)
        patient, membership = self._patient(ecg.patient_uuid)
        return report, ecg, patient, membership

    def list_reports(self, patient_uuid: str) -> list[dict[str, Any]]:
        patient, _ = self._patient(patient_uuid)
        query = (select(DiagnosticReport).join(ECGRecord, ECGRecord.ecg_uuid == DiagnosticReport.ecg_uuid)
                 .where(ECGRecord.patient_uuid == patient.patient_uuid))
        if self.principal.organization_id is not None:
            query = query.where(DiagnosticReport.organization_id == self.principal.organization_id)
        output = []
        for report in self.session.scalars(query.order_by(DiagnosticReport.created_at.desc())):
            document = self.session.scalar(select(ReportDocument).where(ReportDocument.report_uuid == report.report_uuid)
                                           .order_by(ReportDocument.created_at.desc()))
            output.append({"report_uuid": str(report.report_uuid), "ecg_uuid": str(report.ecg_uuid),
                           "status": report.status, "created_at": report.created_at.isoformat(),
                           "issued_at": report.issued_at.isoformat() if report.issued_at else None,
                           "has_pdf": document is not None})
        return output

    def generate_report_pdf(self, report_uuid: str) -> dict[str, Any]:
        from .reports import build_ecg_report_pdf

        report, ecg, patient, membership = self._report_context(report_uuid)
        # Generated reports embed ``waveform_png`` below; enforce the role
        # boundary here as well as at the route so a service caller cannot
        # create a waveform-bearing document for a non-doctor principal.
        self._require_original_ecg_view(ecg)
        organization = self.session.get(Organization, ecg.organization_id)
        analysis = self.session.scalar(select(AIAnalysis).where(AIAnalysis.ecg_uuid == ecg.ecg_uuid,
                                                                  AIAnalysis.status == "COMPLETED")
                                       .order_by(AIAnalysis.created_at.desc()))
        version = self.session.get(AIModelVersion, analysis.model_version_uuid) if analysis else None
        review = self.session.scalar(select(DoctorReview).where(DoctorReview.ecg_uuid == ecg.ecg_uuid)
                                     .order_by(DoctorReview.reviewed_at.desc()))
        condition = self.session.scalar(select(Condition).where(Condition.encounter_uuid == ecg.encounter_uuid)
                                        .order_by(Condition.created_at.desc()))
        prescription = self.session.scalar(select(Prescription).where(Prescription.encounter_uuid == ecg.encounter_uuid)
                                          .order_by(Prescription.created_at.desc()))
        items = self.session.scalars(
            select(PrescriptionItem)
            .where(PrescriptionItem.prescription_uuid == prescription.prescription_uuid)
            .order_by(PrescriptionItem.created_at)
        ).all() if prescription else []
        author = self.session.get(User, report.author_id)
        waveform_png = self.waveform_png(ecg)
        pdf = build_ecg_report_pdf(
            hospital=organization.name if organization else "Hospital not recorded",
            patient_name=" ".join(filter(None, [patient.first_name, patient.middle_name, patient.last_name])),
            empi_id=patient.empi_id, mrn=membership.mrn, encounter_number=ecg.encounter_uuid.hex,
            accession_number=ecg.accession_number, clinician=author.display_name if author else "Not recorded",
            ai_prediction=analysis.predicted_label_name if analysis else None,
            ai_score=analysis.confidence if analysis else None, model_version=version.version if version else None,
            clinician_assessment=review.assessment if review else report.conclusion,
            diagnosis=condition.display if condition else None,
            prescription_items=[
                {
                    "medicine": item.medicine,
                    "dose": item.dose,
                    "route": item.route,
                    "frequency": item.frequency,
                    "duration": item.duration,
                    "instructions": item.instructions,
                }
                for item in items
            ],
            waveform_png=waveform_png,
        )
        key = f"organizations/{ecg.organization_id}/reports/{report.report_uuid}/{uuid.uuid4().hex}.pdf"
        stored = self.storage.put_bytes(key, pdf, "application/pdf")
        document = ReportDocument(report_uuid=report.report_uuid, storage_uri=stored.uri, object_key=stored.object_key,
                                  sha256=stored.sha256, content_type=stored.content_type, size_bytes=stored.size_bytes)
        self.session.add(document)
        report.issued_at = utcnow()
        report.status = "FINAL"
        self.session.commit()
        audit("REPORT_CREATED", "report", str(report.report_uuid), organization_id=ecg.organization_id)
        self.session.commit()
        return {"report_uuid": str(report.report_uuid), "report_document_uuid": str(document.report_document_uuid),
                "status": report.status, "content_type": document.content_type, "size_bytes": document.size_bytes}

    def download_report(self, report_uuid: str) -> tuple[bytes, ReportDocument]:
        report, ecg, _patient, _membership = self._report_context(report_uuid)
        # A stored report contains an original waveform rendering, so serving
        # its bytes would otherwise bypass the direct ECG-view restriction.
        self._require_original_ecg_view(ecg)
        document = self.session.scalar(select(ReportDocument).where(ReportDocument.report_uuid == report.report_uuid)
                                       .order_by(ReportDocument.created_at.desc()))
        if document is None:
            raise APIError("REPORT_DOCUMENT_NOT_FOUND", "No PDF has been generated for this report.", 404)
        payload = self.storage.get_bytes(document.object_key)
        if hashlib.sha256(payload).hexdigest() != document.sha256:
            raise APIError("ASSET_INTEGRITY_ERROR", "The report asset failed an integrity check.", 500)
        audit("REPORT_DOWNLOADED", "report", str(report.report_uuid), organization_id=ecg.organization_id)
        self.session.commit()
        return payload, document

    def dashboard(self) -> dict[str, Any]:
        org = self._organization_id()
        if self.principal.patient_id is not None:
            # A patient account never receives hospital-wide analytics, even
            # aggregate counts. It can only see totals for its linked record.
            patient, _membership = self._patient(self.principal.patient_id)
            return {"organization_id": str(org), "metrics": {
                "my_encounters": self.session.scalar(select(func.count()).select_from(Encounter).where(
                    Encounter.organization_id == org, Encounter.patient_uuid == patient.patient_uuid)) or 0,
                "my_ecgs": self.session.scalar(select(func.count()).select_from(ECGRecord).where(
                    ECGRecord.organization_id == org, ECGRecord.patient_uuid == patient.patient_uuid)) or 0,
                "my_reports": self.session.scalar(select(func.count()).select_from(DiagnosticReport).join(
                    ECGRecord, ECGRecord.ecg_uuid == DiagnosticReport.ecg_uuid).where(
                    DiagnosticReport.organization_id == org, ECGRecord.patient_uuid == patient.patient_uuid)) or 0,
            }, "safety": "Counts are limited to the authenticated patient's own authorized records."}
        role_counts = {
            "patients": self.session.scalar(select(func.count()).select_from(PatientOrganization).where(
                PatientOrganization.organization_id == org, PatientOrganization.active.is_(True))) or 0,
            "ecgs": self.session.scalar(select(func.count()).select_from(ECGRecord).where(
                ECGRecord.organization_id == org)) or 0,
            "pending_reviews": self.session.scalar(select(func.count()).select_from(ECGRecord).where(
                ECGRecord.organization_id == org, ECGRecord.status == "REVIEW_REQUIRED")) or 0,
            "reports": self.session.scalar(select(func.count()).select_from(DiagnosticReport).where(
                DiagnosticReport.organization_id == org)) or 0,
        }
        return {"organization_id": str(org), "metrics": role_counts,
                "safety": "Counts are organization-scoped. AI output remains separate from clinician diagnosis."}

    def list_features(self) -> list[dict[str, Any]]:
        return [{"feature_uuid": str(item.feature_uuid), "key": item.key, "description": item.description,
                 "enabled": item.enabled} for item in self.session.scalars(select(Feature).order_by(Feature.key))]

    def create_feature(self, payload: FeatureRequest) -> dict[str, Any]:
        item = Feature(key=payload.key, description=payload.description, enabled=payload.enabled)
        self.session.add(item)
        try:
            self.session.commit()
        except IntegrityError as exc:
            self.session.rollback()
            raise APIError("FEATURE_EXISTS", "A feature with that key already exists.", 409) from exc
        audit("FEATURE_CHANGED", "feature", str(item.feature_uuid))
        self.session.commit()
        return {"feature_uuid": str(item.feature_uuid), "key": item.key, "enabled": item.enabled}

    def audit_events(self, limit: int = 100) -> list[dict[str, Any]]:
        query = select(AuditEvent).order_by(AuditEvent.timestamp.desc()).limit(min(max(limit, 1), 200))
        if not self.principal.is_super_admin:
            query = query.where(AuditEvent.organization_id == self._organization_id())
        return [{"audit_id": str(row.audit_id), "timestamp": row.timestamp.isoformat(), "action": row.action,
                 "resource_type": row.resource_type, "resource_id": row.resource_id, "success": row.success,
                 "request_id": row.request_id} for row in self.session.scalars(query)]
