# SpacetimeDB

## 1. Status

`not integrated`. The build plan proposed SpacetimeDB for live state; the
actual implementation uses SQLite plus Server-Sent Events instead.

## 2. Role in Still Here

Was proposed as the live-state layer (reducers + subscriptions) behind the
dashboard and event stream. Not used. No SpacetimeDB reducers, tables, or
subscriptions exist anywhere in this codebase.

## 3. Credentials required

None — not integrated.

## 4. SDK / package / version as actually verified

Not installed. Not listed in `backend/requirements.txt`.

## 5. Setup steps

Not started.

## 6. Actual implemented API calls

None against SpacetimeDB. The actual implementation used instead:

- **SQLite**, via `backend/app/db.py` — 15 tables (`users`,
  `verification_codes`, `relationships`, `invitations`, `conversations`,
  `memories`, plus the plan/call/event/consent/idempotency tables), accessed
  through thin `q()` / `q1()` / `ex()` helpers.
- **Server-Sent Events**, via `sse-starlette` **3.5.0** (verified by `pip
  show`), wired in `backend/app/api/events.py` and fed by the real event bus
  in `backend/app/services/events.py` — `emit()` persists every event to the
  `agent_events` table and fans it out live to subscriber queues
  (`backend/app/services/events.py:66`-`115`).

## 7. How to test

- `python -m pytest backend/tests -q` → 113 passed, including coverage of
  the event bus and SQLite persistence layer.
- Manually: `GET /api/events/stream` (SSE) while driving `/api/demo/start`
  to watch events arrive live.

## 8. Known limitations

- No subscriptions, reducers, or any other SpacetimeDB primitive exist —
  do not claim otherwise.
- The adapter seam is clean enough that `services/events.py` could in
  principle be swapped for a SpacetimeDB-backed implementation later (the
  rest of the codebase only calls `events.emit()` / `events.recent()` /
  `events.subscribe()`), but no such swap has been attempted or validated.
- SQLite + SSE is the tested, working fallback actually shipped; it is not a
  temporary stub — it has 113 passing tests behind it.
