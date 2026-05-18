"""Available-time tools — read open slots for booking.

This is read-only. Booking creation goes through the patient-facing widget OR
the appointment-creation tool (Phase C write tools).
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from au_cliniko_mcp.client import ClinikoClient
from au_cliniko_mcp.shaping import list_wrapper


def register(mcp: FastMCP, client: ClinikoClient) -> None:
    @mcp.tool()
    async def list_available_times(
        practitioner_id: str,
        business_id: str,
        appointment_type_id: str,
        from_date: str,
        to_date: str,
    ) -> dict[str, Any]:
        """List open appointment slots for a practitioner at a business on a given date range.

        When to use:
            - "When can Jane book in with Dr Smith next week?"
            - Building a "next-available" search across practitioners
            - Patient-facing scheduling assistants

        WORKING_EXAMPLE:
            ```
            list_available_times(
                practitioner_id="12345...",
                business_id="98765...",
                appointment_type_id="55555...",
                from_date="2026-05-19",
                to_date="2026-05-23",
            )
            ```

        Notes:
            - All four IDs are required AND are 19-digit strings.
            - Get the IDs from `list_practitioners`, `list_businesses`, and
              (Phase B+) `list_appointment_types`.
            - Times are in the business's local timezone.
            - Returns no PHI — slots are administrative.

        Args:
            practitioner_id: 19-digit Cliniko practitioner id.
            business_id: 19-digit Cliniko business id.
            appointment_type_id: 19-digit Cliniko appointment type id.
            from_date: earliest date to check (ISO-8601).
            to_date: latest date to check (ISO-8601).
        """
        params = {
            "practitioner_id": practitioner_id,
            "business_id": business_id,
            "appointment_type_id": appointment_type_id,
            "from": from_date,
            "to": to_date,
        }
        result = await client.get("/available_times", params=params)

        if "error" in result:
            return result

        slots = result.get("available_times", [])
        return list_wrapper(
            items_full=slots,
            summary_lines=[f"- {s.get('appointment_start','?')} → {s.get('appointment_end','?')}" for s in slots],
            total_entries=len(slots),
        )
