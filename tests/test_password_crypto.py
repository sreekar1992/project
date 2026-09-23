"""Password envelope regression checks using synthetic, public test fixtures."""
import unittest
from unittest.mock import patch

from cryptography.exceptions import InvalidTag

from ecg_cvd.crypto import (
    KEY_BYTES,
    MAX_PASSWORD_BYTES,
    NONCE_BYTES,
    PASSWORD_MAGIC,
    SALT_BYTES,
    SCRYPT_N,
    SCRYPT_P,
    SCRYPT_R,
    TAG_BYTES,
    decrypt_bytes,
    decrypt_password,
    encrypt_bytes,
    encrypt_password,
)


class PasswordEncryptionTests(unittest.TestCase):
    PASSWORD = "ECG research test passphrase"
    PLAINTEXT = b"\x89PNG\r\n\x1a\nECG image test\x00\xff"

    @classmethod
    def setUpClass(cls):
        cls.payload = encrypt_password(cls.PLAINTEXT, cls.PASSWORD)

    def test_round_trip_and_documented_envelope(self):
        self.assertEqual(decrypt_password(self.payload, self.PASSWORD), self.PLAINTEXT)
        self.assertTrue(self.payload.startswith(PASSWORD_MAGIC))
        self.assertEqual(
            len(self.payload),
            len(PASSWORD_MAGIC) + SALT_BYTES + NONCE_BYTES + len(self.PLAINTEXT) + TAG_BYTES,
        )
        self.assertNotIn(self.PLAINTEXT, self.payload)
        self.assertNotIn(self.PASSWORD.encode(), self.payload)

    def test_fresh_salt_nonce_and_ciphertext(self):
        other = encrypt_password(self.PLAINTEXT, self.PASSWORD)
        start = len(PASSWORD_MAGIC)
        self.assertNotEqual(self.payload[start:start + SALT_BYTES], other[start:start + SALT_BYTES])
        nonce_start = start + SALT_BYTES
        self.assertNotEqual(
            self.payload[nonce_start:nonce_start + NONCE_BYTES],
            other[nonce_start:nonce_start + NONCE_BYTES],
        )
        self.assertNotEqual(self.payload, other)

    def test_wrong_password_fails_authentication(self):
        for wrong_password in ("a different passphrase", "x"):
            with self.subTest(password_length=len(wrong_password)), self.assertRaises(InvalidTag):
                decrypt_password(self.payload, wrong_password)

    def test_tampering_salt_nonce_ciphertext_or_tag_fails_authentication(self):
        salt_start = len(PASSWORD_MAGIC)
        nonce_start = salt_start + SALT_BYTES
        ciphertext_start = nonce_start + NONCE_BYTES
        for position in (salt_start, nonce_start, ciphertext_start, len(self.payload) - 1):
            modified = bytearray(self.payload)
            modified[position] ^= 1
            with self.subTest(position=position), self.assertRaises(InvalidTag):
                decrypt_password(bytes(modified), self.PASSWORD)

    def test_appending_bytes_fails_authentication(self):
        with self.assertRaises(InvalidTag):
            decrypt_password(self.payload + b"extra", self.PASSWORD)

    def test_truncated_ciphertext_fails_authentication(self):
        with self.assertRaises(InvalidTag):
            decrypt_password(self.payload[:-1], self.PASSWORD)

    def test_truncated_or_unsupported_headers_rejected_before_kdf(self):
        minimum = len(PASSWORD_MAGIC) + SALT_BYTES + NONCE_BYTES + TAG_BYTES
        malformed = [self.payload[:length] for length in range(minimum)]
        malformed.extend((b"unknown" + self.payload, self.payload.replace(b"GCM-1", b"GCM-2", 1)))
        with patch("ecg_cvd.crypto._password_key") as derive:
            for payload in malformed:
                with self.subTest(length=len(payload)), self.assertRaises(ValueError):
                    decrypt_password(payload, self.PASSWORD)
            derive.assert_not_called()

    def test_legacy_format_is_separate_and_still_works(self):
        key = bytes(range(KEY_BYTES))
        legacy = encrypt_bytes(self.PLAINTEXT, key)
        self.assertEqual(decrypt_bytes(legacy, key), self.PLAINTEXT)
        with self.assertRaises(ValueError):
            decrypt_password(legacy, self.PASSWORD)
        with self.assertRaises(ValueError):
            decrypt_bytes(self.payload, key)

    def test_empty_plaintext_round_trip(self):
        self.assertEqual(decrypt_password(encrypt_password(b"", self.PASSWORD), self.PASSWORD), b"")

    def test_password_spaces_and_unicode_are_preserved(self):
        password = "  ECG p\u00e4ssword\U0001f512  "
        payload = encrypt_password(self.PLAINTEXT, password)
        self.assertEqual(decrypt_password(payload, password), self.PLAINTEXT)
        with self.assertRaises(InvalidTag):
            decrypt_password(payload, password.strip())

    def test_encrypt_password_limits_validated_before_kdf(self):
        invalid = ("", "12345678901", "\U0001f512" * 11,
                   "x" * (MAX_PASSWORD_BYTES + 1), "\U0001f512" * 257,
                   "invalid text\ud800", None, b"not text")
        with patch("ecg_cvd.crypto._password_key") as derive:
            for password in invalid:
                with self.subTest(kind=type(password).__name__), self.assertRaises(ValueError):
                    encrypt_password(self.PLAINTEXT, password)
            derive.assert_not_called()

    def test_decrypt_password_limits_validated_before_kdf(self):
        invalid = ("", "x" * (MAX_PASSWORD_BYTES + 1), "\U0001f512" * 257,
                   "invalid text\ud800", None, b"not text")
        with patch("ecg_cvd.crypto._password_key") as derive:
            for password in invalid:
                with self.subTest(kind=type(password).__name__), self.assertRaises(ValueError):
                    decrypt_password(self.payload, password)
            derive.assert_not_called()

    def test_exact_password_limits_are_accepted(self):
        for password in ("a" * 12, "\U0001f512" * 256):
            with self.subTest(length=len(password)):
                payload = encrypt_password(self.PLAINTEXT, password)
                self.assertEqual(decrypt_password(payload, password), self.PLAINTEXT)

    def test_kdf_cost_is_fixed_and_header_is_authenticated(self):
        with patch("ecg_cvd.crypto.Scrypt") as scrypt, patch("ecg_cvd.crypto.AESGCM") as aes:
            scrypt.return_value.derive.return_value = bytes(KEY_BYTES)
            aes.return_value.encrypt.return_value = b"test tag and ciphertext"
            payload = encrypt_password(self.PLAINTEXT, self.PASSWORD)
            salt_start = len(PASSWORD_MAGIC)
            salt_end = salt_start + SALT_BYTES
            scrypt.assert_called_once_with(
                salt=payload[salt_start:salt_end], length=32, n=2 ** 17, r=8, p=1
            )
            aes.return_value.encrypt.assert_called_once_with(
                payload[salt_end:salt_end + NONCE_BYTES], self.PLAINTEXT, payload[:salt_end]
            )
        self.assertEqual((SCRYPT_N, SCRYPT_R, SCRYPT_P), (2 ** 17, 8, 1))


if __name__ == "__main__":
    unittest.main()
