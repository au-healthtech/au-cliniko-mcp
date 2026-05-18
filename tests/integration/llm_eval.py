"""LLM-in-loop eval: Claude reasons over our MCP tools against live Cliniko.

Differs from the programmatic eval suite in that this version uses the real
Anthropic API to send each test question to Claude, lets Claude pick + chain
tools, and scores how well it does.

Run:
    export ANTHROPIC_API_KEY=$(cat ~/.anthropic-api-key)
    set -a; source .env; set +a
    PYTHONPATH=src python tests/integration/llm_eval.py

Uses Haiku 4.5 to keep cost minimal (~$0.50-1 for all 26 questions).
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

import anthropic

from au_cliniko_mcp.auth import ClinikoCredential
from au_cliniko_mcp.client import ClinikoClient


# Subset of MCP tool schemas — the ones a Claude session would actually need
# for the 26 consulting questions. These mirror what FastMCP would expose;
# we re-declare them here for the eval since we can't trivially introspect
# without running the full MCP server in subprocess.
TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "list_patients",
        "description": (
            "List patients on the Cliniko account. Use for 'who are my patients', "
            "'show me everyone', or as the first step before drilling into a "
            "specific patient. Returns total_entries (use as patient count) + a "
            "summary of each patient. Patient IDs are 19-digit strings."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "page": {"type": "integer", "default": 1},
                "per_page": {"type": "integer", "default": 25},
            },
        },
    },
    {
        "name": "search_patients_by_name",
        "description": (
            "Search patients by partial name match (first or last). Use when the "
            "user mentions a patient by name (e.g. 'find Jane Smith' or 'look up Bonaldi'). "
            "Returns matching patients with id, name, DOB, contact."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
    {
        "name": "list_practitioners",
        "description": (
            "List active practitioners on the account. Use for 'who works here', "
            "'show me practitioners', or as context before any question that "
            "references a clinician by name. Returns id + name + active status."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"include_inactive": {"type": "boolean", "default": False}},
        },
    },
    {
        "name": "list_businesses",
        "description": "List practice locations (Cliniko 'businesses'). Use for multi-site practices.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "list_appointments",
        "description": (
            "List individual appointments, optionally filtered by date range, "
            "practitioner, or business. Use for 'what's tomorrow's schedule', "
            "'show me last week's appointments', 'who's coming in today'. "
            "Dates are ISO YYYY-MM-DD. PHI: links to patient records."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "from_date": {"type": "string"},
                "to_date": {"type": "string"},
                "practitioner_id": {"type": "string"},
                "business_id": {"type": "string"},
                "per_page": {"type": "integer", "default": 50},
            },
        },
    },
    {
        "name": "list_appointments_for_patient",
        "description": (
            "List all appointments for a SPECIFIC patient by id. Faster + smaller "
            "than the practice-wide list when the question is per-patient ('Eric "
            "Shin's history', 'has Jane been in recently'). "
            "Use this NOT list_appointments for per-patient questions."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "patient_id": {"type": "string"},
                "from_date": {"type": "string"},
                "to_date": {"type": "string"},
            },
            "required": ["patient_id"],
        },
    },
    {
        "name": "get_patient_appointment_stats",
        "description": (
            "Aggregate counts + dates for ONE patient: count_total, count_completed, "
            "count_no_show, count_cancelled, first_appointment_at, last_appointment_at, "
            "no_show_rate. Use this when you need a SUMMARY of a patient's history "
            "rather than the full appointment list. Use this for 'is this patient "
            "new or returning', 'when did Jane last come in'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "patient_id": {"type": "string"},
                "since_date": {"type": "string"},
            },
            "required": ["patient_id"],
        },
    },
    {
        "name": "get_practitioner_schedule_overview",
        "description": (
            "Aggregate booked-slot counts per practitioner over a date range. Use for "
            "'which practitioner is busiest' or 'who has the most gaps'. Returns "
            "per-practitioner booked_appointments + ranking_busiest_first. Date args "
            "are ISO YYYY-MM-DD."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "from_date": {"type": "string"},
                "to_date": {"type": "string"},
            },
            "required": ["from_date", "to_date"],
        },
    },
    {
        "name": "get_appointment_invoice_join",
        "description": (
            "Cross-reference appointments with invoices to find capture rate, "
            "average $/appt by type, and unbilled appointment ids. Use for "
            "'which appointments don't have invoices', 'what's our average revenue "
            "by appointment type', billing-completeness audits."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "from_date": {"type": "string"},
                "to_date": {"type": "string"},
            },
            "required": ["from_date", "to_date"],
        },
    },
    {
        "name": "list_invoices",
        "description": (
            "List invoices, optionally filtered by issue_date range or status. "
            "Cliniko invoice status is an INTEGER code (20 = Paid; other codes "
            "TBC). Pass the int directly or the string 'paid'. Use for 'what was "
            "invoiced this week', 'show me paid invoices'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "from_date": {"type": "string"},
                "to_date": {"type": "string"},
                "status": {"description": "Integer code (20=Paid) or 'paid'"},
            },
        },
    },
    {
        "name": "list_unpaid_invoices",
        "description": (
            "Outstanding invoices over N days old. Use for 'who owes us money', "
            "'unpaid invoices over 30 days'. Returns count + sample ids. Note: "
            "Cliniko's list view doesn't expose true outstanding balance — fetch "
            "individual invoices for that."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"over_days": {"type": "integer", "default": 30}},
        },
    },
    {
        "name": "list_recalls_due",
        "description": (
            "Recalls due within next N days. Use for 'who's due for a recall this "
            "week/month'. Note: Cliniko's recall_at is NOT q[]-filterable, so this "
            "fetches all recalls and filters client-side."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"within_days": {"type": "integer", "default": 30}},
        },
    },
    {
        "name": "list_treatment_notes_for_patient",
        "description": (
            "List a patient's treatment notes (METADATA ONLY — clinical content not in "
            "summary). Use after identifying a patient. To READ note bodies, call "
            "get_treatment_note per id. PHI: high sensitivity."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"patient_id": {"type": "string"}, "per_page": {"type": "integer", "default": 25}},
            "required": ["patient_id"],
        },
    },
    {
        "name": "get_treatment_note",
        "description": (
            "Get one treatment note's FULL record including clinical body. Use after "
            "list_treatment_notes_for_patient. PHI: highest sensitivity."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"treatment_note_id": {"type": "string"}},
            "required": ["treatment_note_id"],
        },
    },
]


# --- Tool implementations (re-using the same Cliniko client calls) ---

async def call_tool(name: str, args: dict, client: ClinikoClient) -> Any:
    """Dispatch a Claude tool_use call to the corresponding Cliniko query."""
    if name == "list_patients":
        return await client.get("/patients", params={
            "page": args.get("page", 1),
            "per_page": args.get("per_page", 25),
        })
    if name == "search_patients_by_name":
        q = args["query"].strip().split()
        params: list[tuple[str, str]] = [("per_page", "25")]
        for part in q:
            params.append(("q[]", f"first_name:like:{part}"))
            params.append(("q[]", f"last_name:like:{part}"))
        return await client.get("/patients", params=params)
    if name == "list_practitioners":
        r = await client.get("/practitioners", params={"per_page": 100})
        if not args.get("include_inactive") and "practitioners" in r:
            r["practitioners"] = [p for p in r["practitioners"] if p.get("active")]
        return r
    if name == "list_businesses":
        return await client.get("/businesses", params={"per_page": 100})
    if name == "list_appointments":
        q_params: list[tuple[str, str]] = [("per_page", str(args.get("per_page", 50)))]
        if args.get("from_date"):
            q_params.append(("q[]", f"starts_at:>={args['from_date']}T00:00:00Z"))
        if args.get("to_date"):
            q_params.append(("q[]", f"starts_at:<={args['to_date']}T23:59:59Z"))
        if args.get("practitioner_id"):
            q_params.append(("q[]", f"practitioner_id:={args['practitioner_id']}"))
        if args.get("business_id"):
            q_params.append(("q[]", f"business_id:={args['business_id']}"))
        return await client.get("/individual_appointments", params=q_params)
    if name == "list_appointments_for_patient":
        q_params = [("per_page", "100"), ("q[]", f"patient_id:={args['patient_id']}")]
        if args.get("from_date"):
            q_params.append(("q[]", f"starts_at:>={args['from_date']}T00:00:00Z"))
        if args.get("to_date"):
            q_params.append(("q[]", f"starts_at:<={args['to_date']}T23:59:59Z"))
        return await client.get("/individual_appointments", params=q_params)
    if name == "get_patient_appointment_stats":
        params = [("per_page", "100"), ("q[]", f"patient_id:={args['patient_id']}")]
        if args.get("since_date"):
            params.append(("q[]", f"starts_at:>={args['since_date']}T00:00:00Z"))
        r = await client.get("/individual_appointments", params=params)
        if "error" in r:
            return r
        appts = r.get("individual_appointments", [])
        total = len(appts)
        no_show = sum(1 for a in appts if a.get("did_not_arrive"))
        cancelled = sum(1 for a in appts if a.get("cancelled_at"))
        starts = sorted([a.get("starts_at") for a in appts if a.get("starts_at")])
        return {
            "patient_id": args["patient_id"],
            "count_total": total,
            "count_completed": total - no_show - cancelled,
            "count_no_show": no_show,
            "count_cancelled": cancelled,
            "first_appointment_at": starts[0] if starts else None,
            "last_appointment_at": starts[-1] if starts else None,
            "no_show_rate": round(no_show / total, 3) if total else 0.0,
        }
    if name == "get_practitioner_schedule_overview":
        pr = await client.get("/practitioners", params={"per_page": 100})
        practs = [p for p in pr.get("practitioners", []) if p.get("active")]
        rows = []
        for p in practs:
            ar = await client.get("/individual_appointments", params=[
                ("per_page", "1"),
                ("q[]", f"practitioner_id:={p['id']}"),
                ("q[]", f"starts_at:>={args['from_date']}T00:00:00Z"),
                ("q[]", f"starts_at:<={args['to_date']}T23:59:59Z"),
            ])
            rows.append({
                "id": p["id"],
                "name": f"{p.get('first_name','')} {p.get('last_name','')}".strip(),
                "booked_appointments": ar.get("total_entries", 0) if "error" not in ar else 0,
            })
        rows.sort(key=lambda x: x["booked_appointments"], reverse=True)
        return {"from": args["from_date"], "to": args["to_date"], "practitioners": rows}
    if name == "get_appointment_invoice_join":
        ar = await client.get("/individual_appointments", params=[
            ("per_page", "100"),
            ("q[]", f"starts_at:>={args['from_date']}T00:00:00Z"),
            ("q[]", f"starts_at:<={args['to_date']}T23:59:59Z"),
        ])
        appts = ar.get("individual_appointments", [])
        inv_from = (date.fromisoformat(args["from_date"]) - timedelta(days=7)).isoformat()
        inv_to = (date.fromisoformat(args["to_date"]) + timedelta(days=14)).isoformat()
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
        unbilled = [a["id"] for a in appts if a["id"] not in appt_to_inv]
        return {
            "appointments_in_range": len(appts),
            "appointments_with_invoice": len(appts) - len(unbilled),
            "appointments_without_invoice": len(unbilled),
            "capture_rate": round((len(appts) - len(unbilled)) / len(appts), 3) if appts else 0.0,
            "unbilled_appointment_ids": unbilled[:20],
        }
    if name == "list_invoices":
        q_params = [("per_page", "100")]
        if args.get("from_date"):
            q_params.append(("q[]", f"issue_date:>={args['from_date']}"))
        if args.get("to_date"):
            q_params.append(("q[]", f"issue_date:<={args['to_date']}"))
        if args.get("status") is not None:
            s = args["status"]
            if isinstance(s, str) and s.lower() == "paid":
                s = 20
            q_params.append(("q[]", f"status:={s}"))
        return await client.get("/invoices", params=q_params)
    if name == "list_unpaid_invoices":
        days = args.get("over_days", 30)
        cutoff = (date.today() - timedelta(days=days)).isoformat()
        r = await client.get("/invoices", params={"per_page": 100})
        invs = r.get("invoices", []) if "error" not in r else []
        aged = [i for i in invs if i.get("status") != 20 and (i.get("issue_date") or "9999") <= cutoff]
        return {"count": len(aged), "sample_ids": [i["id"] for i in aged[:5]]}
    if name == "list_recalls_due":
        days = args.get("within_days", 30)
        cutoff = (date.today() + timedelta(days=days)).isoformat()
        r = await client.get("/recalls", params={"per_page": 100})
        recalls = r.get("recalls", []) if "error" not in r else []
        due = [x for x in recalls if x.get("recall_at") and x["recall_at"] <= cutoff]
        return {"count_due": len(due), "sample": [{"id": x["id"], "recall_at": x.get("recall_at")} for x in due[:5]]}
    if name == "list_treatment_notes_for_patient":
        return await client.get("/treatment_notes", params=[
            ("per_page", str(args.get("per_page", 25))),
            ("q[]", f"patient_id:={args['patient_id']}"),
        ])
    if name == "get_treatment_note":
        return await client.get(f"/treatment_notes/{args['treatment_note_id']}")
    return {"error": "unknown_tool", "tool_name": name}


QUESTIONS = [
    ("Q1", "How many active patients does this clinic have?"),
    ("Q2", "What was our average appointments per week over the last 3 months?"),
    ("Q3", "Who are the top 10 practitioners by appointment count?"),
    ("Q4", "What's the breakdown of appointment types by frequency?"),
    ("Q5", "What percentage of patients are new vs returning? Sample the first 10 patients."),
    ("Q6", "What does next week's schedule look like — fully booked or gaps?"),
    ("Q7", "Which practitioners have the most gaps in the next 14 days?"),
    ("Q8", "Which day of the week has the highest no-show rate? Use the last 90 days."),
    ("Q9", "How many appointments came through online booking vs phone?"),
    ("Q10", "List all patients who haven't been in for 6+ months."),
    ("Q11", "List patients who had a course of care recommended but didn't complete it."),
    ("Q12", "Which patients have a recall due in the next 30 days?"),
    ("Q13", "What percentage of patients return for a second appointment? Sample 10 patients."),
    ("Q14", "What's our total outstanding invoice balance right now?"),
    ("Q15", "List unpaid invoices over 30 days old."),
    ("Q16", "Last week's appointments with no invoice issued — find them."),
    ("Q17", "What's the average dollar amount per appointment by appointment type?"),
    ("Q18", "List all no-shows from the last 4 weeks."),
    ("Q19", "Which patients have the highest no-show frequency? Sample 30 patients."),
    ("Q20", "Draft a follow-up SMS for each no-show this week."),
    ("Q21", "Summarise patient Eric Shin's last 5 visits in 3 bullets each. His id is 1897453889804840137."),
    ("Q22", "Which patients have an open medical alert that hasn't been reviewed in 12 months?"),
    ("Q23", "Find patients with 'plantar' mentioned in their recent treatment notes."),
    ("Q24", "Find duplicate patient records (same name + DOB)."),
    ("Q25", "Find patients with no email or phone on file."),
    ("Q26", "Find appointments in the last 30 days with no notes attached."),
]


SYSTEM_PROMPT = (
    "You are a Cliniko data analyst connected to a real Australian allied-health clinic's "
    "practice management system via the au-cliniko-mcp tools. Today's date is "
    f"{date.today().isoformat()}. The clinic is on Cliniko shard au5 (test sandbox). "
    "Use the tools to answer each question precisely. Cliniko IDs are 19-digit strings — never coerce to int. "
    "If a question CAN'T be answered with current tools, say so and explain why succinctly. "
    "Give a final answer in plain English at the end of your turn. Keep each answer under 200 words."
)


async def run_one(api_client: anthropic.Anthropic, cliniko: ClinikoClient, qid: str, question: str) -> dict[str, Any]:
    """Run a single question through Claude API with tool-loop."""
    print(f"\n=== {qid}: {question[:80]} ===", flush=True)
    messages: list[dict[str, Any]] = [{"role": "user", "content": question}]
    tool_calls: list[str] = []
    final_text: str = ""
    t0 = time.time()
    total_input = 0
    total_output = 0

    for hop in range(8):  # max 8 tool hops
        try:
            resp = api_client.messages.create(
                model="claude-haiku-4-5",
                max_tokens=2000,
                system=SYSTEM_PROMPT,
                tools=TOOL_SCHEMAS,
                messages=messages,
            )
        except Exception as exc:
            return {"qid": qid, "question": question, "error": repr(exc), "elapsed": time.time() - t0}

        total_input += resp.usage.input_tokens
        total_output += resp.usage.output_tokens

        # Capture text + tool calls
        tool_uses = []
        for block in resp.content:
            if block.type == "tool_use":
                tool_uses.append(block)
                tool_calls.append(block.name)
                print(f"  → tool: {block.name}({json.dumps(block.input)[:80]})", flush=True)
            elif block.type == "text":
                final_text = block.text

        if resp.stop_reason == "tool_use" and tool_uses:
            # Execute every tool call and feed results back
            messages.append({"role": "assistant", "content": resp.content})
            results = []
            for tu in tool_uses:
                try:
                    out = await call_tool(tu.name, tu.input, cliniko)
                    out_str = json.dumps(out, default=str)[:8000]
                except Exception as exc:
                    out_str = json.dumps({"error": "tool_exception", "msg": repr(exc)})
                results.append({"type": "tool_result", "tool_use_id": tu.id, "content": out_str})
            messages.append({"role": "user", "content": results})
            continue

        # No more tools — Claude is done
        break

    elapsed = time.time() - t0
    print(f"  ✓ {elapsed:.1f}s | tools={len(tool_calls)} | tokens={total_input}+{total_output}", flush=True)
    return {
        "qid": qid,
        "question": question,
        "tools_called": tool_calls,
        "tool_call_count": len(tool_calls),
        "final_answer": final_text[:1000],
        "elapsed": round(elapsed, 1),
        "input_tokens": total_input,
        "output_tokens": total_output,
    }


async def main() -> None:
    api_key = open(os.path.expanduser("~/.anthropic-api-key")).read().strip()
    api = anthropic.Anthropic(api_key=api_key)

    cred = ClinikoCredential.from_env(
        api_key=os.environ["CLINIKO_API_KEY"],
        user_agent_email=os.environ["CLINIKO_USER_AGENT_EMAIL"],
    )

    async with ClinikoClient(cred) as cliniko:
        results = []
        for qid, q in QUESTIONS:
            r = await run_one(api, cliniko, qid, q)
            results.append(r)

    # Summary
    total_in = sum(r.get("input_tokens", 0) for r in results)
    total_out = sum(r.get("output_tokens", 0) for r in results)
    haiku_cost = (total_in * 0.80 / 1_000_000) + (total_out * 4.00 / 1_000_000)
    avg_tools = sum(r.get("tool_call_count", 0) for r in results) / len(results)
    answered = sum(1 for r in results if r.get("final_answer") and "can't" not in (r.get("final_answer","").lower()))

    print()
    print(f"=== Summary ===")
    print(f"Questions:     {len(results)}")
    print(f"Answered:      {answered}/{len(results)}")
    print(f"Avg tools/Q:   {avg_tools:.1f}")
    print(f"Total tokens:  in={total_in:,}  out={total_out:,}")
    print(f"Haiku cost:    ~${haiku_cost:.3f}")

    # Persist
    out_path = os.path.join(os.path.dirname(__file__), "llm_eval_results.json")
    with open(out_path, "w") as f:
        json.dump({
            "model": "claude-haiku-4-5",
            "run_at": datetime.now().isoformat(),
            "total_input_tokens": total_in,
            "total_output_tokens": total_out,
            "estimated_cost_usd": round(haiku_cost, 3),
            "questions_answered": answered,
            "results": results,
        }, f, indent=2, default=str)
    print(f"Results saved to {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
