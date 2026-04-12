"""AES-256-GCM document decryption service.

Mirrors the Java platform API's EncryptionService / DocumentEncryptionService.

Encryption format (both files and wrapped DEKs):
    [12-byte IV][AES-256-GCM ciphertext + 16-byte auth tag]

Per-user DEK is stored in the ``user_encryption_keys`` table as
    base64([12-byte IV][wrapped DEK ciphertext])
and is unwrapped with the master key before use.
"""

import base64
import logging
from functools import lru_cache

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from legal_agent.config import Settings

logger = logging.getLogger(__name__)

IV_LENGTH = 12  # bytes


class DecryptionError(Exception):
    """Raised when decryption fails."""


class DecryptionService:
    def __init__(self, settings: Settings):
        self._master_key = self._decode_master_key(settings.document_encryption_master_key)
        dsn = (
            f"host={settings.postgres_host} port={settings.postgres_port} "
            f"dbname={settings.postgres_db} "
            f"user={settings.postgres_username} password={settings.postgres_password}"
        )
        self._pool = ConnectionPool(
            conninfo=dsn, min_size=1, max_size=3,
            kwargs={"row_factory": dict_row},
        )
        self._pool.wait()
        logger.info("DecryptionService ready")

    # ── public API ──────────────────────────────────────────────────────────

    def decrypt_file(self, encrypted_bytes: bytes, user_id: str) -> bytes:
        """Decrypt an S3 object encrypted with the user's DEK."""
        raw_dek = self._get_raw_dek(user_id)
        return self._aes_gcm_decrypt(encrypted_bytes, raw_dek)

    # ── internals ───────────────────────────────────────────────────────────

    @lru_cache(maxsize=256)
    def _get_raw_dek(self, user_id: str) -> bytes:
        """Fetch wrapped DEK from DB and unwrap with master key. Cached per user."""
        with self._pool.connection() as conn:
            row = conn.execute(
                "SELECT encrypted_dek FROM user_encryption_keys WHERE user_id = %s::uuid",
                (user_id,),
            ).fetchone()
        if not row:
            raise DecryptionError(f"No encryption key found for user {user_id}")
        wrapped = base64.b64decode(row["encrypted_dek"])
        return self._aes_gcm_decrypt(wrapped, self._master_key)

    @staticmethod
    def _aes_gcm_decrypt(data: bytes, key: bytes) -> bytes:
        """Decrypt [12-byte IV][ciphertext+tag] with AES-256-GCM."""
        if len(data) <= IV_LENGTH:
            raise DecryptionError("Data too short to contain IV + ciphertext")
        iv = data[:IV_LENGTH]
        ciphertext = data[IV_LENGTH:]
        return AESGCM(key).decrypt(iv, ciphertext, None)

    @staticmethod
    def _decode_master_key(master_key_b64: str) -> bytes:
        if not master_key_b64:
            raise DecryptionError(
                "DOCUMENT_ENCRYPTION_MASTER_KEY is not configured. "
                "Set it in .env to enable document decryption."
            )
        return base64.b64decode(master_key_b64)

    def close(self) -> None:
        self._pool.close()
