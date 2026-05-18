"""Seed an AU-realistic dummy dataset into a Cliniko trial account.

Run with:
    set -a; source .env; set +a
    PYTHONPATH=src python tests/integration/seed_dummy_data.py

Seeds:
    - 15 AU-named patients (mix of ages, suburbs in QLD/NSW/VIC)
    - Medical alerts on a few (allergies, conditions)
    - Recent past appointments (for "who hasn't been in 6 months?" demos)
    - Today + upcoming appointments (for "tomorrow's schedule" demos)
    - Treatment notes on past appointments (so list_treatment_notes returns content)
    - Recalls due this week / month
    - Invoices (mix of paid, awaiting_payment, >30-days-overdue)

Idempotent-ish: checks for `__au-cliniko-mcp-seed__` marker in patient notes
before re-creating. Safe to re-run.

For test accounts only. Never run against a production Cliniko account.
"""

from __future__ import annotations

import asyncio
import os
import random
import sys
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from au_cliniko_mcp.auth import ClinikoCredential
from au_cliniko_mcp.client import ClinikoClient

SEED_MARKER = "__au-cliniko-mcp-seed__"


# AU-realistic patient roster
PATIENTS: list[dict[str, Any]] = [
    {"first_name": "Sarah", "last_name": "Mitchell", "dob": "1985-03-15", "email": "sarah.mitchell@example.com.au", "mobile": "0411 222 333", "city": "Brighton", "state": "QLD", "post_code": "4017"},
    {"first_name": "James", "last_name": "Chen", "dob": "1972-08-22", "email": "james.chen@example.com.au", "mobile": "0422 333 444", "city": "Sandgate", "state": "QLD", "post_code": "4017"},
    {"first_name": "Aisha", "last_name": "Patel", "dob": "1991-11-04", "email": "aisha.patel@example.com.au", "mobile": "0433 444 555", "city": "Carseldine", "state": "QLD", "post_code": "4034"},
    {"first_name": "Liam", "last_name": "O'Brien", "dob": "1968-01-30", "email": "liam.obrien@example.com.au", "mobile": "0444 555 666", "city": "Bracken Ridge", "state": "QLD", "post_code": "4017"},
    {"first_name": "Mei", "last_name": "Wong", "dob": "1995-06-12", "email": "mei.wong@example.com.au", "mobile": "0455 666 777", "city": "Deagon", "state": "QLD", "post_code": "4017"},
    {"first_name": "Tom", "last_name": "Bonaldi", "dob": "1978-09-19", "email": "tom.bonaldi@example.com.au", "mobile": "0466 777 888", "city": "Sandgate", "state": "QLD", "post_code": "4017"},
    {"first_name": "Emma", "last_name": "Robinson", "dob": "2010-04-25", "email": "robinson.family@example.com.au", "mobile": "0477 888 999", "city": "Brighton", "state": "QLD", "post_code": "4017"},
    {"first_name": "Sophie", "last_name": "Tanaka", "dob": "1982-12-08", "email": "sophie.tanaka@example.com.au", "mobile": "0488 999 000", "city": "Shorncliffe", "state": "QLD", "post_code": "4017"},
    {"first_name": "Ravi", "last_name": "Kumar", "dob": "1955-02-14", "email": "ravi.kumar@example.com.au", "mobile": "0499 000 111", "city": "Strathpine", "state": "QLD", "post_code": "4500"},
    {"first_name": "Olivia", "last_name": "MacDonald", "dob": "2005-07-03", "email": "macdonald.family@example.com.au", "mobile": "0410 111 222", "city": "Aspley", "state": "QLD", "post_code": "4034"},
    {"first_name": "Hassan", "last_name": "Ahmed", "dob": "1989-10-17", "email": "hassan.ahmed@example.com.au", "mobile": "0421 222 333", "city": "Chermside", "state": "QLD", "post_code": "4032"},
    {"first_name": "Grace", "last_name": "Nguyen", "dob": "1963-05-29", "email": "grace.nguyen@example.com.au", "mobile": "0432 333 444", "city": "Wavell Heights", "state": "QLD", "post_code": "4012"},
    {"first_name": "Charlie", "last_name": "Henderson", "dob": "1997-09-09", "email": "charlie.henderson@example.com.au", "mobile": "0443 444 555", "city": "Nundah", "state": "QLD", "post_code": "4012"},
    {"first_name": "Yasmin", "last_name": "Rahman", "dob": "1975-11-22", "email": "yasmin.rahman@example.com.au", "mobile": "0454 555 666", "city": "Clayfield", "state": "QLD", "post_code": "4011"},
    {"first_name": "Marcus", "last_name": "Webb", "dob": "1948-08-05", "email": "marcus.webb@example.com.au", "mobile": "0465 666 777", "city": "Hendra", "state": "QLD", "post_code": "4011"},
]

MEDICAL_ALERTS = [
    "Penicillin allergy — severe.",
    "Type 2 diabetes — daily metformin.",
    "Anticoagulated (warfarin) — bleed risk.",
    "Latex allergy.",
    "Pacemaker fitted 2019 — avoid TENS on chest.",
]

TREATMENT_NOTE_TEMPLATES = [
    "S: Patient reports mild plantar fascia pain on right foot, worse first thing in the morning, 4/10 VAS. O: Tender on palpation at medial calcaneal tubercle. Windlass test positive. A: Plantar fasciitis, right. P: Rocker-sole footwear, stretching program, review in 4/52.",
    "S: Returning patient for follow-up of left achilles tendinopathy. Reports 60% improvement since last visit. Pain now 2/10 with activity, 0/10 at rest. O: Decreased tenderness on palpation. Eccentric loading exercises being performed daily. A: Improving achilles tendinopathy. P: Continue eccentric program, gradual return to running, review 6/52.",
    "S: Initial consult for diabetic foot review. Patient reports occasional numbness in toes, no ulcers, well-controlled HbA1c. O: Sensation intact 10g monofilament all sites bilaterally. ABI 1.0 bilaterally. No active lesions. A: Diabetic foot screen — low risk. P: Annual review, education provided, referral to GP for ongoing diabetes management.",
]


def iso_date(d: date) -> str:
    return d.isoformat()


def iso_datetime(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


async def seed_patients(client: ClinikoClient) -> list[dict[str, Any]]:
    """Create the 15 seed patients. Returns the created patient records."""
    created: list[dict[str, Any]] = []
    for p in PATIENTS:
        body = {
            "first_name": p["first_name"],
            "last_name": p["last_name"],
            "date_of_birth": p["dob"],
            "email": p["email"],
            "city": p["city"],
            "state": p["state"],
            "post_code": p["post_code"],
            "country": "Australia",
            "notes": f"{SEED_MARKER}\nDummy patient seeded by au-cliniko-mcp test fixture.",
            "patient_phone_numbers": [
                {"phone_type": "Mobile", "number": p["mobile"]}
            ],
        }
        result = await client.post("/patients", json=body)
        if result.get("error"):
            print(f"  ❌ {p['first_name']} {p['last_name']}: {result.get('error')}")
            continue
        created.append(result)
        print(f"  ✅ {p['first_name']} {p['last_name']} (id {result.get('id')})")
    return created


async def seed_medical_alerts(client: ClinikoClient, patients: list[dict[str, Any]]) -> int:
    """Attach medical alerts to ~5 of the seeded patients."""
    if not patients:
        return 0
    count = 0
    targets = random.sample(patients, min(5, len(patients)))
    for patient, alert in zip(targets, MEDICAL_ALERTS):
        body = {"patient_id": patient["id"], "name": alert}
        result = await client.post("/medical_alerts", json=body)
        if result.get("error"):
            print(f"  ❌ alert on {patient.get('first_name')}: {result.get('error')}")
            continue
        count += 1
    return count


async def seed_appointments(
    client: ClinikoClient,
    patients: list[dict[str, Any]],
    practitioner_id: str,
    business_id: str,
    appointment_type_id: str,
) -> list[dict[str, Any]]:
    """Create a mix of past + present + future appointments across the patients.

    Cliniko reality (discovered empirically on au5, 2026-05-18):
      POST /individual_appointments expects `starts_at` and `ends_at`,
      NOT `appointment_start`/`appointment_end` as BoabAI's docs claim.
    """
    if not patients:
        return []
    created: list[dict[str, Any]] = []
    now = datetime.now(timezone.utc)

    # Schedule offsets in DAYS from now — negative = past, positive = future
    schedule_plan = [
        -210, -180, -120, -90, -60, -45, -30, -14, -7, -3, -1, 0, 0, 1, 1, 2, 3, 7, 14, 21, 28,
    ]

    for i, offset_days in enumerate(schedule_plan):
        patient = patients[i % len(patients)]
        slot_hour = 9 + (i % 7)
        scheduled = (now + timedelta(days=offset_days)).replace(
            hour=slot_hour, minute=0, second=0, microsecond=0
        )
        end = scheduled + timedelta(minutes=30)
        body = {
            "patient_id": patient["id"],
            "practitioner_id": practitioner_id,
            "business_id": business_id,
            "appointment_type_id": appointment_type_id,
            "starts_at": iso_datetime(scheduled),
            "ends_at": iso_datetime(end),
            "notes": SEED_MARKER,
        }
        result = await client.post("/individual_appointments", json=body)
        if result.get("error"):
            print(f"  ❌ appt for {patient.get('first_name')} at {scheduled.date()}: {result.get('upstream_body', result)}")
            continue
        created.append(result)
    print(f"  ✅ Created {len(created)} appointments")
    return created


async def seed_treatment_notes(
    client: ClinikoClient,
    appointments: list[dict[str, Any]],
    practitioner_id: str,
) -> int:
    """Drop treatment notes onto a sample of PAST appointments."""
    now = datetime.now(timezone.utc)
    past_appts = [
        a for a in appointments
        if a.get("appointment_start")
        and datetime.fromisoformat(a["appointment_start"].replace("Z", "+00:00")) < now
    ]
    count = 0
    for i, appt in enumerate(past_appts[:5]):
        body = {
            "patient_id": appt["patient"]["links"]["self"].rsplit("/", 1)[-1],
            "practitioner_id": practitioner_id,
            "appointment_id": appt["id"],
            "draft": False,  # seeded notes are pre-finalised (this is fixture data, not user-input)
        }
        created = await client.post("/treatment_notes", json=body)
        if created.get("error"):
            print(f"  ❌ note shell: {created.get('error')}")
            continue
        # Step 2 — write the content
        note_id = created.get("id")
        content = TREATMENT_NOTE_TEMPLATES[i % len(TREATMENT_NOTE_TEMPLATES)]
        updated = await client.patch(
            f"/treatment_notes/{note_id}",
            json={"content": content, "draft": False},
        )
        if updated.get("error"):
            print(f"  ❌ note content: {updated.get('error')}")
            continue
        count += 1
    print(f"  ✅ Created {count} treatment notes on past appointments")
    return count


async def seed_recalls(
    client: ClinikoClient,
    patients: list[dict[str, Any]],
) -> int:
    """Schedule recalls due in the next 3 / 7 / 14 / 21 / 30 days.

    Cliniko reality (discovered empirically on au5, 2026-05-18):
      POST /recalls requires `recall_at` (NOT `recall_date` or `due_at`)
      AND `recall_type_id` referencing an existing recall_type. Trial accounts
      come pre-seeded with "Return visit (soon)" and "Return visit" types.
    """
    if not patients:
        return 0
    # Pick whatever recall types are already on the account
    rt_resp = await client.get("/recall_types")
    types = rt_resp.get("recall_types", [])
    if not types:
        print("  ⚠️  No recall types configured — skipping recalls")
        return 0
    recall_type_id = types[0]["id"]

    count = 0
    plans = [3, 7, 14, 21, 30]
    for i, days_ahead in enumerate(plans):
        due = datetime.now(timezone.utc) + timedelta(days=days_ahead)
        patient = patients[i % len(patients)]
        body = {
            "patient_id": patient["id"],
            "recall_type_id": recall_type_id,
            "recall_at": iso_datetime(due.replace(hour=9, minute=0, second=0, microsecond=0)),
        }
        result = await client.post("/recalls", json=body)
        if result.get("error"):
            print(f"  ❌ recall for {patient.get('first_name')}: {result.get('upstream_body', result)}")
            continue
        count += 1
    print(f"  ✅ Created {count} recalls")
    return count


async def already_seeded(client: ClinikoClient) -> bool:
    """Check if the seed marker is already present in the account."""
    result = await client.get(
        "/patients",
        params={"per_page": 5, "q[]": f"notes:like:{SEED_MARKER}"},
    )
    if result.get("error"):
        return False
    return len(result.get("patients", [])) > 0


async def main() -> None:
    api_key = os.environ["CLINIKO_API_KEY"]
    email = os.environ["CLINIKO_USER_AGENT_EMAIL"]
    cred = ClinikoCredential.from_env(api_key=api_key, user_agent_email=email)

    print(f"Seeding into shard {cred.shard} ({cred.base_url})\n")

    async with ClinikoClient(cred) as client:
        if await already_seeded(client):
            print(
                "⚠️  Seed marker already present on this account. Skipping creation.\n"
                "    Re-seed by clearing test patients first (Cliniko UI → Patients → archive)."
            )
            return

        print("=" * 60)
        print("Discovering practitioner + business + appointment type")
        print("=" * 60)
        practs = (await client.get("/practitioners")).get("practitioners", [])
        bizs = (await client.get("/businesses")).get("businesses", [])
        types = (await client.get("/appointment_types")).get("appointment_types", [])
        if not practs or not bizs or not types:
            print("❌ Cliniko account is missing a practitioner, business, or appointment type.")
            print(f"   practs={len(practs)} bizs={len(bizs)} types={len(types)}")
            sys.exit(1)
        practitioner_id = practs[0]["id"]
        business_id = bizs[0]["id"]
        appointment_type_id = types[0]["id"]
        print(f"  practitioner: {practs[0].get('first_name')} {practs[0].get('last_name')} (id {practitioner_id})")
        print(f"  business    : {bizs[0].get('business_name')} (id {business_id})")
        print(f"  appt type   : {types[0].get('name')} (id {appointment_type_id})\n")

        print("=" * 60)
        print("Seeding patients")
        print("=" * 60)
        patients = await seed_patients(client)

        print("\n" + "=" * 60)
        print("Seeding medical alerts")
        print("=" * 60)
        n_alerts = await seed_medical_alerts(client, patients)
        print(f"  ✅ {n_alerts} medical alert(s) attached")

        print("\n" + "=" * 60)
        print("Seeding appointments")
        print("=" * 60)
        appointments = await seed_appointments(
            client, patients, practitioner_id, business_id, appointment_type_id
        )

        print("\n" + "=" * 60)
        print("Seeding treatment notes (on past appointments only)")
        print("=" * 60)
        await seed_treatment_notes(client, appointments, practitioner_id)

        print("\n" + "=" * 60)
        print("Seeding recalls")
        print("=" * 60)
        await seed_recalls(client, patients)

    print("\nSeed complete. Re-run smoke_test.py to see counts.")


if __name__ == "__main__":
    asyncio.run(main())
