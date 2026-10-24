# Implementation Status — the honest audit

Last verified: 2026-10-25, against the code on `feat/spacetimedb-fluid-agent`
(commit `e2c5e08` and worktree state on top of it). Toolchain: Windows 11,
Python 3.14.2, Node 24.12.0. Backend: fastapi 0.162.0, pydantic 2.14.1,
uvicorn 0.54.0, httpx 0.28.1, sse-starlette 3.5.0, qrcode 8.2, pytest 9.1.1,
`cartesia` 4.2.0, `anthropic` 2.3.0, `spacetimedb` (TS) 2.13.0. Frontend:
React 19.3.1, TypeScript, Vite 8.3.5, Tailwind v4, react-router-dom v7.18.

## 60-second verification for a judge

```
spacetime start --listen-addr 127.0.0.1:3000   # if not already running
python scripts/stdb_setup.py --fresh           # publish a clean module
make test   # -> 195 passed  (backend/tests, pytest)
make e2e    # -> 49 passed, 0 failed  (scripts/e2e_demo.py)
```

Both re-run and reconfirmed during this documentation pass. **One caveat a
judge will hit if they skip the `--fresh` republish**: see "A real
non-hermeticity, found and reproduced" below — this is not theoretical.

## The two headlines

1. **A real outbound PSTN call was placed and answered** — see
   `docs/LIVE_PROVIDER_EVIDENCE.md` for the full transcript, evidence file,
   and an unflinching list of what that call does and does not prove.
2. **SpacetimeDB is now the authoritative transactional backend** for the
   shared coordination workflow (sessions, call state, plan proposals,
   responses, confirmation) — not a stretch goal, not a parallel store. See
   §"SpacetimeDB" below.

## Component status

| Component | Status | Evidence |
|---|---|---|
| Consent gate (`services/consent.verify_outreach`) | **Implemented** | `backend/app/services/consent.py`; enforced unconditionally before any dial in `services/voice.start_outbound_call`; `backend/tests/test_consent.py` |
| Plan state machine | **Implemented** | `backend/app/domain/state_machine.py`; `evaluate_confirmation()` remains the only pure-function path to `PLAN_CONFIRMED`; `backend/tests/test_state_machine.py` |
| **SpacetimeDB coordination layer** | **Implemented** | `backend/app/services/coordination.py` — `confirm_plan()` treats `confirm_if_matching` as authoritative and writes no local confirmation on refusal. Module: `spacetime/spacetimedb` (TypeScript, `spacetimedb` npm 2.13.0), published locally as database `stillhere`. Reducers: `start_session`, `set_call_state`, `propose_or_revise_plan`, `respond_as_friend`, `record_patient_approval`, `confirm_if_matching`, `revoke_or_cancel`, `set_consent_state`, `append_sanitized_event`, `upsert_demo_presence`. **21 live reducer tests pass** in `backend/tests/test_spacetimedb.py`, including the full invariant (`TestConfirmationInvariant`, lines 140-228) and the privacy boundary (`TestPrivacyBoundary`, lines 230-287: no clinical language or full phone numbers ever reach a public table). |
| SpacetimeDB ↔ SQLite revision bridge | **Implemented, with a documented prior bug fixed** | `backend/app/services/coordination.py::proposal_ref` (`<plan_id>_v<version>`) keys every SpacetimeDB proposal by exact version, because SQLite keeps one mutable `plans` row while SpacetimeDB keeps one immutable row per version. Reusing the bare plan id (the original bug, per the code comment at `coordination.py:63`) left SpacetimeDB stuck on v1 while SQLite advanced to v2, so the reducer compared the wrong terms and refused to confirm — observed and fixed during this build. |
| Fluid conversation engine (Claude tool-use loop) | **Implemented** | `backend/app/agents/conversation.py` — a real `client.messages.create(tools=[...])` loop (`_run_claude`) that dispatches `tool_use` blocks through `agents/tools.py::dispatch`, feeds `tool_result`s back, and only ever lets the model produce a sentence + a label via the `respond_to_contact` output tool. Four safety properties enforced in code regardless of the model's output: no health disclosure (`_assert_no_disclosure`), no invented availability (`_resolve_start`/`_is_grounded_time` against `scheduling.find_free_slots`), no third-party disclosure (`ask_add_person` reply gating), no state change (the model records nothing; the backend acts on the returned intent). |
| Deterministic classifier (labelled fallback) | **Implemented and exercised** | `backend/app/agents/conversation.py::_classify` — ordered, commented regex branches (opt-out first, then health, then injection/probe, then third-party, then provenance, then content extraction, then boundary, then defer, then counter, then accept/decline). Runs whenever `ANTHROPIC_API_KEY` is absent, and also supplies the intent label whenever Claude answers in prose instead of calling `respond_to_contact`. Honestly reported as `conversation_mode: "keyword_fallback"`. |
| Memory store + visibility ladder | **Implemented** | `backend/app/services/memory.py`; SQLite-backed, survives restart; two independent contact-facing guards (visibility allow-list + regex content guard), reinforced on the live-call path by the wider `_DISCLOSURE_RE` in `conversation.py` |
| Connection planner (`agents/orchestrator.py`) | **Implemented**, rule-based | Scores contacts/activities from stored memory rows and consent state; deterministic Python, no LLM call in this path |
| SQLite persistence (private/companion data) | **Implemented** | `backend/app/db.py`; retained by design for conversation transcripts, clinical memories, verification codes, invitation tokens, and phone numbers — see the division of authority in `specs/CONTRACTS_V2.md §1` |
| Live event bus (SSE) | **Implemented** | `backend/app/services/events.py` + `backend/app/api/events.py`; every dashboard row is a real `agent_events` insert, mirrored into SpacetimeDB's public `agent_events` table via `coordination.append_event` (already-sanitized payloads only) |
| Cartesia outbound PSTN voice | **Implemented — one live call verified, with caveats** | `backend/app/adapters/cartesia_adapter.py` targets `POST https://api.cartesia.ai/agents/calls`. A real call was placed and answered on 2026-10-24 (`evidence/real_call_ac_ZzzPbaPYKstadZguL5TRtF.json`) — see `docs/LIVE_PROVIDER_EVIDENCE.md` for the full account including what that call does *not* prove (no real plan context on the call, full system prompt presence unverified, health-disclosure case not captured, no tool call observed). |
| Local voice simulator | **Implemented and exercised** | `backend/app/services/voice.py`; drives the full plan lifecycle in tests and `scripts/e2e_demo.py`; every event it emits carries `simulated: true` |
| Anthropic companion chat + memory extraction | **Implemented, live-capable** | `backend/app/adapters/anthropic_adapter.py` targets `anthropic` 2.3.0, model `claude-opus-5`. Observed live in this environment: `reasoning_mode: "claude"`, `extraction_mode: "claude"`. A real regression this build fixed: the previous keyword-branching implementation matched "ok" inside "Has he been **ok**ay?" and recorded a worried question as an acceptance; with Claude, that utterance now classifies as `ask_about_patient_health`, discloses nothing, and advances no state. |
| Three honest mode flags | **Implemented** | `GET /api/status` → `voice_mode` (`cartesia_pstn` \| `simulated`), `coordination_mode` (`spacetimedb` \| `sqlite_fallback`), `conversation_mode` (`claude` \| `keyword_fallback`) — `backend/app/config.py::Settings.redacted()`. A real bug fixed here: `voice_mode` previously reported `cartesia_pstn` for a mis-pasted agent id because it only checked the string was non-empty; `cartesia_id_problems()` (`config.py:75-104`) now validates id prefixes (`agent_` vs `ap_` vs a bare voice UUID) and names the specific mistake. |
| Twilio SMS/number provider | **Not integrated** | `config.py`; `twilio_configured` is `False`; a Cartesia-provisioned number was used instead, so Twilio was never needed for the voice path that shipped |
| Photon (photon.codes) | **Verified as a product, not integrated** | `docs/PHOTON_VERIFICATION.md`; a plausible future fit for an SMS/iMessage "Text me" onboarding branch, correctly deprioritized behind the voice path |
| QR onboarding → phone verification → consent | **Implemented** | `backend/app/api/onboarding.py`; dual opt-in, token-based invites, phone ownership requires a code |
| Dashboard (judge-facing) | **Implemented** | `frontend/src/pages/Dashboard.tsx`; reads `/api/events/stream` and `/api/events/recent`; renders `SIMULATED` badges off real event flags |
| Two-client SpacetimeDB sync (frontend) | **Code present, unverified** | `frontend/src/hooks/useSpacetime.ts` subscribes to all eight public tables plus a presence reducer; generated bindings exist in `frontend/src/spacetime_bindings/`. This has **not** been visually confirmed working with two independent browser clients by the lead — treat "live two-client sync" as `planned`/unverified, not demonstrated. |
| Moss, Fetch.ai, Factory Droid, Creao, Notability | **Not integrated** | Zero dependency, import, or reference anywhere in `backend/` or `frontend/`; see each `docs/TOOL_*.md` for what each would have been for |
| Meta, Microsoft | **Judging tracks, not SDK integrations** | No Meta or Microsoft product/SDK anywhere in this codebase; see `docs/META_SPONSOR.md` / `docs/MICROSOFT_SPONSOR.md` for the track-fit argument, clearly separated from any code claim |

## A real non-hermeticity, found and reproduced during this documentation pass

Running `python -m pytest backend/tests -q` against this environment's
**already-running** SpacetimeDB module (which had accumulated state from the
live call and manual demo runs earlier the same day) produced **5 failures,
190 passed** — not 195. Each failing test passed individually in isolation.
The cause: SpacetimeDB is a persistent external service, not reset between
test runs the way the per-test SQLite file is (`backend/tests/conftest.py`'s
`fresh_db` fixture wipes SQLite but has no equivalent for the live
`stillhere` module). Deterministic test/demo ids collided with stale
proposals/confirmations left over from earlier manual runs, and
`confirm_if_matching` correctly refused those stale-looking requests —
exactly as designed, just not what the test assertions expected against a
dirty module.

Running `python scripts/stdb_setup.py --fresh` (wipes and republishes the
module) and then re-running the suite produced a clean **195 passed**,
confirmed twice during this pass. This is a real, reproducible gap, not a
flaky test: **the backend test suite is not currently hermetic with respect
to the live SpacetimeDB module's state.** Recorded as an open item in
`docs/FINAL_HANDOFF.md`.

## What is actually proven by the test suite (195 backend + 49 e2e)

- Outreach is blocked without dual opt-in (`test_consent.py`).
- An unverified phone blocks calling even after the contact accepts
  (`test_api_flow.py`).
- SMS-only consent blocks voice calls; quiet hours and the per-day rate cap
  are enforced server-side (`test_consent.py`).
- STOP revokes every consent for that phone number immediately
  (`test_consent.py::TestStop`).
- Consent revocation halts any in-flight plan (`test_api_flow.py`;
  `e2e_demo.py` step 13).
- Private clinical memories never reach contact-facing context, via two
  independent guards (`test_memory.py::TestPrivacyFiltering`).
- A plan cannot confirm unless both parties approved the *identical* activity
  and start time — enforced twice: once as a pure function
  (`test_state_machine.py::TestConfirmationGate`) and once as a live
  SpacetimeDB reducer transaction (`test_spacetimedb.py::TestConfirmationInvariant`,
  including stale-version, activity-mismatch, time-mismatch, missing-approval,
  revoked-consent, and idempotent-replay cases).
- No SpacetimeDB public table ever contains clinical language or a full phone
  number (`test_spacetimedb.py::TestPrivacyBoundary`).
- The LLM/voice-tool surface has no `confirm_plan` tool; confirmation is
  backend-only (`agents/tools.py::MODEL_FORBIDDEN_TOOLS = {"confirm_plan",
  "send_confirmation", "start_outbound_call"}`).
- A changed term (day, activity, or both) invalidates prior approvals and
  creates a new proposal version, mirrored into a new immutable SpacetimeDB
  proposal row (`test_spacetimedb.py::test_revision_invalidates_prior_approvals`).
- Memory survives a simulated process restart (`test_memory.py::TestPersistence`).
- Corrections and deletions change later planning output (`e2e_demo.py` step 12).

## Honesty mechanisms baked into the code (not just this document)

- `config.Settings.voice_mode()` returns `"cartesia_pstn"` only when the key,
  both ids, and well-formed id prefixes are all present (`config.py:106-120,
  130-132`).
- `config.Settings.coordination_mode()` returns `"spacetimedb"` only when the
  adapter is configured **and** a live health probe succeeds
  (`config.py:141-154`); otherwise `"sqlite_fallback"`.
- `config.Settings.conversation_mode()` returns `"claude"` only when the
  fluid engine itself reports it can reach Claude (`config.py:156-166`,
  `agents/conversation.py::conversation_mode()`).
- Every `calls` row and every `agent_events` row carries `mode`/`simulated`;
  the dashboard renders a `SIMULATED` badge directly off that flag.
- `GET /api/status` exposes all three mode flags plus `real_call_possible`
  and `spacetimedb_reachable` (`backend/app/main.py`, `config.py::redacted()`).
- `services/coordination.py::_call()` distinguishes "SpacetimeDB unreachable"
  (`stdb.unavailable` event, fallback continues) from "SpacetimeDB ran and
  refused" (`stdb.reducer_rejected` event, a real rejection) — the two are
  never conflated.
- Mock showtime data is labeled `source: "mock"` with an explicit disclaimer
  at every layer it passes through (`services/scheduling.py`).
