# specs/CONTRACTS.md — FROZEN SHARED CONTRACTS

Status: **frozen 2026-10-24** by the lead agent. Owned by the lead only.
Implementation agents must code against this file and must not change it.
If a contract is wrong, report it to the lead rather than editing.

Verified toolchain: Python 3.14.2, Node 24.12.0, fastapi 0.162.0, pydantic 2.14.1,
cartesia 4.2.0, anthropic 2.3.0, httpx 0.28.1, pytest 9.1.1.

---

## 1. Ground rules encoded in code

1. **Claude proposes, code authorizes.** The LLM can never write a terminal state.
   Only `domain/state_machine.evaluate_confirmation()` can authorize `PLAN_CONFIRMED`.
2. **One consent gate.** Every outbound contact passes `services/consent.verify_outreach()`.
   There is no provider call path that skips it.
3. **Private memory cannot reach a contact.** Contact-facing text is built only by
   `services/memory.shareable_context()` and re-checked by `assert_no_sensitive_leak()`.
4. **Honesty flags.** `config.Settings.voice_mode()` returns `cartesia_pstn` or
   `simulated`. Every call row and event carries this. UI must label `simulated`.
5. **No secret ever enters an event, log, or API response.** `services/events.redact()`.

## 2. Visibility ladder

| value | meaning |
|---|---|
| `private_to_sam` | never leaves owner's own view |
| `agent_internal` | usable for planning, never spoken to a third party |
| `shareable_with_contact` | may be referenced on a call with an opted-in contact |
| `public_demo_safe` | safe for the judge-facing dashboard |

Audience allow-lists live in `memory.AUDIENCE_VISIBILITY` (`owner`, `planning`,
`contact`, `demo`). Unknown audience fails closed to `demo`.

## 3. Plan states

```
OPPORTUNITY_IDENTIFIED -> CONSENT_VERIFIED -> MEMORY_RETRIEVED
  -> CANDIDATE_PLAN_CREATED -> CONTACT_IN_PROGRESS
  -> FRIEND_ACCEPTED | FRIEND_DECLINED | FRIEND_COUNTERPROPOSED
  -> PATIENT_APPROVAL_PENDING -> PATIENT_ACCEPTED | PATIENT_DECLINED
  -> AVAILABILITY_REVALIDATED -> PLAN_CONFIRMED -> NOTIFICATIONS_SENT -> COMPLETE
```
`FRIEND_COUNTERPROPOSED -> CANDIDATE_PLAN_CREATED` (loops, never confirms directly).
`HALTED_CONSENT_REVOKED` is reachable from every live state.
Illegal transitions raise `TransitionError`.

## 4. HTTP API

All JSON. Errors: `{"error": {"code": str, "message": str}}` with HTTP 4xx/5xx.
`code` values: `validation_error`, `consent_denied`, `not_found`, `conflict`,
`illegal_transition`, `provider_error`, `not_configured`, `unauthorized`.

### 4.1 Public / onboarding
| method | path | body / query | returns |
|---|---|---|---|
| GET | `/health` | — | `{status, db, voice_mode}` |
| GET | `/api/status` | — | `{cartesia_configured, cartesia_call_ready, cartesia_version, cartesia_agent_id_present, cartesia_from_number_id_present, anthropic_configured, anthropic_model, twilio_configured, voice_mode:'cartesia_pstn'\|'simulated', target_phone_present, real_call_possible, notes}` |
| GET | `/api/qr.png` | `?target=` | PNG QR to the signup URL |
| POST | `/api/signup` | `{phone_e164, preferred_channel:'call'\|'text', adult_attested:bool, display_name}` | `{user_id, verification_required:true, dev_code?}` |
| POST | `/api/verify` | `{phone_e164, code}` | `{user_id, verified:true}` |
| POST | `/api/onboarding/preferences` | `{user_id, timezone?, activity_preferences?[], quiet_hours_start?, quiet_hours_end?}` | `{ok:true}` |
| POST | `/api/outreach/activate` | `{user_id, active:bool}` | `{user_id, outreach_active}` |

`dev_code` is returned ONLY when no SMS provider is configured, so local
onboarding is testable. It is absent whenever Twilio/Photon is live.

### 4.2 Circle & dual consent
| method | path | body | returns |
|---|---|---|---|
| POST | `/api/circle/invite` | `{patient_id, contact_name, contact_phone_e164, relationship_label}` | `{token, invite_url, expires_at}` |
| GET | `/api/invite/{token}` | — | `{patient_display_name, contact_name, relationship_label, expires_at, status}` — **no clinical data** |
| POST | `/api/invite/{token}/accept` | `{code?}` | `{contact_id, relationship_status:'accepted', consent_id}` |
| POST | `/api/invite/{token}/decline` | — | `{relationship_status:'declined'}` |
| GET | `/api/consents` | `?patient_id=` | `[{id, contact_id, allowed_actions[], allowed_fields[], quiet_hours_start, quiet_hours_end, max_per_day, active}]` |
| POST | `/api/consents` | `{patient_id, contact_id, allowed_actions[], allowed_fields[], quiet_hours_start?, quiet_hours_end?, max_per_day?}` | `{consent_id}` |
| POST | `/api/consents/revoke` | `{patient_id, contact_id}` | `{revoked:int}` |
| GET | `/api/circle` | `?patient_id=` | `[{token, invite_url, contact_name, contact_id, relationship_label, status, relationship_status, phone_verified, callable, expires_at}]` |
| GET | `/api/contacts` | `?patient_id=` | `[{contact_id, display_name, relationship_label, relationship_status, phone_verified}]` |
| POST | `/api/sms/stop` | `{phone_e164}` | `{ok, consents_revoked}` |

> **Amendment (2026-10-24):** `/api/circle` and `/api/contacts` were added after
> the frontend review found that a patient's invitations were otherwise only
> recoverable from browser localStorage, and that consent screens could show a
> raw contact id but not a name. `callable` is the honest composite flag:
> `status == "accepted" AND phone_verified`.
>
> In `/api/circle` and `/api/invite/{token}`, a **decline outranks an earlier
> accept** — the most restrictive answer wins, so an accepted-then-declined
> invitation is never `callable`.

Invitation tokens expire (default 7 days) and are single-use.

### 4.3 Companion & memory
| method | path | body / query | returns |
|---|---|---|---|
| POST | `/api/companion/message` | `{user_id, text, session_id?}` | `{reply, session_id, memories_considered[], memories_extracted[], reasoning_mode:'claude'\|'fallback', extraction_mode:'claude'\|'fallback', provider_error:str\|null}` |

> **Amendment (2026-10-24):** `extraction_mode` and `provider_error` were added
> after implementation. Reply generation and memory extraction are two
> independent LLM calls that can fall back separately, so a single
> `reasoning_mode` could not describe the real state. The UI must label each.
| GET | `/api/memories` | `?user_id=&audience=owner` | `[Memory]` |
| PATCH | `/api/memories/{id}` | `{value?, visibility?}` | `Memory` (correction creates a new row and supersedes the old) |
| DELETE | `/api/memories/{id}` | — | `{deleted:true}` |

`Memory` = `{id, owner_id, subject_id, kind, value, source_conversation_id,
source_quote, confidence, confirmed, visibility, created_at, updated_at}`.
`kind` ∈ `fact|preference|relationship|boundary`.

### 4.4 Plans, demo, events
| method | path | body | returns |
|---|---|---|---|
| POST | `/api/demo/reset` | — | `{ok, reset_version, demo_session_id}` |
| POST | `/api/demo/start` | `{force_mode?:'simulated'\|'cartesia_pstn'}` | `{plan_id, call_id, mode, state}` |
| POST | `/api/demo/sam-response` | `{plan_id, response:'accepted'\|'declined', activity?, starts_at?}` | `{plan_id, state, simulated:true}` |
| GET | `/api/plans/{id}` | — | `Plan` + `participants[]` |
| GET | `/api/events/recent` | `?limit=200` | `[Event]` |
| GET | `/api/events/stream` | SSE, `?replay=50` | `Event` frames |

Both events endpoints require the dashboard token as **either** the
`X-Dashboard-Token` header **or** a `dashboard_token` query parameter.

> **Amendment (2026-10-24):** the query-parameter form is not a convenience. The
> browser's native `EventSource` cannot set custom headers, so header-only auth
> made the SSE stream unimplementable from the dashboard. Found by the frontend
> review.

`Plan` = `{id, patient_id, contact_id, activity, location, starts_at, status,
version, rationale, memory_citations[], confirmed_at}`.
`Event` = `{id, seq, event_type, safe_summary, session_id, plan_id, call_id,
payload, simulated, timestamp}`.

Sam's response is **always** `simulated:true` in the solo demo and the UI must
render a `SIMULATED` badge.

### 4.5 Voice — Cartesia webhook tools (Cartesia → us)
Auth: header `X-StillHere-Secret: $WEBHOOK_SECRET`. Missing/wrong ⇒ 401.

| path | body | returns |
|---|---|---|
| `POST /api/voice/tools/get_support_context` | `{call_id}` | `{contact_first_name, shared_interests[], relationship, boundaries[], citations[]}` — contact-safe only |
| `POST /api/voice/tools/lookup_showtimes` | `{movie, date?}` | `{source:'mock', options[]}` |
| `POST /api/voice/tools/record_friend_response` | `{call_id, response:'accepted'\|'declined'\|'counterproposed', activity?, starts_at?, note?}` | `{plan_id, state}` |
| `POST /api/voice/tools/request_patient_approval` | `{call_id}` | `{plan_id, state:'PATIENT_APPROVAL_PENDING'}` |
| `POST /api/voice/webhook` | provider call-status event | `{ok:true}` |

Tool endpoints are **idempotent**: a duplicate `(call_id, tool, payload)` returns
the first result and does not re-transition state.

### 4.6 Voice — local simulator (honest fallback)
Used only when `voice_mode == 'simulated'`. Every event it emits has
`simulated: true`.

| path | body | returns |
|---|---|---|
| `POST /api/voice/simulate/turn` | `{call_id, text}` | `{reply, tool_calls[], state}` |

## 5. Claude tool schemas (server-validated)

`get_support_context`, `verify_consent`, `find_free_slots`, `suggest_activity`,
`record_friend_response`, `request_patient_approval`, `lookup_showtimes`,
`confirm_plan`, `send_confirmation`, `store_preference_feedback`.

Defined in `backend/app/agents/tools.py` as `TOOL_SPECS` (Anthropic tool format).
Every tool handler must: validate input, emit one `agent_events` row with
elapsed ms + status, and never widen consent.

## 6. Event type vocabulary (stable; UI switches on these)

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

## 7. Frontend routes

`/` landing + QR · `/signup` · `/onboarding` · `/circle` · `/invite/:token` ·
`/settings/consent` · `/companion` · `/dashboard` (token-protected).

Dashboard reads `GET /api/events/stream` and renders only real events.
`X-Dashboard-Token: $DASHBOARD_TOKEN` required for `/dashboard` data routes.

## 8. File ownership (no two agents write the same file)

| area | owner | files |
|---|---|---|
| shared contracts, config, db, state machine, main | **lead** | `config.py`, `db.py`, `domain/*`, `main.py`, `specs/*` |
| consent/memory/voice services | **lead** | `services/*`, `adapters/*`, `agents/*`, `api/*` |
| frontend | **agent E** | `frontend/**` only |
| docs | **agent F** | `docs/**`, `README.md` only |
| tests | **lead + agent F** | `backend/tests/**` |
