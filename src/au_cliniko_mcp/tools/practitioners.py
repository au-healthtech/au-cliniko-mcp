"""Practitioner tools.

Read-only for v1. Phase E will add write tools for practitioner profile updates
and Practitioner Reference Number (AHPRA / Medicare provider #) helpers.
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from au_cliniko_mcp.client import ClinikoClient
from au_cliniko_mcp.shaping import list_wrapper, summarise_practitioner


def register(mcp: FastMCP, client: ClinikoClient) -> None:
    @mcp.tool()
    async def list_practitioners(include_inactive: bool = False) -> dict[str, Any]:
        """List practitioners on the active Cliniko account.

        When to use:
            - "Who are the practitioners at this clinic?"
            - As context before booking or assigning anything to a specific clinician
            - When generating reports that need practitioner IDs

        WORKING_EXAMPLE:
            ```
            list_practitioners()                       # active only
            list_practitioners(include_inactive=True)  # archived too
            ```

        Notes:
            - Inactive practitioners are filtered out by default. Set
              `include_inactive=True` to see archived staff.
            - Returns no PHI — practitioner records are administrative.

        Args:
            include_inactive: If True, also include practitioners with `active=False`.
        """
        params: dict[str, Any] = {"per_page": 100}
        result = await client.get("/practitioners", params=params)

        if "error" in result:
            return result

        practs = result.get("practitioners", [])
        if not include_inactive:
            practs = [p for p in practs if p.get("active")]

        return list_wrapper(
            items_full=practs,
            summary_lines=[summarise_practitioner(p) for p in practs],
            total_entries=len(practs),
        )
