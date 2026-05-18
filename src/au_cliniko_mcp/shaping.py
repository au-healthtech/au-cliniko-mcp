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
    dob = p.get("date_of_birth", "?")
    email = p.get("email", "—")
    return f"- **{name}** (id `{pid}`, DOB {dob}, {email})"


def summarise_appointment(a: dict[str, Any]) -> str:
    """One-line markdown summary of an appointment."""
    aid = a.get("id", "?")
    start = a.get("starts_at", "?")
    patient = (a.get("patient") or {}).get("links", {}).get("self", "?")
    pract = (a.get("practitioner") or {}).get("links", {}).get("self", "?")
    return f"- {start} — appt `{aid}` (patient `{patient}`, practitioner `{pract}`)"


def summarise_invoice(i: dict[str, Any]) -> str:
    """One-line markdown summary of an invoice."""
    iid = i.get("id", "?")
    number = i.get("number", "?")
    total = i.get("total", "?")
    balance = i.get("balance", "?")
    status = i.get("status", "?")
    return f"- Invoice `{number}` (id `{iid}`): total ${total}, balance ${balance}, status {status}"
