"""Seed a mid-sized AU podiatry practice's worth of demo data into the Cliniko sandbox.

Goal: make prospect demos of `au-cliniko-mcp` look like a real practice's account, not a
toy account. The existing `seed_dummy_data.py` script created ~15 patients + a handful
of appointments — enough to verify the tools wire up, but not enough to demonstrate
list-pagination, dedup, capture-rate, recall-funnel, or no-show patterns.

This script ADDS to whatever is already on the account (does not wipe). Existing seed
patients are detected via the `__au-cliniko-mcp-seed__` marker; this script uses its
own marker `__au-cliniko-mcp-fullseed__` so the two cohorts can be told apart.

Run with:
    set -a; source .env; set +a
    PYTHONPATH=src python tests/integration/seed_full_practice.py

What it creates (target shape — actuals printed at end):

    Billable items catalogue (10-15 items)
        Private:  Standard ($90), Initial ($130), Long ($180),
                  Nail surgery ($450), Orthotic prescription ($550)
        MBS:      10962 CDM ($61.80 rebate),  81360 ATSI ($61.80 rebate)
        DVA:      F004 / F012 / F033 / F221 (current Jan-2026 fees)
        NDIS:     Podiatry hourly $188.99 (current 2025-26 NDIS price guide)

    Patients (500)
        Distribution across 11 AU-realistic chief complaints (plantar fasciitis,
        diabetic foot, fungal nails, etc.). Postcodes weighted to north-Brisbane
        catchment. 60/40 female/male. Age distribution biased to each condition's
        natural demographic (paeds 5-15, sports 25-45, chronic 50-80). 3-5 duplicate
        name+DOB pairs for the dedup demo. 10% missing email, 5% missing phone.

    Appointments (~2000)
        6 months of history + 3 months of forward bookings. ~10% past appointments
        marked `did_not_arrive`, ~5% `cancelled_at`. Practice hours Mon-Fri 8a-5p,
        Sat 9a-1p.

    Treatment notes (~70% of past appointments)
        SOAP-format content tailored per condition. ~30% deliberately left blank
        for the data-hygiene audit demo.

    Recalls (~150)
        Spread across the next 90 days. Clinical reason in note text.

    NOT created (Cliniko API limitation — see docs/API-LIMITATIONS.md):
        Invoices. POST /invoices returns 404 on au5; Cliniko's REST API does not
        expose invoice creation. Invoices must be drafted in the Cliniko UI. The
        billable-items catalogue is still seeded so the UI dropdown is populated.

Rate-limit policy:
    Cliniko enforces 200 req/min/user. We pace at ~3 req/sec (sleep 0.35s between
    calls) plus the client's built-in 429 backoff. A full run is ~30-45 minutes.

Idempotency:
    Each created patient carries `__au-cliniko-mcp-fullseed__` in `notes`. If the
    script detects ANY patient with that marker, it skips patient creation (you
    can clean by archiving + searching `notes:like:__au-cliniko-mcp-fullseed__`
    in the Cliniko UI). Re-running is cheap if the script is interrupted.

For test accounts only. Never run against a production Cliniko account.
"""

from __future__ import annotations

import asyncio
import os
import random
import sys
from datetime import date, datetime, timedelta, timezone
from typing import Any

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from au_cliniko_mcp.auth import ClinikoCredential
from au_cliniko_mcp.client import ClinikoClient

# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------
SEED_MARKER = "__au-cliniko-mcp-fullseed__"
TARGET_PATIENT_COUNT = 500
DUPLICATE_PAIR_COUNT = 4  # name+DOB duplicates for the dedup demo
API_PACE_SECONDS = 0.35  # ~3 req/sec average — safely below 200/min
PROGRESS_PATIENT_EVERY = 25
PROGRESS_APPT_EVERY = 100
PROGRESS_NOTE_EVERY = 50
RANDOM_SEED = 20260518  # deterministic data set for reproducibility

random.seed(RANDOM_SEED)

# ---------------------------------------------------------------------------
# Catalogue: billable items to seed
# ---------------------------------------------------------------------------
#
# Sources:
#   MBS:  https://www9.health.gov.au/mbs/fullDisplay.cfm  (verified May 2026)
#         10962 = podiatry CDM, $72.65 schedule fee, $61.80 (85%) rebate
#   DVA:  https://www.dva.gov.au/.../podiatristfees-1-jan-2026.pdf
#         F004 = initial rooms,   F012 = subsequent rooms,
#         F033 = subsequent home, F221 = orthoses pair
#   NDIS: https://www.ndis.gov.au/.../pricing-arrangements (2025-26)
#         podiatry hourly limit $188.99 (was $193.99 to 30 Jun 2025)
#   Private rates: Australian Podiatry Association median range, May 2026.
#
BILLABLE_CATALOGUE: list[dict[str, str]] = [
    # Private (cash / receipt-only) services
    {"name": "Standard Consultation (private)", "item_code": "STD-30", "price": "90.00"},
    {"name": "Initial Consultation (private)", "item_code": "INI-45", "price": "130.00"},
    {"name": "Long Consultation – Biomechanical (private)", "item_code": "LONG-60", "price": "180.00"},
    {"name": "Nail Surgery – Partial Nail Avulsion + PNA", "item_code": "NAIL-SURG", "price": "450.00"},
    {"name": "Custom Orthotic Prescription + Dispense", "item_code": "ORTH-RX", "price": "550.00"},
    # Medicare — Chronic Disease Management
    {"name": "MBS 10962 – Podiatry CDM (≥20 min)", "item_code": "MBS-10962", "price": "61.80"},
    # Medicare — Aboriginal & Torres Strait Islander allied health
    {"name": "MBS 81360 – Podiatry ATSI follow-up", "item_code": "MBS-81360", "price": "61.80"},
    # DVA — current Jan 2026 schedule
    {"name": "DVA F004 – Initial Consultation (rooms)", "item_code": "DVA-F004", "price": "94.60"},
    {"name": "DVA F012 – Subsequent Consultation (rooms)", "item_code": "DVA-F012", "price": "94.60"},
    {"name": "DVA F033 – Subsequent Consultation (home)", "item_code": "DVA-F033", "price": "94.60"},
    {"name": "DVA F221 – Custom-made orthoses (pair)", "item_code": "DVA-F221", "price": "650.00"},
    # NDIS — 2025-26 price guide
    {"name": "NDIS Podiatry – Standard hourly", "item_code": "NDIS-POD-HR", "price": "188.99"},
    # Workcover / TAC catch-all
    {"name": "Workcover / TAC consultation", "item_code": "WC-STD", "price": "120.00"},
]

# ---------------------------------------------------------------------------
# Demographics for synthetic patients
# ---------------------------------------------------------------------------
FIRST_NAMES_FEMALE = [
    "Sarah", "Emma", "Olivia", "Sophie", "Grace", "Charlotte", "Amelia", "Mia", "Isla",
    "Ava", "Ella", "Aisha", "Yasmin", "Mei", "Lucia", "Sofia", "Chiara", "Bianca",
    "Maria", "Sienna", "Zara", "Priya", "Anika", "Leila", "Fatima", "Layla", "Hannah",
    "Ruby", "Chloe", "Maya", "Tahnee", "Indi", "Willow", "Daisy", "Poppy", "Ivy",
    "Hazel", "Audrey", "Eve", "Iris", "Nina", "Lara", "Mila", "Olive", "Pearl",
    "Maeve", "Aria", "Eloise", "Frankie", "Harper",
]
FIRST_NAMES_MALE = [
    "James", "Liam", "Marcus", "Tom", "Hassan", "Charlie", "Ravi", "Oliver", "Jack",
    "Noah", "Lucas", "Leo", "Henry", "George", "Ethan", "Hugo", "Oscar", "William",
    "Alexander", "Sebastian", "Mateo", "Diego", "Luca", "Marco", "Antonio", "Giuseppe",
    "Hiroshi", "Daichi", "Kenji", "Akira", "Wei", "Jian", "Min", "Sanjay", "Arjun",
    "Vikram", "Ahmad", "Omar", "Yusuf", "Khaled", "Cooper", "Hunter", "Levi", "Mason",
    "Riley", "Sam", "Ben", "Jonah", "Zac", "Felix",
]
SURNAMES = [
    "Smith", "Jones", "Williams", "Brown", "Wilson", "Taylor", "Johnson", "White",
    "Martin", "Anderson", "Thompson", "Nguyen", "Tran", "Pham", "Le", "Singh", "Patel",
    "Kumar", "Sharma", "Mehta", "Chen", "Lin", "Liu", "Zhang", "Wong", "Tanaka",
    "Yamamoto", "Sato", "Kim", "Park", "Cho", "Mitchell", "Robinson", "Walker", "Wright",
    "MacDonald", "O'Brien", "O'Sullivan", "Murphy", "Kelly", "Bonaldi", "Russo", "Conti",
    "Ricci", "Esposito", "Romano", "Ahmed", "Hassan", "Rahman", "Khan", "Ali",
    "Henderson", "Webb", "Hughes", "Edwards", "Collins", "Stewart", "Morris", "Rogers",
    "Hill", "Bennett", "Cox", "Reed", "Carter", "Howard", "Ward", "Phillips", "Evans",
    "Parker", "Bell", "Murray", "Cooper", "Fischer", "Schmidt", "Wagner", "Kowalski",
    "Papadopoulos", "Christofides", "Manning", "Andersen", "Larsen",
]
POSTCODES = [
    # North-Brisbane catchment with suburb context
    ("4017", "Brighton", "QLD"),
    ("4017", "Sandgate", "QLD"),
    ("4017", "Bracken Ridge", "QLD"),
    ("4017", "Deagon", "QLD"),
    ("4017", "Shorncliffe", "QLD"),
    ("4034", "Carseldine", "QLD"),
    ("4034", "Aspley", "QLD"),
    ("4500", "Strathpine", "QLD"),
    ("4500", "Bray Park", "QLD"),
    ("4032", "Chermside", "QLD"),
    ("4030", "Stafford", "QLD"),
    ("4030", "Kedron", "QLD"),
    ("4012", "Nundah", "QLD"),
    ("4012", "Wavell Heights", "QLD"),
    ("4011", "Clayfield", "QLD"),
    ("4011", "Hendra", "QLD"),
    ("4053", "Everton Park", "QLD"),
    ("4053", "Stafford Heights", "QLD"),
]

# ---------------------------------------------------------------------------
# Condition profiles (drives visit cadence, age, notes content)
# ---------------------------------------------------------------------------
#
# Each condition has:
#   share          fraction of total patient cohort (must roughly sum to 1.0)
#   visits_range   (min, max) past-visit count
#   age_range      (min, max) patient age
#   cadence_weeks  typical inter-visit gap, weeks
#   bill_mix       per-visit billable_item code probabilities
#   note_templates SOAP-format treatment-note bodies
#
CONDITIONS: list[dict[str, Any]] = [
    {
        "key": "plantar_fasciitis",
        "label": "Plantar fasciitis",
        "share": 0.16,
        "visits_range": (3, 6),
        "age_range": (35, 70),
        "cadence_weeks": (2, 4),
        "bill_mix": [("INI-45", 0.25), ("STD-30", 0.55), ("LONG-60", 0.05), ("MBS-10962", 0.10), ("DVA-F012", 0.05)],
        "note_templates": [
            "S: Plantar fascia pain right foot, worse on first steps after rest, 6/10 VAS. Aggravated by standing >1hr at work.\nO: Tenderness on palpation at medial calcaneal tubercle. Positive windlass test. Reduced ankle dorsiflexion (5 deg).\nA: Right plantar fasciitis — chronic, mechanically driven.\nP: Issued rocker-sole footwear. Demonstrated calf + plantar fascia stretching program 3x daily. Cushioned heel inserts dispensed. Review 4/52.",
            "S: Follow-up plantar fasciitis. Reports ~40% improvement since last visit. Pain now 3/10, mostly first-step pain only.\nO: Reduced tenderness on palpation. Compliance with stretching program reported good. No new symptoms.\nA: Improving plantar fasciitis, right.\nP: Continue stretching program. Trial of low-Dye taping for 2/52 for high-load activity days. Review in 4/52.",
            "S: Recurrent plantar fasciitis flare after return to running. Pain 7/10 on running, 2/10 walking.\nO: Tender medial calcaneal tubercle. Stiffness in gastrocnemius bilaterally.\nA: Acute-on-chronic plantar fasciitis. Likely overload from training spike.\nP: Reduce running load 50% for 2/52. Daily eccentric calf raises. Consider custom orthotic if no improvement at next review. Booked for 3/52.",
        ],
    },
    {
        "key": "heel_pain_general",
        "label": "Heel pain (general)",
        "share": 0.12,
        "visits_range": (3, 5),
        "age_range": (30, 65),
        "cadence_weeks": (2, 4),
        "bill_mix": [("INI-45", 0.30), ("STD-30", 0.60), ("MBS-10962", 0.10)],
        "note_templates": [
            "S: Posterior heel pain, gradual onset, no acute injury. Pain 4/10 with weight-bearing first thing AM.\nO: Tender on palpation posterior heel + calcaneal insertion. No swelling or erythema.\nA: Insertional achilles tendinopathy. R/O Haglund's deformity — none visible.\nP: Heel raise inserts both shoes. Calf stretches 3x daily. Footwear review — patient to bring current shoes next visit. Review 3/52.",
            "S: Heel pain bilateral, both lateral. Worse with court sports (tennis). Pain 5/10 during activity.\nO: Tender at calcaneocuboid joint bilaterally. No swelling. Subtalar ROM full.\nA: Subtalar joint irritation, mechanical.\nP: Lateral wedge insert trial 4/52. Modified return-to-sport program. Review at end of trial.",
        ],
    },
    {
        "key": "diabetic_foot",
        "label": "Diabetic foot screen + care",
        "share": 0.14,
        "visits_range": (4, 8),
        "age_range": (50, 80),
        "cadence_weeks": (8, 12),
        "bill_mix": [("MBS-10962", 0.55), ("STD-30", 0.20), ("DVA-F012", 0.20), ("MBS-81360", 0.05)],
        "note_templates": [
            "S: Annual diabetic foot screen. T2DM x 12y, metformin only. No reported numbness, no ulcers, no infections.\nO: 10g monofilament intact all 10 sites bilaterally. Pedal pulses palpable both feet. ABI 1.05 R / 1.02 L. No active lesions. Skin in good condition.\nA: Diabetic foot — low risk category. Sensation + perfusion preserved.\nP: Annual review. Patient educated re: daily foot self-checks, footwear, prompt review of any wound. Letter to GP.",
            "S: Diabetic patient with intermittent claudication right calf, ~200m walking. T2DM, HbA1c 8.1%.\nO: Diminished posterior tibial pulse right. ABI 0.78 R, 0.95 L. Sensation reduced 4/10 sites L hallux. Dry callus apex L hallux.\nA: Diabetic foot — moderate risk. Vascular insufficiency right. Sensory loss L forefoot.\nP: Refer GP for vascular review. Callus reduced. Cushioning insert L. 8-week review.",
            "S: Routine 8-weekly diabetic foot care. No new symptoms. Compliance with self-checks reported good.\nO: Multiple sites of dry callus 1st + 5th MTP joints bilaterally. Callus reduced atraumatically with scalpel. Nails trimmed. No ulceration.\nA: Stable diabetic foot — moderate risk. Maintenance care.\nP: 8/52 review. Continue daily emollient (urea 10%). Annual neurovascular assessment due next visit.",
        ],
    },
    {
        "key": "fungal_nails",
        "label": "Fungal nails (onychomycosis)",
        "share": 0.10,
        "visits_range": (4, 6),
        "age_range": (40, 75),
        "cadence_weeks": (6, 10),
        "bill_mix": [("INI-45", 0.20), ("STD-30", 0.70), ("DVA-F012", 0.10)],
        "note_templates": [
            "S: Discoloured + thickened toenails bilateral, gradual progression over 2+ years. No pain. Cosmetic concern.\nO: Yellow-brown discoloration 1st-3rd nails bilaterally. Onycholysis 1st nail R. Thickness 2.5mm. No paronychia.\nA: Onychomycosis bilateral, moderate-severe. Clinical diagnosis.\nP: Nail thinning + debridement performed. Topical Loceryl 5% nail lacquer weekly applications. Education re: footwear hygiene + shared spaces. Review 12/52.",
            "S: Fungal nail review. Compliance with Loceryl ~80%. Subjective improvement reported.\nO: Reduced discoloration 1st nails bilateral. Onycholysis healing. No new nail involvement.\nA: Onychomycosis — responding to topical therapy.\nP: Continue Loceryl. Reassess at 6/12 mark. Maintenance debridement today.",
        ],
    },
    {
        "key": "ingrown_toenails",
        "label": "Ingrown toenails",
        "share": 0.08,
        "visits_range": (1, 3),
        "age_range": (15, 50),
        "cadence_weeks": (1, 2),
        "bill_mix": [("INI-45", 0.40), ("NAIL-SURG", 0.40), ("STD-30", 0.20)],
        "note_templates": [
            "S: Painful ingrown right hallux nail, recurrent. Has had episodes for 18+ months. Pain 7/10 on pressure.\nO: Erythema + mild purulent discharge medial sulcus R hallux. No granulation tissue. Surrounding skin intact.\nA: Chronic onychocryptosis R hallux, medial border. Mild paronychia.\nP: Discussed conservative vs surgical options. Patient opting for PNA + matrix phenolisation. Consent obtained. Procedure booked next week.",
            "S: 1-week post nail surgery (PNA + phenolisation R hallux medial). Mild discomfort, decreasing.\nO: Wound healing well. No active discharge. Suture-free. Patient compliant with saline soaks.\nA: Uncomplicated post-op recovery.\nP: Continue saline soaks twice daily. Return if signs of infection. Routine review 4/52.",
        ],
    },
    {
        "key": "callus_corn_maintenance",
        "label": "Callus / corn maintenance",
        "share": 0.09,
        "visits_range": (4, 8),
        "age_range": (55, 85),
        "cadence_weeks": (6, 10),
        "bill_mix": [("DVA-F012", 0.50), ("STD-30", 0.30), ("MBS-10962", 0.15), ("DVA-F033", 0.05)],
        "note_templates": [
            "S: 8-weekly routine care. Recurrent painful corn 5th MTP joint R foot.\nO: IPK 5th MTPJ R, ~6mm diameter. Multiple sites diffuse callus plantar forefoot bilaterally. No ulceration.\nA: Mechanical hyperkeratosis. Pressure focal at 5th MTPJ R.\nP: Sharp debridement performed atraumatically. Pressure-redistribution insert dispensed. Footwear advice. 8/52 review.",
            "S: Maintenance visit. Patient reports comfort improved with last orthotic adjustment.\nO: General hyperkeratotic skin reduced compared to last visit. No new lesions. Nails neat.\nA: Stable. Maintenance care effective.\nP: Routine debridement. Continue 8/52 review schedule.",
        ],
    },
    {
        "key": "biomechanical_orthotics",
        "label": "Biomechanical / orthotics",
        "share": 0.10,
        "visits_range": (3, 5),
        "age_range": (25, 60),
        "cadence_weeks": (2, 6),
        "bill_mix": [("LONG-60", 0.40), ("ORTH-RX", 0.20), ("STD-30", 0.30), ("DVA-F221", 0.10)],
        "note_templates": [
            "S: Bilateral medial knee pain on running, 3+ km. Patient describes 'feet rolling in'. Otherwise active.\nO: Bilateral pes planus, weight-bearing. Excess STJ pronation in midstance. Tibial internal rotation. Mild Q-angle excess. Hip ER weakness 4/5.\nA: Excess pronation contributing to medial knee load.\nP: Long biomechanical assessment performed. Hip strength program issued. Trial of off-the-shelf orthotic 4/52, custom if no improvement.",
            "S: 4/52 orthotic trial review. Marked improvement in knee symptoms. Now running 5km pain-free.\nO: With orthotic in place, midstance pronation visibly reduced. Tibial rotation normalised. No new symptoms.\nA: Good response to OTC orthotic. Patient wishes to proceed to custom device for longevity.\nP: Custom orthotic prescription today. Cast taken. Dispense in 2-3 weeks.",
        ],
    },
    {
        "key": "achilles_tendinopathy",
        "label": "Achilles tendinopathy",
        "share": 0.08,
        "visits_range": (4, 8),
        "age_range": (30, 60),
        "cadence_weeks": (3, 5),
        "bill_mix": [("INI-45", 0.20), ("STD-30", 0.60), ("LONG-60", 0.10), ("MBS-10962", 0.10)],
        "note_templates": [
            "S: Right achilles pain, mid-portion, 6 weeks. Worse on hills + first steps AM. Pain 5/10 with running.\nO: Tender mid-substance right achilles ~4cm from insertion. No nodularity. Calf strength 4/5. Single-leg heel raise reproduces symptom.\nA: Mid-portion achilles tendinopathy R.\nP: Alfredson eccentric loading program issued — 3 sets x 15 reps, 2x daily, 12 weeks. Heel raises in both shoes. Activity modification. Review 6/52.",
            "S: 6/52 follow-up achilles tendinopathy. Compliance with eccentrics good (~90%). Pain 2/10 on running, 0 at rest.\nO: Tenderness markedly reduced. Single-leg heel raise pain-free at 15 reps. Calf strength 5/5.\nA: Resolving achilles tendinopathy. Good response to loading program.\nP: Continue eccentrics for remaining 6 weeks. Gradual return to full running volume. Review at week 12.",
        ],
    },
    {
        "key": "sports_injuries",
        "label": "Sports injuries",
        "share": 0.07,
        "visits_range": (4, 6),
        "age_range": (18, 45),
        "cadence_weeks": (2, 3),
        "bill_mix": [("INI-45", 0.30), ("STD-30", 0.50), ("LONG-60", 0.10), ("WC-STD", 0.10)],
        "note_templates": [
            "S: Lateral ankle pain post inversion injury during touch footy, 5 days ago. Initial swelling settled. Pain 4/10 weight-bearing.\nO: Mild residual swelling lateral ankle. Tender ATFL. No CFL tenderness. Anterior drawer mild laxity but firm endpoint. Squeeze test negative.\nA: Grade I-II lateral ankle sprain, R.\nP: PEACE+LOVE protocol explained. Compression sleeve. Progressive return-to-play program. Single-leg balance starting today. Review 2/52.",
            "S: 4/52 post ankle sprain. Returned to non-contact training. Pain 0/10 at rest, 2/10 cutting.\nO: Full ROM. Strength 5/5. Single-leg hop 90% of contralateral. Y-balance mild deficit posterolateral.\nA: Late-stage ankle rehab.\nP: Continue agility ladder + sport-specific drills. Cleared for full training in 2/52 pending hop symmetry >95%.",
        ],
    },
    {
        "key": "pediatric",
        "label": "Pediatric (in-toeing, etc.)",
        "share": 0.03,
        "visits_range": (1, 2),
        "age_range": (4, 14),
        "cadence_weeks": (8, 16),
        "bill_mix": [("INI-45", 0.50), ("STD-30", 0.40), ("LONG-60", 0.10)],
        "note_templates": [
            "S: 6yo presenting with in-toeing noted by parents + teacher. Trips occasionally. No pain. Otherwise developmentally normal.\nO: Bilateral femoral anteversion mild. Tibial torsion within normal limits. Foot progression angle -5 deg bilaterally. Full ROM. Strength age-appropriate.\nA: Femoral anteversion — physiological. Improving with age expected.\nP: Reassurance to parents. No active treatment required. Review at 12 months if no improvement.",
            "S: 9yo with bilateral heel pain post sport. Pain after football training, settles overnight.\nO: Tender bilateral calcaneal apophyses. Tight gastroc-soleus complex. Sever's positive bilaterally.\nA: Sever's disease, bilateral (calcaneal apophysitis).\nP: Heel raises both shoes. Stretching program for parents to supervise. Activity not restricted unless severe. Review 6/52.",
        ],
    },
    {
        "key": "general_other",
        "label": "General / other",
        "share": 0.03,
        "visits_range": (1, 3),
        "age_range": (20, 80),
        "cadence_weeks": (2, 6),
        "bill_mix": [("INI-45", 0.40), ("STD-30", 0.50), ("DVA-F012", 0.10)],
        "note_templates": [
            "S: General foot check, no specific complaint. Reports occasional fatigue post long shifts (nurse).\nO: No structural abnormality. Skin + nails good condition. Footwear appropriate. Mild postural fatigue signs.\nA: No clinical pathology. Occupational fatigue.\nP: Footwear advice. Compression sock trial. PRN review.",
        ],
    },
]

MEDICAL_ALERTS = [
    "Penicillin allergy — severe.",
    "Type 2 diabetes — daily metformin.",
    "Anticoagulated (warfarin) — bleed risk.",
    "Latex allergy.",
    "Pacemaker fitted 2019 — avoid TENS on chest.",
    "Severe asthma — carries Ventolin.",
    "Peripheral arterial disease — left limb.",
    "Recent total knee replacement (8 weeks).",
    "MRSA-positive nasal carrier.",
    "Rheumatoid arthritis on methotrexate.",
    "Pregnant (T2, due Sep).",
    "Lidocaine adverse reaction — use bupivacaine.",
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def iso_date(d: date) -> str:
    return d.isoformat()


def iso_datetime(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


async def pace() -> None:
    await asyncio.sleep(API_PACE_SECONDS)


def random_dob_for_age(min_age: int, max_age: int) -> str:
    age = random.randint(min_age, max_age)
    today = date.today()
    # Pick a random date within the year-of-birth band
    year = today.year - age
    month = random.randint(1, 12)
    day = random.randint(1, 28)
    return date(year, month, day).isoformat()


def random_mobile() -> str:
    """AU mobile in 04XX XXX XXX format."""
    return f"04{random.randint(10, 99):02d} {random.randint(100, 999):03d} {random.randint(100, 999):03d}"


def pick_with_weight(choices: list[tuple[str, float]]) -> str:
    """Weighted random pick of (code, weight) pairs. Weights need not sum to 1."""
    items, weights = zip(*choices, strict=True)
    return random.choices(items, weights=weights, k=1)[0]


# ---------------------------------------------------------------------------
# Discovery: account state + lookup tables
# ---------------------------------------------------------------------------


async def discover_account_context(client: ClinikoClient) -> dict[str, Any]:
    """Read the existing config and validate the prerequisites."""
    practs = (await client.get("/practitioners")).get("practitioners", [])
    await pace()
    bizs = (await client.get("/businesses")).get("businesses", [])
    await pace()
    types = (await client.get("/appointment_types")).get("appointment_types", [])
    await pace()
    rtypes = (await client.get("/recall_types")).get("recall_types", [])
    await pace()
    items = (await client.get("/billable_items", params={"per_page": 100})).get("billable_items", [])
    await pace()

    if not practs or not bizs or not types or not rtypes:
        raise RuntimeError(
            f"Cliniko account missing prerequisites. practs={len(practs)} "
            f"bizs={len(bizs)} appt_types={len(types)} recall_types={len(rtypes)}"
        )
    return {
        "practitioner_id": practs[0]["id"],
        "business_id": bizs[0]["id"],
        "standard_appt_type_id": next(
            (t["id"] for t in types if "standard" in t["name"].lower()),
            types[0]["id"],
        ),
        "initial_appt_type_id": next(
            (t["id"] for t in types if "first" in t["name"].lower() or "initial" in t["name"].lower()),
            types[0]["id"],
        ),
        "recall_type_default_id": rtypes[0]["id"],
        "existing_items_by_code": {
            (it.get("item_code") or it.get("name", "")).strip(): it for it in items
        },
    }


# ---------------------------------------------------------------------------
# Seeding: billable items
# ---------------------------------------------------------------------------


async def seed_billable_items(
    client: ClinikoClient, existing_by_code: dict[str, Any]
) -> dict[str, str]:
    """Create the billable-item catalogue. Returns code -> billable_item_id map."""
    code_to_id: dict[str, str] = {}
    for entry in BILLABLE_CATALOGUE:
        code = entry["item_code"]
        if code in existing_by_code:
            code_to_id[code] = existing_by_code[code]["id"]
            print(f"  · {code:14}  exists (id {code_to_id[code]})")
            continue
        body = {
            "name": entry["name"],
            "item_code": code,
            "price": entry["price"],
            "item_type": "Service",
        }
        r = await client.post("/billable_items", json=body)
        await pace()
        if r.get("error") or not r.get("id"):
            print(f"  ✗ {code}: {r.get('upstream_body', r.get('error'))}")
            continue
        code_to_id[code] = r["id"]
        print(f"  + {code:14}  {entry['name'][:50]:50}  ${entry['price']}")
    return code_to_id


# ---------------------------------------------------------------------------
# Seeding: patients
# ---------------------------------------------------------------------------


def assign_condition() -> dict[str, Any]:
    """Pick a condition profile weighted by its `share`."""
    weights = [c["share"] for c in CONDITIONS]
    return random.choices(CONDITIONS, weights=weights, k=1)[0]


def build_patient_record(condition: dict[str, Any], duplicate_of: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build a synthetic patient dict ready to POST."""
    if duplicate_of:
        # Same name+DOB but different demographics (the dedup demo)
        first = duplicate_of["first_name"]
        last = duplicate_of["last_name"]
        dob = duplicate_of["date_of_birth"]
        sex_pool = duplicate_of["_pool_was"]
    else:
        # 60/40 female/male
        sex_pool = FIRST_NAMES_FEMALE if random.random() < 0.60 else FIRST_NAMES_MALE
        first = random.choice(sex_pool)
        last = random.choice(SURNAMES)
        dob = random_dob_for_age(*condition["age_range"])

    postcode, city, state = random.choice(POSTCODES)
    # Data-hygiene demos: 10% missing email, 5% missing phone
    email = (
        f"{first.lower()}.{last.lower().replace(chr(39), '').replace(' ', '')}@example.com.au"
        if random.random() > 0.10
        else None
    )
    phone = random_mobile() if random.random() > 0.05 else None

    rec = {
        "first_name": first,
        "last_name": last,
        "date_of_birth": dob,
        "city": city,
        "state": state,
        "post_code": postcode,
        "country": "Australia",
        "notes": f"{SEED_MARKER}\nChief complaint: {condition['label']}.",
        "_pool_was": sex_pool,
        "_condition": condition["key"],
    }
    if email:
        rec["email"] = email
    if phone:
        rec["patient_phone_numbers"] = [{"phone_type": "Mobile", "number": phone}]
    return rec


async def seed_patients(client: ClinikoClient) -> list[dict[str, Any]]:
    """Create 500 patients distributed across the 11 condition profiles."""
    created: list[dict[str, Any]] = []
    pending: list[dict[str, Any]] = []
    duplicate_seeds: list[dict[str, Any]] = []

    # Build the in-memory cohort first (deterministic)
    for _ in range(TARGET_PATIENT_COUNT):
        cond = assign_condition()
        pending.append(build_patient_record(cond))

    # Convert a few to duplicates of earlier ones for the dedup demo
    if len(pending) >= DUPLICATE_PAIR_COUNT * 2:
        indices = random.sample(range(len(pending)), DUPLICATE_PAIR_COUNT)
        for idx in indices:
            original = pending[idx]
            dupe_cond = assign_condition()
            dupe = build_patient_record(dupe_cond, duplicate_of=original)
            dupe["_condition"] = original["_condition"]  # keep the same complaint
            pending.append(dupe)
            duplicate_seeds.append(dupe)

    print(f"  Planned cohort: {len(pending)} patients "
          f"({DUPLICATE_PAIR_COUNT} of them duplicates for the dedup demo)")

    for i, rec in enumerate(pending, start=1):
        body = {k: v for k, v in rec.items() if not k.startswith("_")}
        r = await client.post("/patients", json=body)
        await pace()
        if r.get("error") or not r.get("id"):
            print(f"  ✗ {rec['first_name']} {rec['last_name']}: "
                  f"{r.get('upstream_body', r.get('error'))}")
            continue
        # Stash the condition + record back on the API result so downstream seeding can use it
        r["_condition"] = rec["_condition"]
        created.append(r)
        if i % PROGRESS_PATIENT_EVERY == 0:
            print(f"  · {i}/{len(pending)} patients created")
    print(f"  ✅ {len(created)} patients created")
    return created


# ---------------------------------------------------------------------------
# Seeding: medical alerts (~10% of cohort)
# ---------------------------------------------------------------------------


async def seed_medical_alerts(client: ClinikoClient, patients: list[dict[str, Any]]) -> int:
    if not patients:
        return 0
    target_count = max(20, int(len(patients) * 0.10))
    targets = random.sample(patients, min(target_count, len(patients)))
    count = 0
    for p in targets:
        alert = random.choice(MEDICAL_ALERTS)
        body = {"patient_id": p["id"], "name": alert}
        r = await client.post("/medical_alerts", json=body)
        await pace()
        if r.get("error"):
            continue
        count += 1
    print(f"  ✅ {count} medical alerts attached")
    return count


# ---------------------------------------------------------------------------
# Seeding: appointments
# ---------------------------------------------------------------------------
#
# Each patient gets a clinic-realistic appointment trail:
#   * pick the condition profile
#   * choose total past visits + future scheduled visits
#   * walk backwards from "now" at the cadence_weeks gap for past visits
#   * walk forwards from "now" at the cadence_weeks gap for future visits
#   * ~10% of past visits flipped to did_not_arrive
#   * ~5% of past visits flipped to cancelled_at
#
# Cohort-time-band balancing (per spec):
#   25 patients have visits only in the last 30 days (new)
#   200 are active (last 6 months, 3-8 visits)
#   150 in maintenance (last 6 months + scheduled forward)
#   100 discharged (last visit 3-6 months ago — recall candidates)
#   25 long-term inactive (>6 months ago)


def practice_hour_slot(day: date) -> datetime:
    """Pick a realistic start-time on a practice day.
    Mon-Fri 8:00-16:30 (last appt 16:30), Sat 9:00-12:30 (last appt 12:30)."""
    wd = day.weekday()  # 0=Mon..6=Sun
    if wd == 6:  # Sun — push to Mon
        day = day + timedelta(days=1)
        wd = 0
    if wd == 5:  # Sat
        hour = random.randint(9, 12)
        minute = random.choice([0, 30])
    else:
        hour = random.randint(8, 16)
        minute = random.choice([0, 15, 30, 45])
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=timezone.utc) - timedelta(hours=10)


def build_appointment_plan(
    patient: dict[str, Any], cohort: str
) -> tuple[list[datetime], list[datetime]]:
    """Return (past_starts, future_starts) for a patient given their cohort band."""
    cond = next(c for c in CONDITIONS if c["key"] == patient["_condition"])
    cadence_min, cadence_max = cond["cadence_weeks"]
    visits_min, visits_max = cond["visits_range"]
    today = date.today()

    past_starts: list[datetime] = []
    future_starts: list[datetime] = []

    if cohort == "new":
        # Last 30 days, 1-2 visits
        n = random.randint(1, 2)
        anchor = today - timedelta(days=random.randint(1, 28))
        for i in range(n):
            d = anchor - timedelta(weeks=i * random.randint(cadence_min, cadence_max))
            past_starts.append(practice_hour_slot(d))
    elif cohort == "active":
        # Last 6 months, 3-8 visits + maybe 1-2 future scheduled
        n_past = random.randint(max(3, visits_min), max(3, visits_max))
        anchor = today - timedelta(days=random.randint(0, 14))
        for i in range(n_past):
            gap_weeks = random.randint(cadence_min, cadence_max)
            d = anchor - timedelta(weeks=i * gap_weeks)
            if (today - d).days > 180:  # cap at 6 months
                break
            past_starts.append(practice_hour_slot(d))
        # 60% of active patients have at least one future booking
        if random.random() < 0.60:
            n_fut = random.randint(1, 2)
            for i in range(n_fut):
                d = today + timedelta(weeks=(i + 1) * random.randint(cadence_min, cadence_max))
                if (d - today).days > 90:
                    break
                future_starts.append(practice_hour_slot(d))
    elif cohort == "maintenance":
        # 2-4 past visits over last 6 mo, 1-2 future booked
        n_past = random.randint(2, 4)
        anchor = today - timedelta(days=random.randint(30, 90))
        for i in range(n_past):
            d = anchor - timedelta(weeks=i * random.randint(cadence_min, cadence_max))
            if (today - d).days > 180:
                break
            past_starts.append(practice_hour_slot(d))
        n_fut = random.randint(1, 2)
        for i in range(n_fut):
            d = today + timedelta(weeks=(i + 1) * random.randint(cadence_min, cadence_max))
            if (d - today).days > 90:
                break
            future_starts.append(practice_hour_slot(d))
    elif cohort == "discharged":
        # Last visit 3-6 months ago, 2-4 past visits trailing back, no future
        last_visit_days_ago = random.randint(90, 180)
        anchor = today - timedelta(days=last_visit_days_ago)
        n_past = random.randint(2, 4)
        for i in range(n_past):
            d = anchor - timedelta(weeks=i * random.randint(cadence_min, cadence_max))
            past_starts.append(practice_hour_slot(d))
    elif cohort == "inactive":
        # Last visit >6 months ago, 1-3 visits then nothing
        last_visit_days_ago = random.randint(190, 365)
        anchor = today - timedelta(days=last_visit_days_ago)
        n_past = random.randint(1, 3)
        for i in range(n_past):
            d = anchor - timedelta(weeks=i * random.randint(cadence_min, cadence_max))
            past_starts.append(practice_hour_slot(d))

    return past_starts, future_starts


def assign_cohorts(patients: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Allocate patients into the 5 cohort time-bands per the spec ratios."""
    total = len(patients)
    # Spec ratios: 25/200/150/100/25 of 500 → 5/40/30/20/5%
    ratios = {
        "new": 0.05,
        "active": 0.40,
        "maintenance": 0.30,
        "discharged": 0.20,
        "inactive": 0.05,
    }
    pool = list(patients)
    random.shuffle(pool)
    buckets: dict[str, list[dict[str, Any]]] = {}
    cursor = 0
    for k, frac in ratios.items():
        n = int(round(total * frac))
        buckets[k] = pool[cursor:cursor + n]
        cursor += n
    # Any leftover into active
    if cursor < total:
        buckets["active"].extend(pool[cursor:])
    return buckets


async def seed_appointments(
    client: ClinikoClient,
    patients: list[dict[str, Any]],
    ctx: dict[str, Any],
) -> list[dict[str, Any]]:
    """Create the full appointment trail across all 500 patients."""
    cohorts = assign_cohorts(patients)
    cohort_summary = ", ".join(f"{k}={len(v)}" for k, v in cohorts.items())
    print(f"  Cohort allocation: {cohort_summary}")

    all_appts: list[dict[str, Any]] = []
    plan_count = 0
    dna_target = 0
    cancel_target = 0

    # Build the full schedule plan first
    work_items: list[tuple[dict[str, Any], datetime, bool]] = []  # (patient, start, is_past)
    for cohort_name, plist in cohorts.items():
        for p in plist:
            past_list, future_list = build_appointment_plan(p, cohort_name)
            plan_count += len(past_list) + len(future_list)
            for s in past_list:
                work_items.append((p, s, True))
            for s in future_list:
                work_items.append((p, s, False))

    print(f"  Planned {len(work_items)} appointments across {len(patients)} patients")
    random.shuffle(work_items)  # so progress prints look natural

    now = datetime.now(timezone.utc)
    for i, (patient, start, is_past) in enumerate(work_items, start=1):
        # First-ever past appt for that patient → initial; everything else standard
        # (cheap heuristic — counts past appts already created for this patient)
        appt_type_id = ctx["standard_appt_type_id"]
        # 35-45 min for initial vs 30 for standard
        end = start + timedelta(minutes=30)
        body = {
            "patient_id": patient["id"],
            "practitioner_id": ctx["practitioner_id"],
            "business_id": ctx["business_id"],
            "appointment_type_id": appt_type_id,
            "starts_at": iso_datetime(start),
            "ends_at": iso_datetime(end),
            "notes": f"{SEED_MARKER}",
        }
        r = await client.post("/individual_appointments", json=body)
        await pace()
        if r.get("error") or not r.get("id"):
            err_body = r.get("upstream_body", r.get("error"))
            # Tame the noise — print first 200 chars only
            print(f"  ✗ appt {patient['first_name']} {patient['last_name']} @ {start.date()}: {str(err_body)[:200]}")
            continue
        r["_patient_id"] = patient["id"]
        r["_condition"] = patient["_condition"]
        r["_is_past"] = is_past
        all_appts.append(r)

        # Mutate ~10% of past appts to did_not_arrive, ~5% to cancelled
        if is_past:
            roll = random.random()
            if roll < 0.10:
                upd = await client.patch(
                    f"/individual_appointments/{r['id']}",
                    json={"did_not_arrive": True},
                )
                await pace()
                if not upd.get("error"):
                    dna_target += 1
            elif roll < 0.15:
                # Try with cancellation_reason first (Cliniko UI expects 1-7); fall back if 4xx
                upd = await client.patch(
                    f"/individual_appointments/{r['id']}",
                    json={
                        "cancelled_at": iso_datetime(now),
                        "cancellation_reason": random.randint(1, 6),
                        "cancellation_note": random.choice([
                            "Patient unwell — rescheduled.",
                            "Patient called to cancel.",
                            "Family emergency.",
                            "Work commitment.",
                        ]),
                    },
                )
                await pace()
                if not upd.get("error"):
                    cancel_target += 1

        if i % PROGRESS_APPT_EVERY == 0:
            print(f"  · {i}/{len(work_items)} appointments created")

    print(f"  ✅ {len(all_appts)} appointments created  "
          f"({dna_target} did_not_arrive, {cancel_target} cancelled)")
    return all_appts


# ---------------------------------------------------------------------------
# Seeding: treatment notes
# ---------------------------------------------------------------------------


def _soap_text_to_sections(soap_text: str) -> list[dict[str, Any]]:
    """Convert a plain SOAP-format string into the Cliniko `content.sections.questions` shape.

    Cliniko stores treatment notes as a structured document:
        content.sections[i].questions[j] = {name, type, answer}
    Free-text answers are HTML strings. We map S/O/A/P lines onto four paragraph
    questions in a single section.
    """
    parts: dict[str, list[str]] = {"S": [], "O": [], "A": [], "P": []}
    current = "S"
    for line in soap_text.splitlines():
        line = line.strip()
        if not line:
            continue
        head, _, body = line.partition(":")
        head = head.strip().upper()
        if head in parts:
            current = head
            parts[head].append(body.strip())
        else:
            parts[current].append(line)
    return [
        {
            "questions": [
                {"name": "Subjective", "type": "paragraph",
                 "answer": "<p>" + " ".join(parts["S"]).strip() + "</p>"},
                {"name": "Objective", "type": "paragraph",
                 "answer": "<p>" + " ".join(parts["O"]).strip() + "</p>"},
                {"name": "Assessment", "type": "paragraph",
                 "answer": "<p>" + " ".join(parts["A"]).strip() + "</p>"},
                {"name": "Plan", "type": "paragraph",
                 "answer": "<p>" + " ".join(parts["P"]).strip() + "</p>"},
            ]
        }
    ]


async def seed_treatment_notes(
    client: ClinikoClient,
    appointments: list[dict[str, Any]],
    ctx: dict[str, Any],
) -> int:
    """One-shot POST /treatment_notes for ~70% of past, non-cancelled, non-DNA appointments.

    Cliniko quirk (verified au5, 2026-05-18): the `draft` field is REQUIRED on POST.
    Omit it → 422 "draft is not included in the list". The other quirk is the
    `content` field must be a STRUCTURED object — not a string — with at least one
    non-empty section. Free-text-content POSTs fail with "'sections' must not be empty".
    """
    past_appts = [
        a for a in appointments
        if a.get("_is_past")
        and not a.get("did_not_arrive")
        and not a.get("cancelled_at")
    ]
    print(f"  {len(past_appts)} past appointments are note-eligible (excludes DNA + cancelled)")
    targets = random.sample(past_appts, k=int(len(past_appts) * 0.70))
    print(f"  Targeting {len(targets)} notes (~70% of eligible past)")

    count = 0
    for i, appt in enumerate(targets, start=1):
        cond_key = appt.get("_condition", "general_other")
        cond = next((c for c in CONDITIONS if c["key"] == cond_key), CONDITIONS[-1])
        soap_text = random.choice(cond["note_templates"])
        sections = _soap_text_to_sections(soap_text)

        body = {
            "patient_id": appt["_patient_id"],
            "practitioner_id": ctx["practitioner_id"],
            "appointment_id": appt["id"],
            "title": f"{cond['label']} — consultation",
            "draft": False,
            "content": {"sections": sections},
        }
        r = await client.post("/treatment_notes", json=body)
        await pace()
        if r.get("error") or not r.get("id"):
            err = r.get("upstream_body", r.get("error"))
            if count < 3:
                print(f"  ✗ note {appt['id']}: {str(err)[:200]}")
            continue
        count += 1
        if i % PROGRESS_NOTE_EVERY == 0:
            print(f"  · {i}/{len(targets)} notes created")
    print(f"  ✅ {count} treatment notes created on past appointments")
    return count


# ---------------------------------------------------------------------------
# Seeding: recalls
# ---------------------------------------------------------------------------


async def seed_recalls(
    client: ClinikoClient,
    patients: list[dict[str, Any]],
    ctx: dict[str, Any],
    target_count: int = 150,
) -> int:
    """Schedule recalls across the next 90 days for a random sample of patients."""
    if not patients:
        return 0
    sample = random.sample(patients, min(target_count, len(patients)))
    count = 0
    for i, patient in enumerate(sample, start=1):
        days_ahead = random.randint(7, 90)
        cond = next(
            (c for c in CONDITIONS if c["key"] == patient.get("_condition")),
            CONDITIONS[-1],
        )
        due = datetime.now(timezone.utc) + timedelta(days=days_ahead)
        due = due.replace(hour=9, minute=0, second=0, microsecond=0)
        body = {
            "patient_id": patient["id"],
            "recall_type_id": ctx["recall_type_default_id"],
            "recall_at": iso_datetime(due),
            "notes": f"{cond['label']} — clinical review.",
        }
        r = await client.post("/recalls", json=body)
        await pace()
        if r.get("error"):
            if count < 3:
                print(f"  ✗ recall {patient.get('first_name')}: {r.get('upstream_body', r)}")
            continue
        count += 1
        if i % 25 == 0:
            print(f"  · {i}/{len(sample)} recalls created")
    print(f"  ✅ {count} recalls created")
    return count


# ---------------------------------------------------------------------------
# Idempotency + end-of-run summary
# ---------------------------------------------------------------------------


async def existing_fullseed_patients(client: ClinikoClient) -> int:
    """How many patients already carry our fullseed marker.

    Empirical quirk on au5 (May 2026): `q[]=notes:like:...` returns
        400 {"message": "notes is not filterable"}
    so we can't ask Cliniko to do the matching server-side. Page through and
    count client-side instead. Cheap for any cohort up to a few thousand.
    """
    count = 0
    page = 1
    while True:
        r = await client.get(
            "/patients",
            params={"per_page": 100, "page": page},
        )
        await pace()
        if isinstance(r, dict) and r.get("error"):
            return count
        batch = r.get("patients", [])
        if not batch:
            break
        for p in batch:
            if SEED_MARKER in (p.get("notes") or ""):
                count += 1
        if len(batch) < 100:
            break
        page += 1
    return count


async def account_state_report(client: ClinikoClient) -> dict[str, int]:
    """Single-call totals across the major collections."""
    summary: dict[str, int] = {}
    for ep, key in [
        ("/patients", "patients"),
        ("/individual_appointments", "individual_appointments"),
        ("/treatment_notes", "treatment_notes"),
        ("/recalls", "recalls"),
        ("/billable_items", "billable_items"),
        ("/medical_alerts", "medical_alerts"),
        ("/invoices", "invoices"),
    ]:
        r = await client.get(ep, params={"per_page": 1})
        await pace()
        if isinstance(r, dict) and r.get("error"):
            summary[key] = -1
        else:
            summary[key] = int(r.get("total_entries", 0))
    return summary


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main() -> None:
    api_key = os.environ["CLINIKO_API_KEY"]
    email = os.environ["CLINIKO_USER_AGENT_EMAIL"]
    cred = ClinikoCredential.from_env(api_key=api_key, user_agent_email=email)

    started_at = datetime.now(timezone.utc)
    print(f"Seeding into shard {cred.shard} ({cred.base_url})")
    print(f"User-Agent: {cred.user_agent}")
    print(f"Started at  : {started_at.isoformat()}")
    print(f"Random seed : {RANDOM_SEED}\n")

    async with ClinikoClient(cred) as client:
        print("=" * 60)
        print("Discovering account context")
        print("=" * 60)
        ctx = await discover_account_context(client)
        print(f"  practitioner_id   : {ctx['practitioner_id']}")
        print(f"  business_id       : {ctx['business_id']}")
        print(f"  standard_appt_type: {ctx['standard_appt_type_id']}")
        print(f"  initial_appt_type : {ctx['initial_appt_type_id']}")
        print(f"  recall_type_id    : {ctx['recall_type_default_id']}")
        print(f"  existing items    : {len(ctx['existing_items_by_code'])}")

        already = await existing_fullseed_patients(client)
        if already > 0:
            print(f"\n⚠️  Detected {already} existing patients with marker `{SEED_MARKER}`.")
            print("   This script previously ran (perhaps partially). Skipping patient creation;")
            print("   downstream stages will operate ONLY on the existing fullseed cohort.")

        print("\n" + "=" * 60)
        print("Stage 1 / 6 — Billable items catalogue")
        print("=" * 60)
        code_to_id = await seed_billable_items(client, ctx["existing_items_by_code"])
        print(f"  catalogue size: {len(code_to_id)} items")

        # If we've already seeded, load the existing cohort instead of creating again
        if already > 0:
            print("\n" + "=" * 60)
            print("Stage 2 / 6 — Loading existing fullseed cohort (no creation)")
            print("=" * 60)
            patients = await load_fullseed_patients(client)
            print(f"  loaded {len(patients)} existing patients")
        else:
            print("\n" + "=" * 60)
            print("Stage 2 / 6 — Patients")
            print("=" * 60)
            patients = await seed_patients(client)

        # Distribute condition tags onto patients loaded from server (notes field carries the label)
        patients = [p for p in patients if p.get("id")]

        print("\n" + "=" * 60)
        print("Stage 3 / 6 — Medical alerts")
        print("=" * 60)
        await seed_medical_alerts(client, patients)

        print("\n" + "=" * 60)
        print("Stage 4 / 6 — Appointments (6 mo history + 3 mo forward)")
        print("=" * 60)
        appointments = await seed_appointments(client, patients, ctx)

        print("\n" + "=" * 60)
        print("Stage 5 / 6 — Treatment notes (~70% of past appointments)")
        print("=" * 60)
        await seed_treatment_notes(client, appointments, ctx)

        print("\n" + "=" * 60)
        print("Stage 6 / 6 — Recalls (next 90 days)")
        print("=" * 60)
        await seed_recalls(client, patients, ctx, target_count=150)

        print("\n" + "=" * 60)
        print("Final account state")
        print("=" * 60)
        state = await account_state_report(client)
        for k, v in state.items():
            label = k.replace("_", " ").title()
            print(f"  {label:30}  total_entries = {v}")

    finished_at = datetime.now(timezone.utc)
    elapsed = (finished_at - started_at).total_seconds()
    print(f"\nElapsed: {elapsed:.1f}s ({elapsed/60:.1f} min)")
    print(f"Finished at {finished_at.isoformat()}")
    print("\nNote: Invoices are NOT created — Cliniko's REST API does not expose POST /invoices.")
    print("      See docs/API-LIMITATIONS.md for the workaround (Cliniko UI only).")


async def load_fullseed_patients(client: ClinikoClient) -> list[dict[str, Any]]:
    """Page through all patients and keep those carrying the fullseed marker.

    Filter is client-side because `notes` is not q[]-filterable on au5
    (see existing_fullseed_patients for the API quirk).
    """
    all_pats: list[dict[str, Any]] = []
    page = 1
    while True:
        r = await client.get(
            "/patients",
            params={"per_page": 100, "page": page},
        )
        await pace()
        if isinstance(r, dict) and r.get("error"):
            break
        batch = r.get("patients", [])
        if not batch:
            break
        for p in batch:
            if SEED_MARKER in (p.get("notes") or ""):
                all_pats.append(p)
        if len(batch) < 100:
            break
        page += 1
    # Restore the _condition tag from the notes
    cond_labels = {c["label"]: c["key"] for c in CONDITIONS}
    for p in all_pats:
        notes = p.get("notes", "") or ""
        for label, key in cond_labels.items():
            if label in notes:
                p["_condition"] = key
                break
        if "_condition" not in p:
            p["_condition"] = "general_other"
    return all_pats


if __name__ == "__main__":
    asyncio.run(main())
