"""Response shaping — turn verbose Cliniko JSON into LLM-token-efficient summaries.

Cliniko's raw responses are nested and field-heavy. A `GET /patients` page can be
60-100KB of JSON for 25 results when most fields are useless for a casual query.
The markdown summary cuts that to ~1-2KB while preserving the IDs and primary
fields the LLM needs.

For drill-down, the caller can request the full record via a separate tool.

This is one of the bigger token-efficiency wins. The reference commercial product
(Practisight) does similar; the hobby implementations return raw JSON.
"""

from __future__ import annotations

from typing import Any

# Output envelope every list tool returns.
ListWrapper = dict[str, Any]


def list_wrapper(
    items_full: list[dict[str, Any]],
    *,
    summary_lines: list[str],
    total_entries: int | None = None,
    page: int = 1,
    has_more: bool = False,
    next_cursor: str | None = None,
) -> ListWrapper:
    """Build the standard list-response envelope.

    Args:
        items_full: The full Cliniko objects (used if the caller wants details).
        summary_lines: One line per item, already formatted as markdown bullet text.
        total_entries: Total matching entries upstream (not just the current page).
        page: Current page number.
        has_more: True if there are more pages.
        next_cursor: Optional cursor for keyset pagination.

    Returns:
        The standard list envelope. The LLM should usually read `summary_markdown`
        first; only consume `items` when it needs specific fields not in the summary.
    """
    return {
        "items": items_full,
        "total_entries": total_entries if total_entries is not None else len(items_full),
        "page": page,
        "has_more": has_more,
        "next_cursor": next_cursor,
        "summary_markdown": "\n".join(summary_lines) if summary_lines else "_(no items)_",
    }


def summarise_patient(p: dict[str, Any]) -> str:
    """One-line markdown summary of a patient."""
    pid = p.get("id", "?")
    name = " ".join(filter(None, [p.get("first_name"), p.get("last_name")])).strip() or "(no name)"
    dob = p.get("date_of_birth") or "?"
    email = p.get("email") or "—"
    phone = p.get("patient_phone_numbers") or []
    primary_phone = phone[0].get("number") if phone else "—"
    return f"- **{name}** (id `{pid}`, DOB {dob}, {email}, {primary_phone})"


def summarise_appointment(a: dict[str, Any]) -> str:
    """One-line markdown summary of an appointment."""
    aid = a.get("id", "?")
    start = a.get("starts_at", "?")
    end = a.get("ends_at", "?")
    cancelled = " ❌ CANCELLED" if a.get("cancelled_at") else ""
    patient_link = (a.get("patient") or {}).get("links", {}).get("self", "?")
    patient_id = _id_from_link(patient_link)
    pract_link = (a.get("practitioner") or {}).get("links", {}).get("self", "?")
    pract_id = _id_from_link(pract_link)
    return f"- {start} → {end} | appt `{aid}` (patient `{patient_id}`, practitioner `{pract_id}`){cancelled}"


def summarise_practitioner(p: dict[str, Any]) -> str:
    """One-line markdown summary of a practitioner."""
    pid = p.get("id", "?")
    name = (p.get("display_name") or " ".join(filter(None, [p.get("first_name"), p.get("last_name")])) or "(no name)").strip()
    title = p.get("title") or ""
    designation = p.get("designation") or ""
    active = "✅ active" if p.get("active") else "🚫 inactive"
    return f"- **{title} {name}** {designation} (id `{pid}`, {active})".replace("  ", " ").strip()


def summarise_business(b: dict[str, Any]) -> str:
    """One-line markdown summary of a business (practice location)."""
    bid = b.get("id", "?")
    name = b.get("business_name") or b.get("label") or "(no name)"
    city = b.get("city") or ""
    state = b.get("state") or ""
    country = b.get("country") or ""
    loc = ", ".join(filter(None, [city, state, country]))
    return f"- **{name}** (id `{bid}`, {loc})".rstrip(", ")


def summarise_invoice(i: dict[str, Any]) -> str:
    """One-line markdown summary of an invoice.

    Cliniko field reality (au5, 2026-05-18):
      - total_amount (not 'total')
      - status_description holds the human-readable status; status is an int code
      - NO 'balance' field — we can't compute outstanding without payments[] sum
    """
    iid = i.get("id", "?")
    number = i.get("number") or "?"
    total = i.get("total_amount") or i.get("total") or "?"  # fall back to legacy in case API differs by tier
    status_desc = i.get("status_description") or f"code={i.get('status','?')}"
    issued = i.get("issue_date") or "?"
    return f"- Invoice `{number}` (id `{iid}`): issued {issued}, total ${total}, status {status_desc}"


def summarise_treatment_note(n: dict[str, Any]) -> str:
    """One-line markdown summary of a treatment note (clinical content NOT included)."""
    nid = n.get("id", "?")
    created = n.get("created_at", "?")
    status = "📝 draft" if n.get("draft", True) else "✅ finalised"
    pract_link = (n.get("practitioner") or {}).get("links", {}).get("self", "?")
    pract_id = _id_from_link(pract_link)
    # Deliberately DO NOT include the note content in the summary — PHI.
    return f"- Note `{nid}` ({status}, {created}, by practitioner `{pract_id}`)"


def summarise_recall(r: dict[str, Any]) -> str:
    """One-line markdown summary of a recall."""
    rid = r.get("id", "?")
    due = r.get("due_at") or r.get("recall_date") or "?"
    note = r.get("note") or ""
    note_short = (note[:60] + "…") if len(note) > 60 else note
    return f"- Recall `{rid}` due {due}: {note_short}"


def _id_from_link(link: str) -> str:
    """Extract the trailing numeric id from a Cliniko HATEOAS self-link.

    Cliniko returns links like `https://api.au1.cliniko.com/v1/patients/12345678901234567890`.
    We just want `12345678901234567890`. Returns the original string if no slash present.
    """
    if not link or "/" not in link:
        return link or "?"
    return link.rstrip("/").rsplit("/", 1)[-1]
