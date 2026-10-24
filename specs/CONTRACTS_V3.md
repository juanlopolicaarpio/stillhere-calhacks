# specs/CONTRACTS_V3.md — FROZEN ADDENDUM (proactive detection, calendars, groups, booking)

Status: **frozen 2026-10-25** by the lead. Lead-owned; implementation agents
code against it and must not edit it. Extends `CONTRACTS.md` and `CONTRACTS_V2.md`.

Source: `docs/extraclaude.txt`. Target population restated there: **adults in the
first 30 days after discharge from inpatient psychiatric care who have
difficulty maintaining relationships.** The product is a *proactive,
permission-based reconnection system*, not an agent that schedules on request.

## 0. Baseline at freeze time (measured)

255 pytest passing; 49 e2e checks passing; frontend builds clean. Rollback point
is commit `b5b0473`. A real PSTN call is verified
(`evidence/real_call_ac_ZzzPbaPYKstadZguL5TRtF.json`). SpacetimeDB is
authoritative for confirmation. Claude drives conversation and memory.

**Nothing below may regress any of that.**

## 1. Status vocabulary (required in every report and doc)

Exactly one per feature: `implemented` · `externally_verified` ·
`simulated` · `blocked`. `externally_verified` requires evidence from a real
third-party system in `evidence/`. Never upgrade a status without that evidence.

## 2. Longitudinal baseline + opportunity detection — Agent G

Replaces the hardcoded "three weeks post-discharge" scenario with computed
history. **The scenario stays as seed data; the *detection* must be real.**

New files (agent G owns exclusively):
`backend/app/services/baseline.py`, `backend/app/services/opportunity.py`,
`backend/tests/test_baseline.py`, `docs/OPPORTUNITY_DETECTION.md`.

New table (agent G adds via the additive-migration block in `db.py` — the ONLY
edit agent G may make to a lead-owned file, and it must be additive):

```
contact_events(id, patient_id, contact_id, kind, occurred_at, source, note)
  kind: 'call'|'message'|'met_in_person'|'plan_completed'|'plan_declined'
  source: 'seed'|'observed'|'user_reported'
```

```python
# baseline.py
def record_contact_event(patient_id, contact_id, kind, *, occurred_at=None,
                         source="observed", note="") -> str
def relationship_baseline(patient_id, contact_id) -> Baseline
def all_baselines(patient_id) -> list[Baseline]
```
`Baseline` carries at minimum: `contact_id`, `events_considered`,
`median_gap_days`, `mean_gap_days`, `current_gap_days`, `deviation_ratio`
(`current_gap / median_gap`), `trend` (`"stable"|"drifting"|"dormant"`),
`confidence` (low with <4 events), `basis` (human-readable sentence citing the
real numbers).

```python
# opportunity.py
def detect(patient_id, *, now=None) -> list[Opportunity]
```
`Opportunity`: `contact_id`, `score`, `reason` (cites real numbers),
`baseline`, `shared_free_slots`, `suppressed_reason|None`.

Hard rules, test-enforced:
- **A decline or silence must never escalate.** After `plan_declined`, or after
  an unanswered call, the next opportunity for that contact must be
  *suppressed* for a cooling-off window (default 14 days, configurable) and
  `suppressed_reason` must say so. Never shorten the window after a decline,
  and never raise the score.
- Fewer than 4 contact events ⇒ `confidence="low"`, and detection must say so
  rather than inventing a baseline.
- Detection reads only `agent_internal`-or-wider memory. Never clinical rows.
- Pure functions over the DB: no LLM call inside detection. Claude proposes the
  *activity* afterwards (existing `agents/orchestrator.py`), not the opportunity.

## 3. Calendars — Agent H

Real **Google Calendar OAuth** + **Microsoft Graph** free/busy. Verify current
scopes and endpoints against live docs; do not invent fields.

New files (agent H owns exclusively):
`backend/app/adapters/google_calendar.py`,
`backend/app/adapters/microsoft_graph.py`,
`backend/app/api/calendar.py`, `backend/tests/test_calendar.py`,
`docs/CALENDAR_INTEGRATION.md`.

```python
# both adapters expose the same shape
def configured() -> bool
def auth_url(user_id: str, redirect_uri: str) -> str
def exchange_code(code: str, redirect_uri: str) -> dict   # tokens
def free_busy(user_id, window_start, window_end) -> list[Busy]
def create_event(user_id, *, title, starts_at, ends_at, attendees, location, description) -> dict
```

- Store tokens in a new `calendar_tokens` table (additive migration, agent H's
  one permitted `db.py` edit): `(id, user_id, provider, access_token,
  refresh_token, expires_at, scopes, created_at)`. Tokens are **secrets**:
  never logged, never in an event payload, never in an API response.
- Routes: `GET /api/calendar/{provider}/auth`, `GET /api/calendar/{provider}/callback`,
  `GET /api/calendar/status?user_id=`, `POST /api/calendar/disconnect`.
- **Degrade honestly.** With no credentials, `configured()` is False and the
  existing seeded `availability` table remains the source. `/api/status` gains
  `calendar_mode: "google" | "microsoft" | "seeded"`. Never claim a real
  calendar is connected when it is not.
- Requesting free/busy only — do **not** request full-event-content scopes.
  Document the exact scopes used and why they are minimal.

## 4. Group planning — LEAD

N opted-in participants, not just 1:1. Touches `plans.py`, `state_machine.py`,
`coordination.py` and the SpacetimeDB module, so the **lead** implements it.
Agents must not edit those files.

Contract other agents may rely on:
- `plan_participants` already supports N rows. `role` becomes
  `'patient'|'contact'`, with multiple contacts allowed.
- Confirmation requires the patient **and every** participating contact to have
  accepted the identical activity and start time on the same active version.
- A configured cap (`MAX_GROUP_SIZE`, default 4) bounds participants.
- Each contact's own consent is checked independently; one contact declining
  does not reveal that contact's identity to the others.

## 5. Booking — Agent J

New files (agent J owns exclusively):
`backend/app/adapters/booking.py`, `backend/tests/test_booking.py`,
`docs/BOOKING.md`.

```python
def providers() -> list[str]                 # only genuinely configured ones
def search(activity, *, location, starts_at) -> list[Option]
def reservation_link(option) -> str          # a REAL deep link
def book(option, *, idempotency_key, approved_by) -> BookingResult
```
- **No cost or transaction without explicit approval.** `book()` must refuse
  unless `approved_by` names a user who approved *that exact* option and price.
  No approval ⇒ return an honest **unbooked** plan carrying a real reservation
  link the human can complete.
- Integrate at most **one** real provider, and only if credentials exist.
  Otherwise every option is `source: "unbooked"` with a real link, labelled in
  the UI. Mock showtimes stay labelled `MOCK DATA`.
- `/api/status` gains `booking_mode: "<provider>" | "unbooked"`.

## 6. Two-client subscription proof — Agent K

Owns `frontend/**` exclusively. The SpacetimeDB subscription code exists
(`frontend/src/hooks/useSpacetime.ts`, generated bindings) but has **never been
visually verified**, so it is currently `simulated`. Make it
`externally_verified`: two independent browser contexts updating with no polling
and no reload, with a screenshot or recording in `evidence/`. Also surface the
new modes (`calendar_mode`, `booking_mode`) and group participants. The app must
still build and run with SpacetimeDB absent.

## 7. File ownership — no two agents write the same file

| agent | exclusive write |
|---|---|
| **lead** | `specs/**`, `config.py`, `db.py` (except the two permitted additive migrations), `main.py`, `domain/**`, `services/{plans,voice,coordination,consent,memory,companion,demo_reset,scheduling,events}.py`, `api/**`, `agents/**`, `spacetime/**`, `Makefile`, `.env.example`, `README.md`, root docs |
| **G** | `services/baseline.py`, `services/opportunity.py`, `tests/test_baseline.py`, `docs/OPPORTUNITY_DETECTION.md` |
| **H** | `adapters/google_calendar.py`, `adapters/microsoft_graph.py`, `api/calendar.py`, `tests/test_calendar.py`, `docs/CALENDAR_INTEGRATION.md` |
| **J** | `adapters/booking.py`, `tests/test_booking.py`, `docs/BOOKING.md` |
| **K** | `frontend/**` |

`api/calendar.py` is agent H's despite living under `api/`; the lead wires the
router into `main.py`.

## 8. Non-negotiables

1. 255 pytest + 49 e2e must still pass. Run them.
2. Tests stay hermetic: never call a real provider, never place a real call.
   `conftest.py` blanks provider env vars and resets coordination state.
3. No clinical data in any contact-facing surface or public SpacetimeDB table.
4. Consent gates unchanged; a decline never escalates.
5. The judge-answers-the-phone demo keeps working.
6. Every feature reported with a §1 status and real evidence.
