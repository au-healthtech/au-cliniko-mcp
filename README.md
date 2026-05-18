# au-cliniko-mcp

**The open-source Cliniko MCP built for Australian allied-health practices.**

MIT-licensed. Audit-logged. AHPRA-aware. Clinical templates bundled — not sold separately.

---

## Why this exists

Cliniko is the dominant practice-management system for Australian allied-health practitioners (physios, psychologists, podiatrists, OTs, speech pathologists, dietitians). The Model Context Protocol (MCP) lets Claude work directly with your Cliniko data — read patient records, draft treatment notes, query invoices, manage recalls — all from natural-language conversation.

There are existing Cliniko MCPs. None of them target the regulatory and clinical realities of an AU allied-health practice:

- **No audit log** of which tool touched which patient, when, by which practitioner.
- **No PHI guards** beyond a draft gate on treatment notes.
- **No AU compliance posture** (AHPRA, OAIC APPs, MBS, NDIS, DVA, IHI service).
- **No clinical workflow templates** for the specific note shapes AU practitioners use.
- **No multi-tenant model** — they assume one clinic per install.
- **Closed-source or source-available** — you can read the code but can't fork it, and the runtime is license-gated.

This project fixes that. MIT-licensed, audit-logged, with bundled discipline-specific clinical templates and an explicit AU compliance stance.

## Who maintains this

[Tradd Horne](https://principalpodiatry.com.au) — AHPRA-registered podiatrist (POD0001880268), Principal Podiatry Pty Ltd (ABN 19 615 606 347). Building this from inside a working AU allied-health practice, not from outside the industry.

## Status

🚧 **Pre-alpha.** Initial scaffolding 2026-05-18. Tracking toward v1.0 in ~10 weeks.

| Phase | Target | Status |
|---|---|---|
| A — Foundations | Week 1 | scaffolded |
| B — Tier 1 endpoints (15+) | Weeks 2-3 | pending |
| C — Compliance layer (audit log, PHI guards, vault) | Weeks 3-4 | pending |
| D — Clinical templates (6 disciplines + NDIS) | Weeks 4-5 | pending |
| E — Tier 2-3 endpoints | Weeks 5-7 | pending |
| F — Hosted gateway | Weeks 7-10 | pending |
| G — v1.0 release | Week 10 | pending |

## Quick start (when v1.0 ships)

```bash
pip install au-cliniko-mcp
```

Then add to your Claude Desktop config (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "cliniko": {
      "command": "au-cliniko-mcp",
      "env": {
        "CLINIKO_API_KEY": "your-api-key-here"
      }
    }
  }
}
```

Shard is auto-detected from the API key suffix. No manual configuration.

## Architecture

```
src/au_cliniko_mcp/
├── server.py          FastMCP setup + tool registration
├── client.py          Shared async httpx client; shard auto-detect
├── auth.py            API key parsing, User-Agent shaping
├── vault.py           Encrypted key storage (Fernet → KMS)
├── audit.py           PostgreSQL audit log writer
├── phi.py             PHI-flag decorators, consent-gate decorators
├── shaping.py         Markdown summary builders (10x compression)
├── pagination.py      Cursor + page helpers
├── errors.py          Status-specific LLM-friendly error builders
├── models/            Pydantic models per Cliniko resource
├── tools/             One module per Cliniko resource group
├── resources/         MCP resources (read-only data feeds)
├── templates/         AU clinical templates (podiatry SOAP, physio, OT, ...)
└── prompts/           MCP prompts (canned workflows)
```

## What sets this apart

| | au-cliniko-mcp | Alternatives |
|---|---|---|
| Licence | **MIT** — fork, modify, sell | Closed or source-available |
| Audit log | **PostgreSQL, per-tool, per-tenant** | None |
| PHI guards | **`@phi_flagged` decorator + categorisation** | None |
| Consent gate | **Default draft → explicit commit** on every write | Draft gate on treatment notes only |
| AU compliance docs | **APP, OAIC, AHPRA, MBS, NDIS, DVA, IHI** explicit | None |
| Clinical templates | **6 disciplines + NDIS bundled free** | Sold separately at $129/discipline |
| Multi-tenant | **Per-clinic vault + dashboard** | Single-clinic install |
| Open source | **Truly MIT, fork-friendly** | License-key gated runtime |
| Built by | **AHPRA-registered podiatrist** | Developers |

## Compliance posture

Read `docs/COMPLIANCE.md` for the explicit position on:

- Privacy Act 1988 + Australian Privacy Principles (APP), especially APP 8 (cross-border) and APP 11 (security)
- OAIC Notifiable Data Breaches scheme
- AHPRA advertising guidelines (this project markets time/admin/revenue savings only — never clinical outcome claims)
- Medicare Benefits Schedule (MBS) and Chronic Disease Management billing flows
- NDIS service log requirements
- Healthcare Identifiers Act 2010 (only relevant if Healthcare Identifier service is touched)
- Cliniko's own data residency (AU shard `api.au1.cliniko.com` through `api.au4.cliniko.com`)

## Contributing

Issues and pull requests welcome from the AU allied-health community. See `docs/CONTRIBUTING.md` when it exists.

## Licence

MIT. See `LICENSE`.

## Disclosure on AI use

This codebase is co-developed with Claude (Anthropic's AI). Every code change is reviewed by Tradd before merge. See `docs/AI-DISCLOSURE.md` (forthcoming) for the full transparency statement.
