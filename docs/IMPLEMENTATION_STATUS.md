# Implementation Status — the honest audit

Last verified: 2026-10-24, against the code in this repo (not the build plan).
Toolchain: Python 3.14.2, Node 24.12.0. Backend: fastapi 0.162.0, pydantic 2.14.1,
uvicorn 0.54.0, httpx 0.28.1, sse-starlette 3.5.0, qrcode 8.2, pytest 9.1.1,
cartesia 4.2.0, anthropic 2.3.0. Frontend: React 19.3.1, TypeScript, Vite 8.3.5,
Tailwind v4, react-router-dom v7.18.

## 60-second verification for a judge

```
make test   # -> 113 passed  (backend/tests, pytest)
make e2e    # -> 49 passed, 0 failed  (scripts/e2e_demo.py)
```

Both run against the real FastAPI app and the real SQLite schema — no mocks of
our own code, only the absence of third-party API keys.

## Component status

| Component | Status | Evidence |
|---|---|---|
| Consent gate (`services/consent.verify_outreach`) | **Implemented** | `backend/app/services/consent.py:93-179`; enforced unconditionally before any dial in `services/voice.start_outbound_call` (`backend/app/services/voice.py:80-92`); 11 tests in `backend/tests/test_consent.py` |
| Plan state machine | **Implemented** | `backend/app/domain/state_machine.py`; illegal transitions raise `TransitionError`; `evaluate_confirmation()` is the only path to `PLAN_CONFIRMED` (lines 123-168); 13 tests in `test_state_machine.py` |
| Memory store + visibility ladder | **Implemented** | `backend/app/services/memory.py`; SQLite-backed, survives restart (`test_memory.py::TestPersistence`); two independent contact-facing guards (visibility allow-list + regex content guard) |
| Connection planner (`agents/orchestrator.py`) | **Implemented**, rule-based | Scores contacts/activities from stored memory rows and consent state, not hardcoded; no LLM call in this path — it is deterministic Python (`backend/app/agents/orchestrator.py`) |
| SQLite persistence | **Implemented** | `backend/app/db.py`; 15 tables, WAL mode, one connection per thread |
| Live event bus (SSE) | **Implemented** | `backend/app/services/events.py` + `backend/app/api/events.py`; every dashboard row is a real `agent_events` insert, nothing is synthesized for display |
| **SpacetimeDB** | **Not implemented** | No dependency, no code reference anywhere in `backend/`. SQLite + SSE was used instead for all persistence and live updates. See `docs/ARCHITECTURE.md`. |
| Cartesia outbound PSTN voice | **Code real, never executed against the live API** | `backend/app/adapters/cartesia_adapter.py` targets the verified endpoint `POST https://api.cartesia.ai/agents/calls` with header `Cartesia-Version: 2026-08-14`. **No `CARTESIA_API_KEY` was supplied**, so `cartesia_call_ready` is always `False` and `config.voice_mode()` returns `"simulated"` (`backend/app/config.py:61-75`). No real phone call has been placed or verified. |
| Local voice simulator | **Implemented and exercised** | `backend/app/services/voice.py:268-527`; drives the full plan lifecycle in tests and `scripts/e2e_demo.py`; every event it emits carries `simulated: true` |
| Anthropic companion chat | **Code real, untested against live API; fallback is what actually ran** | `backend/app/adapters/anthropic_adapter.py` targets `anthropic` 2.3.0 `messages.create`, model `claude-opus-5`. **No `ANTHROPIC_API_KEY` was supplied**, so every companion reply and memory extraction in this build ran the deterministic rule-based fallback and reported `reasoning_mode: "fallback"` (confirmed by `test_api_flow.py::TestCompanionAndMemoryApi::test_message_persists_and_extracts`, line 176: `assert r["reasoning_mode"] == "fallback"`). |
| Memory extraction (LLM path) | **Code real, untested**; fallback used | `adapters/anthropic_adapter.py:178-213` (Claude path) vs. `:226-277` (`_fallback_extract`, real string/regex work, not a stub) |
| Twilio SMS/number provider | **Not configured, SDK not installed** | `config.py:44-46,70-71`; `twilio_configured` is `False`; `services/plans.send_confirmation` always falls back to a labeled `simulated` notification (`services/plans.py:324-325`) |
| QR onboarding → phone verification → consent | **Implemented** | `backend/app/api/onboarding.py`; dev verification codes returned only when no SMS provider exists (contract-compliant); see `docs/ONBOARDING.md` |
| Dual opt-in / circle invites | **Implemented** | `api/onboarding.py:247-485`; token-based, single accept/decline, phone ownership requires a code, not just the link |
| Dashboard (judge-facing) | **Implemented** | `frontend/src/pages/Dashboard.tsx`; reads only `/api/events/stream` and `/api/events/recent`; renders a `SIMULATED` badge whenever `event.simulated` is true |
| Moss, Fetch.ai, Factory Droid, Creao, Notability, Meta, Microsoft | **Not integrated at all** | No dependency, import, or reference anywhere in `backend/` or `frontend/` |

## What is actually proven by the test suite (113 backend + 49 e2e)

- Outreach is blocked without dual opt-in (`test_consent.py::TestOutreachGate::test_contact_who_never_accepted_is_blocked`).
- An unverified phone blocks calling even after the contact accepts (`test_api_flow.py::test_unverified_contact_cannot_be_called`).
- SMS-only consent blocks voice calls (`test_consent.py::test_action_outside_consented_scope_is_blocked`).
- Quiet hours and the per-day rate cap are enforced server-side (`test_consent.py::TestQuietHourWrap`, `test_rate_limit_blocks_after_cap`).
- STOP revokes every consent for that phone number immediately (`test_consent.py::TestStop`).
- Consent revocation halts any in-flight plan (`test_api_flow.py::test_counterproposal_clears_prior_approvals`… and `e2e_demo.py` step 13).
- Private clinical memories never reach contact-facing context, via two independent guards — visibility allow-list and a regex content guard catching mislabelled rows (`test_memory.py::TestPrivacyFiltering`).
- A plan cannot confirm unless both parties approved the *identical* activity and start time (`test_state_machine.py::TestConfirmationGate`).
- The LLM/voice-tool surface has no `confirm_plan` tool; confirmation is backend-only (`agents/tools.py:158-161`, `MODEL_FORBIDDEN_TOOLS`).
- Confirmation and notification are idempotent (`services/plans.py:234-301`, `:303-356`; `test_api_flow.py` idempotency checks).
- Memory survives a simulated process restart (`test_memory.py::TestPersistence::test_survives_a_simulated_restart`).
- Corrections and deletions change later planning output (`e2e_demo.py` step 12: a stated boundary suppresses the theater activity on the next planning pass).

## Honesty mechanisms baked into the code (not just this document)

- `config.Settings.voice_mode()` returns `"cartesia_pstn"` only when the API key, agent id, and from-number id are all present; otherwise `"simulated"` — there is no way to fake `"cartesia_pstn"` by partial configuration (`backend/app/config.py:73-75`).
- Every `calls` row and every `agent_events` row carries `mode`/`simulated`; the dashboard renders a `SIMULATED` badge directly off that flag (`frontend/src/components/Badges.tsx:3-9`), never off a separate "is this real" guess.
- `GET /api/status` exposes `real_call_possible` and a plain-English `notes` string explaining exactly why a real call can or cannot happen (`backend/app/main.py:93-110`).
- Mock showtime data is labeled `source: "mock"` with an explicit disclaimer at every layer it passes through (`services/scheduling.py:161-169`).
