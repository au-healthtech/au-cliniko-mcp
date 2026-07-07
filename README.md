# au-cliniko-mcp

**The open-source Cliniko MCP for Australian allied-health business intelligence.**

MIT-licensed. Audit-logged. AHPRA-aware. Built to answer "how's my practice actually doing?" — not to write your clinical notes.

---

## Why this exists

Cliniko is the dominant practice-management system for Australian allied-health practitioners (physios, psychologists, podiatrists, OTs, speech pathologists, dietitians). The Model Context Protocol (MCP) lets Claude work directly with your Cliniko data.

**Positioning: "Heidi writes your notes. We run the other 90% of the business."**

Note-writing is a crowded, well-served category (Heidi, and note-template add-ons from every existing Cliniko MCP). This project deliberately does **not** compete there. Instead it's the layer that answers the practice-owner questions Cliniko's own reporting doesn't:

- What's our revenue trend, and how does this month compare to last?
- What's our capture rate — are we actually invoicing every appointment we do?
- How much revenue have we leaked this quarter, and where?
- Which practitioners are busiest / most underutilised?
- What's our patient retention rate?
- Which patients are lapsed, overdue on invoices, or due for recall — and can we draft the outreach?

No existing Cliniko MCP does this. They're all CRUD wrappers (read/write patients, appointments, notes) with no aggregation, no KPI layer, and no revenue-audit tooling.

## Who maintains this

[Tradd Horne](https://principalpodiatry.com.au) — AHPRA-registered podiatrist (POD0001880268), Principal Podiatry Pty Ltd (ABN 19 615 606 347). Building this from inside a working AU allied-health practice, not from outside the industry. Co-developed with Claude; every change reviewed by Tradd before merge.

## Status — 7 July 2026

🚧 **Pre-alpha, actively built.** Not yet packaged/published. Initial scaffold 18 May 2026.

| Phase | What it covers | Status |
|---|---|---|
| A — Foundations | Shard auto-detection, auth, client, one live tool | ✅ Shipped |
| B — Tier 1 CRUD endpoints | 18 tools across 10 Cliniko resource modules (patients, appointments, bookings, treatment notes, invoices, practitioners, businesses, recalls, communications, available time) | ✅ Shipped |
| C — Compliance layer | Audit log (SQLite), `@phi_flagged` PHI decorator, Fernet-encrypted vault | ✅ Shipped |
| D — Business intelligence layer | KPI digest tools, revenue-audit tools, cross-tool workflow recipes, N+1 aggregators | ✅ Shipped (this is now the core differentiator — see below) |
| Clinical note templates (podiatry SOAP, physio, OT, etc.) | Discipline-specific note drafting | ❌ **Cut from scope.** Not building this — see "What we explicitly don't do" below. |
| Tier 2-3 endpoints | Remaining ~19 Cliniko resource groups (attachments, patient forms, settings, tax/concessions, etc.) | Pending |
| Hosted gateway | Multi-tenant SaaS, Stripe billing, per-clinic dashboard | Pending, deferred |
| v1.0 / PyPI release | Public package, install docs, submission to MCP registries | Pending |

**Not yet packaged** — `pyproject.toml` still reads `version = "0.0.1"`, classifier `Pre-Alpha`. Runs from source only; no `pip install au-cliniko-mcp` yet.

**Repo:** [`au-healthtech/au-cliniko-mcp`](https://github.com/au-healthtech/au-cliniko-mcp) (GitHub org, public). Local working copy tracks branch `feat/full-practice-seed-data`.

## What's actually built right now

**39 tools** registered across 14 modules, plus **7 named workflow prompts**.

| Module | Tools | Purpose |
|---|---|---|
| `insights.py` | 9 | KPI digest engine — revenue summary, new patients, no-shows, capture rate, practitioner utilisation, retention rate, per-tenant KPI preferences, digest composer |
| `revenue.py` | 4 | Revenue audit + missed-billing detection — `revenue_audit`, `find_billing_gaps` (appointment-centric and patient-cohort modes), billable-items catalog, concession types |
| `aggregators.py` | 3 | Multi-resource joins the LLM would otherwise chain by hand — patient appointment stats, practitioner schedule overview, appointment↔invoice join |
| `patients.py` | 4 | CRUD |
| `appointments.py` | 3 | CRUD |
| `treatment_notes.py` | 3 | CRUD with draft-gate |
| `invoices.py` | 3 | Read + create-helper |
| `workflows.py` | 3 tools + 7 prompts | Cliniko-side data shaping for downstream connectors (recall outreach drafts, invoice chase drafts, calendar-event formatting) + named recipes: `weekly_recall_review`, `invoice_chase_workflow`, `no_show_followup_workflow`, `monday_morning_digest`, `appointment_calendar_sync`, `end_of_month_report` |
| `recalls.py` | 2 | CRUD |
| `available_time.py`, `bookings.py`, `businesses.py`, `communications.py`, `practitioners.py` | 1 each | Read |

**Eval-tested, not just built.** `tests/integration/llm_eval.py` runs a 26-question eval suite against a live Cliniko sandbox with `claude-haiku-4-5` as the calling model. Latest run: **25/26 questions answered correctly for ~$0.27**. This is how gaps get found — several tools (aggregators, cost-confirmation gates, pagination fixes) exist specifically because the eval surfaced a question the tool set couldn't answer cleanly.

**Safety features already in place:**
- Cost-confirmation gates on any tool that could fan out across large date ranges (`revenue_audit`, `find_billing_gaps`, `get_appointment_invoice_join`) — refuses and asks the user to confirm before burning API calls/tokens on a large scope.
- Consent-gate pattern on treatment-note writes (draft → explicit commit).
- `@phi_flagged` decorator + categorisation on every tool that returns PHI, audit-logged to `~/.au-cliniko-mcp/audit.db`.
- API keys stored in a Fernet-encrypted vault (`~/.au-cliniko-mcp/vault.db`), never plaintext.

**Test data:** `tests/integration/seed_full_practice.py` generates a synthetic 500-patient mid-sized AU podiatry practice for realistic testing without touching real patient data.

## Architecture (as built)

```
src/au_cliniko_mcp/
├── server.py          FastMCP setup + tool registration
├── client.py          Shared async httpx client; shard auto-detect
├── auth.py            API key parsing, User-Agent shaping
├── vault.py           Fernet-encrypted key + preferences storage
├── audit.py           SQLite audit log writer
├── phi.py             PHI-flag decorators, categories
└── tools/
    ├── patients.py, appointments.py, bookings.py, treatment_notes.py,
    │   invoices.py, practitioners.py, businesses.py, recalls.py,
    │   communications.py, available_time.py     — Tier 1 CRUD
    ├── revenue.py                                — revenue audit / leakage detection
    ├── insights.py                               — KPI digest engine
    ├── aggregators.py                            — cross-resource joins
    └── workflows.py                              — Shape-A workflow recipes + prompts
```

Audit log is currently **SQLite** (`~/.au-cliniko-mcp/audit.db`), not Postgres — the original architecture doc called for Postgres but SQLite is what's actually running for the single-tenant pre-alpha stage. Revisit for the hosted-gateway phase, if that phase happens.

## What we explicitly don't do

- **No clinical note templates.** Originally planned (podiatry SOAP, physio initial assessment, OT/psych/speech/dietetics templates), explicitly **cut from scope**. Heidi and similar tools already do this well; it's not our differentiator and duplicating it dilutes the BI positioning.
- **Not competing on raw CRUD/API coverage.** Other Cliniko MCPs (Practisight, various hobby forks) already do CRUD adequately. We only build CRUD depth where a BI tool needs it as a dependency (e.g. invoices, appointments).
- **Not a chat UI.** Consumed via Claude Desktop / Claude Code / Claude.ai only.
- **No diagnostic/therapeutic claims** (TGA-medical-device territory) — everything is framed as time/admin/revenue insight, never clinical outcomes, in line with AHPRA advertising rules.

## Compliance layer

Every tool touching Protected Health Information carries an explicit `@phi_flagged` decorator declaring the PHI categories it returns. Each call is:

- **Audit-logged** to local SQLite with timestamp, tool name, patient/practitioner id (when present), PHI categories, result status, elapsed ms, and redacted args.
- **Tagged with a `_phi` response header** so downstream consumers know the sensitivity of what they just received.

See `docs/COMPLIANCE.md` (APP/OAIC/AHPRA/MBS/NDIS/DVA position), `docs/SECURITY.md` (threat model), `docs/PIA-template.md` (Privacy Impact Assessment template for clinics to complete pre-install), `docs/INSTALL-SOP.md`, `docs/API-LIMITATIONS.md`.

## Licence

MIT. See `LICENSE`.

## Disclosure on AI use

This codebase is co-developed with Claude (Anthropic's AI). Every code change is reviewed by Tradd before merge.
