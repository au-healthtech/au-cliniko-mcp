"""au-cliniko-mcp — FastMCP server entry point.

This is the boot path. It:
1. Reads credentials from environment.
2. Builds the shared ClinikoClient.
3. Registers tools, resources, and prompts.
4. Starts the stdio MCP server.

Phase B (this state) wires the full Tier-1 read-side + the safety-gated
`draft_treatment_note` write tool. Phase C will add @phi_flagged and
@consent_gated decorators that wrap every tool registered here, plus
PostgreSQL audit logging.
"""

from __future__ import annotations

import os
import sys

from mcp.server.fastmcp import FastMCP

from au_cliniko_mcp.audit import get_audit_log
from au_cliniko_mcp.auth import ClinikoCredential, InvalidClinikoApiKey
from au_cliniko_mcp.client import ClinikoClient
from au_cliniko_mcp.tools import (
    aggregators as aggregators_tool,
    appointments as appointments_tool,
    available_time as available_time_tool,
    bookings as bookings_tool,
    businesses as businesses_tool,
    communications as communications_tool,
    invoices as invoices_tool,
    patients as patients_tool,
    practitioners as practitioners_tool,
    recalls as recalls_tool,
    revenue as revenue_tool,
    treatment_notes as treatment_notes_tool,
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

    # Touch the audit-log singleton early — creates ~/.au-cliniko-mcp/audit.db
    # if it doesn't exist yet, so the first tool call is fast.
    get_audit_log()

    mcp = FastMCP(
        name="au-cliniko-mcp",
        instructions=(
            "Open-source Cliniko MCP for Australian allied-health practices. "
            "Connects Claude directly to a Cliniko account via the Cliniko REST API. "
            f"Current Cliniko shard: {credential.shard}. "
            "\n\n"
            "SAFETY: All write operations default to draft mode and require an "
            "explicit human review step in the Cliniko UI before they take effect. "
            "PHI-touching reads are audit-logged when the audit-log database is configured."
            "\n\n"
            "COST CONTROL — IMPORTANT: This MCP can issue requests that scale "
            "with the practice's patient count. Before any operation that would "
            "visit MORE THAN 100 patients (e.g. duplicate detection, missing-"
            "contact audit, per-patient fan-out for percentage questions), "
            "PAUSE and ask the user how they want to proceed. Offer concrete "
            "options:\n"
            "    - Sample 100 patients (fast, low cost, ±10% statistical accuracy)\n"
            "    - Sample 500 patients (moderate cost, ±4% accuracy)\n"
            "    - Sample 1000 patients (higher cost, ±3% accuracy)\n"
            "    - Enumerate ALL patients (full clinic, highest cost)\n"
            "Only run the enumeration the user explicitly authorises. For "
            "compliance-critical workflows (recall lists, AHPRA-mandated "
            "communications, billing chase) the user will typically choose ALL."
            "\n\n"
            "If a tool returns `needs_confirmation: true`, the operation has "
            "been BLOCKED at the tool layer for cost reasons. Ask the user "
            "and retry with the appropriate scope parameter."
        ),
    )

    # Phase B — Tier-1 read-side + safety-gated treatment-note drafting:
    patients_tool.register(mcp, client)              # 3 tools
    practitioners_tool.register(mcp, client)         # 1 tool
    businesses_tool.register(mcp, client)            # 1 tool
    appointments_tool.register(mcp, client)          # 3 tools (incl. per-patient lookup)
    bookings_tool.register(mcp, client)              # 1 tool
    invoices_tool.register(mcp, client)              # 3 tools
    recalls_tool.register(mcp, client)               # 2 tools
    communications_tool.register(mcp, client)        # 1 tool
    available_time_tool.register(mcp, client)        # 1 tool
    treatment_notes_tool.register(mcp, client)       # 3 tools (1 safety-gated write)
    aggregators_tool.register(mcp, client)           # 3 tools (multi-resource composites)
    revenue_tool.register(mcp, client)               # 4 tools (Phase D-Revenue)
    # Total: 26 tools

    return mcp, client


def main() -> None:
    """CLI entry point — registered as `au-cliniko-mcp` in pyproject.toml."""
    mcp, _client = build_server()
    mcp.run()


if __name__ == "__main__":
    main()
