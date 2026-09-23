"""Lossless dataset encryption for the local, explicitly demo-only workbench.

Each batch has a random AES-256 data-encryption key. Scrypt derives a separate
key from the requested fixed demonstration password and wraps that random key.
Every MAT, generated PNG, and manifest receives its own fresh AES-GCM nonce.
Original source data are preserved; plaintext images and keys are never saved.

The fixed password is PUBLIC demo configuration, not a production secret. This
does not replace the stronger passphrase requirements of ``crypto.py``.
"""
from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import hashlib
from io import BytesIO
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import stat
from threading import Lock
from typing import Callable
import uuid

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
import numpy as np
from PIL import Image
from scipy.io import loadmat

from .crypto import MAGIC, NONCE_BYTES, TAG_BYTES, decrypt_bytes, encrypt_bytes
from .camouflage_container import (create_camouflaged_mat, inspect_camouflaged_mat,
                                   restore_camouflaged_mat)

DEMO_PASSWORD = "987654321"
ALGORITHM = "AES-256-GCM + Scrypt key wrapping (N=131072, r=8, p=1)"
DEMO_WARNING = "Fixed public demo password; do not use this configuration for sensitive patient data."
KEY_MAGIC = b"ECG-DATASET-KEY-1\x00"
KEY_FILE = "key.ecgkey"
MANIFEST_FILE = "manifest.json.ecgenc"
INDEX_FILE = "index.json"
_ID = re.compile(r"^[0-9a-f]{32}$")
_KIND = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
_PLOT_LOCK = Lock()
_MAX_FILE_BYTES = 16 * 1024 * 1024


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json(data: object) -> bytes:
    return json.dumps(data, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _valid_id(value: str) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ValueError("Expected a 32-character hexadecimal batch or recording identifier.")
    return value


def _safe_child(base: Path, relative: str, *, must_exist: bool = True) -> Path:
    """Reject traversal and symlinks rather than silently following either."""
    if not isinstance(relative, str) or "\\" in relative or "\x00" in relative:
        raise ValueError("Invalid dataset path.")
    parts = PurePosixPath(relative)
    if parts.is_absolute() or not parts.parts or any(p in {".", ".."} for p in parts.parts):
        raise ValueError("Dataset paths must stay inside their designated directory.")
    base = Path(base)
    if base.is_symlink():
        raise ValueError("Symlink dataset directories are not supported.")
    resolved_base = base.resolve(strict=True)
    child = base
    for part in parts.parts:
        child = child / part
        if child.is_symlink():
            raise ValueError("Symlink dataset files or directories are not supported.")
    if not child.resolve(strict=must_exist).is_relative_to(resolved_base):
        raise ValueError("Dataset path escapes its designated directory.")
    return child


def _read(path: Path, *, max_bytes: int = _MAX_FILE_BYTES) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    with os.fdopen(fd, "rb") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > max_bytes:
            raise ValueError("Expected a regular dataset file within the size limit.")
        data = handle.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise ValueError("Dataset file exceeds the size limit.")
        return data


def _write_new(path: Path, data: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)


def _artifact_read(batch: Path, filename: str) -> bytes:
    try:
        return _read(_safe_child(batch, filename))
    except OSError as exc:
        raise ValueError("An encrypted dataset artifact is missing or unreadable.") from exc


def _password_key(password: str, salt: bytes) -> bytes:
    if not isinstance(password, str) or not password:
        raise ValueError("Enter the decryption password.")
    encoded = password.encode("utf-8")
    if len(encoded) > 1024:
        raise ValueError("Password is too long.")
    return Scrypt(salt=salt, length=32, n=2 ** 17, r=8, p=1).derive(encoded)


def _wrap_key(key: bytes, batch_id: str) -> bytes:
    salt, nonce = os.urandom(16), os.urandom(12)
    header = KEY_MAGIC + salt
    wrapping_key = _password_key(DEMO_PASSWORD, salt)
    return header + nonce + AESGCM(wrapping_key).encrypt(nonce, key, header + batch_id.encode("ascii"))


def _unwrap_key(payload: bytes, batch_id: str, password: str) -> bytes:
    header_end = len(KEY_MAGIC) + 16
    # The wrapped plaintext is exactly one 32-byte data-encryption key.
    if not payload.startswith(KEY_MAGIC) or len(payload) != header_end + 12 + 32 + 16:
        raise ValueError("Invalid encrypted dataset key envelope.")
    key = _password_key(password, payload[len(KEY_MAGIC):header_end])
    return AESGCM(key).decrypt(payload[header_end:header_end + 12], payload[header_end + 12:],
                               payload[:header_end] + batch_id.encode("ascii"))


def _signal(mat_bytes: bytes) -> np.ndarray:
    arrays = {name: np.asarray(value).squeeze() for name, value in loadmat(BytesIO(mat_bytes)).items()
              if not name.startswith("__") and isinstance(value, np.ndarray)
              and np.issubdtype(value.dtype, np.number)}
    raw = arrays.get("val")
    if raw is None:
        candidates = [a for a in arrays.values() if a.ndim == 1 and a.size == 3600]
        if len(candidates) != 1:
            raise ValueError("Expected one 3,600-sample ECG waveform in the MAT file.")
        raw = candidates[0]
    if raw.ndim != 1 or raw.size != 3600 or np.iscomplexobj(raw):
        raise ValueError("Expected one real-valued 3,600-sample ECG waveform.")
    raw = raw.astype(np.float32)
    if not np.isfinite(raw).all() or float(np.ptp(raw)) == 0:
        raise ValueError("ECG waveform must be finite and non-flat.")
    return raw


def render_waveform(raw: np.ndarray) -> bytes:
    """Render a deterministic 360 Hz raw-signal PNG entirely in memory."""
    raw = np.asarray(raw, dtype=np.float32).reshape(-1)
    if raw.size != 3600 or not np.isfinite(raw).all():
        raise ValueError("Waveform rendering requires 3,600 finite samples.")
    with _PLOT_LOCK:
        figure = Figure(figsize=(10, 3), dpi=120, facecolor="#f8fafc")
        FigureCanvasAgg(figure)
        axis = figure.add_subplot(111)
        axis.plot(np.arange(raw.size) / 360.0, raw, color="#087e8b", linewidth=1.0)
        axis.set(xlim=(0, 10), xlabel="Time (seconds)", ylabel="Raw amplitude (dataset units)")
        axis.set_facecolor("#ffffff")
        axis.grid(alpha=0.2, color="#94a3b8")
        axis.spines[["top", "right"]].set_visible(False)
        figure.subplots_adjust(left=0.095, right=0.98, bottom=0.2, top=0.95)
        buffer = BytesIO()
        figure.savefig(buffer, format="png", metadata={"Software": "ECG Dataset Workbench"})
        return buffer.getvalue()


def render_ciphertext_preview(payload: bytes) -> bytes:
    """Visualize ciphertext byte values, without attempting decryption.

    Envelope validation is structural only, not an authenticity check. The
    version header, nonce, and authentication tag are excluded from the image.
    This PNG is a diagnostic of ciphertext, not an encrypted image format or
    a recovered ECG waveform.
    """
    offset = len(MAGIC) + NONCE_BYTES
    if (not isinstance(payload, bytes) or not payload.startswith(MAGIC)
            or not offset + TAG_BYTES < len(payload) <= _MAX_FILE_BYTES):
        raise ValueError("Expected a supported encrypted ECG image envelope.")
    values = np.frombuffer(payload[offset:-TAG_BYTES], dtype=np.uint8)
    side = min(256, math.isqrt(len(values)))
    preview = BytesIO()
    Image.fromarray(values[:side * side].reshape(side, side)).save(preview, format="PNG")
    return preview.getvalue()


def batch_path(root: Path, batch_id: str) -> Path:
    """Resolve an existing batch with strict ID, containment, and symlink checks."""
    root = Path(root).resolve(strict=True)
    batch_id = _valid_id(batch_id)
    try:
        storage = _safe_child(root, "artifacts_dataset")
        batch = _safe_child(storage, batch_id)
    except (FileNotFoundError, NotADirectoryError) as exc:
        raise ValueError("Encrypted dataset batch was not found.") from exc
    if not batch.is_dir():
        raise ValueError("Encrypted dataset batch was not found.")
    return batch


def _manifest(batch: Path, batch_id: str, key: bytes) -> dict:
    data = decrypt_bytes(_artifact_read(batch, MANIFEST_FILE), key)
    manifest = json.loads(data)
    if (not isinstance(manifest, dict) or manifest.get("version") != 1
            or manifest.get("batch_id") != batch_id or not isinstance(manifest.get("records"), dict)):
        raise ValueError("Invalid authenticated dataset manifest.")
    return manifest


def _json_object(value: object, description: str) -> dict:
    """Copy finite JSON metadata; never permit arrays/keys to leak by accident."""
    if not isinstance(value, dict):
        raise ValueError(f"{description} must be a JSON object.")
    try:
        encoded = json.dumps(value, allow_nan=False, ensure_ascii=True)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{description} must contain finite JSON-safe values.") from exc
    if len(encoded) > 65536:
        raise ValueError(f"{description} exceeds its metadata size limit.")
    return json.loads(encoded)


def _public_camouflage_report(report: dict) -> dict:
    """Explicit allowlist: public decoys must not expose the clean prediction."""
    def finite_number(value):
        try:
            return type(value) in (float, int) and bool(np.isfinite(float(value)))
        except (OverflowError, ValueError, TypeError):
            return False

    public = {}
    for field in ("model", "model_id"):
        if isinstance(report.get(field), str):
            public[field] = report[field]
    if type(report.get("label_changed")) is bool:
        public["label_changed"] = report["label_changed"]
    if finite_number(report.get("epsilon")):
        public["epsilon"] = report["epsilon"]
    prediction = report.get("camouflaged_prediction")
    if isinstance(prediction, dict):
        allowed = {}
        for field in ("label", "label_name"):
            if isinstance(prediction.get(field), str):
                allowed[field] = prediction[field]
        if finite_number(prediction.get("confidence")):
            allowed["confidence"] = prediction["confidence"]
        if isinstance(prediction.get("top_predictions"), list):
            allowed["top_predictions"] = []
            for item in prediction["top_predictions"][:32]:
                if (isinstance(item, dict) and isinstance(item.get("label"), str)
                        and finite_number(item.get("probability"))):
                    top = {"label": item["label"], "probability": item["probability"]}
                    if isinstance(item.get("label_name"), str):
                        top["label_name"] = item["label_name"]
                    allowed["top_predictions"].append(top)
        public["camouflaged_prediction"] = allowed
    return public


def encrypt_dataset(root: Path, progress: Callable[[int, int, str], None] | None = None, *,
                    camouflage_generator: Callable[[bytes, str], tuple[np.ndarray, dict]] | None = None,
                    camouflage_config: dict | None = None) -> dict:
    """Encrypt every MLII MAT and its rendered PNG; verify all round trips.

    The public catalog is written last, so incomplete batches are never listed
    as successful. It has names/labels only and is never trusted for file paths.
    """
    if camouflage_generator is not None and not callable(camouflage_generator):
        raise ValueError("Camouflage generator must be callable.")
    config = _json_object(camouflage_config or {}, "Camouflage configuration")
    root = Path(root).resolve(strict=True)
    try:
        source = _safe_child(root, "MLII")
    except (FileNotFoundError, NotADirectoryError) as exc:
        raise ValueError("MLII must be a dataset directory.") from exc
    if not source.is_dir():
        raise ValueError("MLII must be a dataset directory.")
    paths = sorted(source.rglob("*.mat"))
    if not paths:
        raise ValueError("No MAT recordings were found in MLII.")
    # Validate every source path before creating the output directory.
    for path in paths:
        _safe_child(source, path.relative_to(source).as_posix())
    storage = _safe_child(root, "artifacts_dataset", must_exist=False)
    storage.mkdir(mode=0o700, exist_ok=True)
    batch_id = uuid.uuid4().hex
    batch = storage / batch_id
    batch.mkdir(mode=0o700)
    key = AESGCM.generate_key(bit_length=256)
    _write_new(batch / KEY_FILE, _wrap_key(key, batch_id))
    created_at = datetime.now(timezone.utc).isoformat()
    records, catalog = {}, []
    camouflage_count = label_changed_count = 0
    if progress:
        progress(0, len(paths), "Encrypting MAT recordings and waveform images")
    for number, path in enumerate(paths, 1):
        path = _safe_child(source, path.relative_to(source).as_posix())
        mat_bytes = _read(path)
        png_bytes = render_waveform(_signal(mat_bytes))
        record_id = uuid.uuid4().hex
        mat_filename, image_filename = f"{record_id}.mat.ecgenc", f"{record_id}.png.ecgenc"
        mat_cipher, image_cipher = encrypt_bytes(mat_bytes, key), encrypt_bytes(png_bytes, key)
        # A batch is reported successful only if every exact byte is recovered.
        if decrypt_bytes(mat_cipher, key) != mat_bytes or decrypt_bytes(image_cipher, key) != png_bytes:
            raise RuntimeError("Encryption round-trip integrity verification failed.")
        _write_new(batch / mat_filename, mat_cipher)
        _write_new(batch / image_filename, image_cipher)
        entry = {"record_id": record_id, "name": path.name, "label": path.parent.name}
        catalog.append(entry)
        records[record_id] = {
            **entry, "source_id": path.relative_to(root).as_posix(),
            "mat_filename": mat_filename, "image_filename": image_filename,
            "mat_size": len(mat_bytes), "image_size": len(png_bytes),
            "hashes": {"mat_plaintext_sha256": _sha(mat_bytes), "image_plaintext_sha256": _sha(png_bytes),
                       "mat_ciphertext_sha256": _sha(mat_cipher), "image_ciphertext_sha256": _sha(image_cipher)},
        }
        if camouflage_generator is not None:
            camouflaged_signal, raw_report = camouflage_generator(mat_bytes, path.parent.name)
            report = _json_object(raw_report, "Camouflage generator report")
            context = f"{batch_id}:{record_id}"
            camouflage_bytes = create_camouflaged_mat(mat_bytes, camouflaged_signal, key, context)
            if restore_camouflaged_mat(camouflage_bytes, key, context) != mat_bytes:
                raise RuntimeError("Camouflaged MAT exact recovery verification failed.")
            filename = f"{record_id}.camouflaged.mat"
            _write_new(batch / filename, camouflage_bytes)
            records[record_id]["hashes"]["camouflaged_mat_sha256"] = _sha(camouflage_bytes)
            records[record_id]["camouflage"] = {"filename": filename, "report": report}
            entry["camouflage"] = _public_camouflage_report(report)
            camouflage_count += 1
            label_changed_count += int(report.get("label_changed") is True)
        if progress:
            progress(number, len(paths), f"Encrypted and verified {number}/{len(paths)} recordings")
    summary = {"batch_id": batch_id, "count": len(records), "mat_count": len(records),
               "image_count": len(records), "verified_mat_count": len(records),
               "verified_image_count": len(records), "algorithm": ALGORITHM,
               "created_at": created_at, "demo_only": True, "warning": DEMO_WARNING,
               "mode": "camouflage" if camouflage_generator is not None else "aes",
               "camouflaged_count": camouflage_count, "label_changed_count": label_changed_count,
               "camouflage_config": config if camouflage_generator is not None else {}}
    _write_new(batch / MANIFEST_FILE, encrypt_bytes(_json({"version": 1, **summary, "records": records}), key))
    _write_new(batch / INDEX_FILE, _json({"version": 1, **summary, "records": catalog}))
    return summary


def _public_index(batch: Path, batch_id: str) -> dict:
    index = json.loads(_artifact_read(batch, INDEX_FILE))
    if (not isinstance(index, dict) or index.get("version") != 1 or index.get("batch_id") != batch_id
            or not isinstance(index.get("records"), list)):
        raise ValueError("Invalid public dataset catalog.")
    return index


def list_batches(root: Path) -> list[dict]:
    """List completed public summaries; the catalog is not cryptographic proof."""
    root = Path(root).resolve(strict=True)
    storage = _safe_child(root, "artifacts_dataset", must_exist=False)
    if not storage.exists():
        return []
    summaries = []
    for path in sorted(storage.iterdir()):
        if not _ID.fullmatch(path.name) or path.is_symlink() or not path.is_dir():
            continue
        try:
            index = _public_index(batch_path(root, path.name), path.name)
            summary = {key: index[key] for key in (
                "batch_id", "count", "mat_count", "image_count", "verified_mat_count",
                "verified_image_count", "algorithm", "created_at", "demo_only", "warning")}
            summary.update({"mode": index.get("mode", "aes"),
                            "camouflaged_count": index.get("camouflaged_count", 0),
                            "label_changed_count": index.get("label_changed_count", 0),
                            "camouflage_config": index.get("camouflage_config", {})})
            summaries.append(summary)
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return sorted(summaries, key=lambda item: item["created_at"], reverse=True)


def list_records(root: Path, batch_id: str) -> list[dict]:
    index = _public_index(batch_path(root, batch_id), batch_id)
    records = []
    for entry in index["records"]:
        if not isinstance(entry, dict):
            raise ValueError("Invalid dataset catalog recording.")
        record_id = _valid_id(entry.get("record_id"))
        if not isinstance(entry.get("name"), str) or not isinstance(entry.get("label"), str):
            raise ValueError("Invalid dataset catalog labels.")
        records.append({"record_id": record_id, "name": entry["name"], "label": entry["label"]})
    return records


def camouflage_preview(root: Path, batch_id: str, record_id: str) -> dict | None:
    """Read a deliberately public decoy; catalog predictions are unauthenticated.

    No password, recovery key, original MAT, or clean prediction is returned.
    A caller must clearly label this as altered research data, not an ECG for care.
    """
    record_id = _valid_id(record_id)
    batch = batch_path(root, batch_id)
    index = _public_index(batch, batch_id)
    matches = [item for item in index["records"]
               if isinstance(item, dict) and item.get("record_id") == record_id]
    if len(matches) != 1:
        raise ValueError("Recording was not found in the public dataset catalog.")
    if index.get("mode", "aes") != "camouflage":
        return None
    filename = f"{record_id}.camouflaged.mat"
    payload = _artifact_read(batch, filename)
    inspected = inspect_camouflaged_mat(payload, f"{batch_id}:{record_id}")
    raw_report = matches[0].get("camouflage", {})
    report = _public_camouflage_report(raw_report if isinstance(raw_report, dict) else {})
    return {"mat_bytes": payload, "signal": inspected["signal"], "report": report,
            "filename": filename, "metadata_authenticated": False,
            "warning": "Public altered research signal; metadata and signal are not authenticated until decryption."}


def encrypted_record_preview(root: Path, batch_id: str, record_id: str) -> bytes:
    """Read only the selected public ciphertext, never a key or original file.

    Public catalog membership permits selection but does not authenticate the
    file. Paths are derived from validated IDs, never from catalog filenames.
    """
    record_id = _valid_id(record_id)
    batch = batch_path(root, batch_id)
    index = _public_index(batch, batch_id)
    matches = [item for item in index["records"]
               if isinstance(item, dict) and item.get("record_id") == record_id]
    if len(matches) != 1:
        raise ValueError("Recording was not found in the public dataset catalog.")
    return render_ciphertext_preview(_artifact_read(batch, f"{record_id}.png.ecgenc"))


def decrypt_record(root: Path, batch_id: str, record_id: str, password: str) -> dict:
    """Authenticate and decrypt a recording in memory, then compare originals.

    ``key`` is INTERNAL ONLY: API callers must never serialize it into responses.
    Original bytes are optional: encrypted data remain decryptable if sources
    are removed. Missing/changed originals never invalidate authenticated data.
    """
    root = Path(root).resolve(strict=True)
    record_id = _valid_id(record_id)
    batch = batch_path(root, batch_id)
    key = _unwrap_key(_artifact_read(batch, KEY_FILE), batch_id, password)
    manifest = _manifest(batch, batch_id, key)
    record = manifest["records"].get(record_id)
    if not isinstance(record, dict) or record.get("record_id") != record_id:
        raise ValueError("Recording is not in the authenticated batch manifest.")
    mat_cipher = _artifact_read(batch, record["mat_filename"])
    image_cipher = _artifact_read(batch, record["image_filename"])
    mat_bytes, image_bytes = decrypt_bytes(mat_cipher, key), decrypt_bytes(image_cipher, key)
    actual_hashes = {"mat_plaintext_sha256": _sha(mat_bytes), "image_plaintext_sha256": _sha(image_bytes),
                     "mat_ciphertext_sha256": _sha(mat_cipher), "image_ciphertext_sha256": _sha(image_cipher)}
    camouflage = None
    camouflage_entry = record.get("camouflage")
    if camouflage_entry is not None:
        filename = f"{record_id}.camouflaged.mat"
        if (not isinstance(camouflage_entry, dict) or camouflage_entry.get("filename") != filename
                or not isinstance(camouflage_entry.get("report"), dict)):
            raise ValueError("Invalid authenticated camouflage metadata.")
        camouflage_bytes = _artifact_read(batch, filename)
        actual_hashes["camouflaged_mat_sha256"] = _sha(camouflage_bytes)
        if actual_hashes["camouflaged_mat_sha256"] != record.get("hashes", {}).get("camouflaged_mat_sha256"):
            raise ValueError("Camouflaged MAT integrity does not match its authenticated manifest.")
        context = f"{batch_id}:{record_id}"
        restored = restore_camouflaged_mat(camouflage_bytes, key, context)
        if restored != mat_bytes:
            raise ValueError("Camouflaged recovery does not match the authenticated original MAT.")
        mat_bytes = restored
        camouflage = {"mat_bytes": camouflage_bytes,
                      "signal": inspect_camouflaged_mat(camouflage_bytes, context)["signal"],
                      "report": camouflage_entry["report"], "filename": filename,
                      "metadata_authenticated": True}
    if actual_hashes != record.get("hashes"):
        raise ValueError("Recording integrity does not match its authenticated manifest.")
    original_mat = original_image = None
    source_id = record.get("source_id", "")
    if not isinstance(source_id, str) or not source_id.startswith("MLII/"):
        raise ValueError("Invalid original source identifier in the authenticated manifest.")
    try:
        original_mat = _read(_safe_child(root, source_id))
        original_image = render_waveform(_signal(original_mat))
    except (FileNotFoundError, ValueError, OSError):
        # A missing/changed/unsafe source is not needed for successful decryption.
        original_mat = original_image = None
    mat_equal = original_mat == mat_bytes if original_mat is not None else None
    image_equal = original_image == image_bytes if original_image is not None else None
    metadata = {"record_id": record_id, "batch_id": batch_id, "name": record["name"],
                "label": record["label"], "source_id": source_id, "hashes": actual_hashes,
                "mat_size": len(mat_bytes), "image_size": len(image_bytes),
                "byte_equal": mat_equal, "image_byte_equal": image_equal,
                "authenticated": True, "algorithm": ALGORITHM,
                "original_source_sha256": _sha(original_mat) if original_mat is not None else None,
                "decrypted_source_sha256": actual_hashes["mat_plaintext_sha256"],
                "original_available": original_mat is not None}
    result = {"mat_bytes": mat_bytes, "image_bytes": image_bytes,
            "encrypted_image_bytes": image_cipher, "image_ciphertext": image_cipher,
            "original_mat_bytes": original_mat,
            "original_image_bytes": original_image, "metadata": metadata, "key": key}
    if camouflage is not None:
        result["camouflage"] = camouflage
    return result


def save_result(root: Path, batch_id: str, record_id: str, kind: str, plaintext: bytes, key: bytes) -> dict:
    """Save an additional encrypted prediction artifact without overwriting."""
    record_id = _valid_id(record_id)
    if not isinstance(kind, str) or not _KIND.fullmatch(kind):
        raise ValueError("Invalid result artifact kind.")
    if not isinstance(plaintext, bytes) or len(plaintext) > _MAX_FILE_BYTES:
        raise ValueError("Invalid or oversized result bytes.")
    batch = batch_path(root, batch_id)
    if record_id not in _manifest(batch, batch_id, key)["records"]:
        raise ValueError("Recording is not in this encrypted batch.")
    suffix = "png" if "image" in kind else "json" if "report" in kind else "bin"
    filename = f"{record_id}.{kind}.{uuid.uuid4().hex}.{suffix}.ecgenc"
    ciphertext = encrypt_bytes(plaintext, key)
    _write_new(batch / filename, ciphertext)
    return {"filename": filename, "saved_path": (batch / filename).relative_to(Path(root).resolve()).as_posix(),
            "ciphertext": base64.b64encode(ciphertext).decode("ascii")}


def main() -> None:
    parser = argparse.ArgumentParser(description="Encrypt all MLII recordings and PNGs for the local demo.")
    parser.add_argument("--project", type=Path, default=Path.cwd())
    args = parser.parse_args()
    def show_progress(done: int, total: int, message: str) -> None:
        if done % 100 == 0 or done == total:
            print(message, flush=True)
    print(json.dumps(encrypt_dataset(args.project, show_progress), indent=2))


if __name__ == "__main__":
    main()
