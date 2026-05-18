"""Appointment tools.

Cliniko has two appointment resource types:
    - `individual_appointments` — single-patient bookings (most common)
    - `group_appointments` — multi-patient bookings (yoga / pilates / group classes)

Phase B wires both as read-only listing + get. Phase C adds write tools wrapped
with the draft → commit consent gate. Phase E adds the group-appointment write
paths.

NOTE on appointment field names (empirically verified on au5, 2026-05-18):
    Cliniko's `individual_appointments` POST + PATCH + LIST all use `starts_at`
    and `ends_at`. Earlier hobby implementations (BoabAI, andymillar84) claim
    POST requires `appointment_start`/`appointment_end` — this is incorrect.
    See `docs/API-LIMITATIONS.md`.
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from au_cliniko_mcp.client import ClinikoClient
from au_cliniko_mcp.shaping import list_wrapper, summarise_appointment


def register(mcp: FastMCP, client: ClinikoClient) -> None:
    @mcp.tool()
    async def list_appointments(
        from_date: str | None = None,
        to_date: str | None = None,
        practitioner_id: str | None = None,
        business_id: str | None = None,
        page: int = 1,
        per_page: int = 50,
    ) -> dict[str, Any]:
        """List individual appointments, optionally filtered by date / practitioner / location.

        When to use:
            - "Show me tomorrow's schedule"
            - "What appointments did Sarah have last week?"
            - "List today's appointments at the Brighton clinic"
            - As input to recall-list or no-show-followup workflows

        WORKING_EXAMPLE:
            ```
            list_appointments(from_date="2026-05-18", to_date="2026-05-18")
            list_appointments(from_date="2026-05-18", practitioner_id="12345...")
            ```

        Notes:
            - Dates are ISO-8601 (`YYYY-MM-DD`). The filter is inclusive on both ends.
            - Practitioner and business IDs are 19-digit strings. Get them from
              `list_practitioners` / `list_businesses`.
            - Cancelled appointments are included; check the `cancelled_at` field
              (or look for ❌ in `summary_markdown`).
            - PHI: appointments link to patients. Audit-logged with
              `phi_categories=['appointment_metadata','patient_link']`.

        Args:
            from_date: ISO date for the earliest appointment to include.
            to_date: ISO date for the latest appointment to include.
            practitioner_id: optional filter by practitioner.
            business_id: optional filter by clinic location.
            page: 1-indexed page number.
            per_page: results per page (Cliniko max 100).
        """
        q_params: list[tuple[str, str]] = [
            ("page", str(page)),
            ("per_page", str(per_page)),
        ]
        if from_date:
            q_params.append(("q[]", f"starts_at:>={from_date}T00:00:00Z"))
        if to_date:
            q_params.append(("q[]", f"starts_at:<={to_date}T23:59:59Z"))
        if practitioner_id:
            q_params.append(("q[]", f"practitioner_id:={practitioner_id}"))
        if business_id:
            q_params.append(("q[]", f"business_id:={business_id}"))

        result = await client.get("/individual_appointments", params=q_params)

        if "error" in result:
            return result

        appts = result.get("individual_appointments", [])
        total = result.get("total_entries") or len(appts)
        has_more = bool(result.get("links", {}).get("next"))

        return list_wrapper(
            items_full=appts,
            summary_lines=[summarise_appointment(a) for a in appts],
            total_entries=total,
            page=page,
            has_more=has_more,
        )

    @mcp.tool()
    async def get_appointment(appointment_id: str) -> dict[str, Any]:
        """Get one individual appointment by id.

        When to use:
            After `list_appointments` has identified a specific booking and you
            need the full record — notes attached, cancellation reason, billing
            link, etc.

        WORKING_EXAMPLE:
            ```
            get_appointment(appointment_id="12345678901234567890")
            ```

        Notes:
            - Appointment IDs are 19-digit strings.
            - PHI: same as `list_appointments`.
        """
        return await client.get(f"/individual_appointments/{appointment_id}")

    @mcp.tool()
    async def list_appointments_for_patient(
        patient_id: str,
        from_date: str | None = None,
        to_date: str | None = None,
        per_page: int = 100,
    ) -> dict[str, Any]:
        """List all appointments for a specific patient, optionally within a date range.

        When to use:
            - "Has Jane been in recently?"
            - "Show me Eric Shin's appointment history"
            - "When was this patient's last visit?"
            - Reasoning about "who hasn't been in for 6 months" — drill into
              individual patients rather than fetching ALL appointments
              across the whole practice

        WORKING_EXAMPLE:
            ```
            list_appointments_for_patient(patient_id="12345678901234567890")
            list_appointments_for_patient(patient_id="12345...", from_date="2025-11-18")
            ```

        Notes:
            - Faster + smaller payload than filtering the whole practice list.
              Use THIS for per-patient questions; use `list_appointments` for
              practice-wide schedule views.
            - Patient IDs are 19-digit strings.
            - PHI: same as `list_appointments`.

        Args:
            patient_id: 19-digit Cliniko patient id.
            from_date: optional ISO date — earliest appointment to include.
            to_date: optional ISO date — latest appointment to include.
            per_page: results per page (Cliniko max 100). Default 100.
        """
        q_params: list[tuple[str, str]] = [
            ("per_page", str(per_page)),
            ("q[]", f"patient_id:={patient_id}"),
        ]
        if from_date:
            q_params.append(("q[]", f"starts_at:>={from_date}T00:00:00Z"))
        if to_date:
            q_params.append(("q[]", f"starts_at:<={to_date}T23:59:59Z"))

        result = await client.get("/individual_appointments", params=q_params)

        if "error" in result:
            return result

        appts = result.get("individual_appointments", [])
        total = result.get("total_entries") or len(appts)

        return list_wrapper(
            items_full=appts,
            summary_lines=[summarise_appointment(a) for a in appts],
            total_entries=total,
        )
