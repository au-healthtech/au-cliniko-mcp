"""Aggregator tools — multi-resource composites that fan out internally.

These are the "consulting-grade" tools: a single LLM-callable function that
handles the N+1 / multi-resource join the LLM would otherwise have to
orchestrate by hand. Each one unblocks 1-3 of the 26 eval questions and is
faster + cheaper (fewer Claude round-trips) than chained per-resource calls.

Built 2026-05-18 after the v0.1 eval showed the gap.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any

from mcp.server.fastmcp import FastMCP

from au_cliniko_mcp.client import ClinikoClient


def register(mcp: FastMCP, client: ClinikoClient) -> None:
    """Wire the aggregator tools onto the MCP server."""

    @mcp.tool()
    async def get_patient_appointment_stats(
        patient_id: str,
        since_date: str | None = None,
    ) -> dict[str, Any]:
        """Aggregate appointment counts + status + dates for ONE patient.

        When to use:
            - "How many appointments has Jane had?"
            - "When was this patient's first / last visit?"
            - "What's this patient's no-show rate?"
            - As an input to: "% new vs returning", "highest no-show patients",
              "course-of-care completion" reasoning
            - Cheaper than calling list_appointments_for_patient + counting in chat

        WORKING_EXAMPLE:
            ```
            get_patient_appointment_stats(patient_id="12345...")
            get_patient_appointment_stats(patient_id="12345...", since_date="2025-11-18")
            ```

        Returns:
            {
              "patient_id": str,
              "count_total": int,
              "count_completed": int,        # not cancelled, not no-show
              "count_no_show": int,          # did_not_arrive=True
              "count_cancelled": int,        # cancelled_at is set
              "first_appointment_at": str | None,
              "last_appointment_at": str | None,
              "no_show_rate": float          # 0.0-1.0
            }

        Notes:
            - PHI: appointment metadata. Audit-logged.
            - Patient IDs are 19-digit strings.
            - `since_date` filters appointments AFTER that date (ISO YYYY-MM-DD).

        Args:
            patient_id: 19-digit Cliniko patient id.
            since_date: optional ISO date — only include appointments at-or-after this date.
        """
        params: list[tuple[str, str]] = [
            ("per_page", "100"),
            ("q[]", f"patient_id:={patient_id}"),
        ]
        if since_date:
            params.append(("q[]", f"starts_at:>={since_date}T00:00:00Z"))

        all_appts: list[dict[str, Any]] = []
        page = 1
        while True:
            params_paged = params + [("page", str(page))]
            r = await client.get("/individual_appointments", params=params_paged)
            if "error" in r:
                return r
            all_appts.extend(r.get("individual_appointments", []))
            if not r.get("links", {}).get("next") or page >= 5:
                break
            page += 1

        total = len(all_appts)
        no_show = sum(1 for a in all_appts if a.get("did_not_arrive"))
        cancelled = sum(1 for a in all_appts if a.get("cancelled_at"))
        completed = total - no_show - cancelled

        starts = sorted([a.get("starts_at") for a in all_appts if a.get("starts_at")])
        first = starts[0] if starts else None
        last = starts[-1] if starts else None

        return {
            "patient_id": patient_id,
            "count_total": total,
            "count_completed": completed,
            "count_no_show": no_show,
            "count_cancelled": cancelled,
            "first_appointment_at": first,
            "last_appointment_at": last,
            "no_show_rate": round(no_show / total, 3) if total else 0.0,
        }

    @mcp.tool()
    async def get_practitioner_schedule_overview(
        from_date: str,
        to_date: str,
        include_inactive: bool = False,
    ) -> dict[str, Any]:
        """Aggregate booked-slot counts per practitioner over a date range.

        When to use:
            - "Which practitioners have the most gaps next week?"
            - "Who's the busiest?"
            - "Schedule utilisation by practitioner this month"

        WORKING_EXAMPLE:
            ```
            get_practitioner_schedule_overview(from_date="2026-05-19", to_date="2026-06-01")
            ```

        Returns:
            {
              "from": str,
              "to": str,
              "practitioners": [
                {
                  "id": str,
                  "name": str,
                  "active": bool,
                  "booked_appointments": int,
                  "no_shows": int,
                  "cancelled": int,
                  "ranking_busiest_first": int  // 1 = busiest
                },
                ...
              ]
            }

        Notes:
            - "Gaps" here means low `booked_appointments` count, NOT true available-time
              calculation. Cliniko's `available_times` endpoint requires per-appt-type
              filtering which makes whole-clinic utilisation expensive. This tool gives
              the practical answer Claude needs for the "who has gaps" question.
            - For exact open-slot lookups use `list_available_times` (per practitioner).
            - PHI: no patient data — administrative only.

        Args:
            from_date: ISO date YYYY-MM-DD (inclusive).
            to_date: ISO date YYYY-MM-DD (inclusive).
            include_inactive: include archived practitioners (default False).
        """
        pr = await client.get("/practitioners", params={"per_page": 100})
        if "error" in pr:
            return pr
        practs = pr.get("practitioners", [])
        if not include_inactive:
            practs = [p for p in practs if p.get("active")]

        results: list[dict[str, Any]] = []
        for p in practs:
            q_params = [
                ("per_page", "100"),
                ("q[]", f"practitioner_id:={p['id']}"),
                ("q[]", f"starts_at:>={from_date}T00:00:00Z"),
                ("q[]", f"starts_at:<={to_date}T23:59:59Z"),
            ]
            r = await client.get("/individual_appointments", params=q_params)
            if "error" in r:
                continue
            appts = r.get("individual_appointments", [])
            total = r.get("total_entries") or len(appts)
            no_shows = sum(1 for a in appts if a.get("did_not_arrive"))
            cancelled = sum(1 for a in appts if a.get("cancelled_at"))
            results.append({
                "id": p["id"],
                "name": f"{p.get('first_name','')} {p.get('last_name','')}".strip() or "(no name)",
                "active": bool(p.get("active")),
                "booked_appointments": total,
                "no_shows": no_shows,
                "cancelled": cancelled,
            })

        results.sort(key=lambda x: x["booked_appointments"], reverse=True)
        for rank, r in enumerate(results, 1):
            r["ranking_busiest_first"] = rank

        return {
            "from": from_date,
            "to": to_date,
            "practitioners": results,
        }

    @mcp.tool()
    async def get_appointment_invoice_join(
        from_date: str,
        to_date: str,
        confirm_over: int = 500,
        confirmed: bool = False,
    ) -> dict[str, Any]:
        """For each appointment in range, find the matching invoice (if any).

        ⚠️ COST-GATED: refuses to run if the appointment count in the range
        exceeds `confirm_over` UNLESS `confirmed=True`. Returns a structured
        refusal with cost estimate so the LLM can ask the user.

        When to use:
            - "Which appointments last week don't have an invoice issued?"
            - "What's the average $ per appointment by type?"
            - "What's our billing capture rate?"

        WORKING_EXAMPLE:
            ```
            get_appointment_invoice_join(from_date="2026-05-11", to_date="2026-05-18")
            ```

        Returns:
            {
              "from": str,
              "to": str,
              "appointments_in_range": int,
              "appointments_with_invoice": int,
              "appointments_without_invoice": int,
              "capture_rate": float,            // 0.0-1.0
              "by_appointment_type": {           // average $/appt + count
                "<type_id>": {
                    "name": str,
                    "count": int,
                    "invoiced_count": int,
                    "total_revenue": float,
                    "avg_per_appointment": float
                },
                ...
              },
              "unbilled_appointment_ids": [str, ...]   // first 20
            }

        Notes:
            - The join is done by fetching invoices for the same date range AND
              cross-referencing the embedded `appointment.links.self` field.
              Invoices use issue_date NOT appointment date, so we widen the
              invoice window by 7 days each side for safety.
            - PHI: appointment+billing metadata. Audit-logged.
            - Cliniko invoice list view has no `balance` field — `total_amount` is
              the gross billed amount; outstanding requires per-invoice fetches.

        Args:
            from_date: ISO date YYYY-MM-DD (inclusive) on appointments.
            to_date: ISO date YYYY-MM-DD (inclusive) on appointments.
            confirm_over: refuse if appointment count > this. Default 500.
            confirmed: pass True to bypass the cost gate after asking the user.
        """
        # Step 0: cheap probe — appointment count in range
        probe_params = [
            ("per_page", "1"),
            ("q[]", f"starts_at:>={from_date}T00:00:00Z"),
            ("q[]", f"starts_at:<={to_date}T23:59:59Z"),
        ]
        probe = await client.get("/individual_appointments", params=probe_params)
        if "error" in probe:
            return probe
        appt_count = probe.get("total_entries", 0)
        if appt_count > confirm_over and not confirmed:
            est = round((appt_count // 100 + 1) * 4000 * 0.80 / 1_000_000, 3)
            return {
                "needs_confirmation": True,
                "appointment_count_in_range": appt_count,
                "from": from_date,
                "to": to_date,
                "estimated_cost_usd_haiku": est,
                "message": (
                    f"{appt_count:,} appointments fall in this date range. The join "
                    "would visit each one against the invoice list and could exceed "
                    "the configured cost ceiling. Ask the user whether to proceed."
                ),
                "options_to_offer_user": {
                    "narrow_date_range": "re-call with a shorter from/to window",
                    "proceed_anyway": "re-call with confirmed=True",
                },
            }

        # Fetch appointments
        appt_params = [
            ("per_page", "100"),
            ("q[]", f"starts_at:>={from_date}T00:00:00Z"),
            ("q[]", f"starts_at:<={to_date}T23:59:59Z"),
        ]
        ar = await client.get("/individual_appointments", params=appt_params)
        if "error" in ar:
            return ar
        appts = ar.get("individual_appointments", [])

        # Build appointment-type id → name map
        at = await client.get("/appointment_types", params={"per_page": 100})
        type_map = {t["id"]: t.get("name", f"type-{t['id']}") for t in at.get("appointment_types", [])}

        # Fetch invoices in a widened window (issue_date might trail appointment date)
        inv_from = (date.fromisoformat(from_date) - timedelta(days=7)).isoformat()
        inv_to = (date.fromisoformat(to_date) + timedelta(days=14)).isoformat()
        inv_params = [
            ("per_page", "100"),
            ("q[]", f"issue_date:>={inv_from}"),
            ("q[]", f"issue_date:<={inv_to}"),
        ]
        ir = await client.get("/invoices", params=inv_params)
        invoices = ir.get("invoices", []) if "error" not in ir else []

        # Build appointment_id → invoice map
        appt_to_invoice: dict[str, dict[str, Any]] = {}
        for inv in invoices:
            link = (inv.get("appointment") or {}).get("links", {}).get("self", "")
            if not link:
                continue
            appt_id = link.rstrip("/").rsplit("/", 1)[-1]
            appt_to_invoice[appt_id] = inv

        # Aggregate
        by_type: dict[str, dict[str, Any]] = defaultdict(lambda: {
            "name": "(unknown)",
            "count": 0,
            "invoiced_count": 0,
            "total_revenue": 0.0,
            "avg_per_appointment": 0.0,
        })
        unbilled: list[str] = []

        # Also count appointments with notes attached, using Cliniko's
        # has_patient_appointment_notes flag (saves a join entirely).
        appts_with_notes = 0
        appts_missing_notes_ids: list[str] = []
        for a in appts:
            at_link = (a.get("appointment_type") or {}).get("links", {}).get("self", "")
            at_id = at_link.rstrip("/").rsplit("/", 1)[-1] if at_link else "unknown"
            bucket = by_type[at_id]
            bucket["name"] = type_map.get(at_id, f"type-{at_id}")
            bucket["count"] += 1
            inv = appt_to_invoice.get(a["id"])
            if inv:
                bucket["invoiced_count"] += 1
                try:
                    bucket["total_revenue"] += float(inv.get("total_amount") or 0)
                except (TypeError, ValueError):
                    pass
            else:
                unbilled.append(a["id"])
            if a.get("has_patient_appointment_notes"):
                appts_with_notes += 1
            else:
                appts_missing_notes_ids.append(a["id"])

        # Compute averages
        for t in by_type.values():
            t["avg_per_appointment"] = round(t["total_revenue"] / t["invoiced_count"], 2) if t["invoiced_count"] else 0.0
            t["total_revenue"] = round(t["total_revenue"], 2)

        total_appts = len(appts)
        with_invoice = total_appts - len(unbilled)

        return {
            "from": from_date,
            "to": to_date,
            "appointments_in_range": total_appts,
            "appointments_with_invoice": with_invoice,
            "appointments_without_invoice": len(unbilled),
            "capture_rate": round(with_invoice / total_appts, 3) if total_appts else 0.0,
            "appointments_with_notes": appts_with_notes,
            "appointments_missing_notes": len(appts_missing_notes_ids),
            "missing_notes_sample_ids": appts_missing_notes_ids[:20],
            "by_appointment_type": dict(by_type),
            "unbilled_appointment_ids": unbilled[:20],
        }
