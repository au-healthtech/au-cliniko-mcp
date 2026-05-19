# Cliniko API — known limitations

What the Cliniko API genuinely can't do, so neither can this MCP. Documented here
so the LLM can route around the limitation instead of trying to call a non-existent
endpoint.

## Invoices

**Cannot via API:**
- Create new invoices from scratch
- Update existing invoice line items
- Delete or void invoices
- Apply payments to invoices

**Empirical verification (au5, 2026-05-18):** `POST /invoices` with a syntactically
valid body returns 404 (the endpoint isn't routed for write). The Cliniko REST API
exposes invoices for READ only.

**Workaround:** Invoice operations must happen in the Cliniko UI. Our MCP exposes
read tools (`list_invoices`, `list_unpaid_invoices`, `get_invoice`) and will eventually
expose a drafting helper that emits a JSON payload the practitioner can paste into
Cliniko UI manually.

**Demo-data implication:** The `seed_full_practice.py` script seeds the BILLABLE
ITEMS catalogue (which IS writable) so demos show a populated dropdown of MBS/DVA/
NDIS codes, but does NOT create invoices. Demo prospects need to either book +
invoice manually in the UI for one or two examples, or stage screenshots.

## Treatment notes

**Field-name quirk:**
- `POST` body uses `content` and `draft` flags
- Updating clinical content requires a **two-step persist**: `POST` with stub, then
  `PUT /treatment_notes/<id>/content` to write the actual body. The reference
  commercial product (Practisight) handles this correctly; the hobby implementations
  do not.

**Body charts:** Not API-accessible. Charts (anatomical diagrams) must be drawn in
Cliniko UI.

## Appointments — POST field names (empirically verified au5, 2026-05-18)

When creating an `individual_appointment` via POST, Cliniko expects:
- `starts_at` (NOT `appointment_start` — earlier hobby implementations got this wrong)
- `ends_at` (NOT `appointment_end`)

When updating via PATCH or in any LIST response: same — `starts_at` / `ends_at`.

The BoabAI/andymillar84 docs claim `appointment_start`/`appointment_end` are required
on POST. Empirically false on au5 (and likely all shards). Our MCP uses `starts_at`/
`ends_at` consistently for both POST and PATCH.

## Recalls — required fields (empirically verified au5, 2026-05-18)

POST /recalls requires:
- `recall_at` — ISO-8601 datetime (NOT `recall_date`, NOT `due_at`)
- `recall_type_id` — must reference an existing recall type
- `patient_id`

Cliniko trial accounts come pre-seeded with two recall types: "Return visit" and
"Return visit (soon)". Production accounts need at least one configured before
recalls can be created.

## Treatment notes — required title + draft fields + structured content

POST /treatment_notes requires:
- `patient_id`
- `practitioner_id`
- `title` (REQUIRED — undocumented; omitting it returns `422: {"errors": {"title": "can't be blank"}}`)
- `draft` (REQUIRED — omitting it returns `422: {"errors": {"draft": "is not included in the list"}}`)
- `appointment_id` (optional but recommended)

**Content must be STRUCTURED, not a string (verified au5, 2026-05-18).**
The `content` field is an object with `sections[].questions[]` keyed lists:
```json
{
  "content": {
    "sections": [
      { "questions": [
          {"name": "Subjective", "type": "paragraph", "answer": "<p>S: …</p>"},
          {"name": "Objective",  "type": "paragraph", "answer": "<p>O: …</p>"},
          {"name": "Assessment", "type": "paragraph", "answer": "<p>A: …</p>"},
          {"name": "Plan",       "type": "paragraph", "answer": "<p>P: …</p>"}
      ]}
    ]
  }
}
```

`answer` accepts HTML. Sending a plain string under `content` returns
`422: {"errors": {"content": "'sections' must not be empty"}}`.

**One-shot vs two-step**: Once you supply `draft: false` + non-empty content
sections, the one-shot POST works on au5. The two-step (POST shell + PATCH
content) also works and may be preferable for very long notes. Our seed script
uses the one-shot path for efficiency; production tools may prefer two-step.

**Optional `treatment_note_template_id`** scaffolds the section structure off an
existing template. Useful if the practice has standardised templates; ignore
for ad-hoc free-form notes.

## Search

Cliniko's search uses `q[]` array filter syntax, not `?q=`. Examples:
- `q[]=first_name:like:Jane` — partial match
- `q[]=last_name:=Smith` — exact match
- `q[]=date_of_birth:>=1990-01-01` — date range

Operators: `=` (exact), `like` (substring), `>=`, `<=`, `>`, `<`, `!=`.

Multiple `q[]` parameters are AND-combined.

## Rate limits

Cliniko's official limit: **200 req/min per user**. Returns HTTP 429 with `Retry-After`
header on excess. Our client respects `Retry-After` and applies exponential backoff
with jitter on top.

## ID format

All Cliniko entity IDs are **19-digit strings**, not integers. JSON treats them as
strings; some clients silently truncate when coerced to int. The yasboop hobby
implementation typed IDs as `int` and would silently lose precision.

## User-Agent

Cliniko's documentation states: "Please set your User-Agent header to identify your
application and contact email. We may block requests with no User-Agent or generic
ones." Our MCP enforces this at boot time via `CLINIKO_USER_AGENT_EMAIL`.

## Healthcare Identifier Service (HI)

Cliniko optionally surfaces IHI numbers but the HI service itself is operated by
Services Australia and requires separate registration. Our MCP does not touch the
HI service directly; it only reads/writes the `medicare_number` field on patient
records (when present).

## Communications

The `/communications` endpoint reads communication HISTORY (SMS/email sent to a patient).
Cliniko's API does **not** expose a "send communication now" endpoint — automation must
use Cliniko's built-in scheduled-communication feature configured in the UI.

## Invoice status — INTEGER codes, not strings (verified au5, 2026-05-18)

Cliniko's `/invoices` uses INTEGER status codes:
- `20` = Paid

Other codes likely exist (10 = Draft, 15 = Open/Awaiting Payment, 25 = Closed,
30 = Void) but TBC empirically when test data has invoices in those states.

Sending `status:=awaiting_payment` (string) returns:
`400: "Filter value for status must be a number"`

## Invoice fields — no `balance`, no `total` (verified au5, 2026-05-18)

The Cliniko list view returns:
- `total_amount` (NOT `total`)
- `net_amount`
- `tax_amount`
- `status_description` (human-readable: "Paid")

NO `balance` field. To compute outstanding $ on an unpaid invoice, fetch the
invoice individually to get `payments[]`, then subtract sum-of-payments from
total_amount. The list view alone is insufficient.

## Patient `notes` field — NOT q[]-filterable (verified au5, 2026-05-18)

Cliniko returns `400: {"message": "notes is not filterable"}` for
`q[]=notes:like:foo` on /patients. The general `q[]` machinery accepts most
attribute paths but the free-text `notes` field is excluded. The `seed_dummy_data.py`
script's idempotency check (which queried `notes:like:__au-cliniko-mcp-seed__`)
silently returned 0 entries and bypassed the skip logic — bug fixed in the
fullseed script by paginating and filtering client-side.

## Billable items — DELETE works, PATCH `archived_at` doesn't (verified au5, 2026-05-18)

`PATCH /billable_items/{id}` with `{"archived_at": "..."}` returns 200 but does
not actually archive the item — the next GET shows `archived_at: null` again.
The reliable way to remove a billable item is `DELETE /billable_items/{id}`
(returns 204 + the item then 404s on subsequent GETs).

## Recall fields — `recall_at` is NOT q[]-filterable (verified au5, 2026-05-18)

Cliniko returns `400: "recall_at is not filterable"` if you send
`q[]=recall_at:<=2026-06-15`. Despite Cliniko's general q[] support on most
endpoints, recall_at is excluded.

Workaround: fetch all recalls (paginated) and filter `recall_at` client-side.
For large clinics with thousands of recalls this becomes slow — Phase E should
add a local index or persistent cache.

`recall_at` is stored as a DATE string (`YYYY-MM-DD`), not a full ISO datetime.

## Medical alerts taxonomy

Cliniko stores medical alerts as free text. There's no taxonomy field, no severity
field, no SNOMED-CT codes, no allergy / condition / medication categorisation. Our
MCP will overlay an AU-specific taxonomy on top in Phase B+ (TBD).
