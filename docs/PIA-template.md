# Privacy Impact Assessment — au-cliniko-mcp installation

> **Template instructions**: the practice fills in this PIA before installing the MCP in production. It is structured per the OAIC's [Guide to undertaking privacy impact assessments](https://www.oaic.gov.au/privacy/guidance-and-advice/guide-to-undertaking-privacy-impact-assessments). Time to complete: ~45 minutes. Retain alongside your other compliance records.

---

## Section 1 — Project description

**Practice name**: ____________________________________

**Practice ABN**: _____________________________________

**Address**: __________________________________________

**Privacy contact (name + role)**: _____________________

**Date of this PIA**: _________________________________

**Project description (one paragraph)**:
The practice will install `au-cliniko-mcp` on the workstations of authorised staff. The MCP is a software component that lets Claude (Anthropic's AI assistant) query the practice's Cliniko account on behalf of the authorised user. Use cases: patient list queries, schedule overview, recall list generation, invoice chase reports, treatment-note drafting (with practitioner review).

**Names of authorised users**:
- ___________________________________________________
- ___________________________________________________
- ___________________________________________________

---

## Section 2 — Information flows

| Stage | Personal information involved | Where it goes | Why |
|---|---|---|---|
| Staff types a question into Claude | The question (which may contain a patient name) | Anthropic API endpoint (US region by default) | LLM reasoning |
| Claude calls an MCP tool | Tool name + arguments | Local MCP process on workstation | Tool dispatch |
| MCP calls Cliniko API | Cliniko API key + tool args | `api.au[1-5].cliniko.com` (AU shard) over HTTPS | Data retrieval |
| Cliniko returns patient data | PHI (demographics, contact, clinical notes, billing) | Returned to MCP, then to Claude in the tool response | Answer the question |
| Claude shows the answer | Subset of the data, formatted | The staff member's screen | Final answer |
| Audit log writes a record | Tool name, timestamps, patient IDs, **redacted args** | `~/.au-cliniko-mcp/audit.db` on workstation | Compliance / forensics |

---

## Section 3 — Privacy risks identified

### Risk 1: PHI sent to a US-based LLM provider
**Likelihood**: certain (every query).
**Severity**: low–moderate. Anthropic's standard policy is no training on API traffic. The data is in transit and processed in-memory; it is not retained for training.
**Mitigations**:
- ✅ Anthropic plan has data-processing addendum (DPA) signed: ☐ Yes ☐ No
- ✅ Patient-facing privacy policy updated to mention AI assistance: ☐ Yes ☐ No
- ✅ Suggested wording added to consent forms: ☐ Yes ☐ No

**Suggested patient-facing wording**:
> "Our practice uses AI-assisted administrative tools that may process information about your record for the purpose of scheduling, billing, and clinical note preparation. These tools operate under industry-standard contractual safeguards and do not use your information to train AI models. You may opt out at any time by contacting our privacy officer."

### Risk 2: Workstation compromise exposes Cliniko credentials
**Likelihood**: low (clinic environment, locked workstations).
**Severity**: high. Cliniko API key inherits the issuing user's full permissions.
**Mitigations**:
- ✅ Workstation full-disk encryption enabled: ☐ Yes ☐ No
- ✅ Auto-lock screen ≤ 5 minutes: ☐ Yes ☐ No
- ✅ Vault encryption confirmed (file at `~/.au-cliniko-mcp/vault.key` is chmod 600): ☐ Yes ☐ No
- ✅ Cliniko API key rotation schedule (every 90 days): ☐ Yes ☐ No
- ✅ Key revocation procedure documented + tested: ☐ Yes ☐ No

### Risk 3: Audit log loss prevents breach reconstruction
**Likelihood**: low.
**Severity**: moderate. NDB scheme requires being able to identify affected individuals.
**Mitigations**:
- ✅ `~/.au-cliniko-mcp/` is included in encrypted backup: ☐ Yes ☐ No
- ✅ Backup retention ≥ 7 years: ☐ Yes ☐ No
- ✅ Audit-log review cadence agreed (weekly recommended): ☐ Yes ☐ No

### Risk 4: LLM hallucination causes incorrect patient information to be acted on
**Likelihood**: moderate (LLMs make mistakes).
**Severity**: depends on what's acted on. Clinical notes are highest risk; admin queries lowest.
**Mitigations**:
- ✅ Treatment notes ALWAYS drafted, never finalised — practitioner reviews in Cliniko UI: ☐ Yes ☐ No
- ✅ Staff trained that the MCP is an assistant, not an authoritative source: ☐ Yes ☐ No
- ✅ Cost-confirmation gating active (prevents accidental large fan-outs): ☐ Yes ☐ No
- ✅ Staff know to verify any clinically-significant output by re-querying Cliniko directly: ☐ Yes ☐ No

### Risk 5: Cost-runaway when querying all-patient datasets
**Likelihood**: moderate without training.
**Severity**: financial (Anthropic API costs) but not privacy.
**Mitigations**:
- ✅ Cost-confirmation threshold reviewed for this clinic (default 300, recommended 100 for multi-staff): ☐ Yes ☐ No
- ✅ Anthropic API spend cap set: ☐ Yes ☐ No

### Risk 6: PHI in clinical notes copied or screenshotted by staff
**Likelihood**: moderate.
**Severity**: depends on recipient.
**Mitigations**:
- ✅ Practice's existing PHI handling policy referenced in staff training: ☐ Yes ☐ No
- ✅ Audit log review will surface unusual access patterns: ☐ Yes ☐ No

### Additional risks specific to this practice (write below)
- _____________________________________________________
- _____________________________________________________

---

## Section 4 — Privacy management decisions

**Are the risks acceptable to proceed with the installation?**

☐ Yes, with all mitigations above implemented.
☐ Yes, with documented exceptions: ______________________
☐ No, will defer until ___________________________________

**Designated privacy officer**: _______________________

**Date staff training to be completed**: ________________

**Date of first audit-log review**: _____________________

**Date for next PIA review (recommended 12 months)**: ___

---

## Section 5 — Signatures

**Practice owner/director**: ___________________________ Date: __________

**Privacy officer (if separate)**: ______________________ Date: __________

**IT lead (if separate)**: ______________________________ Date: __________

---

## Section 6 — Reviewer notes

Retain this PIA with the practice's compliance records. Provide to OAIC if asked. Update if material changes to the MCP version, the Anthropic plan, or the practice's staffing.

**MCP version assessed**: au-cliniko-mcp v_________ (commit hash: ___________)

**Anthropic plan in use**: ___________ (e.g. Pro, Team, Enterprise, Direct API)

**Cliniko shard**: au___

---

## Appendix — useful links

- OAIC PIA guidance: https://www.oaic.gov.au/privacy/guidance-and-advice/guide-to-undertaking-privacy-impact-assessments
- OAIC NDB scheme: https://www.oaic.gov.au/privacy/notifiable-data-breaches
- AHPRA advertising guidelines: https://www.ahpra.gov.au/Resources/Advertising-hub
- Anthropic privacy policy: https://www.anthropic.com/legal/privacy
- Cliniko privacy: https://www.cliniko.com/policies/privacy
- This MCP's compliance posture: `docs/COMPLIANCE.md`
- This MCP's security architecture: `docs/SECURITY.md`
