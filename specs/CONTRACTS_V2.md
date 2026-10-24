# specs/CONTRACTS_V2.md — FROZEN ADDENDUM (SpacetimeDB + Fluid Conversation)

Status: **frozen 2026-10-24** by the lead. Owned by the lead ONLY.
Extends `specs/CONTRACTS.md` (still in force). Implementation agents code against
this and must NOT edit it. Report defects to the lead instead.

## 0. Verified environment (Gate 0 — measured, not assumed)

| item | verified value |
|---|---|
| `spacetime` CLI | **2.13.0** (`spacetimedb-lib` 2.13.0), installed at `C:\Users\User\AppData\Local\SpacetimeDB\spacetime.exe` |
| module language | **TypeScript** (`spacetimedb` npm pkg **2.13.0**, V8). Chosen because Rust/`cargo` and `dotnet` are NOT installed; Node 24.12.0 is. |
| local server | `spacetime start --listen-addr 127.0.0.1:3000`, `GET /v1/ping` → 200 |
| proven loop | `spacetime build` → `publish` → `call` (wrote a row) → `sql` (read it back) |
| reducer over HTTP | `POST /v1/database/{db}/call/{reducer}`, `Authorization: Bearer <jwt>`, body = **JSON array of positional args** → 200 |
| SQL over HTTP | `POST /v1/database/{db}/sql`, `Content-Type: text/plain`, body = SQL → `[{schema,rows,...}]` |
| CLI token | `spacetimedb_token` in `C:\Users\User\AppData\Local\SpacetimeDB\config\cli.toml` (JWT, 371 chars) |
| baseline tests | **113 pytest + 49 e2e, re-run and passing** at commit `f144f28` |
| git | branch `feat/spacetimedb-fluid-agent`, baseline commit `f144f28` is the rollback point |
| NOT installed | ngrok, cloudflared (needed for live Cartesia tool webhooks) |
| NOT supplied | `CARTESIA_API_KEY`, `ANTHROPIC_API_KEY`, Twilio — all absent |

## 1. Division of authority — SpacetimeDB vs SQLite

SpacetimeDB is the **authoritative transactional source of truth for the shared live
coordination workflow**. SQLite remains the store for private/companion data. This split
is deliberate and must be documented, not blurred.

| data | store | why |
|---|---|---|
| connection sessions, call sessions, plan proposals + versions, participant responses, consent-state snapshots, sanitized agent events, plan confirmations, demo presence | **SpacetimeDB** | shared, real-time, multi-client, transactional confirmation |
| companion conversation transcript, private/clinical memories, memory provenance, verification codes, invitation tokens, phone numbers | **SQLite** | private to one user; must never be subscribable by any client |

**Hard rule:** no raw phone number, verification code, invitation token, conversation
transcript, or `private_to_sam` / `agent_internal` memory value may ever be written to a
SpacetimeDB table. Only sanitized, shareable projections cross the boundary.

**Hard rule:** confirmation happens **only** in the SpacetimeDB reducer
`confirm_if_matching`. The pre-existing SQLite confirmation path becomes a mirror of the
reducer outcome, never an independent authority. There must be no code path that marks a
plan confirmed without the reducer having returned success.

## 2. SpacetimeDB module — canonical schema

Module name: `stillhere`. Path: `spacetime/spacetimedb`. TypeScript.
Table `name:` values are snake_case; TS accessors are camelCase (per the SDK rules in
`spacetime/.cursor/rules/typescript-server.mdc` — that file is authoritative for API shape).

### Tables

| table (`name`) | public? | purpose |
|---|---|---|
| `connection_sessions` | `public: true` | one row per demo/outreach session |
| `call_sessions` | `public: true` | live call state; **no phone numbers**, only `to_number_redacted` |
| `plan_proposals` | `public: true` | **immutable** versions; a revision inserts a new row |
| `participant_responses` | `public: true` | who accepted what, keyed to a specific proposal version |
| `consent_state` | `public: true` | sanitized snapshot: booleans + reasons, never field values |
| `agent_events` | `public: true` | sanitized dashboard feed |
| `plan_confirmations` | `public: true` | written only by `confirm_if_matching` |
| `demo_presence` | `public: true` | optional; proves two-client sync |
| `service_principals` | **`public: false`** | identities allowed to call privileged reducers |

Sensitive-by-construction rule: because every public table above is subscribable, each
MUST contain only sanitized fields. Any table that would hold private data must be
`public: false` **and** carry a `clientVisibilityFilter`.

### Required columns (minimum; agent B may add, never remove)

```
connection_sessions: sessionId t.string().primaryKey(), patientRef t.string(),
  contactRef t.string(), state t.string(), mode t.string(),
  createdAt t.timestamp(), updatedAt t.timestamp()

call_sessions: callId t.string().primaryKey(), sessionId t.string().index('btree'),
  providerCallId t.option(t.string()), toNumberRedacted t.string(),
  status t.string(), mode t.string(), simulated t.bool(),
  requestedAt t.timestamp(), answeredAt t.option(t.timestamp()),
  endedAt t.option(t.timestamp()), error t.option(t.string())

plan_proposals: proposalId t.string().primaryKey(), sessionId t.string().index('btree'),
  version t.u32(), activity t.string(), location t.option(t.string()),
  startsAt t.string(), whyContact t.string(), whyActivity t.string(),
  memoryCitations t.array(t.string()), supersedesVersion t.option(t.u32()),
  active t.bool(), createdAt t.timestamp()

participant_responses: responseId t.string().primaryKey(),
  proposalId t.string().index('btree'), version t.u32(),
  role t.string(),            // 'patient' | 'contact'
  response t.string(),        // 'accepted' | 'declined' | 'counterproposed'
  agreedActivity t.string(), agreedStartsAt t.string(),
  simulated t.bool(), respondedAt t.timestamp()

consent_state: consentId t.string().primaryKey(), sessionId t.string().index('btree'),
  patientOutreachActive t.bool(), contactOptedIn t.bool(),
  contactPhoneVerified t.bool(), allowedActions t.array(t.string()),
  revoked t.bool(), reason t.string(), updatedAt t.timestamp()

agent_events: eventId t.string().primaryKey(), seq t.u64(),
  sessionId t.option(t.string()), proposalId t.option(t.string()),
  callId t.option(t.string()), eventType t.string(), safeSummary t.string(),
  payloadJson t.string(),     // ALREADY redacted by the backend before it arrives
  simulated t.bool(), at t.timestamp()

plan_confirmations: confirmationId t.string().primaryKey(),
  proposalId t.string().unique(), version t.u32(),
  activity t.string(), startsAt t.string(),
  idempotencyKey t.string().unique(), confirmedAt t.timestamp()

demo_presence: connectionId t.string().primaryKey(), label t.string(),
  lastSeen t.timestamp()

service_principals: identity t.identity().primaryKey(), label t.string()
```

### Reducers (exact names — the backend calls these by name)

| reducer | args (positional order is the HTTP body array order) | contract |
|---|---|---|
| `start_session` | `sessionId, patientRef, contactRef, mode` | upsert session, state `OPPORTUNITY_IDENTIFIED` |
| `set_call_state` | `callId, sessionId, providerCallId, toNumberRedacted, status, mode, simulated, error` | upsert call row; set `answeredAt`/`endedAt` from status |
| `propose_or_revise_plan` | `proposalId, sessionId, version, activity, location, startsAt, whyContact, whyActivity, memoryCitations, supersedesVersion` | **insert immutable row**; mark prior versions `active=false`; **delete/ignore all responses for superseded versions** |
| `respond_as_friend` | `responseId, proposalId, version, response, agreedActivity, agreedStartsAt, simulated` | reject if proposal not `active` (stale) |
| `record_patient_approval` | `responseId, proposalId, version, response, agreedActivity, agreedStartsAt, simulated` | reject if proposal not `active` (stale) |
| `confirm_if_matching` | `proposalId, version, idempotencyKey` | **THE gate.** See §3 |
| `revoke_or_cancel` | `sessionId, reason` | mark consent revoked, deactivate proposals, halt session |
| `append_sanitized_event` | `eventId, seq, sessionId, proposalId, callId, eventType, safeSummary, payloadJson, simulated` | append-only |

All privileged reducers MUST verify `ctx.sender` is present in `service_principals`
(seeded via `init` with the publishing identity) and throw `SenderError` otherwise.
Exception: `demo_presence` upsert may be called by any client.

## 3. `confirm_if_matching` — the hard security invariant

Confirms **only if ALL** hold, inside one transaction:

1. the proposal row exists, and `active === true` (not superseded)
2. its `version` equals the passed `version` (stale version rejected)
3. there is a `participant_responses` row with `role='contact'`, `response='accepted'`,
   for **this exact `proposalId` AND `version`**
4. likewise for `role='patient'`
5. both rows' `agreedActivity` **and** `agreedStartsAt` equal the proposal's
   `activity` / `startsAt` **and** each other
6. `consent_state` for the session has `revoked === false`
7. no existing `plan_confirmations` row for this `proposalId`

**Idempotency:** if a `plan_confirmations` row already exists with the same
`idempotencyKey`, return success without inserting again. A different key for an
already-confirmed proposal is a rejection, not a second confirmation.

Any failure throws `SenderError` with a machine-readable reason string from this exact
set: `no_proposal`, `stale_version`, `superseded`, `missing_contact_approval`,
`missing_patient_approval`, `activity_mismatch`, `time_mismatch`, `consent_revoked`,
`already_confirmed`.

## 4. Python ↔ SpacetimeDB adapter

New file, owned by agent B: `backend/app/adapters/spacetimedb_adapter.py`.

```python
def configured() -> bool                      # host + db + token all present
def call_reducer(name: str, args: list) -> dict   # POST /v1/database/{db}/call/{name}
def query_sql(sql: str) -> list[dict]             # POST /v1/database/{db}/sql
def health() -> dict                              # {"reachable": bool, "db": str, "detail": str}
```

- Positional `args` are serialized as a **JSON array**, matching the verified HTTP shape.
- Env: `SPACETIMEDB_HOST` (default `http://127.0.0.1:3000`), `SPACETIMEDB_DB`
  (default `stillhere`), `SPACETIMEDB_TOKEN`.
- `SPACETIMEDB_TOKEN` falls back to reading `spacetimedb_token` from the CLI's
  `cli.toml` **for local development only**, and that fallback must be logged as such.
- Raises `SpacetimeNotConfigured` when unset, `SpacetimeError` on transport/HTTP failure.
- **Never** logs the token or any arg value that could contain a phone number.

### Degradation policy (lead decision — do not change)

`config.Settings.coordination_mode()` returns:
- `"spacetimedb"` when the adapter is configured **and** reachable
- `"sqlite_fallback"` otherwise

`/api/status` must expose `coordination_mode` and `spacetimedb_reachable`. The UI must
state plainly which one is live. When in `sqlite_fallback`, nothing may claim
SpacetimeDB is the backend. This is the same honesty rule as `voice_mode`.

## 5. Fluid conversation engine

New file, owned by agent C: `backend/app/agents/conversation.py`.
**The regex branching in `services/voice.py::simulate_turn` is replaced.** Keyword
matching may remain ONLY as a labelled last-resort fallback when
`ANTHROPIC_API_KEY` is absent, and `/api/status` must report
`conversation_mode: "claude" | "keyword_fallback"`.

```python
def interpret_turn(
    utterance: str, *, call_id: str, conversation: list[dict], context: dict
) -> TurnResult
```

`TurnResult` fields: `reply: str`, `intent: str`, `slots: dict`,
`tool_calls: list[dict]`, `state: str`, `mode: str`, `needs_clarification: bool`.

Claude performs **interpretation and phrasing only**. It receives typed tools
(`specs/AGENT_TOOLS.md`) and its `MODEL_FORBIDDEN_TOOLS` set is unchanged:
`confirm_plan`, `send_confirmation`, `start_outbound_call`. Every state change still
goes through the deterministic backend and, for coordination state, through a reducer.

### Intent vocabulary (stable; the dashboard and tests switch on these)

```
accept  decline  maybe_defer  counter_time  counter_activity  counter_both
ask_why_activity  ask_about_patient_health  ask_what_patient_likes
ask_add_person  request_stop_contact  interrupt  silence  out_of_scope
clarification_needed  unknown
```

### Required behaviours

- **Never invent availability.** A proposed time must come from
  `scheduling.find_free_slots` / `find_slot_on_weekday`. If a requested day has no shared
  window, say so honestly and offer a real alternative.
- **Never invent a booking or a showtime.** Showtimes are `source:"mock"` and must be
  described as illustrative.
- **`ask_about_patient_health` must never disclose.** Offer a bounded, non-clinical
  answer and move on. This is the single most important conversational safety case.
- **`ask_add_person` must not disclose or contact a third party.** Maya's existence is not
  confirmable without her own consent.
- **`maybe_defer` is not an acceptance.** Do not record a response, and do not promise a
  future reminder unless scheduling exists (it does not).
- **`request_stop_contact`** persists an opt-out immediately and ends outreach.
- **Changing terms invalidates prior approvals** and creates a new proposal version.
- Replies are short — target under ~45 spoken words.

## 6. Event vocabulary additions

Added to §6 of `specs/CONTRACTS.md`:

```
stdb.reducer_called  stdb.reducer_rejected  stdb.unavailable
conversation.interpreted  conversation.clarification_requested
proposal.revised  approval.invalidated
```

## 7. File ownership — NO two agents write the same file

| agent | owns (exclusive write) |
|---|---|
| **lead** | `specs/**`, `backend/app/config.py`, `db.py`, `main.py`, `domain/**`, `services/**`, `api/**`, `docs/API_CONTRACTS.md`, `docs/TEST_RESULTS.md`, `Makefile`, `.env.example`, `README.md`, root `CLAUDE.md`/`BUILD_PLAN.md`, all merges |
| **A** Cartesia | `backend/app/adapters/cartesia_adapter.py`, `scripts/discover_cartesia.py`, `scripts/setup_cartesia_agent.py`, `scripts/smoke_call.py`, `backend/tests/test_cartesia_adapter.py`, `docs/CARTESIA_SETUP.md`, `docs/CARTESIA_SPONSOR.md`, `prompts/OUTREACH_AGENT.md` |
| **B** SpacetimeDB | `spacetime/**`, `backend/app/adapters/spacetimedb_adapter.py`, `scripts/stdb_setup.py`, `backend/tests/test_spacetimedb.py`, `docs/SPACETIMEDB_ARCHITECTURE.md`, `docs/SPACETIMEDB_SPONSOR.md` |
| **C** Fluid agent | `backend/app/agents/conversation.py`, `backend/app/agents/tools.py`, `backend/app/adapters/anthropic_adapter.py`, `prompts/COMPANION_SYSTEM.md`, `prompts/MEMORY_EXTRACTION.md`, `prompts/CONVERSATION_POLICY.md`, `backend/tests/test_conversation.py`, `docs/FLUID_CONVERSATION_TEST_MATRIX.md` |
| **D** Frontend | `frontend/**` |
| **E** QA/security | `backend/tests/test_security.py`, `test_negative_paths.py`, `test_concurrency.py`, `scripts/e2e_demo.py`, `scripts/chaos_check.py`, `docs/PRIVACY_SAFETY.md`, `docs/SECURITY.md` |
| **F** Docs/sponsor | `docs/ANTHROPIC_SPONSOR.md`, `MICROSOFT_SPONSOR.md`, `META_SPONSOR.md`, `DEVPOST.md`, `DEMO_RUNBOOK.md`, `IMPLEMENTATION_STATUS.md`, `MULTI_AGENT_ORCHESTRATION.md`, `SPONSOR_MATRIX.md`, `LIVE_PROVIDER_EVIDENCE.md`, `FINAL_HANDOFF.md` |

Agents may **read** anything. Writing outside your list is a contract violation.

## 8. Non-negotiable acceptance criteria

1. `make test` and `make e2e` still pass — the 113 + 49 baseline must not regress.
2. A real `spacetime` module is published and a phone/simulated reply causes an actual
   reducer transaction, demonstrable via `spacetime sql`.
3. Two independent subscribed clients update with no polling or reload.
4. No private data in any public SpacetimeDB table — test-enforced.
5. Confirmation impossible except via `confirm_if_matching` with matching version+terms.
6. Honest mode reporting: `voice_mode`, `coordination_mode`, `conversation_mode`.
7. Nothing claims a live provider call occurred unless evidence exists in `evidence/`.
