"""Revenue audit tools — Phase D-Revenue.

Niche positioning: "Heidi writes notes. We catch the money you're leaving on the table."

These tools answer the questions in the R-series of the test-questions doc:
  - What's our capture rate?
  - Which appointments don't have invoices?
  - Which patients with [condition] haven't been billed for [item]?
  - How much revenue have we leaked this quarter?

Design notes:
  - Generic / clinic-agnostic. Probes the clinic's actual billable_items
    catalog at runtime; works for any allied-health discipline.
  - NDIS support is implicit — NDIS line item codes appear in the
    billable_items catalog and `find_billing_gaps` can filter by item code.
  - Cost-gated where the data scales with appointment volume.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any

from mcp.server.fastmcp import FastMCP

from au_cliniko_mcp.client import ClinikoClient
from au_cliniko_mcp.phi import (
    PHI_APPOINTMENT_METADATA,
    PHI_BILLING,
    PHI_CLINICAL_NOTES,
    PHI_PATIENT_LINK,
    phi_flagged,
)


def register(mcp: FastMCP, client: ClinikoClient) -> None:
    """Wire the D-Revenue tools onto the MCP server."""

    @mcp.tool()
    @phi_flagged(PHI_BILLING)
    async def list_billable_items_catalog(include_archived: bool = False) -> dict[str, Any]:
        """List every billable item configured in this clinic's Cliniko account.

        When to use:
            - As a discovery step before any "missed claim" question.
            - When the user asks "what can we bill for here?"
            - To answer R6: "Which MBS item codes are most billed at this clinic?"

        WORKING_EXAMPLE:
            ```
            list_billable_items_catalog()
            list_billable_items_catalog(include_archived=True)
            ```

        Notes:
            - `item_code` is the MBS/DVA/NDIS line code (when set).
            - `price` is the default price in AUD.
            - Returns no PHI. Administrative catalog only.

        Args:
            include_archived: include billable items the clinic has archived.
        """
        all_items: list[dict[str, Any]] = []
        page = 1
        while page <= 20:
            r = await client.get(
                "/billable_items", params={"page": page, "per_page": 100}
            )
            if "error" in r:
                return r
            all_items.extend(r.get("billable_items", []))
            if not r.get("links", {}).get("next"):
                break
            page += 1

        if not include_archived:
            all_items = [i for i in all_items if not i.get("archived_at")]

        summary = [
            f"- `{i.get('item_code') or '(no code)'}` — **{i.get('name')}** — ${i.get('price')} (id `{i.get('id')}`)"
            for i in all_items
        ]
        return {
            "items": all_items,
            "total_billable_items": len(all_items),
            "summary_markdown": "\n".join(summary) if summary else "_(no billable items configured)_",
        }

    @mcp.tool()
    @phi_flagged(PHI_BILLING)
    async def list_concession_types() -> dict[str, Any]:
        """List concession types configured for this clinic.

        When to use:
            - NDIS detection: NDIS is typically configured as a concession_type
              named "NDIS" (or similar). This tool lets the LLM find that.
            - Pension / DVA / Student / Senior discount detection.

        WORKING_EXAMPLE:
            ```
            list_concession_types()
            ```

        Notes:
            - Returns no PHI.
            - If the practice has no concession types, the result is empty.
              Practice may bill NDIS via billable_items only.
        """
        r = await client.get("/concession_types", params={"per_page": 100})
        if "error" in r:
            return r
        types = r.get("concession_types", [])
        active = [t for t in types if not t.get("archived_at")]
        return {
            "concession_types": active,
            "summary_markdown": "\n".join(
                f"- **{t.get('name')}** (id `{t.get('id')}`)" for t in active
            ) or "_(no concession types configured)_",
        }

    @mcp.tool()
    @phi_flagged(PHI_BILLING, PHI_APPOINTMENT_METADATA, PHI_PATIENT_LINK)
    async def revenue_audit(
        from_date: str,
        to_date: str,
        group_by: str = "appointment_type",
        confirm_over: int = 500,
        confirmed: bool = False,
    ) -> dict[str, Any]:
        """Comprehensive revenue audit over a date range.

        Returns:
            - Total appointments in range
            - Total invoiced (count + $)
            - Capture rate
            - Estimated revenue leakage (unbilled appts × avg appt price)
            - Per-grouping breakdown (appointment_type / practitioner / day)
            - Top missed-billing patterns

        When to use:
            - "What's our capture rate?"
            - "How much did we miss last quarter?"
            - "Per-practitioner billing audit"
            - "Where's our revenue going?"

        WORKING_EXAMPLE:
            ```
            revenue_audit(from_date="2026-04-01", to_date="2026-04-30")
            revenue_audit(from_date="2026-01-01", to_date="2026-04-30", group_by="practitioner")
            ```

        Notes:
            - Cost-gated: refuses if appointment count > confirm_over without confirmed=True.
            - PHI: appointment + billing data. Audit-logged.
            - "Leakage estimate" = (unbilled appts × average price of billed appts in range).
              Conservative — actual leakage may be higher if unbilled appointments
              would have had standard-rate items billed.

        Args:
            from_date / to_date: ISO YYYY-MM-DD (inclusive).
            group_by: 'appointment_type' (default), 'practitioner', or 'day'.
            confirm_over: refuse if appt count > this. Default 500.
            confirmed: bypass the cost gate after asking the user.
        """
        if group_by not in {"appointment_type", "practitioner", "day"}:
            return {
                "error": "validation_failed",
                "what_happened": f"group_by must be one of: appointment_type, practitioner, day. Got: {group_by!r}",
                "what_to_do": "Re-call with a valid group_by value.",
            }

        # Cost-gate probe
        probe = await client.get(
            "/individual_appointments",
            params=[
                ("per_page", "1"),
                ("q[]", f"starts_at:>={from_date}T00:00:00Z"),
                ("q[]", f"starts_at:<={to_date}T23:59:59Z"),
            ],
        )
        if "error" in probe:
            return probe
        appt_count = probe.get("total_entries", 0)
        if appt_count > confirm_over and not confirmed:
            return {
                "needs_confirmation": True,
                "appointment_count_in_range": appt_count,
                "estimated_cost_usd_haiku": round(
                    (appt_count // 100 + 1) * 6000 * 0.80 / 1_000_000, 3
                ),
                "message": (
                    f"{appt_count:,} appointments in this date range. The audit would "
                    "visit appointments + invoices + invoice_items + appointment_types. "
                    "Ask the user to narrow the range or confirm proceeding."
                ),
                "options_to_offer_user": {
                    "narrow_to_30_days": "use a shorter date window",
                    "proceed_anyway": "re-call with confirmed=True",
                },
            }

        # Fetch all appointments in range (paginate)
        appts: list[dict[str, Any]] = []
        page = 1
        while page <= 20:
            r = await client.get(
                "/individual_appointments",
                params=[
                    ("per_page", "100"),
                    ("page", str(page)),
                    ("q[]", f"starts_at:>={from_date}T00:00:00Z"),
                    ("q[]", f"starts_at:<={to_date}T23:59:59Z"),
                ],
            )
            if "error" in r:
                return r
            appts.extend(r.get("individual_appointments", []))
            if not r.get("links", {}).get("next"):
                break
            page += 1

        # Fetch all invoices in a widened range
        inv_from = (date.fromisoformat(from_date) - timedelta(days=7)).isoformat()
        inv_to = (date.fromisoformat(to_date) + timedelta(days=14)).isoformat()
        invoices: list[dict[str, Any]] = []
        page = 1
        while page <= 20:
            r = await client.get(
                "/invoices",
                params=[
                    ("per_page", "100"),
                    ("page", str(page)),
                    ("q[]", f"issue_date:>={inv_from}"),
                    ("q[]", f"issue_date:<={inv_to}"),
                ],
            )
            if "error" in r:
                break
            invoices.extend(r.get("invoices", []))
            if not r.get("links", {}).get("next"):
                break
            page += 1

        # Map appointment_id → invoice
        appt_to_inv: dict[str, dict[str, Any]] = {}
        for inv in invoices:
            link = (inv.get("appointment") or {}).get("links", {}).get("self", "")
            if link:
                appt_to_inv[link.rstrip("/").rsplit("/", 1)[-1]] = inv

        # Look up appointment-type names (for grouping)
        at_resp = await client.get("/appointment_types", params={"per_page": 100})
        type_map = {
            t["id"]: t.get("name", "?")
            for t in at_resp.get("appointment_types", [])
        }
        # Practitioner name map
        pr_resp = await client.get("/practitioners", params={"per_page": 100})
        pract_map = {
            p["id"]: f"{p.get('first_name', '')} {p.get('last_name', '')}".strip() or f"id-{p['id']}"
            for p in pr_resp.get("practitioners", [])
        }

        # Aggregate
        groups: dict[str, dict[str, Any]] = defaultdict(
            lambda: {
                "appointments": 0,
                "with_invoice": 0,
                "without_invoice": 0,
                "total_revenue": 0.0,
            }
        )
        total_revenue = 0.0
        for a in appts:
            if group_by == "appointment_type":
                at_link = (a.get("appointment_type") or {}).get("links", {}).get("self", "")
                at_id = at_link.rstrip("/").rsplit("/", 1)[-1] if at_link else "unknown"
                key = type_map.get(at_id, f"type-{at_id}")
            elif group_by == "practitioner":
                p_link = (a.get("practitioner") or {}).get("links", {}).get("self", "")
                p_id = p_link.rstrip("/").rsplit("/", 1)[-1] if p_link else "unknown"
                key = pract_map.get(p_id, f"practitioner-{p_id}")
            else:  # day
                key = (a.get("starts_at") or "")[:10]

            bucket = groups[key]
            bucket["appointments"] += 1
            inv = appt_to_inv.get(a["id"])
            if inv:
                bucket["with_invoice"] += 1
                try:
                    rev = float(inv.get("total_amount") or 0)
                except (TypeError, ValueError):
                    rev = 0.0
                bucket["total_revenue"] += rev
                total_revenue += rev
            else:
                bucket["without_invoice"] += 1

        # Compute averages + leakage estimate
        billed_appts = sum(g["with_invoice"] for g in groups.values())
        unbilled_appts = sum(g["without_invoice"] for g in groups.values())
        avg_billed = total_revenue / billed_appts if billed_appts else 0.0
        leakage_estimate = round(unbilled_appts * avg_billed, 2)

        for g in groups.values():
            g["avg_revenue_per_appt"] = round(
                g["total_revenue"] / g["with_invoice"], 2
            ) if g["with_invoice"] else 0.0
            g["total_revenue"] = round(g["total_revenue"], 2)
            g["capture_rate"] = round(g["with_invoice"] / g["appointments"], 3) if g["appointments"] else 0.0

        # Rank groups by total revenue (descending)
        ranked = sorted(groups.items(), key=lambda kv: kv[1]["total_revenue"], reverse=True)

        return {
            "from": from_date,
            "to": to_date,
            "group_by": group_by,
            "total_appointments": len(appts),
            "billed_appointments": billed_appts,
            "unbilled_appointments": unbilled_appts,
            "capture_rate": round(billed_appts / len(appts), 3) if appts else 0.0,
            "total_revenue_billed": round(total_revenue, 2),
            "avg_revenue_per_billed_appointment": round(avg_billed, 2),
            "estimated_leakage_usd": leakage_estimate,
            "leakage_methodology": (
                "Estimated as (unbilled appointments) × (average $/billed appointment). "
                "Conservative — actual leakage may be higher if unbilled appointments "
                "would normally include higher-value items."
            ),
            "groups": [{"group": k, **v} for k, v in ranked],
        }

    @mcp.tool()
    @phi_flagged(PHI_BILLING, PHI_APPOINTMENT_METADATA, PHI_PATIENT_LINK, PHI_CLINICAL_NOTES)
    async def find_billing_gaps(
        from_date: str,
        to_date: str,
        expected_item_code: str | None = None,
        expected_item_id: str | None = None,
        condition_keyword: str | None = None,
        confirm_over: int = 200,
        confirmed: bool = False,
    ) -> dict[str, Any]:
        """Find appointments / patients where an expected billable item was NOT charged.

        Two modes (pass ONE of the filters):

        MODE A — appointment-centric:
            Pass `expected_item_code` or `expected_item_id`. Returns all
            appointments in [from_date, to_date] whose invoice does NOT
            include that item. Use for: "find appointments where MBS 11700
            should have been billed but wasn't."

        MODE B — patient-cohort:
            Pass `condition_keyword`. Finds patients with that keyword in
            their treatment-note content and reports per-patient billing
            history for the period. Use for: "find diabetic patients who
            haven't been billed for a chronic disease item this year."

            ⚠️ MODE B is expensive — visits every treatment note in range.
            Cost-gated.

        WORKING_EXAMPLE:
            ```
            # Mode A: appointment-centric
            find_billing_gaps(from_date="2026-04-01", to_date="2026-04-30",
                             expected_item_code="11700")

            # Mode B: patient-cohort
            find_billing_gaps(from_date="2026-01-01", to_date="2026-04-30",
                             condition_keyword="diabetes",
                             expected_item_code="81330")
            ```

        Notes:
            - For NDIS: pass an NDIS-style item_code (e.g. "15_010_0128_1_3").
              The tool treats codes as opaque strings.
            - Mode B scans treatment notes — uses PHI_CLINICAL_NOTES category.

        Args:
            from_date / to_date: ISO YYYY-MM-DD inclusive.
            expected_item_code: Cliniko billable_item.item_code (string).
            expected_item_id: alternatively, the 19-digit billable_item.id.
            condition_keyword: search treatment notes for this string (Mode B).
            confirm_over: refuse if scope > this. Default 200.
            confirmed: bypass the cost gate.
        """
        if not (expected_item_code or expected_item_id or condition_keyword):
            return {
                "error": "validation_failed",
                "what_happened": "Pass at least one of: expected_item_code, expected_item_id, condition_keyword.",
                "what_to_do": "Mode A: pass expected_item_code or expected_item_id. Mode B: pass condition_keyword.",
            }

        # Cost-gate probe
        probe = await client.get(
            "/individual_appointments",
            params=[
                ("per_page", "1"),
                ("q[]", f"starts_at:>={from_date}T00:00:00Z"),
                ("q[]", f"starts_at:<={to_date}T23:59:59Z"),
            ],
        )
        if "error" in probe:
            return probe
        scope = probe.get("total_entries", 0)
        if scope > confirm_over and not confirmed:
            return {
                "needs_confirmation": True,
                "appointment_count_in_range": scope,
                "message": (
                    f"{scope} appointments fall in this date range. The check would visit "
                    "each one's invoice + line items. Ask the user to narrow the date range "
                    "or confirm proceeding."
                ),
                "options_to_offer_user": {
                    "narrow_date_range": "use a shorter from/to window",
                    "proceed_anyway": "re-call with confirmed=True",
                },
            }

        # MODE A: appointment-centric
        if not condition_keyword:
            return await _mode_a_appointment_centric(
                client, from_date, to_date, expected_item_code, expected_item_id
            )

        # MODE B: patient-cohort
        return await _mode_b_patient_cohort(
            client, from_date, to_date, condition_keyword,
            expected_item_code, expected_item_id,
        )


async def _fetch_invoice_items(client: ClinikoClient, invoice_id: str) -> list[dict[str, Any]]:
    """Walk /invoices/{id}/invoice_items for one invoice."""
    r = await client.get(f"/invoices/{invoice_id}/invoice_items", params={"per_page": 100})
    if "error" in r:
        return []
    return r.get("invoice_items", [])


async def _mode_a_appointment_centric(
    client: ClinikoClient,
    from_date: str,
    to_date: str,
    expected_item_code: str | None,
    expected_item_id: str | None,
) -> dict[str, Any]:
    """Mode A: find appointments whose invoice doesn't include the expected item."""
    appt_params = [
        ("per_page", "100"),
        ("q[]", f"starts_at:>={from_date}T00:00:00Z"),
        ("q[]", f"starts_at:<={to_date}T23:59:59Z"),
    ]
    appts: list[dict[str, Any]] = []
    page = 1
    while page <= 10:
        r = await client.get(
            "/individual_appointments",
            params=appt_params + [("page", str(page))],
        )
        if "error" in r:
            return r
        appts.extend(r.get("individual_appointments", []))
        if not r.get("links", {}).get("next"):
            break
        page += 1

    # Fetch invoices in widened window
    inv_from = (date.fromisoformat(from_date) - timedelta(days=7)).isoformat()
    inv_to = (date.fromisoformat(to_date) + timedelta(days=14)).isoformat()
    ir = await client.get("/invoices", params=[
        ("per_page", "100"),
        ("q[]", f"issue_date:>={inv_from}"),
        ("q[]", f"issue_date:<={inv_to}"),
    ])
    invoices = ir.get("invoices", []) if "error" not in ir else []
    appt_to_inv = {}
    for inv in invoices:
        link = (inv.get("appointment") or {}).get("links", {}).get("self", "")
        if link:
            appt_to_inv[link.rstrip("/").rsplit("/", 1)[-1]] = inv

    # For each appointment, check invoice_items for the expected code/id
    matched: list[str] = []
    missing: list[dict[str, Any]] = []
    no_invoice: list[str] = []

    for a in appts:
        inv = appt_to_inv.get(a["id"])
        if not inv:
            no_invoice.append(a["id"])
            continue
        items = await _fetch_invoice_items(client, inv["id"])
        found = False
        for it in items:
            if expected_item_code and it.get("code") == expected_item_code:
                found = True
                break
            if expected_item_id:
                bi_link = (it.get("billable_item") or {}).get("links", {}).get("self", "")
                if bi_link and bi_link.rstrip("/").rsplit("/", 1)[-1] == expected_item_id:
                    found = True
                    break
        if found:
            matched.append(a["id"])
        else:
            missing.append({
                "appointment_id": a["id"],
                "starts_at": a.get("starts_at"),
                "patient_id": (a.get("patient") or {}).get("links", {}).get("self", "").rstrip("/").rsplit("/", 1)[-1],
            })

    return {
        "mode": "A_appointment_centric",
        "from": from_date,
        "to": to_date,
        "filter": {"expected_item_code": expected_item_code, "expected_item_id": expected_item_id},
        "appointments_checked": len(appts),
        "with_expected_item_billed": len(matched),
        "without_expected_item_billed": len(missing) + len(no_invoice),
        "appointments_no_invoice_at_all": len(no_invoice),
        "missing_sample": missing[:20],
        "no_invoice_sample_ids": no_invoice[:20],
    }


async def _mode_b_patient_cohort(
    client: ClinikoClient,
    from_date: str,
    to_date: str,
    condition_keyword: str,
    expected_item_code: str | None,
    expected_item_id: str | None,
) -> dict[str, Any]:
    """Mode B: find patients whose notes mention X who haven't been billed Y."""
    # Stage 1: list all treatment notes in range; we'll filter by keyword client-side
    # (Cliniko doesn't expose full-text search on treatment_note.content).
    notes: list[dict[str, Any]] = []
    page = 1
    while page <= 20:
        r = await client.get("/treatment_notes", params={
            "per_page": 100,
            "page": page,
            "q[]": f"created_at:>={from_date}T00:00:00Z",
        })
        if "error" in r:
            return r
        notes.extend(r.get("treatment_notes", []))
        if not r.get("links", {}).get("next"):
            break
        page += 1

    # For each note, fetch the full body (because list view doesn't include content)
    # and look for the keyword. To control cost we cap at 100 notes.
    matched_patients: dict[str, list[str]] = defaultdict(list)
    notes_to_check = notes[:100]
    for n in notes_to_check:
        # Fetch the note body — list response doesn't include content
        full = await client.get(f"/treatment_notes/{n['id']}")
        if "error" in full:
            continue
        content = (full.get("content") or "")
        if condition_keyword.lower() in content.lower():
            patient_link = (n.get("patient") or {}).get("links", {}).get("self", "")
            patient_id = patient_link.rstrip("/").rsplit("/", 1)[-1] if patient_link else None
            if patient_id:
                matched_patients[patient_id].append(n["id"])

    # Stage 2: for each matched patient, check if they've been billed expected_item
    patients_with_gap: list[dict[str, Any]] = []
    patients_billed: list[str] = []
    for patient_id, note_ids in matched_patients.items():
        # Find this patient's invoices in the range
        inv = await client.get("/invoices", params=[
            ("per_page", "50"),
            ("q[]", f"patient_id:={patient_id}"),
            ("q[]", f"issue_date:>={from_date}"),
            ("q[]", f"issue_date:<={to_date}"),
        ])
        invoices = inv.get("invoices", []) if "error" not in inv else []
        billed = False
        for invoice in invoices:
            items = await _fetch_invoice_items(client, invoice["id"])
            for it in items:
                if expected_item_code and it.get("code") == expected_item_code:
                    billed = True; break
                if expected_item_id:
                    bi_link = (it.get("billable_item") or {}).get("links", {}).get("self", "")
                    if bi_link and bi_link.rstrip("/").rsplit("/", 1)[-1] == expected_item_id:
                        billed = True; break
            if billed:
                break
        if billed:
            patients_billed.append(patient_id)
        else:
            patients_with_gap.append({
                "patient_id": patient_id,
                "matching_note_ids": note_ids[:3],
            })

    return {
        "mode": "B_patient_cohort",
        "from": from_date,
        "to": to_date,
        "filter": {
            "condition_keyword": condition_keyword,
            "expected_item_code": expected_item_code,
            "expected_item_id": expected_item_id,
        },
        "notes_scanned": len(notes_to_check),
        "patients_matching_keyword": len(matched_patients),
        "patients_already_billed": len(patients_billed),
        "patients_with_billing_gap": len(patients_with_gap),
        "patients_with_gap_sample": patients_with_gap[:20],
    }
