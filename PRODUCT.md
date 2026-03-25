# AXIS Producer — Session Handoff
*Generated from product strategy session, March 23, 2026*
*Owner: Stu / CouloirGG LLC*

---

## What AXIS Is

AXIS (Ambient eXperience Intelligence System) is an ambient session capture and analysis tool. It runs silently in the background, listens to voices and reads written communications, transcribes locally, and periodically sends structured batches to the Claude API for analysis. Output is structured session notes: Decisions Locked, Ideas Generated, Open Questions, Action Items, Watch List, Blockers, Key Discussion.

It was road-tested during a game jam weekend (JunkyardTD) and successfully compiled 5+ hours of conversation with only minor errors — all hardware/transcription issues, not analysis failures. The reasoning layer is proven.

**Full name (internal only):** Ambient eXperience Intelligence System
**Public name:** AXIS (just AXIS — the expansion is background only)

---

## Current Codebase State

Repo: `AXIS_Producer/` (Python, Windows-native)

### Core pipeline
- `axis_producer.py` — entry point, CLI args, thread orchestration
- `capture.py` — mic capture via sounddevice + webrtcvad (VAD level 2)
- `loopback_capture.py` — system audio via WASAPI loopback (Windows-specific)
- `transcriber.py` — local Whisper via faster-whisper
- `producer.py` — BatchProducer: collects transcript buffer, sends to Claude API every N minutes, appends to session_log.md
- `session_controller.py` — full state machine orchestrating all components
- `launcher.py` — single entry point, starts HTTP server + tray app
- `tray_app.py` — system tray UI (pystray)
- `dashboard.html` — local web dashboard

### Monitors (optional, settings-driven)
- `slack_monitor.py` — polls Slack channels
- `email_monitor.py` — polls Outlook inbox via pywin32
- `calendar_monitor.py` — Outlook calendar
- `vcs_monitor.py` — git/p4v activity
- `claude_monitor.py` — watches Claude Code JSONL conversation files
- `chat_monitor.py` — clipboard monitor

### Cloud / sync
- `cloud_sync.py` — pushes events to Supabase, subscribes to remote events, runs cross-stream synthesis
- `cloud_db.py` — Supabase client wrapper with RLS
- `digest.py` — post-session digest processor
- `team_bot.py` — Telegram bot for team awareness

### Other
- `settings.py` — Settings dataclass, persisted to tray_settings.json
- `setup.py` / `setup.bat` — current setup wizard (needs replacement)
- `vad_detector.py` — voice activity detection
- `notifications.py` — Windows notifications
- `session_log.md` — output format (markdown, timestamped batches)

### Current stack
- Python 3.11
- faster-whisper (local transcription, default model: base.en)
- Anthropic SDK (claude-sonnet-4-20250514, 1024 max tokens per batch)
- Supabase (shared event store, currently one shared instance — NEEDS isolation)
- pystray + Pillow (tray app)
- pywin32 (Outlook integration, Windows only)
- sounddevice + webrtcvad-wheels (audio capture)
- python-telegram-bot (team bot)

### Known issues to fix
- Supabase URL and anon key are hardcoded in plaintext in `setup.bat` and `setup.py` — must be removed before any public release
- Identity is just a name string — no real auth
- Setup requires Python, pip, manual .env editing — painful for non-developers
- Windows-only (WASAPI loopback) — Mac deferred intentionally, not a current target

---

## Product Decisions Locked

### Target market
- Teams of fewer than 20 people
- Primary: dev teams, business teams, agencies, clinics
- Entry point: team lead or eng manager, bottom-up SaaS sale
- NOT enterprise top-down — no IT approval process, self-serve

### Platform targets
- **Desktop:** Windows 11 (primary, full feature set)
- **Mobile:** Android + iPhone (both tiers, see below)
- **Mac:** explicitly deferred — not a current target market

### Two product tiers

**AXIS Team — $49.99/month flat, up to 20 seats**
- Desktop full feature set
- Phone as wireless mic (local network only, streams to desktop node)
- Cloud sync, shared team workspace
- Desktop runs Whisper locally — no Groq cost
- Your margin: ~$25/month at early scale

**AXIS Pro — $24.99/seat/month**
- Standalone mobile mode — no desktop required
- Exec/traveler use case: record anywhere, get notes on phone
- Cloud Whisper via Groq ($0.11/audio hour)
- Claude API batch analysis same as desktop
- Your margin: ~$10-11/seat
- Can be purchased standalone OR as add-on to Team license

### Mobile architecture decision
- Phone-as-mic (Team tier): streams audio over local network to desktop node, desktop runs Whisper, zero extra cost
- Standalone mobile (Pro tier): chunked audio upload every 60-90 seconds → your backend → Groq transcription → Claude batch → results delivered to phone
- **Chunked upload chosen over streaming for v1** — simpler, sufficient for the use case
- Mobile app: React Native / Expo (familiar from LAMA Mobile)

### The demo moment
QR code pairing is the centerpiece of onboarding and the live demo:
1. Desktop shows QR code
2. Phone scans it
3. Connected — phone is live mic
This sequence should be polished, fast, and work first try every time. It's the 30-second pitch.

### Backend requirement
Standalone mobile requires a real backend. The backend is now **Phase 1**, not optional:
- Auth
- API proxy (Anthropic key — users never manage this)
- Groq Whisper endpoint
- Supabase team isolation
- Billing (Stripe)

**Recommended backend:** Cloudflare Workers or Railway
**API key proxy:** Cloudflare Workers free tier (100k requests/day) — near zero cost

---

## Architecture Changes Required

### Team isolation (CRITICAL — blocks launch)
Currently one shared Supabase instance for all users. Each team needs:
- Scoped project/namespace in Supabase via Row Level Security (RLS)
- Team invite flow (invite code or link) replacing manual key sharing
- No user ever sees or manages Supabase credentials

### Auth layer
- Email + password or Google SSO minimum
- Ties to billing and team isolation
- Identity must be more than a name string

### API key proxy
- Your backend proxies all Anthropic API calls
- Users have no Anthropic account, no API key
- You absorb cost, price it into subscription
- Removes biggest setup friction point

### One-click installer
- PyInstaller bundle for Windows
- No Python prerequisite for end users
- No pip, no CLI, no manual .env editing
- Auto-update via GitHub Releases (check on startup, non-blocking banner)

---

## Workspace Type System

Add `workspace_type` enum to Settings. Five presets drive feature flag defaults and inject a context hint into the Claude system prompt.

### Presets and feature flags

| Workspace | Slack | VCS | Calendar | Email | Claude monitor |
|-----------|-------|-----|----------|-------|----------------|
| Dev team | on | on | on | on | on |
| Business team | on | off | on | on | off |
| Healthcare/clinic | off | off | on | on | off |
| Agency/creative | on | off | on | on | off |
| Custom | user-defined | | | | |

### New Settings fields to add
```python
workspace_type: str = "dev_team"  # dev_team | business_team | healthcare | agency | custom
workspace_context: str = ""  # free text injected into Claude system prompt
                              # e.g. "We are a 12-person veterinary clinic."
output_terminology: dict = {}  # optional label overrides, e.g. {"Blockers": "Waiting on"}
privacy_preset: str = "standard"  # standard | strict | hipaa_aware
```

### System prompt injection
Append `workspace_context` to the existing system prompt in `producer.py` when set:
```
[existing SYSTEM_PROMPT]

Context: {workspace_context}
```

### Healthcare preset behavior
- `privacy_preset` auto-set to `hipaa_aware`
- `cloud_sync` defaults to `False`
- Show explicit warning in UI: "Cloud sync is disabled. AXIS is processing locally only."
- User must manually enable cloud sync and acknowledge the risk

---

## NUX (New User Experience)

Replaces current `setup.py` / `setup.bat`. Web-based flow, works for both desktop first-run and mobile.

### Desktop flow (7 steps)
1. **Account** — email + password or Google SSO, 14-day trial starts, no credit card
2. **Team or solo** — sets billing path (Team vs Pro), skips irrelevant steps
3. **Workspace type** — visual card picker, plain-language descriptions, one click sets all defaults
4. **Mic check** — speak 5 seconds, pipeline runs, shows "Pipeline verified" or specific failure
5. **QR pairing** (team only) — show QR, prompt to open mobile app and scan, show connected device
6. **Invite teammates** — email invite field, sends auto-join link, they skip to mic check
7. **Done** — single "Start session" button, AXIS starts, dashboard opens

### Mobile flow (3 steps)
1. **Sign in or scan** — existing account signs in; QR scan auto-joins team
2. **Mic permission** — single prompt with plain-language explanation of what's recorded and where it goes
3. **Mode shown clearly** — "Connected to [Team Name]" (mic mode) or "Standalone session" (Pro mode)

---

## Feedback and Bug System

### Pipeline
In-app form → your backend → GitHub Issues → you triage weekly

### In-app feedback
- Always visible in tray menu and mobile footer
- Three types: Bug, Suggestion, General
- Auto-attaches: OS, app version, workspace type, last error log (with user consent)
- User writes one sentence

### Crash reporting
- Sentry free tier (~5k errors/month)
- Non-negotiable — catches issues before users file tickets
- Groups duplicates, surfaces patterns

### Triage cadence
- Check GitHub Issues once per week
- Sentry pages only on new crash types or error rate spikes
- Everything else is async

---

## Legal Documents (completed)

Two Word documents generated and validated. Stored separately from this repo.

### AXIS_Legal_Documents.docx — public-facing (5 documents)
1. **Terms of Service** — covers acceptable use, consent obligations, AI disclaimer, billing, liability
2. **Privacy Policy** — exhaustive detail on what's captured, where it goes, who sees it, retention
3. **Data Processing Agreement** — for business customers, covers subprocessors, breach notification, audit rights
4. **Acceptable Use Policy** — prohibited uses including government/law enforcement prohibition (Section 5)
5. **Consent Notice Template** — room posting version and email version, with two-party consent state list

### AXIS_Internal_Policy.docx — internal only, never publish
- Full prohibited use detection and enforcement policy
- Signup-time controls: domain blocklist, keyword screening, self-attestation checkbox
- Behavioral signals and ongoing monitoring procedures
- Step-by-step enforcement: suspend → investigate → terminate → notify
- Subpoena handling procedure
- Enforcement log format
- Technical implementation checklist (Phase 1/2/3)

### Key legal decisions locked
- **Beta ToS addition:** "Beta users agree to use AXIS for internal team communications only. Recording customers, patients, or non-employee third parties is a breach of beta terms."
- **Liability model:** AXIS is a tool like a microphone. Recording responsibility sits entirely with the operator. Company is not liable for misuse.
- **Government prohibition:** Explicit AUP section bars government agencies, law enforcement, military, immigration enforcement, and their contractors. Values-based restriction, not technical. Terminate on discovery, full refund.
- **HIPAA approach:** Not HIPAA-certified. Healthcare preset defaults cloud sync off. BAA required before enabling cloud sync in healthcare context. Local-only mode is the HIPAA-safe path.
- **Two-party consent:** Washington state is two-party consent. Beta at mom's clinic requires consent notice before deployment.
- **AI output disclaimer:** Every session log footer must include: "AI-generated summary. Not a verbatim transcript. Not a legal record. Verify important decisions independently."

### Subprocessors (in DPA)
- Anthropic, PBC — AI analysis
- Groq, Inc. — Pro mobile transcription
- Supabase, Inc. — data storage
- Stripe, Inc. — payments
- Sentry, Inc. — crash reporting

---

## Beta Customer

**Organization:** Animal rescue clinic (unnamed)
**Contact:** Stu's mom (finance department)
**Team size:** ~20 (vets, front desk, owners)
**Pain point:** All-drive-by communication, no tracking, decisions lost
**Workspace preset:** Healthcare/clinic
**Cloud sync:** OFF (beta ToS restriction to internal team only, HIPAA surface area avoidance)
**Cost to them:** Zero, forever. In exchange for structured feedback.
**Feedback cadence:** Weekly 15-min voice note or text: what worked, what was confusing, what was missing.

**Before deploying:** Consent notice must be posted/distributed per Washington state two-party consent law.

---

## Prohibited Use Detection — Technical Backlog

### Phase 1 (ship with auth)
- Email domain blocklist in backend config (not hardcoded): block `.gov`, `.mil`, `.dhs.gov`, `.ice.dhs.gov`, `.cbp.dhs.gov`, `.fbi.gov`, `.dea.gov` — fail silently with generic error
- Organization name keyword scanner: flag (not block) on: police, sheriff, federal, government, agency, enforcement, homeland, immigration, ICE, FBI, DEA, ATF, CBP, DHS, NSA, CIA, DOJ, DOD, "department of", "bureau of", "office of", task force, fusion center
- Self-attestation checkbox at signup — logged with timestamp + IP, stored permanently as legal artifact
- Flagged account queue in admin dashboard — manual review within 48 hours before cloud sync activates

### Phase 2 (post-launch)
- Weekly automated scan of `workspace_context` fields against keyword list
- Anomaly detection flags: high session volume on single seat, 10+ devices on one account, bulk signups from same IP
- Admin dashboard enforcement actions: suspend, terminate, refund, block domain

### Phase 3 (hardening)
- Stripe BIN screening for government purchase cards
- IP rate limiting on account creation
- Canary test accounts to verify blocklist after deployments

---

## Phase 1 Build Order

The backend must exist before anything else. Build in this sequence:

1. **Backend skeleton** — auth, team provisioning, Supabase RLS isolation, API proxy, Groq endpoint
2. **QR pairing + phone-as-mic** — this is the demo, build it well, ship it early
3. **Pro standalone mobile** — Expo app, chunked audio upload, Groq pipeline, results to phone
4. **Win11 installer** — PyInstaller bundle wired to backend, replaces setup.bat
5. **Billing** — Stripe, flat Team tier + per-seat Pro, 14-day free trial
6. **Workspace type system** — Settings additions, NUX flow, system prompt injection

## Phase 2 Build Order
7. Auto-update (GitHub Releases)
8. Crash reporting (Sentry)
9. Privacy controls UI (mute button, private mode toggle visible in tray)
10. Self-serve onboarding docs + status page
11. Feedback/bug system (in-app form → GitHub Issues)
12. Prohibited use detection Phase 1 controls

## Phase 3 (post-launch)
13. Integrations: Slack output, Linear, Jira, Notion
14. Usage dashboard for team leads
15. Prohibited use detection Phase 2-3

---

## Cost Model Reference

| Item | Cost |
|------|------|
| Claude API (Sonnet) per 5hr session | ~$0.45 |
| Groq Whisper per audio hour | ~$0.11 |
| Supabase | Free to start, $25/mo at scale |
| Cloudflare Workers | Free tier (100k req/day) |
| Sentry | Free tier (~5k errors/mo) |
| Stripe | 2.9% + $0.30 per transaction |
| Team cost at 20 sessions/month | ~$25 all-in |
| Pro seat cost at 20 sessions/month | ~$13-14 |

**Team margin:** ~$25/month at $49.99 price
**Pro margin:** ~$10-11/seat at $24.99 price
**1k users infra cost:** ~$2,200/month Groq + ~$50 infra if mostly Pro; much less if mostly Team

---

## Support Model (1k+ users, solo)

- **Tier 0:** Docs, FAQ, status page — absorbs ~70% of questions
- **Tier 1:** Discord community — users answer each other, you monitor
- **Tier 2:** Email/form only, 48hr SLA, no live chat until you hire
- **Crash reporting:** Sentry catches bugs proactively
- **Auto-update:** Fixes bugs overnight, kills stale install long tail
- **Billing self-serve:** Stripe customer portal handles upgrades, cancels, receipts

---

## Naming

**Product name:** AXIS
**Full expansion (internal only):** Ambient eXperience Intelligence System
**Note:** "Experience" is intentionally misspelled as "eXperiance" in the expansion — this is fine, it's internal only and nobody sees it. Correct spelling is Experience if it ever surfaces publicly.

---

## Files Reference

| File | Status | Notes |
|------|--------|-------|
| AXIS_Legal_Documents.docx | Complete | Needs legal review before publishing |
| AXIS_Internal_Policy.docx | Complete | Internal only, never publish |
| axis_producer.py | Working | Core entry point |
| producer.py | Working | Batch processor, system prompt |
| session_controller.py | Working | Full orchestration layer |
| settings.py | Needs update | Add workspace_type, workspace_context, output_terminology, privacy_preset |
| setup.py / setup.bat | Replace | Replaced by web-based NUX |
| cloud_sync.py | Needs update | Per-team isolation, remove hardcoded credentials |
| cloud_db.py | Needs update | RLS, per-team scoping |
| tray_app.py | Needs update | Mute button, private mode toggle, feedback button |
| launcher.py | Needs update | Wire to backend auth |

---

*End of session handoff. Next session: backend architecture and Phase 1 build planning.*
