# Still Here

**An AI that makes it easier for people recovering from psychiatric hospitalization to
reconnect with the people they love, even when depression makes reaching out difficult.**

Still Here is an opt-in, consent-gated, memory-aware companion. It learns who matters to
you, and when you want it to, it reaches out to those people on your behalf — by phone —
and turns an ordinary conversation into a plan you approved.

Built solo for Cal Hacks 13.0.

---

## Honest status, up front

| | |
|---|---|
| Backend, consent engine, memory, planner, dashboard, tests | **Working** |
| Automated tests | **113 pytest + 49 end-to-end checks, all passing** |
| Real outbound PSTN call via Cartesia | **Code complete, NOT yet verified — no API key was supplied** |
| Voice during the demo | **Local simulator, clearly labeled in the UI as `SIMULATED`** |
| Claude reasoning | **Code complete; runs a labeled deterministic fallback without `ANTHROPIC_API_KEY`** |
| SpacetimeDB, Moss, Fetch.ai, Factory Droid, Creao, Notability, Twilio | **Not integrated** |

> **No real phone call has been placed by this build.** The Cartesia adapter targets the
> real verified API and the smoke test is written and armed, but without a key it cannot
> run. `GET /api/status` reports `voice_mode: "simulated"`, and the UI shows a persistent
> banner saying no real call is placed. Nothing in this repo claims otherwise.
> See [docs/PROVIDER_STATUS.md](docs/PROVIDER_STATUS.md).

All demo data is **fictional**. Sam, Josh, Maya and Daniel are invented. Showtimes are
mock. Sam's approval in the solo demo is **simulated and labeled as such**.

---

## Quick start

```bash
python -m venv .venv
./.venv/Scripts/python.exe -m pip install -r backend/requirements.txt   # Windows
# source .venv/bin/activate && pip install -r backend/requirements.txt  # macOS/Linux

cp .env.example .env            # optional; works without any keys

make backend                    # http://localhost:8000
make frontend                   # http://localhost:5173

make test                       # 113 unit/integration tests
make e2e                        # 49-check end-to-end run
```

Then open <http://localhost:5173/dashboard> (dev token `dev-dashboard-token`), click
**Reset demo**, then **Start outreach**.

Verify the honesty flags yourself:

```bash
curl -s localhost:8000/api/status
curl -s localhost:8000/health
```

---

## Enabling the real phone call

This is the one thing that needs your credentials. Six steps:

1. **Get a key** at <https://play.cartesia.ai/keys> → put it in `.env` as `CARTESIA_API_KEY`.
2. **Create a Managed Agent** in the Playground (Agents → new). Paste the system prompt
   and welcome message from [prompts/OUTREACH_AGENT.md](prompts/OUTREACH_AGENT.md).
   Build on **Managed Agents, not the Line SDK** — Cartesia stops hosting Line agents on
   2026-12-01.
3. **Attach a US phone number** — provision one in Cartesia, or import a Twilio number.
   Cartesia-provisioned numbers are US-only at both ends.
4. **Expose the backend** so Cartesia can reach your tool webhooks:
   `ngrok http 8000` or `cloudflared tunnel --url http://localhost:8000`, then set
   `PUBLIC_BASE_URL` to the public HTTPS URL.
5. **Register the four webhook tools** (`make agent` prints them with your real URLs),
   each with header `X-StillHere-Secret: $WEBHOOK_SECRET`.
6. **Dial it:**

```bash
make preflight     # validates key, lists agents + numbers, resolves the ids for .env
make call          # places a REAL call to MY_PHONE_E164 and writes evidence/
```

`make call` reports exactly what the provider returned. If it prints `FAILED`, no call
happened — and nothing should be claimed. Evidence lands in `evidence/`.

---

## What actually works

**Onboarding without a signup wall.** A QR code (`GET /api/qr.png`) opens `/signup`.
Phone number, "Call me" or "Text me", an 18+ attestation, a 6-digit verification code.
No referral, no diagnosis, no password. **Scanning the QR authorizes nothing.**

**Dual consent, enforced server-side.** Sam activates outreach *and* each friend
separately accepts an invitation *and* their phone is verified before any call is
possible. Holding an invite link is not proof of phone ownership, so a code is still
required. Quiet hours, per-day frequency caps, `STOP`, and revocation are all enforced in
`services/consent.verify_outreach()` — the single gate every outbound path must pass.

**Memory with provenance.** Conversations persist; candidate memories are extracted with
a source quote, a confidence, and a visibility. Memory survives a restart. Corrections
create a new row and supersede the old one; deletions tombstone it. Both change later
planning — tell it "I don't want crowded theaters anymore" and the planner stops
proposing the movie and proposes coffee instead, because coffee is also grounded in a
stored memory.

**Privacy that is provable, not asserted.** Two independent guards stand between Sam's
clinical history and his friend: an explicit visibility allow-list per audience, and a
regex content guard that catches a row even if it were mislabeled. The seed deliberately
contains two private clinical memories so the tests can prove they never reach Josh.

**A planner that cites its reasons.** Nothing about Josh or Marvel is hardcoded in the
planner. It scores relationships and activities from stored memory and returns
`why_contact` and `why_activity` with the memory ids it used. Delete the Marvel memory
and the plan changes.

**A confirmation gate that cannot be talked around.** A plan confirms only when both
parties accepted the **identical** activity and start time, after availability is
re-validated. "Josh said Sunday, Sam approved Saturday" is a hard refusal. The LLM has no
`confirm_plan` tool at all — confirmation is backend-only. Confirmation and notification
are idempotent.

**A dashboard of real events.** Every panel renders events a backend function actually
emitted, streamed over SSE. Nothing is animated or replayed. Anything not real carries a
`SIMULATED` badge, and mock showtimes carry a `MOCK DATA` badge.

---

## Architecture

```
React 19 + Vite + Tailwind          FastAPI (Python 3.14)
┌───────────────────────┐           ┌──────────────────────────────────────┐
│ /            QR       │           │ api/        onboarding companion     │
│ /signup /onboarding   │──── /api ─▶│             demo events voice       │
│ /circle /invite/:token│           ├──────────────────────────────────────┤
│ /settings/consent     │           │ agents/     orchestrator  tools      │
│ /companion            │           ├──────────────────────────────────────┤
│ /dashboard  ◀── SSE ──┤           │ services/   consent  memory  plans   │
└───────────────────────┘           │             voice  companion  events │
                                    ├──────────────────────────────────────┤
                                    │ domain/     state_machine (strict)   │
                                    ├──────────────────────────────────────┤
                                    │ adapters/   cartesia  anthropic      │
                                    └──────────────────────────────────────┘
                                         │                    │
                                   SQLite (15 tables)   Cartesia Managed
                                                        Agents (PSTN)
```

**The core boundary: Claude proposes, code authorizes.** The model can suggest anything;
`domain/state_machine.py` and `services/consent.py` decide what is permitted. Consent
checks and terminal state transitions are deterministic Python, never model output.

Full detail in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) and the frozen contract in
[specs/CONTRACTS.md](specs/CONTRACTS.md).

---

## The demo (2 minutes)

The judge plays Josh and answers the phone.

1. **0:00** Sam is three weeks post-discharge. Reaching out feels impossible. Judge is
   handed the phone.
2. **0:25** The phone rings. The agent introduces itself as an AI, references what Sam
   and Josh actually used to do, and proposes a specific outing. The judge can say yes,
   say no, ask "who is this?", ask "how is Sam doing?" (it refuses to disclose), suggest
   another day, suggest coffee instead, or interrupt. The agent re-plans mid-call.
3. **1:30** Sam's approval is requested — **clearly labeled `SIMULATED`** in the solo
   demo. Both approvals are re-checked against identical terms, availability is
   re-validated, then the plan confirms.
4. **1:45** The dashboard shows the whole chain: which memories were retrieved and which
   were withheld, every tool call with timings, both approvals, and the confirmed plan.

Reset and run it again with **Reset demo**. Runbook:
[docs/DEMO_RUNBOOK.md](docs/DEMO_RUNBOOK.md).

---

## What this is not

- Not clinical monitoring, therapy, diagnosis, or crisis prediction.
- It does not infer distress or risk from tone, sleep, message length, or missed activity.
- It makes no claim of improving health outcomes. That would need clinical validation.
- If someone states they are in immediate danger, it does not play clinician: it says so
  plainly and surfaces 988 / 911. It does not promise to alert anyone.
- It books and pays for nothing. Showtimes are mock and labeled.

See [docs/PRIVACY_SAFETY.md](docs/PRIVACY_SAFETY.md).

---

## Testing

```bash
make test    # 113 passed  — consent, memory, state machine, API, privacy, idempotency
make e2e     # 49 checks   — full loop incl. counterproposal, revocation, STOP, mismatch
```

The end-to-end run asserts the negative properties too: an un-accepted contact cannot be
called, an SMS-only consent blocks voice, mismatched approvals refuse to confirm, revoking
consent halts an in-flight plan, and private memories never appear in contact-facing
context. Results: [docs/TEST_RESULTS.md](docs/TEST_RESULTS.md).

---

## Repo layout

```
backend/app/     config db main · api/ agents/ services/ domain/ adapters/
backend/tests/   113 tests
frontend/src/    pages/ components/ hooks/ api/
scripts/         discover_cartesia  setup_cartesia_agent  smoke_call  e2e_demo
specs/           CONTRACTS (frozen)  STATE_MACHINE  AGENT_TOOLS
prompts/         COMPANION_SYSTEM  MEMORY_EXTRACTION  OUTREACH_AGENT
docs/            architecture, privacy, security, per-provider status, runbook
evidence/        provider responses from preflight and real call attempts
```

## Security

No secrets in the repo. `.env` is gitignored; `.env.example` lists every variable.
Events and logs are redacted before persistence — phone numbers and keys never appear.
Dashboard and voice-tool endpoints require tokens. Invitation tokens expire and are
single-use. See [docs/SECURITY.md](docs/SECURITY.md).

## Why I built this

Depression makes the thing that would help most — reaching out — feel impossible. The
people who love you are usually still there, and still willing. Still Here is an attempt
to lower the cost of that first move, without ever taking the decision away from the
person it belongs to.
