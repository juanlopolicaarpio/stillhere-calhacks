# Architecture

This describes the system as built, not as planned. See `docs/IMPLEMENTATION_STATUS.md`
for the implemented/simulated/not-implemented breakdown per component.

## Components

```
                     ┌─────────────────────────┐
                     │   Frontend (React 19,    │
                     │   Vite, Tailwind v4)     │
                     │   frontend/src/pages/*   │
                     └────────────┬─────────────┘
                                  │ fetch (JSON) + EventSource (SSE)
                                  ▼
                     ┌─────────────────────────┐
                     │   FastAPI app            │
                     │   backend/app/main.py    │
                     │   api/onboarding.py      │
                     │   api/companion.py       │
                     │   api/demo.py            │
                     │   api/events.py          │
                     │   api/voice.py           │
                     └──┬────────┬────────┬─────┘
                        │        │        │
          ┌─────────────┘        │        └──────────────┐
          ▼                      ▼                       ▼
 ┌──────────────────┐  ┌──────────────────┐   ┌───────────────────┐
 │ services/consent  │  │ services/memory   │   │ services/voice     │
 │ services/plans     │  │ services/companion │   │ agents/orchestrator│
 │ domain/state_machine│ │ agents/tools       │   │ services/scheduling│
 └─────────┬──────────┘  └─────────┬─────────┘   └──────────┬──────────┘
           │                       │                        │
           │                       ▼                        ▼
           │            ┌─────────────────────┐  ┌───────────────────────┐
           │            │ adapters/anthropic_  │  │ adapters/cartesia_    │
           │            │ adapter.py           │  │ adapter.py            │
           │            │ (Claude, or fallback)│  │ (real PSTN, or local  │
           │            │                      │  │  simulator)           │
           │            └─────────────────────┘  └───────────────────────┘
           ▼
 ┌──────────────────────────────────────────────────────────────┐
 │ services/events.py  — real event bus (persist + SSE fan-out)  │
 └──────────────────────────────────────────────────────────────┘
           ▼
 ┌──────────────────────────────────────────────────────────────┐
 │ db.py — SQLite (stillhere.db), 15 tables, WAL mode             │
 └──────────────────────────────────────────────────────────────┘
```

## The "Claude proposes, code authorizes" boundary

This is a hard architectural line, not a convention:

- `backend/app/agents/tools.py:22-156` defines `TOOL_SPECS`, the full set of tools a
  model (Claude via Anthropic tool-use, or the Cartesia Managed Agent) may call.
- `MODEL_FORBIDDEN_TOOLS = {"confirm_plan", "send_confirmation", "start_outbound_call"}`
  (`agents/tools.py:161`) are never exposed to any model. There is no tool schema for
  them at all.
- `dispatch()` (`agents/tools.py:281-320`) is the single entry point every model-driven
  tool call goes through; it rejects forbidden names before they can execute and logs
  a `safety.blocked` event when a model tries anyway.
- The only function that can write `PLAN_CONFIRMED` is
  `domain.state_machine.evaluate_confirmation()` (`backend/app/domain/state_machine.py:123-168`),
  called exclusively from `services/plans.confirm_plan()` (`services/plans.py:234-301`).
  It requires both parties to have accepted the *identical* activity and start time,
  and that availability was revalidated — a model voicing "sounds good, I'll lock
  that in" has no mechanism to make it true.

A Cartesia Managed Agent voice tool and a Claude companion-chat call both funnel into
the *same* handlers in `agents/tools.py`, so there is exactly one implementation of
each capability and one place validation/consent is enforced — not two paths that
could drift.

## Adapter pattern

Two provider adapters, both written against verified real APIs, both with an honest
local fallback used automatically when no API key is present:

| Adapter | Real path | Fallback | Selector |
|---|---|---|---|
| `adapters/cartesia_adapter.py` | `POST https://api.cartesia.ai/agents/calls`, header `Cartesia-Version: 2026-08-14` | `services/voice._open_simulated()` / `simulate_turn()` | `config.Settings.voice_mode()` — `"cartesia_pstn"` only when API key + agent id + from-number id are all present (`config.py:73-75`) |
| `adapters/anthropic_adapter.py` | `anthropic.Anthropic(...).messages.create(model="claude-opus-5", ...)` | `_fallback_reply()` / `_fallback_extract()` — real keyword/regex logic, not a stub | `adapters/anthropic_adapter.available()` — `anthropic_configured` (API key presence) |

Neither fallback is silent: `LLMResult.mode` and `OutboundCallResult`/`calls.mode`
always carry which path ran, and every row that resulted from a fallback or
simulator is flagged (`reasoning_mode: "fallback"`, `simulated: true`) so the UI can
label it instead of passing it off as real.

## Why SQLite + SSE instead of SpacetimeDB

The build plan specified SpacetimeDB for live multiplayer-style state sync. It was
**not implemented** — no dependency, no code reference anywhere in this repo.
Instead:

- **Persistence**: raw `sqlite3` with a thin helper layer (`backend/app/db.py`),
  one file (`stillhere.db`), WAL mode for concurrent reads during writes, one
  connection per thread (`db.py:216-226`). 15 tables (see `docs/DATA_MODEL.md`).
- **Live updates**: Server-Sent Events via `sse-starlette` (`api/events.py`). The
  `services/events.emit()` function is the single place any event is created; it
  persists the row to `agent_events` *and* fans it out to subscribed SSE queues in
  the same call (`services/events.py:66-115`), so the dashboard can never show
  something that didn't actually happen in the backend.

This substitution was a practical call made because SpacetimeDB adds real
operational complexity (a separate module/server, a different persistence and
subscription model) for a hackathon timeline where the actual requirement was "the
judge's dashboard updates live and nothing shown is fabricated" — SQLite + SSE meets
that requirement with one dependency instead of a new runtime, and keeps every piece
of state queryable with plain SQL for tests and debugging.

## Data flow for one outreach attempt

1. `POST /api/demo/start` → `agents/orchestrator.identify_opportunity()` scores
   contacts and activities from stored memory (`orchestrator.py:192-310`), creates a
   `Plan` row, and walks it through `OPPORTUNITY_IDENTIFIED → CONSENT_VERIFIED →
   MEMORY_RETRIEVED → CANDIDATE_PLAN_CREATED`.
2. `services/voice.start_outbound_call()` re-validates E.164 format, re-runs the
   consent gate, builds `memory.shareable_context()` (contact-safe only), and either
   dials Cartesia or opens the local simulator (`services/voice.py:35-186`).
3. Cartesia webhook tools (`api/voice.py`) or the local simulator
   (`services/voice.simulate_turn`) drive `FRIEND_ACCEPTED/DECLINED/COUNTERPROPOSED`.
4. `POST /api/demo/sam-response` (simulated in the solo demo) or a real patient
   approval advances to `PATIENT_ACCEPTED`, triggers `scheduling.revalidate_availability()`,
   then `services/plans.confirm_plan()` — the one gate — then
   `services/plans.send_confirmation()`.
5. Every step emits a real `agent_events` row consumed by `GET /api/events/stream`.

## Frontend routes

`/` landing+QR, `/signup`, `/onboarding`, `/circle`, `/invite/:token`,
`/settings/consent`, `/companion`, `/dashboard` (token-gated) — matches
`specs/CONTRACTS.md §7` exactly (`frontend/src/App.tsx:29-39`).
