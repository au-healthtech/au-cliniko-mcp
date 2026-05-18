# Compliance posture — au-cliniko-mcp

**Audience**: Australian allied-health practice owners, practice managers, IT consultants, and procurement staff evaluating whether `au-cliniko-mcp` meets their regulatory obligations before they install it on a clinic workstation.

**Last reviewed**: 2026-05-18 against the framework versions current at that date.
**Disclaimer**: this is a self-assessment by the project maintainer (Tradd Horne, AHPRA POD0001880268). It is not legal advice. Consult the OAIC or your state health department for definitive guidance.

---

## 1. Frameworks covered

| Framework | Applies because | Our position |
|---|---|---|
| **Privacy Act 1988 (Cth)** + **Australian Privacy Principles (APPs)** | The practice handles "personal information" and "health information" — covered by APP regardless of turnover for health-service providers (s 6FB). | All 13 APPs addressed below. |
| **OAIC Notifiable Data Breaches scheme** | The practice is an APP entity holding personal information. | See §3 Breach response. |
| **AHPRA advertising guidelines (current edition)** | The practice is an AHPRA-registered health-service provider. | We make no clinical-outcome claims; framing is administrative time-saved only. See §5. |
| **Medicare Benefits Schedule (MBS) compliance** | Some practitioners bill MBS items via Cliniko. | Our MCP does not auto-bill MBS; no auto-submit. See §6. |
| **NDIS Quality and Safeguards Commission Practice Standards** | Some practitioners deliver NDIS-funded services. | We do not handle NDIS service-log submissions automatically. See §6. |
| **Department of Veterans' Affairs (DVA) provider arrangements** | Some practitioners hold DVA provider numbers. | Same as MBS: read-only support, no auto-billing. |
| **My Health Records Act 2012 (s 77 — data residency)** | If the practice uploads to MHR. | **Out of scope**. We do not integrate with MHR. |
| **Healthcare Identifiers Act 2010** | If the practice resolves IHI numbers via HI Service. | **Out of scope**. We do not call the HI Service. |
| **State health-records legislation** (NSW HRIPA 2002, Vic HRA 2001, ACT HRPA 1997) | Multi-state practice operation. | We follow APP 8 cross-border rules + state-specific retention. See §4. |
| **TGA — Therapeutic Goods Act** | Only if the product makes diagnostic/therapeutic claims. | **Not applicable**. We are a workflow tool, not a medical device. See §5. |
| **GDPR (EU) / UK GDPR / HIPAA (US)** | Not applicable — this product targets Australian practices. | Out of scope. Customers serving EU/UK/US residents should consult separate guidance. |

---

## 2. APP-by-APP self-assessment

### APP 1 — Open and transparent management of personal information
- **Public privacy policy**: This document plus `docs/SECURITY.md` constitute the privacy management framework for the MCP layer. Practices using this MCP must also publish their own privacy policy covering their broader operations.
- **Designated privacy officer**: For an MCP install, the practice owner is the de-facto privacy officer. The Vault stores keys under their control.

### APP 2 — Anonymity / pseudonymity
- N/A — patient identification is intrinsic to Cliniko. The MCP does not impose additional identification.

### APP 3 — Collection of solicited personal information
- The MCP does not collect new personal information; it reads what already exists in Cliniko.
- License-key / hosting accounts (Phase F) collect only practice name + billing email; documented separately.

### APP 4 — Dealing with unsolicited personal information
- N/A — we do not receive unsolicited personal information through the MCP.

### APP 5 — Notification of the collection of personal information
- The practice must continue to provide its existing collection notification to patients. The MCP changes only *how* the data is accessed by staff; it does not change *what* is collected.

### APP 6 — Use or disclosure of personal information
- The MCP enables the practice's authorised staff to query Cliniko via natural language with Claude. This is "use" by the practice, the original collecting entity.
- The MCP does **not** disclose personal information to the maintainer (Tradd Horne / au-healthtech), to Cliniko in any new way, or to any third party beyond what Cliniko's own API documentation states.
- Claude (Anthropic) sees information passed to it in tool responses. Practices should be aware this is a use of an overseas processor — addressed under APP 8 below.

### APP 7 — Direct marketing
- N/A — the MCP is not a marketing channel. We do not consume patient data for marketing.

### APP 8 — Cross-border disclosure
- **Claude is operated by Anthropic, a US entity, with model serving in US/global regions.** Practices must:
  1. Inform patients in their privacy policy that AI assistance is used (suggested wording in `docs/PIA-template.md`).
  2. Sign Anthropic's standard Data Processing Addendum (available in Anthropic Console). For Path (iii) BYO-Anthropic SaaS, the practice signs directly. For Path (ii) managed SaaS, the operator signs on the practice's behalf.
  3. Note that Anthropic does not, by default, train on API traffic. Verify this is reflected in the active Anthropic plan.
- **Cliniko is sharded; AU practices are on `api.au1...au5.cliniko.com`.** Our shard auto-detection guarantees we hit the AU shard for AU keys. No data leaves the AU shard.
- **The MCP runs on the practice's own workstation** (Path i / iii) — no third party hosts the MCP itself in this configuration. For Path (ii) managed SaaS we will provide a separate DPA.

### APP 9 — Adoption, use or disclosure of government identifiers
- The MCP reads any government identifiers already present on Cliniko patient records (Medicare number, DVA number) but never *generates*, *cross-references*, or *transmits* them beyond Cliniko itself.
- It does **not** touch the Healthcare Identifier Service or My Health Records (see Frameworks table).

### APP 10 — Quality of personal information
- The MCP reads from Cliniko, which is the source of truth maintained by the practice. We do not alter data quality.
- The `@phi_flagged` decorator tags responses with their PHI categories so an LLM downstream can be told "this is contact info; treat as authoritative".

### APP 11 — Security of personal information ⚠️ This is the heaviest APP for our tool.

| Control | Implementation |
|---|---|
| **Encryption at rest** of any cached secrets | Fernet (AES-128 + HMAC-SHA-256) via `src/au_cliniko_mcp/vault.py`. Key file chmod 600 under `~/.au-cliniko-mcp/`. |
| **Encryption in transit** to Cliniko | HTTPS / TLS 1.2+ enforced by httpx. No HTTP fallback. |
| **Authentication** | Cliniko API key (HTTP Basic Auth). Future Phase C-2 adds per-tenant licence verification. |
| **Authorisation** | Single-tenant install model. Per-tenant vault namespace + per-tenant audit log scope from day 1 (Phase F will add multi-tenant). |
| **Audit logging** | Every tool call recorded to local SQLite at `~/.au-cliniko-mcp/audit.db`. Records: timestamp, tool name, patient_id (if any), practitioner_id (if any), PHI categories touched, result status, elapsed ms, error (if any). Args are **redacted** before storage — free-text PHI fields blanked. |
| **Retention** | Default 7-year retention for audit records (matches AU health-record minimum). Configurable. Pruning is an explicit administrative action, never automatic on the live DB. |
| **Access control on the audit log** | The audit DB file is in the practice owner's home directory. OS-level permissions apply. |
| **Breach detection** | Audit-log query helper (`AuditLog.query_recent`) allows triage. |
| **PHI guards on tool responses** | Every response from a PHI-flagged tool carries a `_phi` header with the categories touched, so any downstream consumer (LLM, dashboard, audit script) knows to treat the response as sensitive. |
| **No clinical-outcome AI claims** | The MCP never claims to be a diagnostic or therapeutic device. See §5. |

### APP 12 — Access to personal information
- Patients' right of access is to the **practice**, not to the MCP. The MCP is a tool the practice uses; the patient relationship is unchanged.
- If a patient invokes their access right, the audit log helps reconstruct what was queried about them — useful when responding to subject-access requests.

### APP 13 — Correction of personal information
- The MCP can write some Cliniko fields via tool calls (e.g. `draft_treatment_note`), but always in DRAFT mode requiring practitioner sign-off in Cliniko's UI. Direct correction is the practice's responsibility through normal Cliniko workflows.

---

## 3. Breach response — OAIC NDB scheme

**Eligible data breach**: occurs if (a) there is unauthorised access to or disclosure of personal information, AND (b) a reasonable person would conclude this is likely to result in serious harm to any of the individuals.

**Plan for the practice using the MCP**:

1. **Detect.** Audit log queries can establish what was accessed during a suspected breach window:
   ```bash
   sqlite3 ~/.au-cliniko-mcp/audit.db "SELECT * FROM tool_call_audit WHERE ts >= '2026-05-18' ORDER BY ts;"
   ```
2. **Contain.** Revoke the Cliniko API key in Cliniko's UI (My Info → Manage API keys → Revoke). Revoke the local Vault entry. Stop the MCP process.
3. **Assess.** Per the OAIC's *Identifying Eligible Data Breaches* guidance. The audit log will show exactly which patient IDs were queried and which PHI categories were exposed.
4. **Notify.** If serious harm is likely:
   - Notify OAIC within 30 days using the OAIC's online form: https://www.oaic.gov.au/privacy/notifiable-data-breaches/notify-the-oaic
   - Notify affected individuals in writing.
5. **Review.** Update the install's vault, rotate keys, re-train staff.

---

## 4. State health-records legislation

| State | Statute | Cross-border restriction | Retention |
|---|---|---|---|
| **NSW** | *Health Records and Information Privacy Act 2002* | Restricts transfer outside NSW unless equivalent protection applies; using AU-resident Anthropic endpoints + Cliniko AU shard meets the bar. | 7 years post last service, or until 25th birthday if minor (whichever is later). |
| **Vic** | *Health Records Act 2001* | Similar. | 7 years post last service. |
| **ACT** | *Health Records (Privacy and Access) Act 1997* | Similar. | 7 years post last service. |
| **QLD / WA / SA / NT / TAS** | Covered by the federal Privacy Act 1988 + APPs; no separate health-records statute. | APP 8 applies. | 7 years (AHPRA guideline). |

Audit log default retention of 7 years (`DEFAULT_RETENTION_DAYS = 2555`) meets every state's bar. For paediatric practices where records must be kept until age 25, override the retention policy via configuration.

---

## 5. AHPRA advertising compliance

The maintainer (Tradd Horne, podiatrist) is bound by AHPRA's Section 133 advertising restrictions. This product:

- **DOES** market on: time-saved, administrative-burden-reduced, revenue-recovered, AHPRA-clinician-built credibility.
- **DOES NOT** make any claim of clinical efficacy, diagnostic accuracy, therapeutic benefit to patients, or "improved outcomes". The product is a workflow tool for the practitioner, not a clinical decision-support system.
- **DOES NOT** use patient testimonials about clinical outcomes anywhere in marketing or documentation.
- **DOES NOT** present itself as a substitute for clinical judgement.

Practices using this MCP must continue to comply with AHPRA's advertising rules in their own materials. Mentioning "AI-assisted administration" in a clinic website is fine; "AI-improved clinical outcomes" is not.

---

## 6. MBS / NDIS / DVA position

| Scheme | Our position |
|---|---|
| **MBS** | The MCP reads invoices via Cliniko's API. It does **not** submit claims to Medicare. It can DRAFT items for the practitioner to issue manually in Cliniko's UI. |
| **NDIS** | The MCP can produce NDIS-style service-log drafts (Phase D — clinical templates) but does not submit them to the NDIS Quality and Safeguards Commission or to NDIA. The practitioner remains the responsible party. |
| **DVA** | Same as MBS — read-only, draft-only support. No auto-submission. |

---

## 7. TGA — medical-device classification

Under TGA's Software as a Medical Device (SaMD) rules, a software product is a medical device only if its intended use is *diagnostic*, *therapeutic*, *monitoring of a physiological condition*, or *treatment-decision support*. This product is **none of those**:
- Patient records management → not a medical device
- Recall list generation → not a medical device
- Treatment-note drafting (with practitioner review) → not a medical device, because the practitioner authorises every clinical record before it persists
- Invoice analysis → not a medical device

If a future capability crosses this line (e.g. an AI-generated treatment recommendation that goes to the patient without practitioner review), the product would need TGA classification. We will not ship such capabilities without explicit TGA review.

---

## 8. Procurement officer checklist

| Question | Answer |
|---|---|
| Is the codebase open source and inspectable? | **Yes.** MIT licence. Public GitHub at https://github.com/au-healthtech/au-cliniko-mcp. |
| Is there an audit log? | **Yes.** Per-tool-call, persisted to local SQLite. See §2 APP 11. |
| Is PHI handled separately from administrative data? | **Yes.** `@phi_flagged` decorator categorises every tool response. |
| Are keys encrypted at rest? | **Yes.** Fernet via `src/au_cliniko_mcp/vault.py`. |
| What's the audit-log retention? | 7 years default. Configurable. |
| Is data sent overseas? | **Only if** the practice's Anthropic plan routes through non-AU regions. Cliniko data stays on the AU shard. See APP 8. |
| Is there a Privacy Impact Assessment template? | **Yes.** `docs/PIA-template.md`. |
| Who is the maintainer? | Tradd Horne, AHPRA-registered podiatrist (POD0001880268), Principal Podiatry Pty Ltd, ABN 19 615 606 347. |
| What happens if the maintainer disappears? | MIT-licensed. Fork-friendly. Code, audit logs, vault, and patient data all remain on the practice's machine. |
| Is there a breach-response plan? | **Yes.** §3 above. |
| Is the product a medical device? | **No.** §7. |

---

## 9. What this product is NOT

To be explicit:

- **Not a substitute for clinical judgement.** Every clinical record drafted by Claude must be reviewed and finalised by the practitioner in Cliniko's UI.
- **Not a medical device.** No diagnostic or therapeutic claims.
- **Not a HIPAA-compliant tool for US healthcare.** AU-focused.
- **Not a multi-tenant SaaS in the local-install configuration.** Each install is single-tenant. Multi-tenant hosted gateway is Phase F.
- **Not certified under SOC 2, ISO 27001, or HITRUST.** Those certifications are appropriate at larger commercial scale; we are pre-revenue. We will pursue SOC 2 Type I when enterprise clinic groups become customers.

---

## 10. Updates to this document

Material changes will be tagged in the GitHub repo. Subscribe to the repo's releases for notifications.
