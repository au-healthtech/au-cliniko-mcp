"""Per-tenant secret vault — Fernet-encrypted at rest.

Why this exists:
    The current reference (BoabAI, andymillar84, Practisight) all store the
    Cliniko API key plaintext in Claude Desktop's config file. Acceptable for
    a hobby project; unacceptable for a clinic with patient data.

    This vault keeps tenant-scoped secrets (Cliniko API key, future license
    key, future Twilio SID etc.) in an encrypted SQLite file under
    ~/.au-cliniko-mcp/vault.db. The encryption key lives in vault.key (chmod 600).

Limitations (deliberate, Phase C-MVP scope):
    - Single-tenant. Phase F (hosted gateway) will move this to KMS-backed
      envelope encryption per-clinic. The interface stays the same.
    - The vault.key file is unprotected beyond chmod 600. Any process running
      as `tradd` can read it. Phase F replaces this with OS keychain on Mac/Win
      and a KMS for the hosted gateway.
    - This is "encrypted at rest" — the in-memory plaintext exists during a
      session, which is unavoidable for an MCP that must send the API key on
      every Cliniko request. The audit log still won't see the key contents.
"""

from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger("au_cliniko_mcp.vault")

DEFAULT_VAULT_DB = Path.home() / ".au-cliniko-mcp" / "vault.db"
DEFAULT_KEY_PATH = Path.home() / ".au-cliniko-mcp" / "vault.key"


class VaultError(Exception):
    pass


class Vault:
    """Per-tenant secret store. Single-tenant for v1; multi-tenant for Phase F."""

    def __init__(
        self,
        db_path: Path | str = DEFAULT_VAULT_DB,
        key_path: Path | str = DEFAULT_KEY_PATH,
    ):
        self.db_path = Path(db_path)
        self.key_path = Path(key_path)
        self._fernet: Fernet | None = None
        self._init_done = False

    def _ensure_init(self) -> None:
        if self._init_done:
            return
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._fernet = Fernet(self._load_or_create_key())
        with self._connect() as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS vault (
                    tenant_id  TEXT NOT NULL,
                    name       TEXT NOT NULL,
                    ciphertext BLOB NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                    PRIMARY KEY (tenant_id, name)
                )"""
            )
        self._init_done = True

    def _load_or_create_key(self) -> bytes:
        """Read the Fernet key from disk; generate one on first run.

        Permissions: chmod 600 so other local users can't read it. On macOS
        this is in ~/.au-cliniko-mcp/, which is the user's home — same trust
        zone as ssh keys.
        """
        if self.key_path.exists():
            key = self.key_path.read_bytes().strip()
            try:
                # Validate by attempting a no-op encrypt/decrypt
                Fernet(key).encrypt(b"_test_")
                return key
            except Exception as exc:
                raise VaultError(
                    f"vault.key at {self.key_path} is corrupt or wrong size: {exc}"
                ) from exc

        # Generate a fresh key
        key = Fernet.generate_key()
        self.key_path.parent.mkdir(parents=True, exist_ok=True)
        self.key_path.write_bytes(key)
        try:
            self.key_path.chmod(0o600)
        except OSError:
            logger.warning("could not chmod 600 on %s — fix manually", self.key_path)
        logger.info("generated new vault.key at %s", self.key_path)
        return key

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.db_path, isolation_level=None)
        try:
            yield conn
        finally:
            conn.close()

    def put(self, name: str, value: str, *, tenant_id: str = "default") -> None:
        """Encrypt + store. Overwrites if name already exists."""
        self._ensure_init()
        assert self._fernet is not None
        ct = self._fernet.encrypt(value.encode("utf-8"))
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO vault (tenant_id, name, ciphertext)
                   VALUES (?, ?, ?)
                   ON CONFLICT(tenant_id, name) DO UPDATE
                     SET ciphertext = excluded.ciphertext,
                         updated_at = datetime('now')""",
                (tenant_id, name, ct),
            )

    def get(self, name: str, *, tenant_id: str = "default") -> str | None:
        """Decrypt + return. None if no entry. Raises VaultError on tamper."""
        self._ensure_init()
        assert self._fernet is not None
        with self._connect() as conn:
            row = conn.execute(
                "SELECT ciphertext FROM vault WHERE tenant_id = ? AND name = ?",
                (tenant_id, name),
            ).fetchone()
        if not row:
            return None
        try:
            return self._fernet.decrypt(row[0]).decode("utf-8")
        except InvalidToken as exc:
            raise VaultError(
                f"vault entry {tenant_id}:{name} failed to decrypt — wrong key or tampered"
            ) from exc

    def delete(self, name: str, *, tenant_id: str = "default") -> bool:
        """Remove an entry. Returns True if a row was deleted."""
        self._ensure_init()
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM vault WHERE tenant_id = ? AND name = ?",
                (tenant_id, name),
            )
            return cur.rowcount > 0

    def list_names(self, *, tenant_id: str = "default") -> list[str]:
        """Names of entries for a tenant (no decryption — cheap)."""
        self._ensure_init()
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT name FROM vault WHERE tenant_id = ? ORDER BY name",
                (tenant_id,),
            ).fetchall()
        return [r[0] for r in rows]
