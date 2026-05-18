"""au-cliniko-mcp — FastMCP server entry point.

This is the boot path. It:
1. Reads credentials from environment.
2. Builds the shared ClinikoClient.
3. Registers tools, resources, and prompts.
4. Starts the stdio MCP server.

Phase A scaffolded one tool (list_patients). Phase B (this commit) wires the
Tier-1 read-side: patients (3 tools), practitioners, businesses, appointments
(2 tools), invoices (3 tools). Phase C will add the @phi_flagged and
@consent_gated decorators that wrap every tool registered here.
"""

from __future__ import annotations

import os
import sys

from mcp.server.fastmcp import FastMCP

from au_cliniko_mcp.auth import ClinikoCredential, InvalidClinikoApiKey
from au_cliniko_mcp.client import ClinikoClient
from au_cliniko_mcp.tools import (
    appointments as appointments_tool,
    businesses as businesses_tool,
    invoices as invoices_tool,
    patients as patients_tool,
    practitioners as practitioners_tool,
)


def _load_credential() -> ClinikoCredential:
    """Read env vars into a credential object. Exit cleanly with a useful message on failure."""
    api_key = os.getenv("CLINIKO_API_KEY", "").strip()
    user_agent_email = os.getenv("CLINIKO_USER_AGENT_EMAIL", "").strip()
    base_url_override = os.getenv("CLINIKO_BASE_URL", "").strip() or None

    if not api_key:
        sys.stderr.write(
            "ERROR: CLINIKO_API_KEY is not set.\n"
            "Set it in your Claude Desktop config or .env file.\n"
            "Generate one in Cliniko: My Info → Manage API keys.\n"
        )
        sys.exit(2)

    if not user_agent_email:
        sys.stderr.write(
            "ERROR: CLINIKO_USER_AGENT_EMAIL is not set.\n"
            "Cliniko may block requests without an identifying contact email.\n"
            "Set it to your practice support email.\n"
        )
        sys.exit(2)

    try:
        return ClinikoCredential.from_env(
            api_key=api_key,
            user_agent_email=user_agent_email,
            base_url_override=base_url_override,
        )
    except InvalidClinikoApiKey as exc:
        sys.stderr.write(f"ERROR: {exc}\n")
        sys.exit(2)


def build_server() -> tuple[FastMCP, ClinikoClient]:
    """Construct the MCP server with all tools registered."""
    credential = _load_credential()
    client = ClinikoClient(credential)

    mcp = FastMCP(
        name="au-cliniko-mcp",
        instructions=(
            "Open-source Cliniko MCP for Australian allied-health practices. "
            "Connects Claude directly to a Cliniko account via the Cliniko REST API. "
            f"Current Cliniko shard: {credential.shard}. "
            "All write operations default to draft mode and require an explicit commit step. "
            "PHI-touching reads are audit-logged when the audit-log database is configured."
        ),
    )

    # Tier 1 read-side (Phase B):
    patients_tool.register(mcp, client)
    practitioners_tool.register(mcp, client)
    businesses_tool.register(mcp, client)
    appointments_tool.register(mcp, client)
    invoices_tool.register(mcp, client)

    # Coming in subsequent Phase B commits:
    # bookings_tool.register(mcp, client)
    # treatment_notes_tool.register(mcp, client)  # with draft gate
    # recalls_tool.register(mcp, client)
    # communications_tool.register(mcp, client)
    # available_time_tool.register(mcp, client)

    return mcp, client


def main() -> None:
    """CLI entry point — registered as `au-cliniko-mcp` in pyproject.toml."""
    mcp, _client = build_server()
    mcp.run()


if __name__ == "__main__":
    main()
