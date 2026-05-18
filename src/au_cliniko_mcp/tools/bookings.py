"""Booking tools.

A "booking" in Cliniko is the patient-facing record (created via the public
online booking widget) that gets converted into an `individual_appointment`
once accepted. They're read-only via this MCP — patients book via Cliniko's
own widget, not via our tool.
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from au_cliniko_mcp.client import ClinikoClient
from au_cliniko_mcp.phi import PHI_APPOINTMENT_METADATA, PHI_CONTACT, phi_flagged
from au_cliniko_mcp.shaping import list_wrapper


def register(mcp: FastMCP, client: ClinikoClient) -> None:
    @mcp.tool()
    @phi_flagged(PHI_APPOINTMENT_METADATA, PHI_CONTACT)
    async def list_bookings(
        from_date: str | None = None,
        to_date: str | None = None,
        page: int = 1,
        per_page: int = 50,
    ) -> dict[str, Any]:
        """List patient-facing bookings (from Cliniko's online booking widget).

        When to use:
            - "How many people booked online this week?"
            - Auditing the public booking pipeline
            - As input to a "convert pending booking → appointment" workflow

        WORKING_EXAMPLE:
            ```
            list_bookings(from_date="2026-05-12", to_date="2026-05-18")
            ```

        Notes:
            - Bookings differ from appointments — they are the PATIENT'S record of
              having clicked through the public widget. Once accepted internally,
              they create a matching `individual_appointment`.
            - PHI: bookings carry patient contact info. Audit-logged with
              `phi_categories=['booking','contact']`.

        Args:
            from_date: earliest booking creation date (ISO-8601).
            to_date: latest booking creation date (ISO-8601).
            page: 1-indexed page number.
            per_page: results per page.
        """
        q_params: list[tuple[str, str]] = [
            ("page", str(page)),
            ("per_page", str(per_page)),
        ]
        if from_date:
            q_params.append(("q[]", f"created_at:>={from_date}T00:00:00Z"))
        if to_date:
            q_params.append(("q[]", f"created_at:<={to_date}T23:59:59Z"))

        result = await client.get("/bookings", params=q_params)

        if "error" in result:
            return result

        bookings = result.get("bookings", [])
        total = result.get("total_entries") or len(bookings)
        has_more = bool(result.get("links", {}).get("next"))

        return list_wrapper(
            items_full=bookings,
            summary_lines=[
                f"- Booking `{b.get('id','?')}` created {b.get('created_at','?')} (patient `{b.get('patient',{}).get('links',{}).get('self','?').rsplit('/',1)[-1]}`)"
                for b in bookings
            ],
            total_entries=total,
            page=page,
            has_more=has_more,
        )
