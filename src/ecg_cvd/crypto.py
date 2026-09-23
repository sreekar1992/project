"""Authenticated file encryption for generated ECG images and reports.

This module deliberately uses a standard AEAD construction rather than custom
chaos, steganography, or neural-network "encryption" schemes.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

MAGIC = b"ECG-AESGCM-1\x00"
NONCE_BYTES = 12
KEY_BYTES = 32

# Password files have a separate versioned envelope from legacy key files.
# Version 1 always uses the parameters below; payloads cannot request an
# attacker-selected work factor or silently downgrade their KDF strength.
PASSWORD_MAGIC = b"ECG-PASSWORD-AESGCM-1\x00"
SALT_BYTES = 16
TAG_BYTES = 16
SCRYPT_N = 2 ** 17
SCRYPT_R = 8
SCRYPT_P = 1
MIN_PASSWORD_CHARS = 12
MAX_PASSWORD_BYTES = 1024


def _password_bytes(password: str, *, encrypting: bool) -> bytes:
    if not isinstance(password, str):
        raise ValueError("Password must be text.")
    if not password or (encrypting and len(password) < MIN_PASSWORD_CHARS):
        if encrypting:
            raise ValueError(f"Use a passphrase of at least {MIN_PASSWORD_CHARS} characters.")
        raise ValueError("Enter the decryption password.")
    try:
        encoded = password.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError("Password must contain valid Unicode text.") from exc
    if len(encoded) > MAX_PASSWORD_BYTES:
        raise ValueError(f"Password must not exceed {MAX_PASSWORD_BYTES} UTF-8 bytes.")
    # Spaces and Unicode are intentional password characters. Never strip or
    # normalize them: decrypting must use exactly the text used to encrypt.
    return encoded


def _password_key(encoded_password: bytes, salt: bytes) -> bytes:
    return Scrypt(salt=salt, length=KEY_BYTES, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P).derive(
        encoded_password
    )


def encrypt_password(plaintext: bytes, password: str) -> bytes:
    """Encrypt with a password-derived AES-256-GCM key and fresh salt/nonce.

    Envelope v1: magic || 16-byte salt || 12-byte nonce || ciphertext || tag.
    The versioned magic and salt are authenticated as associated data. Passwords
    and derived keys are not written to disk. Python does not guarantee secure
    zeroization of the temporary immutable bytes held in process memory.
    """
    encoded = _password_bytes(password, encrypting=True)
    salt = os.urandom(SALT_BYTES)
    nonce = os.urandom(NONCE_BYTES)
    header = PASSWORD_MAGIC + salt
    key = _password_key(encoded, salt)
    ciphertext = AESGCM(key).encrypt(nonce, plaintext, header)
    return header + nonce + ciphertext


def decrypt_password(payload: bytes, password: str) -> bytes:
    """Authenticate and decrypt a v1 password envelope entirely in memory.

    Malformed/unsupported envelopes raise ValueError. Wrong passwords or
    modified salt, nonce, ciphertext, or tag raise cryptography's InvalidTag;
    callers must not disclose partially decrypted data on either failure.
    """
    encoded = _password_bytes(password, encrypting=False)
    header_end = len(PASSWORD_MAGIC) + SALT_BYTES
    nonce_end = header_end + NONCE_BYTES
    if not payload.startswith(PASSWORD_MAGIC) or len(payload) < nonce_end + TAG_BYTES:
        raise ValueError("Not a supported password-encrypted ECG file (version 1).")
    salt = payload[len(PASSWORD_MAGIC):header_end]
    nonce = payload[header_end:nonce_end]
    key = _password_key(encoded, salt)
    return AESGCM(key).decrypt(nonce, payload[nonce_end:], payload[:header_end])


def _read_key(path: Path) -> bytes:
    try:
        key = bytes.fromhex(path.read_text(encoding="ascii").strip())
    except (OSError, ValueError) as exc:
        raise ValueError("Key file must contain one hexadecimal AES-256 key.") from exc
    if len(key) != KEY_BYTES:
        raise ValueError("Key file must contain a 32-byte (64 hexadecimal character) AES-256 key.")
    return key


def _write_new_key(path: Path) -> bytes:
    if path.exists():
        raise FileExistsError(f"Refusing to overwrite existing key file: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    key = AESGCM.generate_key(bit_length=256)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="ascii") as handle:
        handle.write(key.hex() + "\n")
    return key


def encrypt_bytes(plaintext: bytes, key: bytes) -> bytes:
    """Encrypt bytes with AES-256-GCM; tampering is detected on decryption."""
    if len(key) != KEY_BYTES:
        raise ValueError("AES-256-GCM requires a 32-byte key.")
    nonce = os.urandom(NONCE_BYTES)
    ciphertext = AESGCM(key).encrypt(nonce, plaintext, MAGIC)
    return MAGIC + nonce + ciphertext


def decrypt_bytes(payload: bytes, key: bytes) -> bytes:
    if len(key) != KEY_BYTES:
        raise ValueError("AES-256-GCM requires a 32-byte key.")
    if not payload.startswith(MAGIC) or len(payload) < len(MAGIC) + NONCE_BYTES + 16:
        raise ValueError("Not a valid ECG AES-GCM encrypted file.")
    nonce = payload[len(MAGIC):len(MAGIC) + NONCE_BYTES]
    ciphertext = payload[len(MAGIC) + NONCE_BYTES:]
    return AESGCM(key).decrypt(nonce, ciphertext, MAGIC)


def encrypt_main() -> None:
    parser = argparse.ArgumentParser(description="Encrypt an ECG image/report using AES-256-GCM.")
    parser.add_argument("--input", type=Path, required=True, help="PNG, PDF, CSV, or other file to encrypt")
    parser.add_argument("--output", type=Path, required=True, help="Encrypted output, normally *.ecgenc")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--key-file", type=Path, help="Existing 32-byte AES key file")
    group.add_argument("--generate-key-file", type=Path, help="Create a new protected key file (mode 0600)")
    args = parser.parse_args()
    key = _read_key(args.key_file) if args.key_file else _write_new_key(args.generate_key_file)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(encrypt_bytes(args.input.read_bytes(), key))
    print(f"Encrypted {args.input} -> {args.output}")
    if args.generate_key_file:
        print(f"Created key file: {args.generate_key_file} (store it outside the project and do not commit it).")


def decrypt_main() -> None:
    parser = argparse.ArgumentParser(description="Decrypt an AES-256-GCM ECG file.")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--key-file", type=Path, required=True)
    args = parser.parse_args()
    plaintext = decrypt_bytes(args.input.read_bytes(), _read_key(args.key_file))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(plaintext)
    print(f"Decrypted {args.input} -> {args.output}; integrity check passed.")
