# API Reference

Verified against the actual FastAPI routers in `backend/app/api/*.py` and
`backend/app/main.py` — this is a check against the frozen
`specs/CONTRACTS.md §4`, not a copy of it. All JSON. Errors:
`{"error": {"code": str, "message": str}}` with HTTP 4xx/5xx
(`main.py:21-79` — the exception handlers that produce this envelope for
`ConsentError`, `VoiceError`, `TransitionError`, `PlanError`, `MemoryError_`,
and bare `ValueError`).

`code` values in use: `validation_error`, `consent_denied`, `not_found`,
`illegal_transition`, `provider_error`, `unauthorized`. (`not_configured` and
`conflict` are reserved in the contract; `conflict` is used directly via
`HTTPException` in `api/voice.py:319-323` for the real-call/simulator mismatch.)

## Health and status

| method | path | notes |
|---|---|---|
| GET | `/health` | `{status, db, voice_mode}` (`main.py:82-90`) |
| GET | `/api/status` | `config.redacted()` + `real_call_possible` + `notes` (`main.py:93-110`). Never contains secret material — verified by `test_api_flow.py::test_status_leaks_no_secrets` |
| GET | `/api/qr.png?target=` | PNG, `Cache-Control: no-store`, `X-Signup-Url` header (`onboarding.py:32-46`) |

## Onboarding

| method | path | body | returns | file:line |
|---|---|---|---|---|
| POST | `/api/signup` | `{phone_e164, preferred_channel, adult_attested, display_name}` | `{user_id, verification_required:true, dev_code?}` | `onboarding.py:78-122` |
| POST | `/api/verify` | `{phone_e164, code}` | `{user_id, verified:true}` | `:154-167` |
| POST | `/api/onboarding/preferences` | `{user_id, timezone?, activity_preferences?, quiet_hours_start?, quiet_hours_end?}` | `{ok:true, memories_created}` | `:170-204` |
| POST | `/api/outreach/activate` | `{user_id, active}` | `{user_id, outreach_active}` | `:207-229` |

`dev_code` is present on `/api/signup` and on `/api/invite/{token}/accept` only
when `not s.twilio_configured` — true throughout this build, since Twilio isn't
set up (`onboarding.py:118-122`, `:456-466`).

## Circle and dual consent

| method | path | body / query | returns | file:line |
|---|---|---|---|---|
| POST | `/api/circle/invite` | `{patient_id, contact_name, contact_phone_e164, relationship_label}` | `{token, invite_url, expires_at}` | `:247-277` |
| GET | `/api/circle?patient_id=` | — | list with `status`, `relationship_status`, `phone_verified`, `callable` (server-computed) | `:280-320` |
| GET | `/api/contacts?patient_id=` | — | resolved `{contact_id, display_name, relationship_label, relationship_status, phone_verified}` | `:323-341` |
| GET | `/api/invite/{token}` | — | `{patient_display_name, contact_name, relationship_label, expires_at, status}` — no clinical data | `:353-370` |
| POST | `/api/invite/{token}/accept` | `{code?}` | `{contact_id, relationship_status:'accepted', consent_id, phone_verified, verification_required?, dev_code?}` | `:373-466` |
| POST | `/api/invite/{token}/decline` | — | `{relationship_status:'declined'}` | `:469-484` |
| GET | `/api/consents?patient_id=` | — | list incl. `contact_name`, `relationship_status` | `:490-515` |
| POST | `/api/consents` | `{patient_id, contact_id, allowed_actions, allowed_fields, quiet_hours_start?, quiet_hours_end?, max_per_day?}` | `{consent_id}` — 400 if relationship isn't `accepted` yet | `:518-558` |
| POST | `/api/consents/revoke` | `{patient_id, contact_id}` | `{revoked:int, plans_halted:[id...]}` | `:561-588` |
| POST | `/api/sms/stop` | `{phone_e164}` | `{ok, user_id, consents_revoked}` | `:591-609` |

Note: `/api/circle` and `/api/contacts` are **not** in the originally frozen
contract — they were added after the frontend review found invites were
otherwise only recoverable from browser `localStorage` (see
`docs/MULTI_AGENT_ORCHESTRATION.md`). `GET /api/circle`'s `callable` field is
computed server-side (`status == "accepted" AND phone_verified`,
`onboarding.py:317`) rather than left for the frontend to derive from raw flags.

## Companion and memory

| method | path | body / query | returns | file:line |
|---|---|---|---|---|
| POST | `/api/companion/message` | `{user_id, text, session_id?}` | `{reply, session_id, reasoning_mode, extraction_mode, provider_error, memories_considered[], memories_extracted[]}` | `companion.py:18-20` → `services/companion.py:30-118` |
| GET | `/api/memories?user_id=&audience=owner` | — | `[Memory]`, filtered by `AUDIENCE_VISIBILITY[audience]`; 400 on unknown audience | `companion.py:23-33` |
| PATCH | `/api/memories/{id}` | `{value?, visibility?}` | `Memory` — value change creates a new row and supersedes the old | `:41-73` |
| DELETE | `/api/memories/{id}` | — | `{deleted:true}` | `:76-88` |

`reasoning_mode` and `extraction_mode` are both `'claude'|'fallback'` — note the
response actually returns **both** fields (the frozen contract names only
`reasoning_mode`); `extraction_mode` reflects the memory-extraction call
separately from the reply-generation call, since the two LLM calls can
independently succeed or fall back (`companion.py:100-118`).

## Plans, demo, events

| method | path | body | returns | file:line |
|---|---|---|---|---|
| POST | `/api/demo/reset` | — | `{ok, reset_version, demo_session_id, patient_id, contacts, josh_phone_is_real, memories_seeded}` | `demo.py:15-18` |
| POST | `/api/demo/start` | `{patient_id?, force_mode?}` | `{ok, session_id, plan_id, plan, call_id, mode, status, provider_call_id, opening?, error?, why:{contact, why_contact, activity, why_activity, memory_citations}}` | `:21-79` |
| POST | `/api/demo/sam-response` | `{plan_id, response, activity?, starts_at?}` | `{plan_id, state, simulated:true, confirmed, reason?}` | `:82-150` |
| GET | `/api/plans/{id}` | — | `Plan` + `participants[]` | `:153-155` |
| GET | `/api/plans?patient_id=` | — | list of full `Plan` objects (not in the frozen contract; convenience listing) | `:158-165` |
| GET | `/api/events/recent?limit=200` | `dashboard_token=` or header | `[Event]`; 401 without a valid token | `events.py:37-44` |
| GET | `/api/events/stream?replay=50` | `dashboard_token=` or header | SSE `agent_event` frames, replays last N then streams live; `ping` frames every 15s idle | `:47-78` |

`/api/demo/start`'s response shape goes beyond the frozen contract's
`{plan_id, call_id, mode, state}` — it also returns the full `plan`, `opening`
line (simulator only), and a structured `why` block. This is additive, not
breaking.

## Voice — Cartesia webhook tools (Cartesia → us)

Auth: header `X-StillHere-Secret: $WEBHOOK_SECRET`, constant-time compared;
missing/wrong ⇒ 401 (`voice.py:29-37`).

| path | body | returns | file:line |
|---|---|---|---|
| POST `/api/voice/tools/get_support_context` | `{call_id}` | `{contact_first_name, shared_interests, relationship, boundaries, citations, withheld_private_memories}` — contact-safe only | `:89-116` |
| POST `/api/voice/tools/lookup_showtimes` | `{movie, date?}` | `{source:'mock', disclaimer, query, options}` | `:124-145` |
| POST `/api/voice/tools/record_friend_response` | `{call_id, response, activity?, starts_at?, note?}` | `{plan_id, state}` — idempotent on `(call_id, tool, payload)` | `:148-211` |
| POST `/api/voice/tools/request_patient_approval` | `{call_id}` | `{plan_id, state}` | `:213-232` |
| POST `/api/voice/webhook` | provider call-status event | `{ok:true, matched:bool, status?}` | `:238-303` |

Idempotency: `_idem()` (`:40-45`) builds a stable key over
`(call_id, tool, sha256(payload))`; a replay of `record_friend_response` returns
the cached first result (verified by
`test_api_flow.py::test_record_response_is_idempotent`) without re-transitioning
state.

## Voice — local simulator

Used only when `voice_mode == 'simulated'` (always, in this build).

| path | body | returns | file:line |
|---|---|---|---|
| POST `/api/voice/simulate/turn` | `{call_id, text}` | `{reply, tool_calls[], state, simulated:true}`; 409 if the call is actually `cartesia_pstn` | `:309-324` |
| POST `/api/voice/simulate/end` | `{call_id}` | `{ok:true}` | `:327-331` |

## Claude tool schemas (server-validated)

`get_support_context`, `verify_consent`, `find_free_slots`, `suggest_activity`,
`lookup_showtimes`, `record_friend_response`, `request_patient_approval`,
`store_preference_feedback` — defined in `backend/app/agents/tools.py:22-156`
(`TOOL_SPECS`, Anthropic tool-use format). `dispatch()` (`:281-320`) validates
input, emits one `call.tool_invoked` event with elapsed ms + status, and never
widens consent. **Not exposed to any model:** `confirm_plan`,
`send_confirmation`, `start_outbound_call` — there is no tool schema for these at
all (`MODEL_FORBIDDEN_TOOLS`, `:161`).

## Error codes actually produced

| code | when | HTTP |
|---|---|---|
| `validation_error` | `ConsentError`, `MemoryError_`, bare `ValueError` (e.g. invalid phone, unknown audience, bad request shape) | 400 |
| `consent_denied` | `VoiceError` whose message contains "consent denied" | 403 |
| `provider_error` | other `VoiceError` (provider rejection, not-configured) | 502 |
| `illegal_transition` | `TransitionError` from the state machine | 409 |
| `not_found` | `PlanError` (unknown plan id) | 404 |
| `unauthorized` | missing/wrong dashboard token or webhook secret | 401 |
| `conflict` | simulator called against a real `cartesia_pstn` call | 409 (`api/voice.py:319-323`, raised directly via `HTTPException`, not through the generic error handler) |
| (pydantic) `422` | request body fails schema validation (e.g. malformed E.164 at the Pydantic layer before a handler runs) | 422 |
