# Judge Dashboard

Source: `frontend/src/pages/Dashboard.tsx`, `frontend/src/hooks/useAgentEvents.ts`,
`backend/app/api/events.py`, `backend/app/services/events.py`.

## What it is

A token-gated page at `/dashboard` that renders **only** events the backend
actually emitted. The page's own subtitle states this constraint:
*"Live, real data only — nothing on this page is scripted"*
(`Dashboard.tsx:260`). The `useAgentEvents` hook enforces this as a hard rule in
its own doc comment: *"this hook renders ONLY events actually received from the
backend over SSE. It never fabricates, replays, or pre-seeds events"*
(`useAgentEvents.ts:15-18`).

## Token auth: header or query parameter

`specs/CONTRACTS.md §7` documents `X-Dashboard-Token` as the auth header for all
`/dashboard`-serving data routes. That works for plain `fetch` calls
(`getRecentEvents`, `getPlan`, `demoReset`, `demoStart`, `demoSamResponse` in
`frontend/src/api/client.ts:556-605` all send it as a header).

It does **not** work for the live stream, because the browser's native
`EventSource` API cannot set custom headers. `backend/app/api/events.py:26-34`
(`_auth_dashboard`) accepts the token from *either* source:

```python
supplied = header_token or query_token
if not supplied or not secrets.compare_digest(str(supplied), str(expected)):
    raise HTTPException(401, ...)
```

`GET /api/events/stream?dashboard_token=...` and
`GET /api/events/recent?dashboard_token=...` both honor the query-param form.
`useAgentEvents.ts:36-39` appends it specifically because `EventSource` leaves no
other option, and documents this as a known, deliberate gap in the frontend's
report to the lead — not an oversight.

Token comparison uses `secrets.compare_digest` (constant-time), so a wrong or
missing token always 401s with `{"error": {"code": "unauthorized", ...}}`
(`test_api_flow.py::TestDashboardAuth`).

## Panels and what they show

| Panel | Backed by | Notes |
|---|---|---|
| Live event stream indicator | `useAgentEvents` connection state | `connected` toggles on `EventSource.onopen`/`onerror`; shown as a green/gray dot (`Dashboard.tsx:262-275`) |
| Reset / start outreach controls | `POST /api/demo/reset`, `POST /api/demo/start` | Real backend calls; `mode` badge (`simulated`/`cartesia_pstn`) comes straight from the `demo/start` response (`:280-299`) |
| Sam (patient identity + consent indicator) | current `Plan` + `GET /api/consents` | Shows whether an active consent record exists for the plan's contact (`:304-342`) |
| Why this plan? | `Plan.rationale`, `Plan.activity`, `Plan.location` | The structured `why_contact`/`why_activity` fields exist in the data model (`db.py:126-127`) but the current panel renders the combined `rationale` string; see `docs/DATA_MODEL.md` |
| Retrieved memories | `GET /api/memories?audience=demo` | Only `public_demo_safe` memories are visible here, by the same `AUDIENCE_VISIBILITY` allow-list the backend enforces everywhere else — the dashboard gets no special access (`memory.py:200-209`) |
| Availability | `Plan.starts_at`, `Plan.status` | Direct plan fields |
| Live call status | events of type `call.*` filtered to the current `call_id` | Shows a `SIMULATED` badge whenever `event.simulated` is true (`:416-433`) |
| Tool-call timeline | `call.tool_invoked` events | Shows a `MOCK DATA` badge when the payload's `source === "mock"` (showtimes), plus `SIMULATED` when applicable (`:435-463`) |
| Friend response | last `friend.responded` event | — |
| Sam approval simulator | `POST /api/demo/sam-response` | Permanently labeled `SIMULATED SAM APPROVAL` (`:485-532`) — this panel exists *because* there is no second human in the solo demo; it is never presented as a real person responding |
| Confirmed plan | `Plan.status in {PLAN_CONFIRMED, NOTIFICATIONS_SENT, COMPLETE}` | — |
| Notification status | `notification.sent` events | Carries `simulated: true` whenever Twilio isn't configured (always, in this build) |

## Event vocabulary

Stable, UI-switches-on-these vocabulary from `specs/CONTRACTS.md §6`, mirrored in
`frontend/src/api/client.ts:73-101` (`EventType`):

```
demo.reset  demo.start
consent.verified  consent.denied  consent.revoked
memory.retrieved  memory.extracted  memory.corrected  memory.deleted
plan.candidate_created  plan.state_changed  plan.confirmed  plan.rejected
call.requested  call.accepted_by_provider  call.ringing  call.answered
call.first_audio  call.turn  call.tool_invoked  call.ended  call.failed
friend.responded  patient.approval_requested  patient.responded
notification.sent  safety.blocked  error
```

Every one of these is emitted from real backend code via `services/events.emit()`
(`services/events.py:66-115`) — there is no separate list of "demo events" that
gets replayed; the list above is just every `event_type` string that appears as a
literal in the backend source.

## How `simulated` labeling works

`services/events.emit(..., simulated: bool = False)` stores the flag directly on
the `agent_events` row (`events.py:74,92`). Call sites set it explicitly based on
real state, never a UI-side guess:

- `services/voice.py` sets `simulated=(mode != "cartesia_pstn")` on every call
  event (`:155,179`), and hardcodes `simulated=True` throughout the local
  simulator's turn handler (`:276-521`) because every one of those events is, by
  construction, not a real phone conversation.
- `api/demo.py::sam_response` sets `simulated=True` on every state transition and
  response it records, because the solo demo has no second human
  (`:107-149`).
- `services/plans.send_confirmation()` sets `simulated = not s.twilio_configured`
  (`:325`) — in this build, always `True`, since Twilio isn't configured.

The frontend never recomputes this; it reads `event.simulated` directly and
renders `<SimulatedBadge />` (`frontend/src/components/Badges.tsx:3-9`,
amber pill, `●  SIMULATED`).

## Replay-on-connect

`GET /api/events/stream?replay=50` sends the last 50 persisted events before
switching to live streaming (`api/events.py:62-65`), so a dashboard opened
mid-demo shows accurate history instead of starting blank. `Dashboard.tsx:83-100`
additionally seeds from `GET /api/events/recent` and merges both sources by
event `id`, sorted by `seq`, so no event is double-counted or lost across a
reconnect.
