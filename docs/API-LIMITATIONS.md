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

## Appointments — POST vs PATCH field names

When creating an appointment via POST:
- `appointment_start` (not `starts_at`)
- `appointment_end` (not `ends_at`)

When updating via PATCH or in any LIST response:
- `starts_at` / `ends_at`

This is documented inconsistently in Cliniko's docs. Our MCP normalises by using
the wire-correct field for each operation.

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
