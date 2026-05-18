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
    async def list_patients(page: int = 1, per_page: int = 100) -> dict[str, Any]:
        """List ONE PAGE of patients on the active Cliniko account.

        ⚠️ IMPORTANT — pagination semantics:
            This returns ONE PAGE only (up to `per_page`). The `total_entries`
            field is the AUTHORITATIVE patient count for the whole account.
            For "how many patients" questions, ALWAYS read `total_entries` —
            never count the items you got back, because they are page 1 of N.

            If `has_more: true`, there are MORE pages. To enumerate ALL patients
            (e.g. for duplicate detection, missing-contact audit, count-by-criteria),
            use `list_all_patients()` instead — it handles pagination for you
            and is much cheaper than calling list_patients(page=1, 2, 3 ...).

        When to use:
            - Quick browse of patients ("show me the first 25 patients")
            - As a sanity check or first peek before drilling in

        DO NOT use when:
            - The question requires looking at ALL patients
              → use `list_all_patients` instead
            - The question is "how many patients do we have?"
              → call this with per_page=1 and read total_entries

        WORKING_EXAMPLE:
            ```
            list_patients()                              # first 100 patients
            list_patients(per_page=1)                    # just for the count
            list_patients(page=2, per_page=100)          # second page
            ```

        Notes:
            - Patient IDs are 19-digit strings. Never coerce to int.
            - PHI: demographics + contact. Audit-logged in Phase C.

        Args:
            page: 1-indexed page number. Default 1.
            per_page: Results per page (Cliniko max 100). Default 100.
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
    async def list_all_patients(max_pages: int = 30) -> dict[str, Any]:
        """Fetch ALL patients on the account by auto-paginating.

        When to use (preferred for any "across all patients" question):
            - "Find duplicate patient records"
            - "Patients with no email or phone"
            - "How many patients do we have? Show me all of them"
            - "Find patients matching <criterion>"
            - Anything that needs to look at the full patient list

        WORKING_EXAMPLE:
            ```
            list_all_patients()                # default — up to 30 pages × 100 = 3000 patients
            list_all_patients(max_pages=10)    # caps at 1000 patients
            ```

        Notes:
            - Walks pages 1..N at per_page=100 until `has_more=false` or max_pages hit.
            - For practices with >3000 patients, raise max_pages or do criterion-filtered
              fan-outs instead.
            - PHI: demographics + contact for every patient. Audit-logged in Phase C.
            - Cost-aware: this can be expensive on a 5000+ patient practice.
              Prefer `search_patients_by_name` or filtered list_patients when
              the answer doesn't actually require every patient.

        Args:
            max_pages: cap on pagination depth (default 30 = up to 3000 patients).

        Returns the standard list envelope with all patients combined.
        """
        all_patients: list[dict[str, Any]] = []
        page = 1
        total_entries = 0
        while page <= max_pages:
            r = await client.get("/patients", params={"page": page, "per_page": 100})
            if "error" in r:
                return r
            all_patients.extend(r.get("patients", []))
            total_entries = r.get("total_entries") or len(all_patients)
            if not r.get("links", {}).get("next"):
                break
            page += 1

        return list_wrapper(
            items_full=all_patients,
            summary_lines=[summarise_patient(p) for p in all_patients],
            total_entries=total_entries,
            page=1,
            has_more=False,
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
