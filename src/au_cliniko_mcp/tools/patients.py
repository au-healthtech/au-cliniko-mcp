"""Patient tools.

Phase A scope: `list_patients` only — smoke-tests the full request/response stack
end-to-end. Phase B will add `get_patient`, `create_patient`, `update_patient`,
`search_patients_by_name`, plus a PHI-guard decorator that flags every patient
response with its PHI categories (demographics, contact, clinical).
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from au_cliniko_mcp.client import ClinikoClient
from au_cliniko_mcp.shaping import list_wrapper, summarise_patient


def register(mcp: FastMCP, client: ClinikoClient) -> None:
    """Wire patient tools onto the MCP server."""

    @mcp.tool()
    async def list_patients(page: int = 1, per_page: int = 25) -> dict[str, Any]:
        """List patients on the active Cliniko account.

        When to use:
            Call when the practitioner asks something like "who are my patients,"
            "show me everyone in the system," "give me a patient list," or as a
            first step before drilling into a specific patient by id.

        Returns the standard list envelope:
            - `summary_markdown`: one-line summary per patient — read this first
              for casual queries. Each line carries the patient id you can pass
              to subsequent tools.
            - `items`: full Cliniko patient objects — read only when you need
              fields not in the summary.
            - `page`, `total_entries`, `has_more`: pagination.

        WORKING_EXAMPLE:
            ```
            list_patients(page=1, per_page=25)
            ```

        Notes:
            - Patient IDs are 19-digit strings. Never coerce to int — Python ints
              of that size lose precision on JSON round-trips.
            - This tool reads PHI (demographics, contact details). In Phase C,
              calls will be audit-logged with `phi_categories=['demographics','contact']`.

        Args:
            page: 1-indexed page number. Default 1.
            per_page: Results per page (Cliniko max 100). Default 25.
        """
        result = await client.get("/patients", params={"page": page, "per_page": per_page})

        if "error" in result:
            return result

        patients_raw = result.get("patients", [])
        total = (result.get("total_entries") or len(patients_raw))
        has_more = bool(result.get("links", {}).get("next"))

        return list_wrapper(
            items_full=patients_raw,
            summary_lines=[summarise_patient(p) for p in patients_raw],
            total_entries=total,
            page=page,
            has_more=has_more,
        )
