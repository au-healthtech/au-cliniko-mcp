"""End-to-end eval suite for the 26 consulting Q&A test questions.

Runs each question against the live Cliniko sandbox via the MCP's tool layer
(no LLM in the loop yet — this is the programmatic baseline). For each question:

  - calls the tool(s) that would answer it
  - times the call
  - inspects the response shape
  - scores against the eval rubric

Outputs a markdown report at tests/integration/eval_results.md.

Run:
    set -a; source .env; set +a
    PYTHONPATH=src python tests/integration/eval_suite.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from au_cliniko_mcp.auth import ClinikoCredential
from au_cliniko_mcp.client import ClinikoClient


# A test case ties a question to a deterministic implementation that uses
# our client + tools to produce an answer. The implementation is what Claude
# would WANT to do given our current toolset.
class TestCase:
    def __init__(
        self,
        qid: str,
        question: str,
        category: str,
        impl: Callable,
        tools_used: list[str],
        coverage_forecast: str,
    ):
        self.qid = qid
        self.question = question
        self.category = category
        self.impl = impl
        self.tools_used = tools_used
        self.coverage_forecast = coverage_forecast
        self.result: dict[str, Any] = {}


# === Implementations ===
# Each impl is `async def(client) -> dict` returning the answer dict.

async def q1_active_patients(client: ClinikoClient) -> dict[str, Any]:
    """Q1: how many active patients."""
    r = await client.get("/patients", params={"per_page": 1})
    if "error" in r:
        return {"answer": None, "raw_error": r}
    return {"answer": r.get("total_entries"), "metric": "total_patients"}


async def q2_avg_appts_per_week_3m(client: ClinikoClient) -> dict[str, Any]:
    """Q2: avg appointments per week over last 3 months."""
    cutoff = (date.today() - timedelta(days=90)).isoformat()
    q_params = [
        ("per_page", "100"),
        ("q[]", f"starts_at:>={cutoff}T00:00:00Z"),
    ]
    r = await client.get("/individual_appointments", params=q_params)
    if "error" in r:
        return {"answer": None, "raw_error": r}
    appts = r.get("individual_appointments", [])
    total = r.get("total_entries") or len(appts)
    return {
        "answer": round(total / 13, 1),  # 90 days ≈ 13 weeks
        "total_appointments_in_window": total,
        "weeks": 13,
    }


async def q3_top10_practitioners(client: ClinikoClient) -> dict[str, Any]:
    """Q3: top 10 practitioners by appointment count.

    GAP: we don't have a per-practitioner appointment count tool. Would need
    to list all practitioners, then call list_appointments per practitioner.
    For the sandbox (single practitioner), the answer is trivial.
    """
    pr = await client.get("/practitioners")
    if "error" in pr:
        return {"answer": None, "raw_error": pr}
    practs = pr.get("practitioners", [])
    counts = []
    for p in practs:
        appts = await client.get(
            "/individual_appointments",
            params=[("per_page", "1"), ("q[]", f"practitioner_id:={p['id']}")],
        )
        if "error" not in appts:
            counts.append({
                "id": p["id"],
                "name": f"{p.get('first_name','')} {p.get('last_name','')}".strip(),
                "appointment_count": appts.get("total_entries", 0),
            })
    counts.sort(key=lambda x: x["appointment_count"], reverse=True)
    return {"answer": counts[:10]}


async def q4_appt_type_breakdown(client: ClinikoClient) -> dict[str, Any]:
    """Q4: appointment types by frequency.

    GAP: same as Q3 — no per-type count tool. We do it via filter.
    """
    at = await client.get("/appointment_types")
    if "error" in at:
        return {"answer": None, "raw_error": at}
    types = at.get("appointment_types", [])
    breakdown = []
    for t in types:
        c = await client.get(
            "/individual_appointments",
            params=[("per_page", "1"), ("q[]", f"appointment_type_id:={t['id']}")],
        )
        if "error" not in c:
            breakdown.append({
                "id": t["id"],
                "name": t.get("name"),
                "count": c.get("total_entries", 0),
            })
    breakdown.sort(key=lambda x: x["count"], reverse=True)
    return {"answer": breakdown}


async def q5_new_vs_returning(client: ClinikoClient) -> dict[str, Any]:
    """Q5: % new vs returning patients.

    Needs to know which appointments are a patient's first one. Cliniko has
    no `is_first_appointment` flag. Could derive by checking if the patient
    has only one appointment in their history. Expensive query.
    """
    return {
        "answer": None,
        "implementation_gap": (
            "Cliniko has no direct 'first_appointment' or 'is_new_patient' field. "
            "Would need to list all patients, fetch each patient's appointment count, "
            "and label them. Slow (N+1) without a dedicated tool."
        ),
    }


async def q6_next_week_schedule(client: ClinikoClient) -> dict[str, Any]:
    """Q6: next week's bookings."""
    today = date.today()
    monday = today + timedelta(days=(7 - today.weekday()))
    sunday = monday + timedelta(days=6)
    q_params = [
        ("per_page", "100"),
        ("q[]", f"starts_at:>={monday.isoformat()}T00:00:00Z"),
        ("q[]", f"starts_at:<={sunday.isoformat()}T23:59:59Z"),
    ]
    r = await client.get("/individual_appointments", params=q_params)
    if "error" in r:
        return {"answer": None, "raw_error": r}
    appts = r.get("individual_appointments", [])
    return {
        "answer": {
            "from": monday.isoformat(),
            "to": sunday.isoformat(),
            "booked_appointments": len(appts),
            "total_in_window": r.get("total_entries"),
        }
    }


async def q7_practitioner_gaps(client: ClinikoClient) -> dict[str, Any]:
    """Q7: practitioner gaps in next 14 days.

    GAP: would need `list_available_times` called per (practitioner × business
    × appointment_type) combination — that's N×M×K calls. Awkward in chat.
    Better tool needed.
    """
    return {
        "answer": None,
        "implementation_gap": (
            "list_available_times exists but requires ALL of practitioner_id, "
            "business_id, appointment_type_id, from_date, to_date. To answer "
            "for a multi-practitioner clinic, the LLM has to fan out N×M×K calls. "
            "A `get_practitioner_gaps(from, to)` aggregator tool would be cleaner."
        ),
    }


async def q8_no_show_by_day(client: ClinikoClient) -> dict[str, Any]:
    """Q8: which day of week has highest no-show rate.

    Cliniko has `did_not_arrive` field on appointments. Need to fetch all
    recent appointments and bucket by weekday.
    """
    cutoff = (date.today() - timedelta(days=90)).isoformat()
    q_params = [
        ("per_page", "100"),
        ("q[]", f"starts_at:>={cutoff}T00:00:00Z"),
    ]
    r = await client.get("/individual_appointments", params=q_params)
    if "error" in r:
        return {"answer": None, "raw_error": r}
    appts = r.get("individual_appointments", [])
    by_day = {i: {"total": 0, "no_shows": 0} for i in range(7)}
    for a in appts:
        ts = a.get("starts_at")
        if not ts:
            continue
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            wd = dt.weekday()
            by_day[wd]["total"] += 1
            if a.get("did_not_arrive"):
                by_day[wd]["no_shows"] += 1
        except Exception:
            continue
    rates = []
    for wd, stats in by_day.items():
        day_name = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][wd]
        rate = (stats["no_shows"] / stats["total"]) if stats["total"] else 0
        rates.append({"day": day_name, "total": stats["total"], "no_shows": stats["no_shows"], "rate": round(rate, 3)})
    rates.sort(key=lambda x: x["rate"], reverse=True)
    return {"answer": rates}


async def q9_online_vs_phone(client: ClinikoClient) -> dict[str, Any]:
    """Q9: online vs phone booking split.

    GAP: Cliniko bookings (online widget) appear in /bookings; phone bookings
    are direct in /individual_appointments without a `source` field. Hard to
    distinguish cleanly without checking each appointment for an associated booking.
    """
    return {
        "answer": None,
        "implementation_gap": (
            "Cliniko has no `source=online|phone` field on appointments. "
            "Online bookings live in /bookings; phone bookings are created direct "
            "in /individual_appointments. Could infer by joining, but it's noisy."
        ),
    }


async def q10_inactive_6mo(client: ClinikoClient) -> dict[str, Any]:
    """Q10: patients who haven't been in for 6+ months.

    Fetch patient list, fetch appointments in last 180 days, diff.
    """
    cutoff = (date.today() - timedelta(days=180)).isoformat()
    # Get all patients
    all_patients = []
    page = 1
    while True:
        r = await client.get("/patients", params={"per_page": 100, "page": page})
        if "error" in r:
            return {"answer": None, "raw_error": r}
        patients = r.get("patients", [])
        all_patients.extend(patients)
        if not r.get("links", {}).get("next"):
            break
        page += 1
        if page > 5:
            break
    # Get all recent appointments
    recent_q = [("per_page", "100"), ("q[]", f"starts_at:>={cutoff}T00:00:00Z")]
    recent = await client.get("/individual_appointments", params=recent_q)
    if "error" in recent:
        return {"answer": None, "raw_error": recent}
    recent_appts = recent.get("individual_appointments", [])
    seen_patient_ids = set()
    for a in recent_appts:
        link = (a.get("patient") or {}).get("links", {}).get("self", "")
        if link:
            seen_patient_ids.add(link.rsplit("/", 1)[-1])
    inactive = [
        {"id": p["id"], "name": f"{p.get('first_name','')} {p.get('last_name','')}".strip()}
        for p in all_patients
        if p["id"] not in seen_patient_ids
    ]
    return {
        "answer": {
            "total_patients": len(all_patients),
            "active_in_window": len(seen_patient_ids),
            "inactive_6mo": len(inactive),
            "sample": inactive[:5],
        }
    }


async def q11_unfinished_courses(client: ClinikoClient) -> dict[str, Any]:
    """Q11: course-of-care recommended but not completed.

    GAP: Cliniko has no `course_of_care` field. Would need to mine treatment
    notes' free text for recommendations + cross-reference subsequent appointments.
    LLM-driven, treatment-note semantic search needed.
    """
    return {
        "answer": None,
        "implementation_gap": (
            "No structured 'course_of_care' field in Cliniko. Requires treatment-"
            "note semantic search (Phase D clinical-template work)."
        ),
    }


async def q12_recalls_30d(client: ClinikoClient) -> dict[str, Any]:
    """Q12: recalls due in next 30 days.

    Cliniko quirk: recall_at NOT filterable via q[]. Fetch all + filter client-side.
    """
    cutoff = (date.today() + timedelta(days=30)).isoformat()
    r = await client.get("/recalls", params={"per_page": 100})
    if "error" in r:
        return {"answer": None, "raw_error": r}
    recalls = r.get("recalls", [])
    due = [x for x in recalls if x.get("recall_at") and x["recall_at"] <= cutoff]
    return {
        "answer": {
            "total_due_in_30d": len(due),
            "sample": [{"id": x["id"], "recall_at": x.get("recall_at")} for x in due[:5]],
        }
    }


async def q13_return_rate(client: ClinikoClient) -> dict[str, Any]:
    """Q13: % new patients who return for a second appointment.

    Similar gap to Q5. Need per-patient appointment counts.
    """
    return {
        "answer": None,
        "implementation_gap": (
            "Same as Q5 — needs per-patient appointment counts. Could be done "
            "with N+1 calls or a dedicated 'patient_appointment_count' tool."
        ),
    }


async def q14_outstanding_balance(client: ClinikoClient) -> dict[str, Any]:
    """Q14: total outstanding invoice balance.

    Cliniko quirk: status is INTEGER (20=Paid). 'awaiting_payment' fails.
    Fetch all non-paid invoices and sum total_amount - paid (where determinable).
    """
    r = await client.get("/invoices", params={"per_page": 100})
    if "error" in r:
        return {"answer": None, "raw_error": r}
    invoices = r.get("invoices", [])
    outstanding = [i for i in invoices if i.get("status") != 20]  # 20 = Paid
    # No `balance` field on invoice. total_amount is what's billed; outstanding
    # requires payments[] sum which isn't returned in the list view. We surface
    # the limitation honestly.
    total_billed = sum(float(i.get("total_amount") or 0) for i in outstanding)
    return {
        "answer": {
            "outstanding_invoices": len(outstanding),
            "total_billed_outstanding": round(total_billed, 2),
            "all_invoices_in_account": len(invoices),
            "note": (
                "Cliniko's invoice list view doesn't expose `balance` or `payments[]`. "
                "To get true outstanding $, fetch each non-paid invoice individually."
            ),
        }
    }


async def q15_unpaid_30d(client: ClinikoClient) -> dict[str, Any]:
    """Q15: unpaid invoices over 30 days old."""
    cutoff = (date.today() - timedelta(days=30)).isoformat()
    r = await client.get("/invoices", params={"per_page": 100})
    if "error" in r:
        return {"answer": None, "raw_error": r}
    invs = r.get("invoices", [])
    # Filter client-side
    aged_unpaid = [
        i for i in invs
        if i.get("status") != 20 and (i.get("issue_date") or "9999") <= cutoff
    ]
    return {"answer": {"count": len(aged_unpaid), "sample_ids": [i["id"] for i in aged_unpaid[:5]]}}


async def q16_unissued_invoices(client: ClinikoClient) -> dict[str, Any]:
    """Q16: appointments from last week with no invoice issued.

    GAP: need to cross-reference appointments with invoices. No direct flag.
    """
    return {
        "answer": None,
        "implementation_gap": (
            "Requires joining appointments (last week) with invoices to find "
            "appointments without a corresponding invoice. Doable via N+1 but "
            "a dedicated tool would be far cleaner."
        ),
    }


async def q17_avg_dollar_per_appt_type(client: ClinikoClient) -> dict[str, Any]:
    """Q17: average $ per appointment by appointment type.

    GAP: invoices link to billable items, not appointment types directly. Complex join.
    """
    return {
        "answer": None,
        "implementation_gap": (
            "Cliniko invoices link to billable_items, not appointment_types. "
            "Would need 3-way join: appointment → invoice → billable_item → "
            "appointment_type. Worth a dedicated aggregator tool."
        ),
    }


async def q18_recent_no_shows(client: ClinikoClient) -> dict[str, Any]:
    """Q18: no-shows in last 4 weeks."""
    cutoff = (date.today() - timedelta(days=28)).isoformat()
    r = await client.get(
        "/individual_appointments",
        params=[
            ("per_page", "100"),
            ("q[]", f"starts_at:>={cutoff}T00:00:00Z"),
            ("q[]", "did_not_arrive:=true"),
        ],
    )
    if "error" in r:
        return {"answer": None, "raw_error": r}
    appts = r.get("individual_appointments", [])
    return {"answer": {"no_show_count": len(appts), "sample_ids": [a["id"] for a in appts[:5]]}}


async def q19_repeat_no_shows(client: ClinikoClient) -> dict[str, Any]:
    """Q19: patients with highest no-show frequency.

    GAP: need to aggregate no-shows per patient. Same N+1 issue.
    """
    return {
        "answer": None,
        "implementation_gap": (
            "Aggregation across patients needed. Could be done with N+1 calls "
            "(list_appointments_for_patient per patient + filter did_not_arrive) "
            "or a dedicated `patient_no_show_count` aggregator tool."
        ),
    }


async def q20_no_show_followup_draft(client: ClinikoClient) -> dict[str, Any]:
    """Q20: draft a follow-up SMS for each no-show this week.

    GAP: drafting is LLM-side. The tool gap is the SMS-send capability — we
    don't have one (Cliniko's communications endpoint reads HISTORY only).
    So this is "Claude drafts text, displays to user, user copies into Cliniko UI."
    """
    return {
        "answer": None,
        "implementation_gap": (
            "Cliniko API does not support sending communications (history-only). "
            "The draft can be produced by Claude using `list_appointments` "
            "filtered by did_not_arrive, but cannot be auto-sent — must be "
            "pasted into Cliniko UI manually."
        ),
    }


async def q21_summarise_patient_visits(client: ClinikoClient) -> dict[str, Any]:
    """Q21: summarise patient's last 5 visits in 3 bullets.

    Requires treatment notes for a patient. We have that tool.
    """
    # Pick the Cliniko trial patient (Eric Shin) which has historical data
    target_patient_id = "1897453889804840137"
    r = await client.get(
        "/treatment_notes",
        params=[
            ("per_page", "5"),
            ("q[]", f"patient_id:={target_patient_id}"),
        ],
    )
    if "error" in r:
        return {"answer": None, "raw_error": r}
    notes = r.get("treatment_notes", [])
    return {
        "answer": {
            "notes_found": len(notes),
            "patient_id": target_patient_id,
            "note": "Body content not in summary output; LLM would fetch via get_treatment_note(id) per note",
        }
    }


async def q22_unreviewed_medical_alerts(client: ClinikoClient) -> dict[str, Any]:
    """Q22: medical alerts not reviewed in 12 months.

    GAP: Cliniko's medical_alert has no `reviewed_at` or `last_reviewed` field.
    Would need to infer from note timestamps or appointment dates.
    """
    return {
        "answer": None,
        "implementation_gap": (
            "Cliniko medical_alert has no `reviewed_at` field. Best proxy is "
            "patient.last_appointment_at; alerts on patients not seen in 12mo "
            "are presumed unreviewed."
        ),
    }


async def q23_notes_keyword_search(client: ClinikoClient) -> dict[str, Any]:
    """Q23: find patients with [condition keyword] in recent notes.

    GAP: Cliniko's q[] syntax doesn't expose full-text search of treatment_note.content.
    Would need to fetch all notes and grep locally, which is slow + PHI-heavy.
    Better solution: build a local search index (Phase E+).
    """
    return {
        "answer": None,
        "implementation_gap": (
            "Cliniko's q[] doesn't support full-text on treatment_note.content. "
            "Phase E should add a local index or use Cliniko's `embedded` query "
            "if supported. Right now this requires fetching every note and "
            "grep-ing client-side — slow and PHI-heavy."
        ),
    }


async def q24_duplicate_patients(client: ClinikoClient) -> dict[str, Any]:
    """Q24: duplicate patient records (same name + DOB).

    Fetch all patients, find dupes locally.
    """
    all_patients = []
    page = 1
    while True:
        r = await client.get("/patients", params={"per_page": 100, "page": page})
        if "error" in r:
            return {"answer": None, "raw_error": r}
        all_patients.extend(r.get("patients", []))
        if not r.get("links", {}).get("next"):
            break
        page += 1
        if page > 5:
            break
    # Group by (lower(first), lower(last), dob)
    from collections import defaultdict
    groups = defaultdict(list)
    for p in all_patients:
        key = (
            (p.get("first_name") or "").lower().strip(),
            (p.get("last_name") or "").lower().strip(),
            p.get("date_of_birth"),
        )
        groups[key].append(p["id"])
    dupes = {f"{k[0]} {k[1]} ({k[2]})": v for k, v in groups.items() if len(v) > 1}
    return {
        "answer": {
            "total_patients": len(all_patients),
            "duplicate_groups": len(dupes),
            "sample": dict(list(dupes.items())[:5]),
        }
    }


async def q25_missing_contact(client: ClinikoClient) -> dict[str, Any]:
    """Q25: patients with no email or phone on file."""
    all_patients = []
    page = 1
    while True:
        r = await client.get("/patients", params={"per_page": 100, "page": page})
        if "error" in r:
            return {"answer": None, "raw_error": r}
        all_patients.extend(r.get("patients", []))
        if not r.get("links", {}).get("next"):
            break
        page += 1
        if page > 5:
            break
    missing = []
    for p in all_patients:
        email = p.get("email") or ""
        phones = p.get("patient_phone_numbers") or []
        if not email and not phones:
            missing.append({"id": p["id"], "name": f"{p.get('first_name')} {p.get('last_name')}"})
    return {
        "answer": {
            "total_patients": len(all_patients),
            "missing_contact": len(missing),
            "sample": missing[:5],
        }
    }


async def q26_appointments_without_notes(client: ClinikoClient) -> dict[str, Any]:
    """Q26: appointments with no notes attached.

    Need to cross-reference appointments with treatment_notes. Cliniko links
    notes to appointments via `appointment_id`.
    """
    # Get past appointments
    cutoff_past = (date.today() - timedelta(days=30)).isoformat()
    cutoff_today = date.today().isoformat()
    r = await client.get(
        "/individual_appointments",
        params=[
            ("per_page", "100"),
            ("q[]", f"starts_at:>={cutoff_past}T00:00:00Z"),
            ("q[]", f"starts_at:<={cutoff_today}T23:59:59Z"),
        ],
    )
    if "error" in r:
        return {"answer": None, "raw_error": r}
    appts = r.get("individual_appointments", [])
    # For each past appointment, check if notes exist
    appts_no_notes = []
    for a in appts[:30]:  # cap to 30 for speed
        notes = await client.get(
            "/treatment_notes",
            params=[("per_page", "1"), ("q[]", f"appointment_id:={a['id']}")],
        )
        if "error" not in notes and (notes.get("total_entries") or 0) == 0:
            appts_no_notes.append(a["id"])
    return {
        "answer": {
            "checked_appointments": min(30, len(appts)),
            "appointments_without_notes": len(appts_no_notes),
            "sample_ids": appts_no_notes[:5],
        }
    }


TESTS = [
    TestCase("Q1", "How many active patients do we have?", "Practice Health", q1_active_patients, ["list_patients"], "PASS"),
    TestCase("Q2", "Average appointments per week over last 3 months?", "Practice Health", q2_avg_appts_per_week_3m, ["list_appointments"], "PASS"),
    TestCase("Q3", "Top 10 practitioners by appointment count.", "Practice Health", q3_top10_practitioners, ["list_practitioners", "list_appointments"], "PARTIAL"),
    TestCase("Q4", "Appointment types breakdown by frequency.", "Practice Health", q4_appt_type_breakdown, ["appointment_types(direct API)", "list_appointments"], "PARTIAL"),
    TestCase("Q5", "% new vs returning patients.", "Practice Health", q5_new_vs_returning, [], "FAIL"),
    TestCase("Q6", "Next week's schedule — full or gaps?", "Schedule", q6_next_week_schedule, ["list_appointments"], "PASS"),
    TestCase("Q7", "Which practitioners have the most gaps in next 14 days?", "Schedule", q7_practitioner_gaps, ["list_available_times"], "PARTIAL"),
    TestCase("Q8", "Which day of week has highest no-show rate?", "Schedule", q8_no_show_by_day, ["list_appointments"], "PASS"),
    TestCase("Q9", "Online vs phone booking split?", "Schedule", q9_online_vs_phone, [], "FAIL"),
    TestCase("Q10", "Patients who haven't been in for 6+ months.", "Recalls & Retention", q10_inactive_6mo, ["list_patients", "list_appointments"], "PASS"),
    TestCase("Q11", "Course-of-care recommended but not completed.", "Recalls & Retention", q11_unfinished_courses, [], "FAIL"),
    TestCase("Q12", "Recalls due in next 30 days.", "Recalls & Retention", q12_recalls_30d, ["list_recalls_due"], "PASS"),
    TestCase("Q13", "% new patients who return for second appointment.", "Recalls & Retention", q13_return_rate, [], "FAIL"),
    TestCase("Q14", "Total outstanding invoice balance.", "Billing", q14_outstanding_balance, ["list_invoices"], "PASS"),
    TestCase("Q15", "Unpaid invoices over 30 days old.", "Billing", q15_unpaid_30d, ["list_unpaid_invoices"], "PASS"),
    TestCase("Q16", "Last week's appointments with no invoice issued.", "Billing", q16_unissued_invoices, [], "FAIL"),
    TestCase("Q17", "Average $/appointment by appointment type.", "Billing", q17_avg_dollar_per_appt_type, [], "FAIL"),
    TestCase("Q18", "No-shows in last 4 weeks.", "No-shows", q18_recent_no_shows, ["list_appointments"], "PASS"),
    TestCase("Q19", "Patients with highest no-show frequency.", "No-shows", q19_repeat_no_shows, [], "FAIL"),
    TestCase("Q20", "Draft follow-up SMS for each no-show this week.", "No-shows", q20_no_show_followup_draft, [], "PARTIAL"),
    TestCase("Q21", "Summarise patient's last 5 visits in 3 bullets.", "Clinical", q21_summarise_patient_visits, ["list_treatment_notes_for_patient", "get_treatment_note"], "PASS"),
    TestCase("Q22", "Medical alerts not reviewed in 12 months.", "Clinical", q22_unreviewed_medical_alerts, [], "FAIL"),
    TestCase("Q23", "Patients with [keyword] in recent notes.", "Clinical", q23_notes_keyword_search, [], "FAIL"),
    TestCase("Q24", "Duplicate patient records (same name+DOB).", "Operational", q24_duplicate_patients, ["list_patients"], "PASS"),
    TestCase("Q25", "Patients with no email/phone on file.", "Operational", q25_missing_contact, ["list_patients"], "PASS"),
    TestCase("Q26", "Appointments with no notes attached.", "Operational", q26_appointments_without_notes, ["list_appointments", "list_treatment_notes(per appt)"], "PASS"),
]


def score_result(tc: TestCase, t_elapsed: float) -> int:
    """Score 1-5 based on result quality."""
    r = tc.result
    if r.get("implementation_gap"):
        return 1
    if r.get("raw_error"):
        return 2
    if r.get("answer") is None:
        return 1
    if t_elapsed > 10:
        return 3
    if t_elapsed > 3:
        return 4
    return 5


async def main() -> None:
    api_key = os.environ["CLINIKO_API_KEY"]
    email = os.environ["CLINIKO_USER_AGENT_EMAIL"]
    cred = ClinikoCredential.from_env(api_key=api_key, user_agent_email=email)

    print(f"Running 26-question eval against {cred.base_url}\n")

    async with ClinikoClient(cred) as client:
        for tc in TESTS:
            print(f"[{tc.qid}] {tc.question[:70]}", end=" ... ", flush=True)
            t0 = time.time()
            try:
                tc.result = await tc.impl(client)
            except Exception as exc:
                tc.result = {"answer": None, "exception": repr(exc)}
            elapsed = time.time() - t0
            tc.elapsed = elapsed
            tc.score = score_result(tc, elapsed)
            symbol = "✅" if tc.score >= 4 else ("⚠️ " if tc.score == 3 else "❌")
            print(f"{symbol} {elapsed:.2f}s  score={tc.score}")

    # Write markdown report
    report = build_markdown_report()
    out_path = os.path.join(os.path.dirname(__file__), "eval_results.md")
    with open(out_path, "w") as f:
        f.write(report)
    print(f"\nReport written to {out_path}")


def build_markdown_report() -> str:
    lines = [
        "# 26-Question Eval Results",
        "",
        f"Run: {datetime.now().isoformat()}",
        "",
        "## Summary",
        "",
        f"- **Tests run**: {len(TESTS)}",
        f"- **PASS (score ≥4)**: {sum(1 for t in TESTS if t.score >= 4)}",
        f"- **PARTIAL (score = 3)**: {sum(1 for t in TESTS if t.score == 3)}",
        f"- **FAIL (score ≤2)**: {sum(1 for t in TESTS if t.score <= 2)}",
        "",
        "## Results by question",
        "",
        "| QID | Category | Question | Forecast | Score | Time | Status |",
        "|---|---|---|---|---|---|---|",
    ]
    for t in TESTS:
        sym = "✅" if t.score >= 4 else ("⚠️" if t.score == 3 else "❌")
        q_short = t.question[:60].replace("|", "/")
        lines.append(f"| {t.qid} | {t.category} | {q_short} | {t.coverage_forecast} | {t.score}/5 | {t.elapsed:.2f}s | {sym} |")
    lines.append("")
    lines.append("## Per-question details")
    for t in TESTS:
        lines.append("")
        lines.append(f"### {t.qid}: {t.question}")
        lines.append(f"- **Category**: {t.category}")
        lines.append(f"- **Tools used**: {', '.join(t.tools_used) or '(none — gap)'}")
        lines.append(f"- **Forecast**: {t.coverage_forecast}")
        lines.append(f"- **Actual score**: {t.score}/5  ({t.elapsed:.2f}s)")
        r = t.result
        if r.get("implementation_gap"):
            lines.append(f"- **IMPLEMENTATION GAP**: {r['implementation_gap']}")
        elif r.get("answer") is not None:
            import json
            ans_str = json.dumps(r.get("answer"), indent=2, default=str)[:800]
            lines.append("- **Answer**:")
            lines.append("```json")
            lines.append(ans_str)
            lines.append("```")
        elif r.get("raw_error"):
            lines.append(f"- **ERROR**: {r['raw_error']}")
        elif r.get("exception"):
            lines.append(f"- **EXCEPTION**: {r['exception']}")
    return "\n".join(lines)


if __name__ == "__main__":
    asyncio.run(main())
