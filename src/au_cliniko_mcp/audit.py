"""Audit log — every MCP tool call recorded for AU compliance posture.

Why this exists:
    AU allied-health practices fall under the Privacy Act 1988 (APP 11 — security).
    OAIC's Notifiable Data Breaches scheme requires being able to reconstruct
    who accessed which patient's data when. An MCP that lets an LLM query
    Cliniko has to keep a trail; otherwise the practice can't answer a breach
    enquiry, can't satisfy AHPRA fitness-to-practise scrutiny, and can't
    prove to procurement that they have appropriate technical controls.

Design choices:
    - SQLite stored at ~/.au-cliniko-mcp/audit.db (file, single-tenant local install).
      Phase F (hosted gateway) will migrate to PostgreSQL centrally; the AuditLog
      class isolates that decision behind one method.
    - WAL mode for safe concurrent reads while a write is in flight.
    - 7-year retention by default (AU health-record statutory minimum).
    - Args are REDACTED before storage — patient_id stays as a queryable foreign
      key, but free-text fields are blanked. No PHI in the audit log.
    - Schema is forward-evolvable — `_apply_migrations` runs at startup.

Schema (v1):
    tool_call_audit
        id              INTEGER PRIMARY KEY
        ts              TIMESTAMPTZ        (UTC, ISO-8601)
        tenant_id       TEXT               ('default' for local installs)
        tool_name       TEXT               (the @mcp.tool name)
        patient_id      TEXT NULLABLE      (extracted from args/result when present)
        practitioner_id TEXT NULLABLE      (extracted from args/result when present)
        phi_categories  TEXT               (JSON list — see phi.py)
        result_status   TEXT               ('ok' / 'error' / 'uncommitted_draft' / 'committed' / 'cost_blocked')
        args_redacted   TEXT               (JSON, free-text fields blanked)
        elapsed_ms      INTEGER
        error           TEXT NULLABLE
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("au_cliniko_mcp.audit")

DEFAULT_DB_PATH = Path.home() / ".au-cliniko-mcp" / "audit.db"
DEFAULT_RETENTION_DAYS = 7 * 365  # 7 years
DEFAULT_TENANT_ID = "default"

# Args fields that may carry free-text PHI — blanked before persisting.
PHI_TEXT_FIELDS = frozenset({
    "content", "note", "notes", "message", "body", "subject",
    "first_name", "last_name", "email", "mobile", "phone",
    "date_of_birth", "dob", "address", "city", "post_code",
    "query",  # search_patients_by_name — name fragment is PHI
})


class AuditLog:
    """Append-only SQLite-backed tool-call audit log."""

    def __init__(self, db_path: Path | str = DEFAULT_DB_PATH):
        self.db_path = Path(db_path)
        self._init_done = False
        self._write_lock = asyncio.Lock()

    def _ensure_init(self) -> None:
        """Lazy-init: create dir + DB file + schema on first use."""
        if self._init_done:
            return
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            self._apply_migrations(conn)
        self._init_done = True

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.db_path, isolation_level=None, check_same_thread=False)
        try:
            yield conn
        finally:
            conn.close()

    def _apply_migrations(self, conn: sqlite3.Connection) -> None:
        """Idempotent schema setup."""
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS tool_call_audit (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                ts              TEXT NOT NULL,
                tenant_id       TEXT NOT NULL DEFAULT 'default',
                tool_name       TEXT NOT NULL,
                patient_id      TEXT,
                practitioner_id TEXT,
                phi_categories  TEXT NOT NULL DEFAULT '[]',
                result_status   TEXT NOT NULL,
                args_redacted   TEXT,
                elapsed_ms      INTEGER,
                error           TEXT
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_ts ON tool_call_audit(ts)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_patient ON tool_call_audit(patient_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_tool ON tool_call_audit(tool_name)")

    async def record(
        self,
        *,
        tool_name: str,
        args: dict[str, Any] | None,
        phi_categories: list[str],
        result_status: str,
        elapsed_ms: int,
        patient_id: str | None = None,
        practitioner_id: str | None = None,
        error: str | None = None,
        tenant_id: str = DEFAULT_TENANT_ID,
    ) -> None:
        """Persist one audit record. Best-effort — never raises into the caller."""
        try:
            self._ensure_init()
            redacted = redact_args(args or {})
            ts = datetime.now(timezone.utc).isoformat()
            async with self._write_lock:
                # SQLite call is sync; we're holding the lock so concurrent writes don't race.
                with self._connect() as conn:
                    conn.execute(
                        """INSERT INTO tool_call_audit
                        (ts, tenant_id, tool_name, patient_id, practitioner_id,
                         phi_categories, result_status, args_redacted, elapsed_ms, error)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            ts,
                            tenant_id,
                            tool_name,
                            patient_id,
                            practitioner_id,
                            json.dumps(phi_categories),
                            result_status,
                            json.dumps(redacted, default=str),
                            elapsed_ms,
                            error,
                        ),
                    )
        except Exception:
            # Audit failures must never break the tool call. Log + move on.
            logger.exception("audit_log_write_failed tool=%s", tool_name)

    async def prune(self, retention_days: int = DEFAULT_RETENTION_DAYS) -> int:
        """Delete records older than retention_days. Returns rows deleted."""
        self._ensure_init()
        cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).isoformat()
        async with self._write_lock:
            with self._connect() as conn:
                cur = conn.execute("DELETE FROM tool_call_audit WHERE ts < ?", (cutoff,))
                return cur.rowcount

    def query_recent(
        self,
        *,
        limit: int = 50,
        tool_name: str | None = None,
        patient_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Read-side helper for support / forensics."""
        self._ensure_init()
        clauses: list[str] = []
        params: list[Any] = []
        if tool_name:
            clauses.append("tool_name = ?")
            params.append(tool_name)
        if patient_id:
            clauses.append("patient_id = ?")
            params.append(patient_id)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(limit)
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                f"SELECT * FROM tool_call_audit {where} ORDER BY id DESC LIMIT ?",
                params,
            ).fetchall()
        return [dict(r) for r in rows]


def redact_args(args: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of args with PHI free-text fields blanked.

    Patient/practitioner IDs are preserved (they're already opaque and we
    need them for the audit trail). Free-text content is replaced with
    "<redacted:LEN>" so we can later prove "a message of length 142 was sent"
    without recording its contents.
    """
    out: dict[str, Any] = {}
    for k, v in args.items():
        if k in PHI_TEXT_FIELDS and isinstance(v, str):
            out[k] = f"<redacted:{len(v)}>"
        elif isinstance(v, dict):
            out[k] = redact_args(v)
        elif isinstance(v, list):
            out[k] = [redact_args(x) if isinstance(x, dict) else x for x in v]
        else:
            out[k] = v
    return out


# Module-level singleton — wired in server.py
_audit_singleton: AuditLog | None = None


def get_audit_log() -> AuditLog:
    """Lazily create the global audit log singleton."""
    global _audit_singleton
    if _audit_singleton is None:
        path = os.environ.get("AU_CLINIKO_MCP_AUDIT_DB", str(DEFAULT_DB_PATH))
        _audit_singleton = AuditLog(path)
    return _audit_singleton


def _stopwatch_ms_since(t0: float) -> int:
    return int((time.time() - t0) * 1000)
