"""Cross-tool workflow recipes — Phase D-Workflow (Shape A).

Shape A = our MCP handles Cliniko only. Customer brings their own connectors
for Gmail, Calendar, Drive, Xero (via their Anthropic account's native
connectors). Our blast radius is bounded to Cliniko alone.

This module ships in two parts:

1. **Native Cliniko-side aggregators** that produce workflow-friendly outputs.
   These shape Cliniko data into formats that play well with downstream
   connectors the customer already has.

2. **Named MCP prompts (FastMCP @mcp.prompt())** — markdown recipes that
   tell Claude how to orchestrate a multi-step workflow across whatever
   connectors the customer has wired up. The prompts never assume specific
   connectors exist; they describe the goal + the steps.

When the user invokes a prompt from Claude Desktop's "/" menu, Claude reads
the recipe and executes the steps across the customer's available connectors.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

from mcp.server.fastmcp import FastMCP

from au_cliniko_mcp.client import ClinikoClient
from au_cliniko_mcp.phi import (
    PHI_APPOINTMENT_METADATA,
    PHI_BILLING,
    PHI_COMMUNICATIONS,
    PHI_CONTACT,
    PHI_PATIENT_LINK,
    phi_flagged,
)


def register(mcp: FastMCP, client: ClinikoClient) -> None:
    """Wire the workflow tools + prompt recipes onto the MCP server."""

    # ============================================================
    # PART 1 — native Cliniko-side aggregators
    # ============================================================

    @mcp.tool()
    @phi_flagged(PHI_CONTACT, PHI_PATIENT_LINK, PHI_COMMUNICATIONS)
    async def prepare_recall_outreach(
        within_days: int = 14,
        message_template: str | None = None,
        practice_name: str = "the clinic",
    ) -> dict[str, Any]:
        """Pull recalls due in the next N days + format each entry with patient
        contact details + a draft SMS/email-ready message body.

        Customer's downstream connector (Gmail, SMS gateway, etc.) sends the
        actual message. This tool just hands Claude a list of {patient_id,
        name, phone, email, message_body} so Claude can iterate + dispatch.

        When to use:
            - "Run my weekly recall outreach workflow"
            - As step 2 of the weekly_recall_review prompt recipe

        WORKING_EXAMPLE:
            ```
            prepare_recall_outreach(within_days=7)
            prepare_recall_outreach(within_days=14,
                message_template="Hi {first_name}, time for your next visit at {practice}. Reply YES to book.")
            ```

        Notes:
            - {first_name}, {last_name}, {practice} are placeholders the
              tool fills in. Any other braces are passed through verbatim.
            - PHI: contact + communications. Audit-logged.
            - No SMS / email is sent by this tool — output is text only.

        Args:
            within_days: how many days ahead to look. Default 14.
            message_template: format string with {first_name}, {last_name},
                {practice} placeholders. Defaults to a generic recall message.
            practice_name: used in the {practice} placeholder.
        """
        default_template = (
            "Hi {first_name}, this is a friendly reminder from {practice} "
            "that you're due for a follow-up appointment. Please call us or "
            "reply to this message to schedule. Thanks!"
        )
        tpl = message_template or default_template

        # Step 1: list recalls due
        cutoff = (date.today() + timedelta(days=within_days)).isoformat()
        all_recalls: list[dict[str, Any]] = []
        page = 1
        while page <= 5:
            r = await client.get(
                "/recalls", params={"per_page": 100, "page": page}
            )
            if "error" in r:
                return r
            all_recalls.extend(r.get("recalls", []))
            if not r.get("links", {}).get("next"):
                break
            page += 1
        due = [x for x in all_recalls if x.get("recall_at") and x["recall_at"] <= cutoff]

        # Step 2: enrich each with patient contact details
        entries: list[dict[str, Any]] = []
        for recall in due[:50]:
            patient_link = (recall.get("patient") or {}).get("links", {}).get("self", "")
            patient_id = patient_link.rstrip("/").rsplit("/", 1)[-1] if patient_link else None
            if not patient_id:
                continue

            patient = await client.get(f"/patients/{patient_id}")
            if "error" in patient:
                continue

            first = patient.get("first_name") or ""
            last = patient.get("last_name") or ""
            email = patient.get("email") or None
            phones = patient.get("patient_phone_numbers") or []
            mobile = next((p.get("number") for p in phones if p.get("phone_type") == "Mobile"), None)
            if not mobile and phones:
                mobile = phones[0].get("number")

            body = tpl.format(
                first_name=first or "there",
                last_name=last,
                practice=practice_name,
            )

            entries.append({
                "patient_id": patient_id,
                "name": f"{first} {last}".strip(),
                "email": email,
                "mobile": mobile,
                "recall_id": recall["id"],
                "recall_due": recall.get("recall_at"),
                "message_body": body,
                "channel_suggestion": "sms" if mobile else ("email" if email else "phone"),
            })

        return {
            "outreach_entries": entries,
            "total_due_in_window": len(due),
            "entries_returned": len(entries),
            "message_template_used": tpl,
            "next_step_for_claude": (
                "Iterate over outreach_entries. For each entry, dispatch the "
                "message_body via the user's preferred channel — Gmail "
                "connector for email, SMS connector or copy-to-clipboard for "
                "SMS. ALWAYS show the user the draft before sending each."
            ),
        }

    @mcp.tool()
    @phi_flagged(PHI_BILLING, PHI_CONTACT, PHI_PATIENT_LINK, PHI_COMMUNICATIONS)
    async def prepare_invoice_chase(
        over_days: int = 30,
        message_template: str | None = None,
        practice_name: str = "the clinic",
        max_count: int = 25,
    ) -> dict[str, Any]:
        """Pull outstanding invoices over N days old + format each with
        patient contact + a draft chase-email body.

        WORKING_EXAMPLE:
            ```
            prepare_invoice_chase(over_days=30)
            prepare_invoice_chase(over_days=60, max_count=10)
            ```

        Notes:
            - Cliniko has no `balance` field on the list view; we use total_amount
              as the chase amount (caveat: doesn't account for partial payments
              that were applied but not closed). For exact balance per invoice,
              fetch individually via get_invoice.
            - PHI: billing + contact + communications. Audit-logged.

        Args:
            over_days: only chase invoices older than this many days. Default 30.
            message_template: optional format string with {first_name},
                {practice}, {invoice_number}, {amount}, {days_overdue} placeholders.
            practice_name: practice display name.
            max_count: cap the number of entries returned.
        """
        default_template = (
            "Hi {first_name}, this is a friendly reminder from {practice} "
            "that invoice {invoice_number} for ${amount} is now {days_overdue} "
            "days overdue. Please get in touch if you'd like to discuss payment. "
            "Thanks!"
        )
        tpl = message_template or default_template

        cutoff = (date.today() - timedelta(days=over_days)).isoformat()
        # Fetch all unpaid invoices (status != 20)
        all_invs: list[dict[str, Any]] = []
        page = 1
        while page <= 5:
            r = await client.get(
                "/invoices", params={"per_page": 100, "page": page}
            )
            if "error" in r:
                return r
            all_invs.extend(r.get("invoices", []))
            if not r.get("links", {}).get("next"):
                break
            page += 1
        aged = [
            i for i in all_invs
            if i.get("status") != 20 and (i.get("issue_date") or "9999") <= cutoff
        ][:max_count]

        entries: list[dict[str, Any]] = []
        for inv in aged:
            patient_link = (inv.get("patient") or {}).get("links", {}).get("self", "")
            patient_id = patient_link.rstrip("/").rsplit("/", 1)[-1] if patient_link else None
            patient = {}
            if patient_id:
                p = await client.get(f"/patients/{patient_id}")
                if "error" not in p:
                    patient = p

            first = patient.get("first_name") or ""
            last = patient.get("last_name") or ""
            email = patient.get("email") or None

            issue_date = inv.get("issue_date") or ""
            try:
                days_overdue = (date.today() - date.fromisoformat(issue_date)).days
            except ValueError:
                days_overdue = "?"

            body = tpl.format(
                first_name=first or "there",
                practice=practice_name,
                invoice_number=inv.get("number") or inv["id"],
                amount=inv.get("total_amount") or "?",
                days_overdue=days_overdue,
            )

            entries.append({
                "invoice_id": inv["id"],
                "invoice_number": inv.get("number"),
                "patient_id": patient_id,
                "patient_name": f"{first} {last}".strip(),
                "email": email,
                "amount_billed": inv.get("total_amount"),
                "issue_date": issue_date,
                "days_overdue": days_overdue,
                "message_body": body,
            })

        return {
            "chase_entries": entries,
            "total_aged_unpaid": len(aged),
            "message_template_used": tpl,
            "next_step_for_claude": (
                "Iterate over chase_entries. For each one, draft an email via "
                "the user's Gmail connector (or copy-to-clipboard if Gmail not "
                "connected). ALWAYS surface the draft to the user before sending."
            ),
        }

    @mcp.tool()
    @phi_flagged(PHI_APPOINTMENT_METADATA, PHI_PATIENT_LINK)
    async def format_appointments_as_calendar_events(
        from_date: str,
        to_date: str,
    ) -> dict[str, Any]:
        """Shape Cliniko appointments as Calendar-event-ready dicts.

        Output structure matches what most calendar connectors expect:
        {summary, start, end, description, attendees (patient id only)}.

        Customer's Google Calendar connector (or Outlook, iCal, etc) consumes
        these and creates events. We don't send.

        WORKING_EXAMPLE:
            ```
            format_appointments_as_calendar_events(from_date="2026-05-19", to_date="2026-05-25")
            ```

        Notes:
            - Output is JSON the LLM hands to a Calendar connector.
            - We deliberately put the patient_id in the description, not the name,
              to keep PHI surface tighter at the calendar layer.
        """
        appts = []
        page = 1
        while page <= 10:
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

        events = []
        for a in appts:
            if a.get("cancelled_at"):
                continue
            patient_id = (a.get("patient") or {}).get("links", {}).get("self", "").rstrip("/").rsplit("/", 1)[-1]
            pract_id = (a.get("practitioner") or {}).get("links", {}).get("self", "").rstrip("/").rsplit("/", 1)[-1]
            at_id = (a.get("appointment_type") or {}).get("links", {}).get("self", "").rstrip("/").rsplit("/", 1)[-1]
            events.append({
                "appointment_id": a["id"],
                "summary": f"Cliniko appt — patient {patient_id[-6:]}",
                "start": a.get("starts_at"),
                "end": a.get("ends_at"),
                "description": (
                    f"Cliniko appointment_id: {a['id']}\n"
                    f"Patient: {patient_id}\n"
                    f"Practitioner: {pract_id}\n"
                    f"Type: {at_id}\n"
                    f"Cliniko URL: (open in Cliniko UI)"
                ),
                "metadata": {
                    "cliniko_appointment_id": a["id"],
                    "patient_id": patient_id,
                    "practitioner_id": pract_id,
                    "appointment_type_id": at_id,
                },
            })

        return {
            "from": from_date,
            "to": to_date,
            "events_ready_to_create": events,
            "event_count": len(events),
            "next_step_for_claude": (
                "Iterate over events_ready_to_create and call the user's "
                "Calendar connector's `create_event` (or equivalent) for each one. "
                "Skip events whose appointment_id is already in the user's calendar "
                "(idempotency)."
            ),
        }

    # ============================================================
    # PART 2 — named MCP prompts (recipes)
    # ============================================================
    # FastMCP exposes these via the host's "/" prompt menu. The customer
    # invokes them by name; Claude reads the recipe and orchestrates.

    @mcp.prompt()
    def weekly_recall_review() -> str:
        """Monday-morning recall outreach workflow."""
        return (
            "# Weekly recall outreach\n\n"
            "Goal: run this every Monday to clear the recall list for the next 7 days.\n\n"
            "Steps:\n"
            "1. Call `list_recalls_due(within_days=7)` to see the picture.\n"
            "2. Call `prepare_recall_outreach(within_days=7)` to get a list of "
            "drafted SMS/email bodies with patient contact details.\n"
            "3. SHOW THE USER the full list. Ask them which ones to send.\n"
            "4. For each approved entry, dispatch via the user's preferred channel:\n"
            "   - If channel_suggestion is 'sms' and the user has an SMS connector, "
            "draft the SMS for them to send.\n"
            "   - If 'email' and Gmail connector is available, draft + queue the email.\n"
            "   - Otherwise show the body so the user can copy-paste manually.\n"
            "5. NEVER send anything without explicit user approval per message.\n"
            "6. After the user approves a batch, summarise what was dispatched.\n\n"
            "AHPRA note: recall messages must be factual and patient-initiated-only "
            "in tone. Do not include therapeutic claims or testimonials."
        )

    @mcp.prompt()
    def invoice_chase_workflow() -> str:
        """Aged-receivables chase workflow."""
        return (
            "# Invoice chase workflow\n\n"
            "Goal: chase outstanding invoices over 30 days old.\n\n"
            "Steps:\n"
            "1. Call `list_unpaid_invoices(over_days=30)` to see scope.\n"
            "2. Call `prepare_invoice_chase(over_days=30, max_count=25)` for drafts.\n"
            "3. SHOW THE USER the list of chase entries with amounts + days overdue.\n"
            "4. Ask the user to confirm which to send.\n"
            "5. For each approved entry, draft a personalised email via Gmail (if "
            "the user has it connected) or paste the body for them to send manually.\n"
            "6. Log dispatched chases (the user may want to track who's been chased "
            "this week).\n\n"
            "Compliance note: chase emails must comply with the Spam Act 2003 — "
            "include the practice's contact details + an obvious unsubscribe path "
            "in any bulk send. For single-patient chases this is implicit (the "
            "patient already has a relationship)."
        )

    @mcp.prompt()
    def no_show_followup_workflow() -> str:
        """No-show recovery workflow."""
        return (
            "# No-show follow-up workflow\n\n"
            "Goal: identify this week's no-shows + draft re-engagement messages.\n\n"
            "Steps:\n"
            "1. Call `list_appointments(from_date=<7d ago>, to_date=<today>)`.\n"
            "2. Filter for `did_not_arrive=true` appointments.\n"
            "3. For each, fetch the patient's contact (via get_patient or "
            "list_appointments_for_patient).\n"
            "4. Draft a re-engagement message — apologetic in tone, offers easy "
            "rebooking. Use AHPRA-compliant language only.\n"
            "5. Show the user each draft for approval before dispatch.\n"
            "6. Track which patients have had multiple no-shows — use "
            "`get_patient_appointment_stats` for that history.\n"
            "7. Suggest the user consider deposits / charging policy for "
            "repeat-no-show patients."
        )

    @mcp.prompt()
    def monday_morning_digest() -> str:
        """The Monday-morning practice digest."""
        return (
            "# Monday-morning practice digest\n\n"
            "Goal: a one-shot Monday morning snapshot of last week's performance.\n\n"
            "Steps:\n"
            "1. Call `generate_practice_digest()`. It uses the user's saved KPI "
            "preferences (revenue, new patients, no-shows, capture rate, etc).\n"
            "2. If the user hasn't set preferences yet, the defaults run all 6 KPIs.\n"
            "3. PRESENT the digest_markdown to the user. Highlight anything that "
            "deviates >10% from the prior period.\n"
            "4. OFFER (don't auto-do) to email the digest:\n"
            "   - 'Want me to email this to you via Gmail?' — wait for yes.\n"
            "   - 'Want me to save this to a Google Doc?' — wait for yes.\n"
            "5. After delivery, log the timestamp so the user knows when their last "
            "digest ran."
        )

    @mcp.prompt()
    def appointment_calendar_sync() -> str:
        """Sync Cliniko appointments to the user's calendar."""
        return (
            "# Sync upcoming appointments to Google Calendar\n\n"
            "Goal: ensure every Cliniko appointment for the next 14 days exists in "
            "the user's Google Calendar.\n\n"
            "Steps:\n"
            "1. Call `format_appointments_as_calendar_events(from_date=<today>, "
            "to_date=<today+14>)` to get the structured event list.\n"
            "2. If the user has a Google Calendar connector configured, query "
            "their calendar for events tagged with `cliniko_appointment_id` to "
            "find which are already synced.\n"
            "3. For NEW (not-yet-in-calendar) events, create them via the Calendar "
            "connector. Include the cliniko_appointment_id in the description for "
            "idempotency.\n"
            "4. For events that exist in Calendar but have been cancelled in Cliniko, "
            "delete the calendar entry.\n"
            "5. Summarise actions taken.\n\n"
            "If the user has no Calendar connector, output the event list as JSON "
            "for them to import manually."
        )

    @mcp.prompt()
    def end_of_month_report() -> str:
        """End-of-month practice performance briefing."""
        return (
            "# End-of-month practice briefing\n\n"
            "Goal: produce a comprehensive monthly performance report.\n\n"
            "Steps:\n"
            "1. Call `revenue_audit(from_date=<month start>, to_date=<month end>, "
            "group_by='practitioner')` for revenue + leakage.\n"
            "2. Call `kpi_revenue_summary(period_days=30)` for revenue trend.\n"
            "3. Call `kpi_new_patients(period_days=30)` for acquisition trend.\n"
            "4. Call `kpi_no_shows(period_days=30)` for capacity loss.\n"
            "5. Call `kpi_retention_rate()` for retention.\n"
            "6. Synthesise into a one-page markdown report:\n"
            "   - Top-line revenue vs target\n"
            "   - Capture rate + estimated leakage\n"
            "   - Top performing + bottom performing practitioner\n"
            "   - Trend signals (positive + concerning)\n"
            "   - 2-3 recommended actions\n"
            "7. OFFER to save the report to Google Drive (if connected) "
            "and/or email to the practice owner."
        )
