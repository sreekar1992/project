"""Configuration for the separate ECG health-platform application."""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import secrets
from typing import Any


def _truthy(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    """Environment-backed settings with safe local-development defaults.

    A randomly generated development secret intentionally invalidates local
    sessions after restart.  Production deployment must supply both secrets
    through its secret manager; no patient credential belongs in source code.
    """

    environment: str
    project_root: Path
    database_url: str
    jwt_secret: str
    flask_secret: str
    access_token_minutes: int
    refresh_token_days: int
    visual_access_request_hours: int
    visual_access_passcode_minutes: int
    visual_access_session_minutes: int
    visual_access_max_attempts: int
    object_storage_backend: str
    local_object_storage_path: Path
    local_asset_encryption_key_path: Path
    object_storage_endpoint: str | None
    object_storage_bucket: str | None
    object_storage_access_key: str | None
    object_storage_secret_key: str | None
    object_storage_region: str
    allow_object_storage_bucket_create: bool
    redis_url: str | None
    async_analysis: bool
    ai_model_path: Path | None
    ai_model_version: str | None
    medicine_catalog_path: Path
    medicine_catalog_index_path: Path
    max_upload_bytes: int
    cors_origins: tuple[str, ...]
    public_base_url: str | None
    seed_password: str | None

    @property
    def development(self) -> bool:
        return self.environment in {"development", "test"}

    @classmethod
    def from_environment(cls, overrides: dict[str, Any] | None = None) -> "Settings":
        overrides = overrides or {}
        root = Path(overrides.get("PROJECT_ROOT") or os.getenv("ECG_PROJECT_ROOT") or Path.cwd()).resolve()
        environment = str(overrides.get("ENVIRONMENT") or os.getenv("ECG_PLATFORM_ENV", "development")).lower()
        database_url = str(overrides.get("DATABASE_URL") or os.getenv("DATABASE_URL")
                           or f"sqlite+pysqlite:///{root / 'var' / 'ecg_health.db'}")
        jwt_secret = str(overrides.get("JWT_SECRET") or os.getenv("JWT_SECRET") or "")
        flask_secret = str(overrides.get("SECRET_KEY") or os.getenv("SECRET_KEY") or "")
        if not jwt_secret or not flask_secret:
            if environment not in {"development", "test"}:
                raise RuntimeError("JWT_SECRET and SECRET_KEY must be configured outside development.")
            jwt_secret = jwt_secret or secrets.token_urlsafe(48)
            flask_secret = flask_secret or secrets.token_urlsafe(48)
        storage_backend = str(overrides.get("OBJECT_STORAGE_BACKEND")
                              or os.getenv("OBJECT_STORAGE_BACKEND", "local")).lower()
        if storage_backend not in {"local", "s3", "gcs"}:
            raise RuntimeError("OBJECT_STORAGE_BACKEND must be 'local', 's3', or 'gcs'.")
        model = overrides.get("AI_MODEL_PATH", os.getenv("AI_MODEL_PATH"))
        # Vite is commonly opened through either loopback hostname during local
        # development.  Both are explicit, same-machine origins; deployed
        # environments must still configure their own narrowly scoped list.
        origins = overrides.get(
            "CORS_ORIGINS",
            os.getenv("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"),
        )
        if isinstance(origins, str):
            origins = tuple(origin.strip() for origin in origins.split(",") if origin.strip())
        return cls(
            environment=environment,
            project_root=root,
            database_url=database_url,
            jwt_secret=jwt_secret,
            flask_secret=flask_secret,
            access_token_minutes=int(overrides.get("ACCESS_TOKEN_MINUTES")
                                     or os.getenv("ACCESS_TOKEN_MINUTES", "15")),
            refresh_token_days=int(overrides.get("REFRESH_TOKEN_DAYS")
                                   or os.getenv("REFRESH_TOKEN_DAYS", "7")),
            # These are deliberately short-lived, server-enforced limits for
            # doctor-approved, visual-only ECG access grants.  They are not
            # credentials for object storage or a replacement for RBAC.
            visual_access_request_hours=int(overrides.get("VISUAL_ACCESS_REQUEST_HOURS")
                                            or os.getenv("VISUAL_ACCESS_REQUEST_HOURS", "24")),
            visual_access_passcode_minutes=int(overrides.get("VISUAL_ACCESS_PASSCODE_MINUTES")
                                               or os.getenv("VISUAL_ACCESS_PASSCODE_MINUTES", "15")),
            visual_access_session_minutes=int(overrides.get("VISUAL_ACCESS_SESSION_MINUTES")
                                              or os.getenv("VISUAL_ACCESS_SESSION_MINUTES", "10")),
            visual_access_max_attempts=int(overrides.get("VISUAL_ACCESS_MAX_ATTEMPTS")
                                           or os.getenv("VISUAL_ACCESS_MAX_ATTEMPTS", "5")),
            object_storage_backend=storage_backend,
            local_object_storage_path=Path(overrides.get("LOCAL_OBJECT_STORAGE_PATH")
                                           or os.getenv("LOCAL_OBJECT_STORAGE_PATH")
                                           or root / "var" / "clinical_objects").resolve(),
            local_asset_encryption_key_path=Path(overrides.get("LOCAL_ASSET_ENCRYPTION_KEY_PATH")
                                                 or os.getenv("LOCAL_ASSET_ENCRYPTION_KEY_PATH")
                                                 or root / "var" / "clinical_asset_key.bin").resolve(),
            object_storage_endpoint=overrides.get("OBJECT_STORAGE_ENDPOINT")
            or os.getenv("OBJECT_STORAGE_ENDPOINT"),
            object_storage_bucket=overrides.get("OBJECT_STORAGE_BUCKET")
            or os.getenv("OBJECT_STORAGE_BUCKET"),
            object_storage_access_key=overrides.get("OBJECT_STORAGE_ACCESS_KEY")
            or os.getenv("OBJECT_STORAGE_ACCESS_KEY"),
            object_storage_secret_key=overrides.get("OBJECT_STORAGE_SECRET_KEY")
            or os.getenv("OBJECT_STORAGE_SECRET_KEY"),
            object_storage_region=str(overrides.get("OBJECT_STORAGE_REGION")
                                      or os.getenv("OBJECT_STORAGE_REGION", "us-east-1")),
            allow_object_storage_bucket_create=_truthy(
                str(overrides["ALLOW_OBJECT_STORAGE_BUCKET_CREATE"])
                if "ALLOW_OBJECT_STORAGE_BUCKET_CREATE" in overrides
                else os.getenv("ALLOW_OBJECT_STORAGE_BUCKET_CREATE"),
                environment in {"development", "test"},
            ),
            redis_url=overrides.get("REDIS_URL") or os.getenv("REDIS_URL"),
            async_analysis=_truthy(str(overrides["ASYNC_ANALYSIS"])
                                   if "ASYNC_ANALYSIS" in overrides else os.getenv("ASYNC_ANALYSIS"), False),
            ai_model_path=Path(model).resolve() if model else None,
            ai_model_version=overrides.get("AI_MODEL_VERSION") or os.getenv("AI_MODEL_VERSION"),
            medicine_catalog_path=Path(overrides.get("MEDICINE_CATALOG_PATH")
                                       or os.getenv("MEDICINE_CATALOG_PATH")
                                       or root / "data" / "medicine_catalog"
                                       / "india-medicines-and-drug-info-dataset.zip").resolve(),
            medicine_catalog_index_path=Path(overrides.get("MEDICINE_CATALOG_INDEX_PATH")
                                             or os.getenv("MEDICINE_CATALOG_INDEX_PATH")
                                             or root / "var" / "medicine_catalog.sqlite3").resolve(),
            max_upload_bytes=int(overrides.get("MAX_UPLOAD_BYTES")
                                 or os.getenv("MAX_UPLOAD_BYTES", str(16 * 1024 * 1024))),
            cors_origins=tuple(origins),
            public_base_url=overrides.get("PUBLIC_BASE_URL") or os.getenv("PUBLIC_BASE_URL"),
            seed_password=overrides.get("DEVELOPMENT_SEED_PASSWORD")
            or os.getenv("DEVELOPMENT_SEED_PASSWORD"),
        )

    def flask_config(self) -> dict[str, Any]:
        return {
            "SECRET_KEY": self.flask_secret,
            "MAX_CONTENT_LENGTH": self.max_upload_bytes,
            "JSON_SORT_KEYS": False,
            "ECG_PLATFORM_SETTINGS": self,
        }
