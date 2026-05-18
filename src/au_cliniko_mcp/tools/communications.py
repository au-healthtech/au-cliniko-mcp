"""Communication tools — read SMS / email history.

Cliniko's `/communications` endpoint exposes the HISTORY of communications sent
to/from patients. The API does NOT support sending new communications — that
must be done via Cliniko UI's scheduled-communication feature. Documented in
`docs/API-LIMITATIONS.md`.
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from au_cliniko_mcp.client import ClinikoClient
from au_cliniko_mcp.shaping import list_wrapper


def register(mcp: FastMCP, client: ClinikoClient) -> None:
    @mcp.tool()
    async def list_communications_for_patient(
        patient_id: str,
        per_page: int = 25,
    ) -> dict[str, Any]:
        """List communication history (SMS/email) for a specific patient.

        When to use:
            - "Has Jane received our appointment reminder?"
            - Audit / disputes ("did we contact this patient?")
            - Before sending a manual follow-up to avoid duplicate contact

        WORKING_EXAMPLE:
            ```
            list_communications_for_patient(patient_id="12345678901234567890")
            ```

        Notes:
            - This reads HISTORY only. Cliniko's API does NOT support sending
              a new communication via API — set up scheduled communications in
              Cliniko UI instead. See `docs/API-LIMITATIONS.md`.
            - PHI: communications carry message content. Audit-logged with
              `phi_categories=['communications']`.

        Args:
            patient_id: 19-digit Cliniko patient id.
            per_page: results per page.
        """
        q_params: list[tuple[str, str]] = [
            ("per_page", str(per_page)),
            ("q[]", f"patient_id:={patient_id}"),
        ]
        result = await client.get("/communications", params=q_params)

        if "error" in result:
            return result

        comms = result.get("communications", [])
        return list_wrapper(
            items_full=comms,
            summary_lines=[
                f"- {c.get('sent_at','?')} | {c.get('channel','?')} | {c.get('direction','?')} | subject: {(c.get('subject') or '')[:60]}"
                for c in comms
            ],
            total_entries=result.get("total_entries") or len(comms),
        )
