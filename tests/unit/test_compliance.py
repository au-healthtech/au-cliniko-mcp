"""Unit tests for the Phase C compliance layer: audit + phi + vault.

These tests run without network access. They guard the security-critical paths
that procurement officers will look at first.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path

import pytest

from au_cliniko_mcp.audit import AuditLog, redact_args
from au_cliniko_mcp.phi import (
    ALL_PHI_CATEGORIES,
    PHI_CLINICAL_NOTES,
    PHI_CONTACT,
    PHI_DEMOGRAPHICS,
    phi_flagged,
)
from au_cliniko_mcp.vault import Vault, VaultError


# ----- Audit log -----

class TestAuditLog:
    @pytest.fixture
    def tmp_db(self, tmp_path):
        return tmp_path / "audit.db"

    @pytest.mark.asyncio
    async def test_records_a_call(self, tmp_db):
        a = AuditLog(tmp_db)
        await a.record(
            tool_name="list_patients",
            args={"page": 1},
            phi_categories=["demographics"],
            result_status="ok",
            elapsed_ms=42,
            patient_id="pat_123",
        )
        rows = a.query_recent(limit=10)
        assert len(rows) == 1
        assert rows[0]["tool_name"] == "list_patients"
        assert rows[0]["patient_id"] == "pat_123"
        assert rows[0]["elapsed_ms"] == 42

    @pytest.mark.asyncio
    async def test_persists_across_instances(self, tmp_db):
        a1 = AuditLog(tmp_db)
        await a1.record(
            tool_name="get_patient", args={"patient_id": "x"},
            phi_categories=["demographics"], result_status="ok", elapsed_ms=1,
        )
        # New instance reads the same file
        a2 = AuditLog(tmp_db)
        rows = a2.query_recent()
        assert len(rows) == 1

    @pytest.mark.asyncio
    async def test_prune_removes_old_records(self, tmp_db):
        a = AuditLog(tmp_db)
        await a.record(
            tool_name="x", args={}, phi_categories=[],
            result_status="ok", elapsed_ms=1,
        )
        # Force a row to be old by editing its timestamp
        import sqlite3
        with sqlite3.connect(tmp_db) as conn:
            conn.execute("UPDATE tool_call_audit SET ts = '2020-01-01T00:00:00+00:00'")
        deleted = await a.prune(retention_days=30)
        assert deleted == 1
        assert a.query_recent() == []

    @pytest.mark.asyncio
    async def test_filters_by_patient(self, tmp_db):
        a = AuditLog(tmp_db)
        for pid in ["p1", "p2", "p1"]:
            await a.record(
                tool_name="list_appointments_for_patient",
                args={"patient_id": pid}, phi_categories=["appointment_metadata"],
                result_status="ok", elapsed_ms=10, patient_id=pid,
            )
        p1 = a.query_recent(patient_id="p1")
        assert len(p1) == 2
        p2 = a.query_recent(patient_id="p2")
        assert len(p2) == 1

    @pytest.mark.asyncio
    async def test_failure_does_not_raise(self, tmp_path):
        """Audit must never break the tool call. Force a write error and ensure no exception."""
        # Point at a path under a read-only ancestor → write fails
        bad = tmp_path / "ro" / "audit.db"
        bad.parent.mkdir()
        bad.parent.chmod(0o500)  # read+exec only
        try:
            a = AuditLog(bad)
            # Should NOT raise
            await a.record(
                tool_name="x", args={}, phi_categories=[],
                result_status="ok", elapsed_ms=1,
            )
        finally:
            bad.parent.chmod(0o700)


class TestRedaction:
    def test_blanks_clinical_content(self):
        out = redact_args({"content": "patient reports plantar pain"})
        assert out["content"].startswith("<redacted:")

    def test_preserves_ids_and_dates(self):
        out = redact_args({"patient_id": "12345", "page": 1, "from_date": "2026-01-01"})
        assert out["patient_id"] == "12345"
        assert out["page"] == 1
        assert out["from_date"] == "2026-01-01"

    def test_blanks_name_search(self):
        out = redact_args({"query": "Jane Smith"})
        assert out["query"].startswith("<redacted:")

    def test_recurses_into_nested(self):
        out = redact_args({"outer": {"first_name": "Jane", "patient_id": "1"}})
        assert out["outer"]["first_name"].startswith("<redacted:")
        assert out["outer"]["patient_id"] == "1"

    def test_handles_list_of_dicts(self):
        out = redact_args({"items": [{"content": "A"}, {"content": "B"}]})
        assert all(i["content"].startswith("<redacted:") for i in out["items"])


# ----- PHI decorator -----

class TestPhiFlagged:
    @pytest.mark.asyncio
    async def test_attaches_phi_header(self, monkeypatch, tmp_path):
        import au_cliniko_mcp.phi as phi_mod
        a = AuditLog(tmp_path / "a.db")
        monkeypatch.setattr(phi_mod, "get_audit_log", lambda: a)

        @phi_flagged(PHI_DEMOGRAPHICS, PHI_CONTACT)
        async def my_tool():
            return {"patients": []}
        out = await my_tool()
        assert "_phi" in out
        assert out["_phi"]["categories"] == [PHI_DEMOGRAPHICS, PHI_CONTACT]

    @pytest.mark.asyncio
    async def test_records_to_audit_log(self, monkeypatch, tmp_path):
        import au_cliniko_mcp.phi as phi_mod
        a = AuditLog(tmp_path / "a.db")
        monkeypatch.setattr(phi_mod, "get_audit_log", lambda: a)

        @phi_flagged(PHI_DEMOGRAPHICS)
        async def my_tool(patient_id: str):
            return {"id": patient_id}
        await my_tool(patient_id="pat_42")
        rows = a.query_recent()
        assert len(rows) == 1
        assert rows[0]["patient_id"] == "pat_42"
        assert rows[0]["tool_name"] == "my_tool"

    @pytest.mark.asyncio
    async def test_records_error_on_exception(self, monkeypatch, tmp_path):
        import au_cliniko_mcp.phi as phi_mod
        a = AuditLog(tmp_path / "a.db")
        monkeypatch.setattr(phi_mod, "get_audit_log", lambda: a)

        @phi_flagged(PHI_DEMOGRAPHICS)
        async def boom():
            raise ValueError("nope")
        with pytest.raises(ValueError):
            await boom()
        rows = a.query_recent()
        assert rows[0]["result_status"] == "error"
        assert "nope" in (rows[0]["error"] or "")

    @pytest.mark.asyncio
    async def test_classifies_uncommitted_draft(self, monkeypatch, tmp_path):
        import au_cliniko_mcp.phi as phi_mod
        a = AuditLog(tmp_path / "a.db")
        monkeypatch.setattr(phi_mod, "get_audit_log", lambda: a)

        @phi_flagged(PHI_CLINICAL_NOTES, write=True)
        async def draft():
            return {"id": "n_1", "draft": True}
        await draft()
        rows = a.query_recent()
        assert rows[0]["result_status"] == "uncommitted_draft"

    @pytest.mark.asyncio
    async def test_classifies_cost_blocked(self, monkeypatch, tmp_path):
        import au_cliniko_mcp.phi as phi_mod
        a = AuditLog(tmp_path / "a.db")
        monkeypatch.setattr(phi_mod, "get_audit_log", lambda: a)

        @phi_flagged(PHI_DEMOGRAPHICS)
        async def cost_blocked():
            return {"needs_confirmation": True, "total_entries": 5000}
        await cost_blocked()
        rows = a.query_recent()
        assert rows[0]["result_status"] == "cost_blocked"

    def test_rejects_invalid_categories(self):
        with pytest.raises(ValueError, match="Unknown PHI categories"):

            @phi_flagged("not_a_real_category")
            async def _():
                return {}


# ----- Vault -----

class TestVault:
    @pytest.fixture
    def vault(self, tmp_path):
        return Vault(tmp_path / "vault.db", tmp_path / "vault.key")

    def test_put_get_roundtrip(self, vault):
        vault.put("cliniko_api_key", "secret-au5-key")
        assert vault.get("cliniko_api_key") == "secret-au5-key"

    def test_get_missing_returns_none(self, vault):
        assert vault.get("nope") is None

    def test_overwrite(self, vault):
        vault.put("k", "v1")
        vault.put("k", "v2")
        assert vault.get("k") == "v2"

    def test_delete(self, vault):
        vault.put("k", "v")
        assert vault.delete("k") is True
        assert vault.get("k") is None
        assert vault.delete("k") is False  # already gone

    def test_list_names_does_not_leak_values(self, vault):
        vault.put("api_key", "super-secret")
        vault.put("license_key", "another-secret")
        names = vault.list_names()
        assert sorted(names) == ["api_key", "license_key"]

    def test_multi_tenant_isolation(self, vault):
        vault.put("api_key", "clinic-A-key", tenant_id="clinic_A")
        vault.put("api_key", "clinic-B-key", tenant_id="clinic_B")
        assert vault.get("api_key", tenant_id="clinic_A") == "clinic-A-key"
        assert vault.get("api_key", tenant_id="clinic_B") == "clinic-B-key"

    def test_persists_across_instances(self, tmp_path):
        v1 = Vault(tmp_path / "v.db", tmp_path / "v.key")
        v1.put("api_key", "secret")
        v2 = Vault(tmp_path / "v.db", tmp_path / "v.key")
        assert v2.get("api_key") == "secret"

    def test_corrupt_key_raises_vault_error(self, tmp_path):
        # Pre-write a malformed key
        keypath = tmp_path / "vault.key"
        keypath.parent.mkdir(parents=True, exist_ok=True)
        keypath.write_bytes(b"not-a-valid-fernet-key")
        v = Vault(tmp_path / "v.db", keypath)
        with pytest.raises(VaultError, match="corrupt|wrong size"):
            v.put("k", "v")

    def test_tampered_ciphertext_raises_vault_error(self, vault, tmp_path):
        vault.put("k", "real-secret")
        # Manually corrupt the ciphertext in the db
        import sqlite3
        with sqlite3.connect(vault.db_path) as conn:
            conn.execute("UPDATE vault SET ciphertext = ? WHERE name = ?", (b"garbage", "k"))
        with pytest.raises(VaultError, match="failed to decrypt"):
            vault.get("k")


# ----- PHI taxonomy -----

class TestPhiTaxonomy:
    def test_categories_have_string_constants(self):
        # Every PHI_* constant should be in ALL_PHI_CATEGORIES
        from au_cliniko_mcp import phi
        for name in dir(phi):
            if name.startswith("PHI_") and name not in {"PHI_TEXT_FIELDS"}:
                val = getattr(phi, name)
                if isinstance(val, str):
                    assert val in ALL_PHI_CATEGORIES, f"{name}={val} not in ALL_PHI_CATEGORIES"
