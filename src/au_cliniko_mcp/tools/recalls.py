"""Recall tools — patient follow-up scheduling.

Recalls in Cliniko are scheduled reminders to contact a patient (e.g. for a
6-month review, a vaccination due, an annual checkup). This module reads them
for v1. Phase C will add a draft-only `schedule_recall` write tool with the
consent-gate decorator.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from mcp.server.fastmcp import FastMCP

from au_cliniko_mcp.client import ClinikoClient
from au_cliniko_mcp.phi import PHI_PATIENT_LINK, phi_flagged
from au_cliniko_mcp.shaping import list_wrapper, summarise_recall


def register(mcp: FastMCP, client: ClinikoClient) -> None:
    @mcp.tool()
    @phi_flagged(PHI_PATIENT_LINK)
    async def list_recalls_due(within_days: int = 30, per_page: int = 100) -> dict[str, Any]:
        """List recalls due within the next N days.

        When to use:
            - "Who's due for a recall this month?"
            - Weekly recall-list workflow ("Monday morning recall review")
            - As input to an AHPRA-compliant patient-contact campaign

        WORKING_EXAMPLE:
            ```
            list_recalls_due(within_days=7)   # this week
            list_recalls_due(within_days=30)  # this month
            ```

        Notes:
            - **Cliniko quirk (empirically verified au5, 2026-05-18)**:
              `recall_at` is NOT filterable via q[]. Cliniko returns
              `400: "recall_at is not filterable"`. We fetch all recalls
              and filter client-side. For large clinics with thousands of
              recalls this becomes slow — Phase E should add a local index.
            - "Due" means `recall_at <= today + within_days`.
            - PHI: low-grade (patient ID + the recall note). Audit-logged.

        Args:
            within_days: how many days ahead to look. Default 30.
            per_page: results per page (Cliniko max 100).
        """
        cutoff = (date.today() + timedelta(days=within_days)).isoformat()
        # Fetch all recalls (paginate if needed) and filter client-side.
        all_recalls: list[dict[str, Any]] = []
        page = 1
        while True:
            result = await client.get(
                "/recalls", params={"per_page": per_page, "page": page}
            )
            if "error" in result:
                return result
            all_recalls.extend(result.get("recalls", []))
            if not result.get("links", {}).get("next") or page >= 10:
                break
            page += 1

        # Client-side filter on recall_at (date string YYYY-MM-DD).
        due = []
        for r in all_recalls:
            rdate = r.get("recall_at")
            if rdate and rdate <= cutoff:
                due.append(r)

        return list_wrapper(
            items_full=due,
            summary_lines=[summarise_recall(r) for r in due],
            total_entries=len(due),
        )

    @mcp.tool()
    @phi_flagged(PHI_PATIENT_LINK)
    async def list_recalls_for_patient(patient_id: str, per_page: int = 25) -> dict[str, Any]:
        """List all recalls (past and future) for a specific patient.

        When to use:
            - "What recalls does Jane have on file?"
            - Before scheduling a new recall, to check for duplicates

        WORKING_EXAMPLE:
            ```
            list_recalls_for_patient(patient_id="12345678901234567890")
            ```

        Notes:
            - Includes completed recalls.
            - PHI: same as `list_recalls_due`.

        Args:
            patient_id: 19-digit Cliniko patient id.
            per_page: results per page.
        """
        q_params: list[tuple[str, str]] = [
            ("per_page", str(per_page)),
            ("q[]", f"patient_id:={patient_id}"),
        ]
        result = await client.get("/recalls", params=q_params)

        if "error" in result:
            return result

        recalls = result.get("recalls", [])
        return list_wrapper(
            items_full=recalls,
            summary_lines=[summarise_recall(r) for r in recalls],
            total_entries=result.get("total_entries") or len(recalls),
        )
