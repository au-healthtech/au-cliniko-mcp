"""Cliniko API key parsing and shard detection.

Cliniko keys carry their shard as a suffix after the last hyphen:
    MS0xLTEtMTcyOC0xMTAtRkpRNVZUMVRBSjJTRjN-au1
                                              ^^^ shard
The body of the key contains hyphens too, so we use `rsplit('-', 1)` to
isolate only the trailing shard token. The shard determines the API base URL:
    api.au1.cliniko.com, api.au2.cliniko.com, ..., api.uk1.cliniko.com, api.us1.cliniko.com.

This module deliberately fails LOUD on a missing or unrecognised shard.
The reference implementations in the wild silently default to `au1` or `au4`
and break tenants on other shards. We surface the problem at boot instead.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Cliniko shards documented as of 2026-05-18:
#   au1, au2, au3, au4 (Australia)
#   uk1, uk2 (United Kingdom)
#   us1 (United States)
#   ca1 (Canada)
# New shards may appear — we accept any lowercase alpha + digit token matching the pattern.
SHARD_PATTERN = re.compile(r"^[a-z]{2,3}[0-9]{1,2}$")


class InvalidClinikoApiKey(ValueError):
    """Raised when an API key cannot be parsed or carries no shard."""


@dataclass(frozen=True)
class ClinikoCredential:
    """Parsed Cliniko API key + the derived API base URL."""

    api_key: str
    shard: str
    base_url: str
    user_agent: str

    @classmethod
    def from_env(
        cls, api_key: str, *, user_agent_email: str, base_url_override: str | None = None
    ) -> "ClinikoCredential":
        """Parse a credential from environment-style inputs.

        Args:
            api_key: The raw Cliniko API key. Whitespace is trimmed.
            user_agent_email: Contact email surfaced in the User-Agent header. Required.
                Cliniko docs explicitly warn that requests without an identifying
                User-Agent may be blocked in future.
            base_url_override: Optional. If supplied, bypasses shard detection and uses
                this URL verbatim. Used for testing against a recorded fixture server.

        Raises:
            InvalidClinikoApiKey: if the key is empty, malformed, or carries no shard
                (and no base_url_override is supplied).
        """
        cleaned_key = (api_key or "").strip()
        if not cleaned_key:
            raise InvalidClinikoApiKey("Cliniko API key is empty.")

        if not user_agent_email or "@" not in user_agent_email:
            raise InvalidClinikoApiKey(
                "User-Agent email is required (e.g. tradd@principalpodiatry.com.au). "
                "Cliniko may block requests without an identifying User-Agent."
            )

        if base_url_override:
            return cls(
                api_key=cleaned_key,
                shard="override",
                base_url=base_url_override.rstrip("/"),
                user_agent=_build_user_agent(user_agent_email),
            )

        # Split on the LAST hyphen — the key body contains hyphens too.
        if "-" not in cleaned_key:
            raise InvalidClinikoApiKey(
                "API key has no shard suffix. Expected format ending in `-au1`, `-uk1`, etc. "
                "Re-copy the key from Cliniko (My Info → Manage API keys)."
            )

        _, possible_shard = cleaned_key.rsplit("-", 1)
        possible_shard = possible_shard.lower()

        if not SHARD_PATTERN.match(possible_shard):
            raise InvalidClinikoApiKey(
                f"API key carries an unrecognised shard token: `{possible_shard}`. "
                "Expected something like `au1`, `au4`, `uk2`, `us1`. "
                "If Cliniko has added a new shard, set CLINIKO_BASE_URL directly to override."
            )

        return cls(
            api_key=cleaned_key,
            shard=possible_shard,
            base_url=f"https://api.{possible_shard}.cliniko.com/v1",
            user_agent=_build_user_agent(user_agent_email),
        )


def _build_user_agent(contact_email: str) -> str:
    """Compose the User-Agent string Cliniko sees on every request.

    Cliniko docs (https://docs.api.cliniko.com/) state the User-Agent must contain
    an app name + contact email or future requests may be blocked.
    """
    from au_cliniko_mcp import __version__

    return f"au-cliniko-mcp/{__version__} ({contact_email})"
