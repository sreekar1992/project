"""Private asset storage adapters for waveform, explanation, and PDF files."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path, PurePosixPath
import stat
from typing import Protocol

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .config import Settings


LOCAL_ENVELOPE_MAGIC = b"ECG-CLINICAL-AESGCM-1\x00"
AES_GCM_NONCE_BYTES = 12
AES_GCM_TAG_BYTES = 16


@dataclass(frozen=True)
class StoredObject:
    uri: str
    object_key: str
    sha256: str
    size_bytes: int
    content_type: str


@dataclass(frozen=True)
class AssetProtection:
    """A truthful, backend-derived description of one stored object."""

    encrypted_at_rest: bool
    algorithm: str | None
    authenticated_encryption: bool
    key_management: str
    legacy_unencrypted: bool = False


class ObjectStorage(Protocol):
    def put_bytes(self, key: str, data: bytes, content_type: str) -> StoredObject: ...
    def get_bytes(self, object_key: str) -> bytes: ...
    def presigned_get(self, object_key: str, expires_seconds: int = 300) -> str | None: ...
    def protection_status(self, object_key: str) -> AssetProtection: ...


def _validate_key(key: str) -> PurePosixPath:
    path = PurePosixPath(key)
    if not key or path.is_absolute() or ".." in path.parts or path.name in {"", "."}:
        raise ValueError("Invalid private object-storage key.")
    return path


def _read_private_key(path: Path) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    with os.fdopen(os.open(path, flags), "rb") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) & 0o077:
            raise RuntimeError("The local clinical asset key must be a regular 0600 file.")
        key = handle.read(33)
    if len(key) != 32:
        raise RuntimeError("The local clinical asset key must contain exactly 32 bytes.")
    return key


def _load_or_create_private_key(path: Path) -> bytes:
    """Create a durable local AES-256 key without ever placing it in source control.

    This is a development convenience only. Production deployments should put
    object encryption under a KMS/HSM-backed storage policy, not a local key
    file. The path defaults below ``var/`` which is ignored by Git.
    """
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        return _read_private_key(path)
    except FileNotFoundError:
        pass
    if path.is_symlink():
        raise RuntimeError("The local clinical asset key must not be a symlink.")
    key = AESGCM.generate_key(bit_length=256)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        with os.fdopen(os.open(path, flags, 0o600), "wb") as handle:
            handle.write(key)
    except FileExistsError:
        # Another local worker created it between the existence check and the
        # exclusive create. Read the already-created key safely instead.
        return _read_private_key(path)
    return key


class LocalPrivateStorage:
    """Private local storage with AES-256-GCM encryption for new assets.

    The logical storage keys remain unchanged. On disk, every newly written
    ECG, explanation, and report is wrapped with a fresh nonce and associated
    data bound to its object key. Legacy plaintext records remain readable so a
    deployment can migrate them deliberately rather than silently overwriting
    immutable clinical assets.
    """

    def __init__(self, root: Path, key_path: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._key = _load_or_create_private_key(key_path)

    def _path(self, key: str) -> Path:
        safe = _validate_key(key)
        path = (self.root / safe).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError("Object storage path escaped its configured root.")
        return path

    def put_bytes(self, key: str, data: bytes, content_type: str) -> StoredObject:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        nonce = os.urandom(AES_GCM_NONCE_BYTES)
        ciphertext = AESGCM(self._key).encrypt(nonce, data, key.encode("utf-8"))
        envelope = LOCAL_ENVELOPE_MAGIC + nonce + ciphertext
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        try:
            with os.fdopen(os.open(path, flags, 0o600), "wb") as handle:
                handle.write(envelope)
        except FileExistsError as exc:
            raise ValueError("Refusing to overwrite an immutable stored clinical asset.") from exc
        return StoredObject(uri=f"local://{key}", object_key=key,
                            sha256=hashlib.sha256(data).hexdigest(), size_bytes=len(data),
                            content_type=content_type)

    def get_bytes(self, object_key: str) -> bytes:
        path = self._path(object_key)
        if path.is_symlink() or not path.is_file():
            raise FileNotFoundError("Private clinical asset is unavailable.")
        payload = path.read_bytes()
        if not payload.startswith(LOCAL_ENVELOPE_MAGIC):
            # Do not silently overwrite a historic source asset. The security
            # endpoint surfaces this as a legacy, non-encrypted object.
            return payload
        nonce_end = len(LOCAL_ENVELOPE_MAGIC) + AES_GCM_NONCE_BYTES
        if len(payload) < nonce_end + AES_GCM_TAG_BYTES:
            raise ValueError("The encrypted clinical asset has an invalid envelope.")
        try:
            nonce = payload[len(LOCAL_ENVELOPE_MAGIC):nonce_end]
            return AESGCM(self._key).decrypt(nonce, payload[nonce_end:], object_key.encode("utf-8"))
        except InvalidTag as exc:
            raise ValueError("The encrypted clinical asset failed authentication.") from exc

    def presigned_get(self, object_key: str, expires_seconds: int = 300) -> str | None:
        # Flask streams local objects only after its own authorization checks.
        _validate_key(object_key)
        return None

    def protection_status(self, object_key: str) -> AssetProtection:
        path = self._path(object_key)
        if path.is_symlink() or not path.is_file():
            raise FileNotFoundError("Private clinical asset is unavailable.")
        with path.open("rb") as handle:
            marker = handle.read(len(LOCAL_ENVELOPE_MAGIC))
        if marker == LOCAL_ENVELOPE_MAGIC:
            return AssetProtection(
                encrypted_at_rest=True,
                algorithm="AES-256-GCM",
                authenticated_encryption=True,
                key_management="Local private AES-256 key (0600); use managed KMS-backed storage in production.",
            )
        return AssetProtection(
            encrypted_at_rest=False,
            algorithm=None,
            authenticated_encryption=False,
            key_management="Legacy local asset without at-rest encryption.",
            legacy_unencrypted=True,
        )


class S3PrivateStorage:
    def __init__(self, settings: Settings):
        if not all((settings.object_storage_endpoint, settings.object_storage_bucket,
                    settings.object_storage_access_key, settings.object_storage_secret_key)):
            raise RuntimeError("S3 storage requires endpoint, bucket, access key, and secret key configuration.")
        import boto3
        self.bucket = settings.object_storage_bucket
        self.client = boto3.client(
            "s3", endpoint_url=settings.object_storage_endpoint,
            aws_access_key_id=settings.object_storage_access_key,
            aws_secret_access_key=settings.object_storage_secret_key,
            region_name=settings.object_storage_region,
        )
        self._ensure_bucket(settings.object_storage_region, settings.allow_object_storage_bucket_create)

    def _ensure_bucket(self, region: str, allow_create: bool) -> None:
        from botocore.exceptions import ClientError
        try:
            self.client.head_bucket(Bucket=self.bucket)
        except ClientError as exc:
            code = str(exc.response.get("Error", {}).get("Code", ""))
            if code == "403":
                raise RuntimeError("The configured object-storage identity cannot verify the private bucket.") from exc
            if code not in {"404", "NoSuchBucket"}:
                raise
            if not allow_create:
                raise RuntimeError("The configured private object-storage bucket is absent. Provision it before startup.") from exc
            # MinIO/local S3 deployments commonly use us-east-1 without a
            # location constraint; AWS S3 requires it for other regions.
            options = {"Bucket": self.bucket}
            if region != "us-east-1":
                options["CreateBucketConfiguration"] = {"LocationConstraint": region}
            self.client.create_bucket(**options)

    def put_bytes(self, key: str, data: bytes, content_type: str) -> StoredObject:
        _validate_key(key)
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type,
                               ServerSideEncryption="AES256")
        return StoredObject(uri=f"s3://{self.bucket}/{key}", object_key=key,
                            sha256=hashlib.sha256(data).hexdigest(), size_bytes=len(data),
                            content_type=content_type)

    def get_bytes(self, object_key: str) -> bytes:
        _validate_key(object_key)
        return self.client.get_object(Bucket=self.bucket, Key=object_key)["Body"].read()

    def presigned_get(self, object_key: str, expires_seconds: int = 300) -> str | None:
        _validate_key(object_key)
        return self.client.generate_presigned_url("get_object", Params={"Bucket": self.bucket, "Key": object_key},
                                                  ExpiresIn=expires_seconds)

    def protection_status(self, object_key: str) -> AssetProtection:
        _validate_key(object_key)
        return AssetProtection(
            encrypted_at_rest=True,
            algorithm="SSE-S3 AES-256",
            authenticated_encryption=False,
            key_management="Object-storage server-side encryption requested on write.",
        )


class GCSPrivateStorage:
    """Private GCS storage using the pod's Workload Identity credentials."""

    def __init__(self, settings: Settings):
        if not settings.object_storage_bucket:
            raise RuntimeError("GCS storage requires OBJECT_STORAGE_BUCKET.")
        from google.cloud import storage
        self.bucket_name = settings.object_storage_bucket
        self.bucket = storage.Client().bucket(self.bucket_name)

    def put_bytes(self, key: str, data: bytes, content_type: str) -> StoredObject:
        _validate_key(key)
        from google.api_core.exceptions import PreconditionFailed
        blob = self.bucket.blob(key)
        try:
            blob.upload_from_string(data, content_type=content_type, if_generation_match=0)
        except PreconditionFailed as exc:
            raise ValueError("Refusing to overwrite an immutable stored clinical asset.") from exc
        return StoredObject(uri=f"gs://{self.bucket_name}/{key}", object_key=key,
                            sha256=hashlib.sha256(data).hexdigest(), size_bytes=len(data),
                            content_type=content_type)

    def get_bytes(self, object_key: str) -> bytes:
        _validate_key(object_key)
        return self.bucket.blob(object_key).download_as_bytes()

    def presigned_get(self, object_key: str, expires_seconds: int = 300) -> str | None:
        _validate_key(object_key)
        # The app streams downloads after its own authorization checks.
        return None

    def protection_status(self, object_key: str) -> AssetProtection:
        _validate_key(object_key)
        if not self.bucket.blob(object_key).exists():
            raise FileNotFoundError("Private clinical asset is unavailable.")
        return AssetProtection(
            encrypted_at_rest=True,
            algorithm="Google Cloud Storage server-side encryption",
            authenticated_encryption=False,
            key_management="Google Cloud managed encryption keys.",
        )


def build_storage(settings: Settings) -> ObjectStorage:
    if settings.object_storage_backend == "s3":
        return S3PrivateStorage(settings)
    if settings.object_storage_backend == "gcs":
        return GCSPrivateStorage(settings)
    return LocalPrivateStorage(settings.local_object_storage_path, settings.local_asset_encryption_key_path)
