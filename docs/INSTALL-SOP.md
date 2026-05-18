# Install SOP — au-cliniko-mcp clinical install

> Standard operating procedure for installing this MCP for a real allied-health
> practice. Follow every step in order. Skipping steps shifts compliance
> liability to whoever ran the install.

**Audience**: contractor / consultant / configurator doing a paid install for a clinic.
**Time**: 3-4 active hours over 2-3 days (wall clock gated by AWS Bedrock approval).
**Pre-requisite**: practice owner has read and agreed to the COMPLIANCE.md framework.

---

## Stage 0 — Pre-flight (must complete BEFORE creating any clinical account)

### 0.1 Confirm the practice owner has signed off on:
- [ ] `docs/COMPLIANCE.md` read in full
- [ ] `docs/PIA-template.md` completed and filed
- [ ] Privacy policy updated to disclose AI-assisted administrative processing
- [ ] Suggested patient-consent wording added to intake forms

### 0.2 Confirm scope-of-work
- [ ] Whether this install needs AU data residency (Bedrock ap-southeast-2)
      or whether direct Anthropic API is acceptable for the specialty
- [ ] Whether they want the optional monthly retainer ($79-99/mo)
- [ ] Whether other workflow integrations (Gmail / Calendar / Drive) will be
      added — these go on the SAME dedicated clinical account, not a personal one
- [ ] Cancellation + data-handover terms agreed in writing

### 0.3 Decide on architecture path
- [ ] **Path A — Direct Anthropic** (simpler, US-hosted, fine for most allied-health)
- [ ] **Path B — AWS Bedrock ap-southeast-2** (gold standard for compliance-anxious specialties)
- [ ] **Path C — GCP Vertex AI australia-southeast1** (alternative AU residency)

---

## Stage 1 — Dedicated clinical Claude account

### 1.1 Create the account
- [ ] Email: practice-domain (e.g. `claude@<practicedomain>.com.au`)
- [ ] NEVER a practitioner's personal Gmail
- [ ] Strong password generated + stored in the practice's password manager
      (1Password / Bitwarden / similar), NOT in a personal vault
- [ ] 2FA enabled with TOTP (Google Authenticator / Authy / 1Password TOTP)
- [ ] TOTP backup codes printed and stored in the practice safe

### 1.2 Lock down the account settings

Go to **claude.ai → Settings** and confirm:

| Setting | Required state |
|---|---|
| Privacy → "Help improve Claude" | **OFF** |
| Personal → "Generate memory from chat history" | **OFF** |
| Personal → "Search and reference past chats" | **OFF** |

Screenshot each setting in its disabled state, save to the practice's
compliance records folder.

### 1.3 Confirm what is ALLOWED on this account
- [ ] **Only** the Cliniko MCP at install time
- [ ] No personal Gmail / Drive / Calendar connectors
- [ ] No personal Slack / Notion / Linear connectors
- [ ] If the practice wants practice-Gmail / practice-Drive later, those are
      added under a SEPARATE scoped engagement with its own PIA addendum

---

## Stage 2 — AWS Bedrock setup (Path B only)

### 2.1 AWS account
- [ ] Create or confirm an AWS account (practice-owned, not personal)
- [ ] Billing email is the practice owner's
- [ ] Credit card on file is the practice's

### 2.2 Bedrock model access
- [ ] Sign in to AWS Console → switch to ap-southeast-2 (Sydney) region
- [ ] Navigate to Bedrock → Model access
- [ ] Request access to:
  - Anthropic Claude Sonnet 4.6
  - Anthropic Claude Haiku 4.5
- [ ] Submit (approval typically 24-48 hours)
- [ ] Wait — do NOT proceed to next stages until both models show "Access granted"

### 2.3 IAM user for Claude Desktop
- [ ] Create IAM user with Bedrock invoke permissions only (NOT admin)
- [ ] Attach policy: `AmazonBedrockReadOnlyAccess` + custom inline policy
      restricting `bedrock:InvokeModel` to ap-southeast-2 + Claude models only
- [ ] Generate access key + secret
- [ ] Store credentials in practice password manager

### 2.4 Configure Claude Desktop to use Bedrock
- [ ] Edit `~/Library/Application Support/Claude/claude_desktop_config.json`
      (macOS) or equivalent on Windows
- [ ] Add Bedrock provider config + AWS credentials
- [ ] Test: send a hello to Claude, confirm response comes back without errors
- [ ] Verify ap-southeast-2 region in AWS CloudTrail logs

---

## Stage 3 — au-cliniko-mcp install

### 3.1 Install the MCP on the practice's primary workstation
- [ ] `brew install pipx && pipx ensurepath` (Mac) or equivalent
- [ ] `pipx install git+https://github.com/au-healthtech/au-cliniko-mcp.git`
- [ ] `which au-cliniko-mcp` → record the path

### 3.2 Generate Cliniko API key
- [ ] Practice admin logs into Cliniko → My Info → Manage API keys
- [ ] Create a NEW key labelled "au-cliniko-mcp"
- [ ] NEVER reuse an existing key from another integration
- [ ] Copy the key — do NOT screenshot, copy direct from the Cliniko UI

### 3.3 Add the MCP to Claude Desktop config
- [ ] Edit `claude_desktop_config.json`
- [ ] Add the `au-cliniko` server block with the practice's Cliniko key + a
      User-Agent email belonging to the practice
- [ ] Restart Claude Desktop (Cmd+Q + reopen)
- [ ] Verify the MCP shows up in the tool list with 38 tools + 6 prompts

### 3.4 Smoke test
- [ ] Ask Claude: "Using the au-cliniko tools, list our businesses." → expect
      the practice name returned
- [ ] Ask Claude: "List the practitioners on the account." → expect the
      practice's clinicians
- [ ] Confirm an audit-log entry exists at `~/.au-cliniko-mcp/audit.db`

---

## Stage 4 — Workflow setup

### 4.1 Configure KPI preferences
- [ ] Walk the practice owner through `set_kpi_preferences()`
- [ ] Pick which KPIs go into their weekly digest
- [ ] Confirm the comparison period (default: 7 days)

### 4.2 Walk through the 6 prompt recipes
- [ ] Demo `monday_morning_digest` — show the output
- [ ] Demo `weekly_recall_review` — explain the approval gate before any
      message is sent
- [ ] Demo `invoice_chase_workflow` — explain the Spam Act + AHPRA framing
- [ ] Demo `no_show_followup_workflow`
- [ ] Demo `appointment_calendar_sync` — if they have Calendar connected
- [ ] Demo `end_of_month_report`

### 4.3 First-month support
- [ ] Schedule a 30-day check-in
- [ ] Provide a contact channel for "did Bedrock break / did Cliniko quirk hit"
      issues
- [ ] If the monthly retainer is in scope, agree the cadence

---

## Stage 5 — Handover documentation

Hand the practice owner a folder containing:
- [ ] This SOP filled in (each checkbox actually ticked)
- [ ] The completed `docs/PIA-template.md`
- [ ] Screenshots of all settings configured per Rule 0.2
- [ ] List of staff trained + dates
- [ ] Copy of the practice's privacy policy patch
- [ ] Recommended audit-log review cadence (weekly skim of
      `~/.au-cliniko-mcp/audit.db`)
- [ ] Cancellation / data-handover procedure document
- [ ] Contact channel + retainer terms (if applicable)

---

## Stage 6 — Periodic review (post-install, every 12 months)

- [ ] Rotate the Cliniko API key
- [ ] Re-run the PIA template — update for changes in MCP version, Anthropic
      plan, staff
- [ ] Audit-log skim for unusual access patterns
- [ ] Verify training opt-out toggles are still OFF
- [ ] Verify no personal connectors have been added to the clinical account
- [ ] Update privacy policy if anything material has changed