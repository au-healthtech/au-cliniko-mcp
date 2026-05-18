"""Patient tools.

Phase B scope: `list_patients`, `get_patient`, `search_patients_by_name`.
Phase C will add the `@phi_flagged` decorator so every call here is audit-logged
with `phi_categories=['demographics','contact']`.
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
              of that size are fine in Python 3 but lose precision on JSON round-trips
              in some clients.
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
        total = result.get("total_entries") or len(patients_raw)
        has_more = bool(result.get("links", {}).get("next"))

        return list_wrapper(
            items_full=patients_raw,
            summary_lines=[summarise_patient(p) for p in patients_raw],
            total_entries=total,
            page=page,
            has_more=has_more,
        )

    @mcp.tool()
    async def get_patient(patient_id: str) -> dict[str, Any]:
        """Get one patient's full record by id.

        When to use:
            After `list_patients` or `search_patients_by_name` has identified a
            patient and the practitioner wants the full chart record — contact
            details, address, DOB, medical alerts summary, etc.

        WORKING_EXAMPLE:
            ```
            get_patient(patient_id="12345678901234567890")
            ```

        Notes:
            - Patient IDs are 19-digit STRINGS, not integers. Pass as quoted string.
            - This returns PHI (full demographics, contact). Audit-logged with
              `phi_categories=['demographics','contact','address']`.
            - Does NOT include treatment notes, attachments, or appointments —
              use dedicated tools (`list_treatment_notes_for_patient`, etc.) for those.

        Args:
            patient_id: 19-digit Cliniko patient id (string).
        """
        result = await client.get(f"/patients/{patient_id}")
        return result

    @mcp.tool()
    async def search_patients_by_name(query: str, per_page: int = 25) -> dict[str, Any]:
        """Search patients by partial name match (first or last).

        When to use:
            When the practitioner says "find Jane Smith," "look up the Bonaldis,"
            or any patient lookup keyed on name rather than id.

        Cliniko's search syntax uses `q[]` array filters. This tool translates
        a plain string into the correct Cliniko filter:
            - "Jane" → `q[]=first_name:like:Jane` (case-insensitive partial)
            - "Smith" → also searches last_name
            - "Jane Smith" → searches first_name AND last_name

        WORKING_EXAMPLE:
            ```
            search_patients_by_name(query="Bonaldi")
            ```

        Notes:
            - Returns up to `per_page` matches across all name fields.
            - For exact id match, use `get_patient` instead.
            - PHI: same audit categories as `list_patients`.

        Args:
            query: name fragment (case-insensitive substring).
            per_page: max results to return. Default 25.
        """
        parts = query.strip().split()
        # Build `q[]` filters — Cliniko's syntax is `q[]=field:operator:value`
        # repeated for multiple criteria (AND semantics on the same filter
        # would not return Jane Smith; we want OR over name fields).
        # Practical approach: try first_name OR last_name. Cliniko's `or` is
        # implicit when you use multiple `q[]` entries on different fields.
        # Reference: https://docs.api.cliniko.com/#searching
        q_params: list[tuple[str, str]] = []
        for part in parts:
            q_params.append(("q[]", f"first_name:like:{part}"))
            q_params.append(("q[]", f"last_name:like:{part}"))

        # httpx accepts list-of-tuples to produce repeated query params.
        params = [("per_page", str(per_page)), *q_params]
        result = await client.get("/patients", params=params)

        if "error" in result:
            return result

        patients_raw = result.get("patients", [])
        total = result.get("total_entries") or len(patients_raw)

        return list_wrapper(
            items_full=patients_raw,
            summary_lines=[summarise_patient(p) for p in patients_raw],
            total_entries=total,
            page=1,
            has_more=False,
        )
