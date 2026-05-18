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
from au_cliniko_mcp.shaping import list_wrapper, summarise_recall


def register(mcp: FastMCP, client: ClinikoClient) -> None:
    @mcp.tool()
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
            - "Due" means `recall_date <= today + within_days`.
            - Already-completed recalls are excluded.
            - PHI: low-grade (patient ID + the recall note). Audit-logged.

        Args:
            within_days: how many days ahead to look. Default 30.
            per_page: results per page (Cliniko max 100).
        """
        cutoff = (date.today() + timedelta(days=within_days)).isoformat()
        q_params: list[tuple[str, str]] = [
            ("per_page", str(per_page)),
            ("q[]", f"recall_date:<={cutoff}"),
            ("q[]", "completed:=false"),
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

    @mcp.tool()
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
