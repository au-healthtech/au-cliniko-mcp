"""Business tools — Cliniko's term for a practice location.

Most Cliniko accounts have a single business; multi-site practices have one
per location. Business IDs are needed when booking appointments or filtering
invoices by clinic.
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from au_cliniko_mcp.client import ClinikoClient
from au_cliniko_mcp.shaping import list_wrapper, summarise_business


def register(mcp: FastMCP, client: ClinikoClient) -> None:
    @mcp.tool()
    async def list_businesses() -> dict[str, Any]:
        """List all businesses (practice locations) on the active Cliniko account.

        When to use:
            - Multi-site practices: identify which business ID corresponds to which location
            - As a one-off discovery call to grab the business_id needed for other tools
              (e.g. when creating an appointment, you must specify which location)
            - Generating per-location reports

        WORKING_EXAMPLE:
            ```
            list_businesses()
            ```

        Notes:
            - Most single-site practices return exactly one business.
            - Returns no PHI.
        """
        result = await client.get("/businesses", params={"per_page": 100})

        if "error" in result:
            return result

        businesses = result.get("businesses", [])
        return list_wrapper(
            items_full=businesses,
            summary_lines=[summarise_business(b) for b in businesses],
            total_entries=len(businesses),
        )
