"""au-cliniko-mcp — FastMCP server entry point.

This is the boot path. It:
1. Reads credentials from environment.
2. Builds the shared ClinikoClient.
3. Registers tools, resources, and prompts.
4. Starts the stdio MCP server.

Phase-A scaffolding registers ONE tool (`list_patients`) as a smoke test. Phase B
adds the full Tier-1 set; Phase C wraps every tool in audit-log + PHI-guard decorators.
"""

from __future__ import annotations

import os
import sys

from mcp.server.fastmcp import FastMCP

from au_cliniko_mcp.auth import ClinikoCredential, InvalidClinikoApiKey
from au_cliniko_mcp.client import ClinikoClient
from au_cliniko_mcp.tools import patients as patients_tool


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
            "All write operations default to draft mode and require an explicit commit step."
        ),
    )

    # Phase A: one tool, smoke test the whole stack.
    patients_tool.register(mcp, client)

    # Phase B will add: appointments, bookings, treatment_notes, invoices,
    # practitioners, businesses, recalls, communications, available_time.

    return mcp, client


def main() -> None:
    """CLI entry point — registered as `au-cliniko-mcp` in pyproject.toml."""
    mcp, _client = build_server()
    mcp.run()  # stdio transport by default


if __name__ == "__main__":
    main()
