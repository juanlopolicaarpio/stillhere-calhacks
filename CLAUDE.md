# CLAUDE.md — build instruction and execution record

**Status: executed 2026-10-24.** This file was the one-shot instruction for the build.
It is kept as the record of intent, annotated with what actually happened. Where intent
and reality diverge, reality is documented in
[docs/IMPLEMENTATION_STATUS.md](docs/IMPLEMENTATION_STATUS.md) and
[docs/PROVIDER_STATUS.md](docs/PROVIDER_STATUS.md).

---

## Locked product and pitch

Still Here is an opt-in, persistent, memory-aware personal AI companion that helps people
reconnect with loved ones. Anyone 18+ may voluntarily sign up by scanning a QR code or
opening a link — no hospital referral, no diagnosis. The hackathon story is Sam, three
weeks after psychiatric discharge.

**The live pitch and demo were not changed.** The judge plays Josh, answers an AI call on
the developer's own phone, gives an unscripted answer, and watches the dashboard show real
memory retrieval, decision-making, approval, and plan confirmation. No companion memory
tour was inserted before the phone call.

## Non-negotiables, and how each is enforced in code

| requirement | enforcement |
|---|---|
| QR scan or signup is not authorization to call | `services/consent.verify_outreach()` requires patient activation **and** contact acceptance **and** phone verification |
| Each friend must affirmatively opt in | `POST /api/invite/{token}/accept`; `relationships.status` must be `accepted` |
| Contact frequency limits, quiet hours, STOP, revocation | all in `verify_outreach()` + `handle_stop()`; enforced server-side, never model-controlled |
| Patient final approval required | `evaluate_confirmation()` requires both parties on identical terms |
| Never disclose diagnosis, hospitalization, or private conversations | two independent guards: `AUDIENCE_VISIBILITY` allow-lists + `_SENSITIVE_RE` content guard; `assert_no_sensitive_leak()` on every outbound payload |
| Fictional demo data | `services/demo_reset.py`; all four people invented |
| Not clinical monitoring, therapy, or crisis prediction | stated in prompts, README, and docs/PRIVACY_SAFETY.md; no risk inference anywhere in code |
| Crisis resources if danger is expressed | 988/911 surfaced in both the Claude prompt and the deterministic fallback; no promise of care-team alerts |
| No real purchases or bookings | no booking tool exists; showtimes return `source: "mock"` with a disclaimer |
| No secrets in git | `.env` gitignored, `.env.example` lists variables, `events.redact()` strips secrets before persistence |
| Never fake a passing test, provider integration, or completed feature | **no real call was claimed.** `config.voice_mode()` returns `simulated`; `/api/status` says so; smoke test exits non-zero without a key |

## Multi-agent execution — what actually happened

1. **Audit.** Empty repo. Python 3.14.2, Node 24.12.0 available. **No provider
   credentials present** — recorded in docs/IMPLEMENTATION_STATUS.md.
2. **Provider verification before coding.** Cartesia SDK 4.2.0 introspected directly;
   found it exposes no create method for agents or calls, so outbound dialling uses the
   verified raw endpoint `POST /agents/calls`. Confirmed Managed Agents over the Line SDK
   (Line hosting ends 2026-12-01). Verified "Photon" is photon.codes, not the game SDK.
3. **Contracts frozen** in [specs/CONTRACTS.md](specs/CONTRACTS.md) before any
   parallel work, including file ownership so no two agents wrote the same file.
4. **Fan-out.** Subagents owned disjoint areas: frontend (`frontend/**`), core docs, and
   provider docs. The lead built the backend critical path itself.
5. **Deviation, documented:** the planned A–F split was collapsed. The consent, memory,
   planner, voice and state-machine code is too tightly coupled to hand to independent
   cold-start agents without producing incompatible APIs, so the lead owned all of it.
   See docs/MULTI_AGENT_ORCHESTRATION.md.
6. **Integration.** The frontend agent's review surfaced five real contract defects,
   all fixed by the lead: missing `GET /api/circle` and `GET /api/contacts`;
   `EventSource` cannot send custom headers so SSE auth needed a query-param path;
   `Plan.rationale` needed structured `why_contact` / `why_activity`; `/api/status` shape
   was undocumented.
7. **Testing.** 113 pytest + 49 end-to-end checks, all passing. Three genuine bugs were
   found and fixed by testing rather than by inspection — see below.

## Bugs found by testing, not inspection

Worth recording, because they are the ones that would have broken a live demo:

1. **Mid-call re-plan deadlock.** "Can we do Sunday?" then "yeah sounds good" raised an
   illegal transition, because a re-planned plan sat in `CANDIDATE_PLAN_CREATED` while
   acceptance requires `CONTACT_IN_PROGRESS`. This is the most likely improvised path at
   the booth. Fixed by returning to `CONTACT_IN_PROGRESS` after a mid-call re-plan.
2. **Dishonest privacy counter.** `blocked_count` counted only mislabelled rows caught by
   the content guard, so the dashboard would have displayed "0 private memories withheld"
   while actually withholding two. Split into `withheld_by_visibility` and
   `blocked_by_content_guard`.
3. **Naive rescheduling.** The day-change branch did `+1 day` arithmetic and proposed a
   time with no availability, which then failed revalidation. Replaced with
   `scheduling.find_slot_on_weekday()`, which looks up a real shared free slot and says so
   honestly when a day genuinely does not work.

Also fixed: an accepted-then-declined invitation still reported `accepted`
(decline now wins); and a friend retracting after accepting corrupted the approval row,
which is now a legal, handled transition.

## Runtime roles

| role | module |
|---|---|
| Companion / Memory | `services/companion.py`, `services/memory.py` |
| Connection Planner | `agents/orchestrator.py` |
| Voice Outreach | `services/voice.py`, `adapters/cartesia_adapter.py` |
| Consent / Safety | `services/consent.py`, `domain/state_machine.py` |
| Confirmation | `services/plans.py` |

Consent checks and terminal state transitions are deterministic backend functions. The
LLM cannot self-authorize: `confirm_plan`, `send_confirmation` and `start_outbound_call`
are in `MODEL_FORBIDDEN_TOOLS`.

## Definition of done — assessment

| criterion | status |
|---|---|
| Public QR signup and opt-in workflow works | **met** |
| Outreach cannot run before both parties consent | **met**, test-enforced |
| Companion learns and persists memories; corrections affect retrieval | **met**, survives restart |
| Judge can interact unscripted with the agent | **met via the labeled local simulator.** Real PSTN is code-complete but unverified — no API key was supplied |
| Real event dashboard with provenance, tools, approvals, errors, reset | **met** |
| No plan confirms without matching approvals; sends idempotent | **met**, test-enforced |
| Automated tests pass, recorded | **met** — docs/TEST_RESULTS.md |
| Docs reflect implemented code, not aspiration | **met** — every provider doc carries an explicit status |

**The one unmet item is the live phone call, and it is unmet for exactly one reason: no
Cartesia API key was provided.** Everything needed to flip it on is built, documented and
armed. See the README section "Enabling the real phone call".

## Execution priority as applied

- **P0 (done):** onboarding + dual consent, companion memory, planner, dashboard, safe
  approvals, tests, and the complete real-call code path.
- **P1 (partial):** Cartesia polish is written but unverified without a key. SpacetimeDB
  was replaced by SQLite + SSE behind an adapter seam and documented as not integrated.
- **P2/P3 (not started, per the plan's own stop rules):** Moss, Fetch.ai, Factory Droid,
  ticket purchase, medical integrations.
