"""Unit tests for the API-key parser + shard detection in auth.py.

These tests run without network access. They are the safety net against the
class of bugs the reference implementations exhibit (hardcoded `au4`, hardcoded
`uk2`, silent fallback to `au1` on a malformed key).
"""

from __future__ import annotations

import pytest

from au_cliniko_mcp.auth import ClinikoCredential, InvalidClinikoApiKey


EMAIL = "tradd@principalpodiatry.com.au"


class TestShardDetection:
    def test_au1(self):
        c = ClinikoCredential.from_env(api_key="MS0xLTEtMTcyOC0xMTAtRkpRNVZUMVRBSjJTRjNQ-au1", user_agent_email=EMAIL)
        assert c.shard == "au1"
        assert c.base_url == "https://api.au1.cliniko.com/v1"

    def test_au4(self):
        c = ClinikoCredential.from_env(api_key="MS0xLTEtMTcyOC0xMTAtUkVE-au4", user_agent_email=EMAIL)
        assert c.shard == "au4"
        assert c.base_url == "https://api.au4.cliniko.com/v1"

    def test_uk2(self):
        c = ClinikoCredential.from_env(api_key="MS0xLTEtMTcyOC0xMTAtUkVE-uk2", user_agent_email=EMAIL)
        assert c.shard == "uk2"
        assert c.base_url == "https://api.uk2.cliniko.com/v1"

    def test_uppercase_shard_normalised(self):
        c = ClinikoCredential.from_env(api_key="MS0xLTEtMTcyOC0xMTAtUkVE-AU1", user_agent_email=EMAIL)
        assert c.shard == "au1"

    def test_whitespace_trimmed(self):
        c = ClinikoCredential.from_env(api_key="  MS0xLTEtMTcyOC0xMTAtUkVE-au1  \n", user_agent_email=EMAIL)
        assert c.shard == "au1"

    def test_rsplit_handles_hyphens_in_body(self):
        # Real Cliniko keys contain MANY hyphens. The shard is only the trailing token.
        # Earlier hobby implementations used .split('-')[0] and exploded.
        key = "MS0xLTEtMTcyOC0xMTAtRkpRNVZUMVRBSjJTRjNQ-au3"
        c = ClinikoCredential.from_env(api_key=key, user_agent_email=EMAIL)
        assert c.shard == "au3"


class TestFailsLoudly:
    def test_empty_key(self):
        with pytest.raises(InvalidClinikoApiKey, match="empty"):
            ClinikoCredential.from_env(api_key="", user_agent_email=EMAIL)

    def test_missing_email(self):
        with pytest.raises(InvalidClinikoApiKey, match="User-Agent email"):
            ClinikoCredential.from_env(api_key="x-au1", user_agent_email="")

    def test_invalid_email(self):
        with pytest.raises(InvalidClinikoApiKey, match="User-Agent email"):
            ClinikoCredential.from_env(api_key="x-au1", user_agent_email="not-an-email")

    def test_no_hyphen_at_all(self):
        with pytest.raises(InvalidClinikoApiKey, match="no shard suffix"):
            ClinikoCredential.from_env(api_key="MS0xLTEtRkpR", user_agent_email=EMAIL)

    def test_unrecognised_shard_pattern(self):
        with pytest.raises(InvalidClinikoApiKey, match="unrecognised shard"):
            ClinikoCredential.from_env(api_key="MS0xLTEt-notashard", user_agent_email=EMAIL)


class TestBaseUrlOverride:
    def test_override_bypasses_shard_detection(self):
        # If a fixture server is in use, the shard is meaningless.
        c = ClinikoCredential.from_env(
            api_key="anything-au1",
            user_agent_email=EMAIL,
            base_url_override="http://localhost:8080/fixture",
        )
        assert c.shard == "override"
        assert c.base_url == "http://localhost:8080/fixture"

    def test_override_strips_trailing_slash(self):
        c = ClinikoCredential.from_env(
            api_key="anything-au1",
            user_agent_email=EMAIL,
            base_url_override="http://localhost:8080/fixture/",
        )
        assert c.base_url == "http://localhost:8080/fixture"


class TestUserAgent:
    def test_user_agent_includes_email(self):
        c = ClinikoCredential.from_env(api_key="x-au1", user_agent_email=EMAIL)
        assert EMAIL in c.user_agent
        assert "au-cliniko-mcp" in c.user_agent
