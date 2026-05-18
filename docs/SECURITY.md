# Security architecture — au-cliniko-mcp

**Last reviewed**: 2026-05-18.
**Threat audience**: practice IT, security-conscious clinic owners, procurement reviewers.

---

## 1. Threat model

The MCP is a local-install component that lets an LLM (Claude) call Cliniko's REST API on behalf of a clinic's authorised staff. The trust boundary looks like this:

```
[ Authorised staff member ] ←→ [ Claude Desktop / Claude.ai ] ←→ [ MCP process on workstation ]
                                                                          ↓ HTTPS
                                                                  [ Cliniko AU shard ]
```

### Adversaries we defend against
1. **Casual misuse by the LLM itself** — Claude making mistakes, hallucinating answers, calling tools with wrong scope.
2. **Cost-runaway accidents** — well-meaning queries that fan out across thousands of patients.
3. **Workstation compromise after install** — a process running as a different OS user on the same machine.
4. **Backup / disk-image exposure** — the workstation's drive ending up somewhere outside the clinic.
5. **Insider misuse** — a staff member running queries they shouldn't, with subsequent need to audit.
6. **Cliniko credential leak** — the API key escaping into logs, support tickets, or screenshots.

### Adversaries explicitly NOT in scope for v1
- **Nation-state attackers with workstation access** — the home directory is the trust zone. Not defended against a kernel-level adversary.
- **Hardware attacks** — cold-boot, evil-maid. Out of scope at this product tier.
- **Malicious LLM model** — we treat Claude as a trusted reasoning engine, not an adversarial one. Phase D will add per-tool consent gates that mitigate this.

---

## 2. What we do — the controls

### 2.1 Cost-confirmation gating (`Approach A + B`)
Two layers:
- **System prompt** — embedded in FastMCP `instructions` — tells Claude to PAUSE and ask the user before any fan-out > 100 patients, offering concrete scope options.
- **Hard gate on expensive tools** — `list_all_patients` and `get_appointment_invoice_join` refuse to run beyond a configurable threshold unless explicitly confirmed. The refusal payload includes a cost estimate so the LLM has the data to ask intelligently.

### 2.2 Encrypted vault for secrets
- Location: `~/.au-cliniko-mcp/vault.db` (SQLite, encrypted-cells) + `~/.au-cliniko-mcp/vault.key` (chmod 600).
- Algorithm: **Fernet** (AES-128-CBC + HMAC-SHA-256, time-versioned tokens). Reference: https://cryptography.io/en/latest/fernet/
- Tenant isolation: per-tenant namespace from day 1. Multi-tenant hosted gateway (Phase F) will replace local Fernet with KMS-backed envelope encryption per tenant, keeping the same vault interface.
- **Why not OS keychain in v1**: macOS Keychain is the gold standard for single-machine secrets but doesn't port to Linux / Windows cleanly. v1 ships with Fernet for portability. Phase F adds keychain integration for Mac users who prefer it.

### 2.3 Audit log
- Location: `~/.au-cliniko-mcp/audit.db` (SQLite, WAL mode for safe concurrent reads).
- Every PHI-flagged tool call records: timestamp (UTC), tool name, patient_id (if present), practitioner_id (if present), PHI categories touched, result status, elapsed ms, error (if any), redacted args.
- **Args are redacted before storage** — see §2.4. Patient IDs are preserved (they're already opaque); free-text fields are blanked.
- Retention: 7 years default (AU health-record statutory minimum). Pruning is an explicit administrative call (`AuditLog.prune`), never automatic.
- Failure isolation: if audit write fails, the tool call still succeeds. Audit failures log to stderr but never break the caller.

### 2.4 PHI redaction in audit args
Free-text fields blanked before storage:
- `content`, `note`, `notes`, `message`, `body`, `subject`
- `first_name`, `last_name`, `email`, `mobile`, `phone`
- `date_of_birth`, `dob`, `address`, `city`, `post_code`
- `query` (because name-search fragments are PHI)

Blanking shape: `"<redacted:N>"` where N is the original length. Lets us prove "a 142-character message was drafted at this time" without exposing content.

### 2.5 Consent-gated writes
- Every write tool defaults to draft mode.
- `draft_treatment_note` always sets `draft=True`. The practitioner must finalise in Cliniko's UI.
- Audit log distinguishes `uncommitted_draft` from `committed`.

### 2.6 No stdout pollution
Reference implementations (BoabAI) printed `console.log` lines to stdout during demo flows, which corrupts the JSON-RPC stdio MCP transport. Our codebase uses Python's `logging` module exclusively; tool responses are clean JSON; nothing leaks into the stdio frame.

### 2.7 Tight HTTP timeouts
15-second per-request timeout with 3 retries (1+2+4 second exponential backoff). Maximum 7-second worst-case delay before the MCP fails fast. Prevents Claude Desktop's 4-minute tool timeout from being reached, which keeps the LLM responsive even when Cliniko is slow.

### 2.8 Shard auto-detection
Cliniko API key suffixes carry the shard (`-au1` ... `-au5`, `-uk1`, etc.). We parse this on boot and ALWAYS use the AU shard for AU practitioner keys. No hardcoded `au4` default that would silently misroute requests like the reference TypeScript MCPs do.

---

## 3. Trust assumptions

| Component | Trusted to do | NOT trusted to do |
|---|---|---|
| **The OS** (macOS / Linux / Windows) | Enforce file permissions on home-dir files | Defend against root-level compromise |
| **The user account** that runs Claude Desktop | Hold the vault key (chmod 600) | Be free of other processes running as the same user |
| **Claude** (the LLM) | Reason correctly over tool descriptions; honour the system prompt's cost-confirmation policy | Skip the consent gate on writes; ignore the cost gate |
| **Anthropic API** | Not train on API traffic; honour the standard DPA | Be available 100%; not have outages |
| **Cliniko API** | Reject calls with revoked keys | Provide perfect uptime; document every quirk |

---

## 4. Known limitations of v1

1. **In-memory plaintext during a session.** The Cliniko API key must be available in process memory to sign each HTTP request. We cannot do better without an HSM. The vault encryption protects at-rest.
2. **Vault key file is unprotected beyond chmod 600.** Any process running as the same user can read it. Phase F (hosted gateway) replaces with KMS.
3. **Audit log is local-only.** A workstation reformatted without backup loses the audit history. Practices wanting long-term audit retention should back up `~/.au-cliniko-mcp/` to encrypted backup.
4. **No tamper-evident hashing on the audit log.** A determined adversary with file access could rewrite records. Phase C-2 will add hash-chained audit entries.
5. **PHI redaction is blocklist-based.** A new free-text field we don't know about won't be blanked. Worth a periodic review of `PHI_TEXT_FIELDS` in `audit.py` when new tools are added.
6. **Anthropic is a US data processor.** APP 8 applies. Practices must reflect this in their privacy policy and patient-facing materials.

---

## 5. Operational security recommendations for the installing practice

1. **Use a dedicated Cliniko API key for the MCP.** Not the same key as any other automation. Easier to revoke without breaking other integrations.
2. **Set a low cost-confirmation threshold** if junior staff use the MCP. The default is 300 patients; for a clinic where only the owner runs the MCP, 3000+ is fine; for a multi-user clinic where receptionists also use it, drop to 100.
3. **Back up `~/.au-cliniko-mcp/`** to encrypted backup as part of the workstation's normal backup routine.
4. **Rotate the Cliniko API key periodically** (every 90 days). Update via `Vault.put("cliniko_api_key", <new>)` after revoking the old one in Cliniko.
5. **Audit-log review cadence**: weekly skim of `query_recent(limit=200)` is a reasonable hygiene practice. Flag anything unexpected.
6. **Workstation hardening**: full-disk encryption (FileVault / BitLocker / LUKS), auto-lock screen, no shared user account.
7. **For multi-staff use**: each staff member should have their own OS user account, so audit logs are per-user.

---

## 6. Disclosure policy

If you find a security issue in `au-cliniko-mcp`:
- **Do not** open a public GitHub issue with exploit details.
- Email: tradd@principalpodiatry.com.au with `[SECURITY]` in the subject line.
- We will acknowledge within 5 business days.
- For high-severity issues we will agree a disclosure timeline and credit the reporter (if desired).
- No bounty programme at this stage (pre-revenue project).

---

## 7. Forthcoming security work

Phase C-2 (next sprint):
- Hash-chained audit entries (tamper detection)
- macOS Keychain integration for vault.key
- Optional: per-PHI-category configurable retention

Phase F (hosted gateway, weeks 7-10):
- KMS-backed envelope encryption replacing local Fernet
- Per-tenant audit log isolation
- Bearer-token authentication on the HTTP transport
- TLS termination at Cloudflare / Caddy with strict ciphers
- SOC 2 Type I preparation (only if enterprise demand validates the cost)
