# BUILD_PLAN.md — Still Here

Cal Hacks 13.0 · solo build · working engineering specification

**One-line pitch (locked):** I built Still Here, an AI that makes it easier for people
recovering from psychiatric hospitalization to reconnect with the people they love, even
when depression makes reaching out difficult.

This document is the plan. For what was actually built, see
[docs/IMPLEMENTATION_STATUS.md](docs/IMPLEMENTATION_STATUS.md). For provider reality, see
[docs/PROVIDER_STATUS.md](docs/PROVIDER_STATUS.md). Plan items that were changed during
the build are marked **[CHANGED]** with the reason.

---

## 0. Product and demo contract

The judge plays Josh, Sam's best friend, and answers a live AI voice interaction on the
developer's own device. Sam is three weeks post-discharge, has opted into outreach, and
has a fictional pre-seeded history. The agent remembers that Josh and Sam enjoy Marvel
movies, proposes an outing, handles an unscripted reply, obtains Sam's approval, and
confirms a plan. The dashboard reveals actual memory retrieval, decisions, tool calls and
plan status. **No memory tour before the call.**

Intended users: any adult, voluntarily, via QR or public link. No referral or diagnosis.
Scope is social reconnection — **not** diagnosis, therapy, suicide prediction, or crisis
prevention. Any health-outcome claim is aspiration requiring clinical validation.

**Hard rule, honoured:** if telephony fails, the fallback is a clearly labeled local
voice path. Never claim an outbound phone call occurred if it did not.
**No outbound call was placed in this build, and nothing claims one was.**

## 0A. Onboarding contract

QR → phone number → "Call me" / "Text me" → verification → short preference conversation
→ trusted-circle invitations → each friend's affirmative opt-in → explicit outreach
activation. Only then may Still Here contact anyone. Signup or scanning a QR is never
authorization. Implemented; see [docs/ONBOARDING.md](docs/ONBOARDING.md).

**[CHANGED]** The plan's illustrative "no signup wall at all" flow is implemented as a
minimal adult account (phone + 18+ attestation + verification code) rather than zero
account, because consent, revocation and per-contact scoping all need a durable subject
to attach to. There is still no password and no questionnaire.

**[CHANGED]** Holding an invitation link is **not** treated as proof of phone ownership,
because the link is displayed in the UI rather than SMS-delivered. A verification code is
required before any call to that number. This is stricter than the plan.

## 1. Definition of done

See the assessment table in [CLAUDE.md](CLAUDE.md). All criteria met except the live
PSTN call, which is code-complete and unverified solely because no Cartesia API key was
supplied.

## 2. Priority and sequencing

| tier | contents | outcome |
|---|---|---|
| **P0** | real-call code path, Claude tool orchestration, consent, persistent memory, retrieval, approval simulator, availability, confirmation, event dashboard, repeatable reset | **done** |
| **P1** | Cartesia expressive voice, memory provenance, interruption handling, one true end-to-end run | **done except live-call verification** |
| **P2** | Moss, Fetch.ai, real calendar invites, real showtimes | **not started** (stop rules) |
| **P3** | Factory Droid, reservations, payment, clinical analytics, hospital integrations, passive surveillance | **not started; correctly cut** |

**Stop rule applied:** P2/P3 were not begun because P0 verification (the live call) was
blocked on credentials. No sponsor integration was faked to fill the gap.

## 3. Architecture and stack

React + TypeScript + Vite + Tailwind; Python 3.14 / FastAPI; Claude for reasoning and
structured tool calls; Cartesia for voice; **[CHANGED]** SQLite + Server-Sent Events
instead of SpacetimeDB; Twilio optional and not integrated.

**Boundary:** Claude proposes; code authorizes and executes. No agent may bypass consent,
call an unauthorized number, expose private memory, or mark a plan confirmed.

Providers sit behind adapters (`adapters/cartesia_adapter.py`,
`adapters/anthropic_adapter.py`) so the build runs with no keys at all.

## 4. Repository structure

As implemented — see the layout section of [README.md](README.md). Divergences from the
plan's sketch: `services/plans.py` and `services/companion.py` were added; `app/db.py`
replaces an ORM layer; `adapters/telephony_adapter.py` and
`adapters/spacetimedb_adapter.py` were **not** created because neither provider is
integrated, and empty adapter files would misrepresent status.

## 5. Data design and seed scenario

Fictional cast: **Sam Rivera** (patient), **Josh Tanaka** (best friend, played by the
judge, phone = `MY_PHONE_E164`), **Maya Chen** (friend, consented to SMS only),
**Daniel Osei** (invited, never accepted).

Maya and Daniel exist to make the consent gate provable: Maya's SMS-only scope must block
a voice call, and Daniel must be unreachable entirely.

15 tables; see [docs/DATA_MODEL.md](docs/DATA_MODEL.md). Visibility ladder:
`private_to_sam` → `agent_internal` → `shareable_with_contact` → `public_demo_safe`, with
explicit per-audience allow-lists rather than an ordinal threshold, so "private reaches
planning" cannot be expressed by accident.

**[CHANGED]** A seeded coffee memory was added. Without it, correcting "no crowded
theaters" left the planner with no grounded alternative — refusing to invent an activity
the user never mentioned is correct, but the demo needs a real fallback to switch to.

**Critical test, passing:** teach it a fact, restart, retrieve it, then correct a
preference and watch activity selection change. Covered by
`test_memory.py::TestPersistence` and e2e section 12.

## 6. Coordination state machine

See [specs/STATE_MACHINE.md](specs/STATE_MACHINE.md). Alternate responses loop through
candidate selection without confirming; patient decline ends the attempt; revocation
halts immediately; confirmation requires an idempotency key and agreement on identical
terms. The LLM cannot set `PLAN_CONFIRMED`.

**[CHANGED]** Three transitions were added after testing: a mid-call re-plan returns to
`CONTACT_IN_PROGRESS`, and a friend may counterpropose or decline from `FRIEND_ACCEPTED`
and `PATIENT_APPROVAL_PENDING`. All three are normal human behaviour that the original
graph treated as errors.

## 7. API and tool contracts

Frozen in [specs/CONTRACTS.md](specs/CONTRACTS.md); tools in
[specs/AGENT_TOOLS.md](specs/AGENT_TOOLS.md); full reference in
[docs/API.md](docs/API.md).

**[CHANGED]** `GET /api/circle` and `GET /api/contacts` were added after the frontend
could not otherwise list invites or resolve a contact id to a name. SSE auth accepts a
query parameter because `EventSource` cannot set headers. `confirm_plan` and
`send_confirmation` are **not** exposed as model tools.

## 8. Voice experience

Cartesia Managed Agents is the transport. **[CHANGED]** The Line SDK fallback was dropped
on purpose: Cartesia stops hosting Line agents on 2026-12-01 and the Cal Hacks guidance
says build on Managed Agents.

Required branches — yes; no; different day; different activity; "who are you?";
"why Marvel?"; "how is Sam doing?" (no disclosure); "book without asking Sam" (refuse);
interruption; silence; voicemail; busy; no-answer; duplicate webhook — are all
implemented and tested against the scripted adapter, and specified for the live agent in
[prompts/OUTREACH_AGENT.md](prompts/OUTREACH_AGENT.md).

## 9. Dashboard

Two columns. Left: profile, consent indicator, selected relationship, why-this-person and
why-this-activity with memory cards showing source and visibility, availability. Right:
call state, transcript excerpts, tool timeline with timings, friend response, the
**SIMULATED** Sam approval panel, confirmed plan, notification status.

Event-driven only. Nothing is animated or replayed as if live; an empty feed looks empty.
See [docs/DASHBOARD.md](docs/DASHBOARD.md).

## 10. Safety, privacy and honesty

See [docs/PRIVACY_SAFETY.md](docs/PRIVACY_SAFETY.md) and
[docs/SECURITY.md](docs/SECURITY.md). No real patient data; fictional case; simulated
approval and mock showtimes labeled everywhere; granular consent; no clinical inference
from social activity, sleep, tone, or missed check-ins; 988/911 for explicit emergencies
with no promise of automatic alerts; redacted operational logs only.

## 11. Build phases — outcome

| gate | criterion | result |
|---|---|---|
| A | speak to the AI and see a backend event | **met via simulator**; real call blocked on credentials |
| B | learn Josh/Marvel, restart, retrieve, honour a correction | **met** |
| C | whole workflow without voice via API tests | **met** (49-check e2e) |
| D | improvised responses handled; approved plan appears correctly | **met** |
| E | docs, sponsor evidence, privacy, limitations | **met** |
| F | optional sponsor extensions | **not attempted**, per stop rules |

## 12. Test matrix

See [docs/TEST_PLAN.md](docs/TEST_PLAN.md) and recorded output in
[docs/TEST_RESULTS.md](docs/TEST_RESULTS.md). 113 pytest + 49 e2e checks passing.

## 13–15. Sponsors, risks, rehearsal

Sponsor reality: [docs/SPONSOR_MATRIX.md](docs/SPONSOR_MATRIX.md). Nothing is claimed as
integrated that is not. Risk register and demo rehearsal checklist:
[docs/DEMO_RUNBOOK.md](docs/DEMO_RUNBOOK.md) and
[docs/DEMO_EXECUTION.md](docs/DEMO_EXECUTION.md).

The risk that actually materialised was **telephony credentials**, and the mitigation
that actually mattered was building the honest labeled fallback and keeping every claim
in the repo truthful.

## 19. Real phone calls — status

The upgrade to implementation quality was carried out; the story was not changed.

Implemented: `start_outbound_call()` with E.164 validation, contact opt-in, outreach
window, rate limits, explicit demo trigger, idempotency key, audit event, and
provider-call-id persistence. Raw numbers are never exposed to the model. Only shareable
memory summaries are preloaded. The greeting discloses the AI, identifies Still Here,
asks if now is a good time, and respects refusal. Webhook handling covers authenticity,
status, disconnect, busy, no-answer, voicemail, failure, timeout, duplicates. No
unsolicited voicemail without opt-in. Real timestamps are recorded, and call status is
displayed rather than animated.

**Not verified:** no API key was supplied, so no call has been placed. `make preflight`
and `make call` are written and exit non-zero with precise guidance rather than
pretending. Steps to enable are in the README and
[docs/CARTESIA_MANAGED_CALLS.md](docs/CARTESIA_MANAGED_CALLS.md).

Voice cloning was deliberately not started — the plan says do not spend time on it until
a real outbound call and the full coordination loop work.
