"""Readable decoy ECG MAT files with authenticated, byte-exact recovery.

The public ``val`` is deliberately altered research data, NOT ciphertext and
NOT a clinically usable ECG. AES-256-GCM protects the embedded original MAT.
The decoy signal and recording context are authenticated as associated data.
"""
from __future__ import annotations

from io import BytesIO
import os
import struct

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
import numpy as np
from scipy.io import loadmat, savemat, whosmat

MAX_CONTAINER_BYTES = 16 * 1024 * 1024
_VERSION = 1
_AAD_MAGIC = b"ECG-CAMOUFLAGED-MAT-1\x00"
_FIELDS = {"val", "recovery_payload", "recovery_nonce", "camouflage_version", "camouflage_context"}


def _context_bytes(context: str) -> bytes:
    if not isinstance(context, str) or not context or len(context) > 256:
        raise ValueError("A nonempty recording context of at most 256 characters is required.")
    encoded = context.encode("utf-8")
    if len(encoded) > 1024:
        raise ValueError("Recording context is too long.")
    return encoded


def _signal(signal: np.ndarray) -> np.ndarray:
    raw = np.asarray(signal)
    if raw.shape != (3600,) or not np.issubdtype(raw.dtype, np.number) or np.iscomplexobj(raw):
        raise ValueError("Camouflaged ECG must have exactly 3,600 real numeric samples.")
    with np.errstate(over="ignore", invalid="ignore"):
        result = np.ascontiguousarray(raw, dtype="<f4")
    if not np.isfinite(result).all():
        raise ValueError("Camouflaged ECG samples must be finite float32 values.")
    return result


def _aad(signal: np.ndarray, context: str) -> bytes:
    encoded = _context_bytes(context)
    return _AAD_MAGIC + struct.pack("<I", len(encoded)) + encoded + signal.tobytes(order="C")


def _check_key(key: bytes) -> None:
    if not isinstance(key, bytes) or len(key) != 32:
        raise ValueError("Camouflaged MAT recovery requires a 32-byte AES key.")


def create_camouflaged_mat(original_bytes: bytes, signal: np.ndarray, key: bytes, context: str) -> bytes:
    """Return a MAT containing public altered ``val`` and encrypted original bytes."""
    _check_key(key)
    if not isinstance(original_bytes, bytes) or not original_bytes or len(original_bytes) > MAX_CONTAINER_BYTES:
        raise ValueError("Original MAT bytes must be nonempty and within the 16 MiB limit.")
    raw, encoded = _signal(signal), _context_bytes(context)
    nonce = os.urandom(12)
    ciphertext = AESGCM(key).encrypt(nonce, original_bytes, _aad(raw, context))
    output = BytesIO()
    savemat(output, {
        "val": raw.reshape(1, -1),
        "recovery_payload": np.frombuffer(ciphertext, dtype=np.uint8).reshape(1, -1),
        "recovery_nonce": np.frombuffer(nonce, dtype=np.uint8).reshape(1, -1),
        "camouflage_version": np.array([[_VERSION]], dtype=np.uint8),
        "camouflage_context": np.frombuffer(encoded, dtype=np.uint8).reshape(1, -1),
    }, do_compression=False, format="5")
    result = output.getvalue()
    if len(result) > MAX_CONTAINER_BYTES:
        raise ValueError("Camouflaged MAT exceeds the 16 MiB container size limit.")
    return result


def _validate_layout(payload: bytes) -> None:
    """Reject compressed/oversized/malformed MAT elements before array allocation."""
    if not isinstance(payload, bytes) or len(payload) < 128 or len(payload) > MAX_CONTAINER_BYTES:
        raise ValueError("Expected a camouflaged MAT within the 16 MiB limit.")
    if payload[126:128] not in (b"IM", b"MI"):
        raise ValueError("Camouflaged MAT must use the uncompressed MATLAB version 5 format.")
    endian = "<" if payload[126:128] == b"IM" else ">"
    offset, count = 128, 0
    while offset < len(payload):
        if offset + 8 > len(payload):
            raise ValueError("Truncated camouflaged MAT element.")
        kind, size = struct.unpack_from(endian + "II", payload, offset)
        if kind != 14 or size < 32 or size > MAX_CONTAINER_BYTES:
            raise ValueError("Only uncompressed MAT matrix elements are allowed.")
        offset += 8 + ((size + 7) // 8) * 8
        count += 1
        if offset > len(payload) or count > len(_FIELDS):
            raise ValueError("Invalid camouflaged MAT element layout.")
    if offset != len(payload) or count != len(_FIELDS):
        raise ValueError("Incomplete camouflaged MAT container.")


def inspect_camouflaged_mat(payload: bytes, context: str) -> dict:
    """Read the public decoy. This inspection does NOT authenticate its contents."""
    _validate_layout(payload)
    expected_context = _context_bytes(context)
    try:
        layout = whosmat(BytesIO(payload))
        if len(layout) != len(_FIELDS) or {name for name, _, _ in layout} != _FIELDS:
            raise ValueError("Unexpected variables in camouflaged MAT.")
        for name, shape, dtype in layout:
            if len(shape) != 2 or shape[0] != 1:
                raise ValueError("Invalid camouflaged MAT variable shape.")
            size = shape[1]
            if name == "val":
                valid = size == 3600 and dtype == "single"
            elif name == "recovery_nonce":
                valid = size == 12 and dtype == "uint8"
            elif name == "camouflage_version":
                valid = size == 1 and dtype == "uint8"
            elif name == "camouflage_context":
                valid = 1 <= size <= 1024 and dtype == "uint8"
            else:
                valid = 16 < size <= MAX_CONTAINER_BYTES and dtype == "uint8"
            if not valid:
                raise ValueError("Invalid camouflaged MAT variable type or size.")
        data = loadmat(BytesIO(payload), variable_names=list(_FIELDS))
        if any(data[field].dtype != np.dtype("uint8") for field in _FIELDS - {"val"}):
            raise ValueError("Recovery fields must contain unsigned bytes, not complex or other arrays.")
        raw = _signal(data["val"].reshape(-1))
        if data["camouflage_version"].item() != _VERSION:
            raise ValueError("Unsupported camouflaged MAT version.")
        if data["camouflage_context"].tobytes() != expected_context:
            raise ValueError("Camouflaged MAT belongs to a different recording context.")
        return {"signal": raw, "recovery_payload": data["recovery_payload"].tobytes(),
                "nonce": data["recovery_nonce"].tobytes(), "version": _VERSION,
                "authenticated": False}
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("Malformed camouflaged MAT container.") from exc


def restore_camouflaged_mat(payload: bytes, key: bytes, context: str) -> bytes:
    """Authenticate the public decoy/context and recover the original MAT exactly."""
    _check_key(key)
    data = inspect_camouflaged_mat(payload, context)
    return AESGCM(key).decrypt(data["nonce"], data["recovery_payload"], _aad(data["signal"], context))
