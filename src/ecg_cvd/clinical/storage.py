"""Private asset storage adapters for waveform, explanation, and PDF files."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path, PurePosixPath
from typing import Protocol

from .config import Settings


@dataclass(frozen=True)
class StoredObject:
    uri: str
    object_key: str
    sha256: str
    size_bytes: int
    content_type: str


class ObjectStorage(Protocol):
    def put_bytes(self, key: str, data: bytes, content_type: str) -> StoredObject: ...
    def get_bytes(self, object_key: str) -> bytes: ...
    def presigned_get(self, object_key: str, expires_seconds: int = 300) -> str | None: ...


def _validate_key(key: str) -> PurePosixPath:
    path = PurePosixPath(key)
    if not key or path.is_absolute() or ".." in path.parts or path.name in {"", "."}:
        raise ValueError("Invalid private object-storage key.")
    return path


class LocalPrivateStorage:
    """Local development stand-in; it is not an encrypted production vault."""

    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)

    def _path(self, key: str) -> Path:
        safe = _validate_key(key)
        path = (self.root / safe).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError("Object storage path escaped its configured root.")
        return path

    def put_bytes(self, key: str, data: bytes, content_type: str) -> StoredObject:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        try:
            with os.fdopen(os.open(path, flags, 0o600), "wb") as handle:
                handle.write(data)
        except FileExistsError as exc:
            raise ValueError("Refusing to overwrite an immutable stored clinical asset.") from exc
        return StoredObject(uri=f"local://{key}", object_key=key,
                            sha256=hashlib.sha256(data).hexdigest(), size_bytes=len(data),
                            content_type=content_type)

    def get_bytes(self, object_key: str) -> bytes:
        path = self._path(object_key)
        if path.is_symlink() or not path.is_file():
            raise FileNotFoundError("Private clinical asset is unavailable.")
        return path.read_bytes()

    def presigned_get(self, object_key: str, expires_seconds: int = 300) -> str | None:
        # Flask streams local objects only after its own authorization checks.
        _validate_key(object_key)
        return None


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


def build_storage(settings: Settings) -> ObjectStorage:
    if settings.object_storage_backend == "s3":
        return S3PrivateStorage(settings)
    return LocalPrivateStorage(settings.local_object_storage_path)
