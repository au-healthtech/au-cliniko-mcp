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

**Workaround:** Invoice operations must happen in the Cliniko UI. Our MCP exposes
read tools (`list_invoices`, `list_unpaid_invoices`, `get_invoice`) and will eventually
expose a drafting helper that emits a JSON payload the practitioner can paste into
Cliniko UI manually.

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

## Treatment notes — required title field + 500-prone full-payload POST

POST /treatment_notes requires:
- `patient_id`
- `practitioner_id`
- `title` (REQUIRED — undocumented; omitting it returns `422: {"errors": {"title": "can't be blank"}}`)
- `appointment_id` (optional but recommended)

**Empirical 500 quirk (au5, 2026-05-18)**: POSTing the FULL note (`content` + `draft` + `title`)
in one call returns 500 intermittently. The reliable pattern is two-step:
1. POST /treatment_notes with metadata + title only (NO content)
2. PATCH /treatment_notes/{id} with `{content, draft}` to populate the body

Practisight (the deep-read showed this) handles the two-step correctly. Our
`draft_treatment_note` follows the same pattern.

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

## Medical alerts taxonomy

Cliniko stores medical alerts as free text. There's no taxonomy field, no severity
field, no SNOMED-CT codes, no allergy / condition / medication categorisation. Our
MCP will overlay an AU-specific taxonomy on top in Phase B+ (TBD).
