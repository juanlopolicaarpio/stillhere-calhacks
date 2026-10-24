# Demo Execution Plan

## 1. Status

`simulated` end to end unless a Cartesia key is added before demo day, in
which case the call leg becomes `implemented (code) / blocked (live call
unverified until actually run)`. Sam's approval step is always `simulated`,
regardless of whether the call itself is real.

## 2. Role in Still Here

This is the operational runbook for demo day: what runs where, what the
honest fallback is if no Cartesia key is present (the default state today),
what to say out loud if a live call fails, and an explicit reminder that
Sam's approval is simulated and labeled as such in the UI.

## 3. Credentials required

Optional for the demo to run at all (simulator needs none); required only to
attempt a real call: `CARTESIA_API_KEY`, `CARTESIA_AGENT_ID`,
`CARTESIA_FROM_NUMBER_ID`, `PUBLIC_BASE_URL`, `WEBHOOK_SECRET`. Optional:
`ANTHROPIC_API_KEY` for live Claude responses instead of the fallback.

## 4. SDK / package / version as actually verified

Same stack as the rest of the backend: `cartesia` 4.2.0, `anthropic` 2.3.0,
`qrcode` 8.2 / `pillow` 13.0.0, `httpx` 0.28.1, `sse-starlette` 3.5.0 — all
verified via `pip show` in the project virtualenv.

## 5. Setup steps (what runs where)

| Component | Command | Notes |
|---|---|---|
| Backend | `make backend` (`uvicorn app.main:app --app-dir backend --reload --port 8000`) | FastAPI app, SQLite db at `stillhere.db` |
| Frontend | `make frontend` (`cd frontend && npm run dev`) | Vite dev server |
| Reset demo data | `make seed` / `make reset` | Re-initializes db and demo seed state via `services/demo_reset.reset()` |
| Health check | `make health` | Hits `/health` and `/api/status` |
| Cartesia preflight (only if attempting a real call) | `make preflight` (`scripts/discover_cartesia.py`) | Confirms key, lists agents/numbers, writes `evidence/cartesia_discovery.json` |
| Real call smoke test (only if attempting a real call) | `make call` (`scripts/smoke_call.py`) | Places one real PSTN call, writes `evidence/real_call_<id>.json` |
| Full test suite | `make test` → 113 passed | Run before the demo to confirm nothing regressed |
| Scripted e2e | `make e2e` → 49 passed, 0 failed | Exercises the whole flow through the simulator |

## 6. Actual implemented API calls

The demo flow calls, in order: `POST /api/demo/reset`, `POST /api/signup` /
`/api/verify` / `/api/onboarding/preferences` / `/api/outreach/activate`
(onboarding), `POST /api/circle/invite` and `/api/invite/{token}/accept`
(dual consent), `POST /api/demo/start` (`backend/app/api/demo.py:26`, which
calls `orchestrator.identify_opportunity()` then
`voice.start_outbound_call()`), then either real call webhooks or
`POST /api/voice/simulate/turn` calls, and finally
`POST /api/demo/sam-response` (`backend/app/api/demo.py:81`) for the
simulated patient-approval step.

## 7. How to test (dry run before presenting)

1. `make test` and confirm 113 passed.
2. `make e2e` and confirm 49 passed, 0 failed.
3. `make reset` to get a clean demo state.
4. Run through onboarding → circle invite/accept → `/api/demo/start` once in
   the simulator before going live, to confirm the dashboard renders events
   correctly.
5. Only if a Cartesia key has been added: `make preflight`, then `make call`
   to one known-good test number, and read `evidence/real_call_<id>.json`
   before relying on it in front of judges.

## 8. Honest fallback plan and what to say if things fail

- **Default state (no Cartesia key):** the demo runs entirely on the local
  simulator. State this plainly before the call portion: "This call is
  simulated — no real phone rings. The same code paths (consent gate, memory
  citation, state machine, webhook tools) are what a live Cartesia Managed
  Agent call would exercise; we just haven't supplied provider credentials
  for this environment."
- **If a live call is attempted and fails (no answer, provider error,
  network error):** say so directly — "The call didn't go through; here's
  the actual error from Cartesia" — and read `result.error` /
  `evidence/real_call_<id>.json` rather than guessing. Then fall back to
  `force_mode="simulated"` on `/api/demo/start` to continue the walkthrough
  live.
- **Sam's approval is always simulated.** `POST /api/demo/sam-response`
  (`backend/app/api/demo.py:81`) is explicitly documented in code as
  simulated — "In the solo demo there is no second human, so Sam's approval
  is simulated" — and every event it produces carries `simulated=True`. The
  dashboard must show this label; never present it as a real second person
  approving in real time. Say this out loud: "Sam's yes here is me clicking
  a button that simulates his approval — in production this would be a
  notification to Sam's own device."
- Every event emitted anywhere in the simulated path carries `simulated:
  true` (`backend/app/services/events.py:82`-`93`); if the dashboard is ever
  showing an unlabeled event during a simulated run, that is a bug, not a
  real result.

## Known limitations

- No rehearsed live call exists yet; the honest fallback above is the
  expected path for this demo unless credentials are added beforehand.
- If credentials are added last-minute, run the full dry run above at least
  once before presenting, since no real call has ever been exercised in this
  codebase.
