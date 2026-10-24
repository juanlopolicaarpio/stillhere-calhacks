# docs/API_CONTRACTS.md

**Generated from the live FastAPI OpenAPI schema** by
`scripts/gen_api_contracts.py`. Do not hand-edit — re-run the generator.

- Generated: 2026-10-25
- App version: 1.0.0
- Routes documented: 35

Hand-written contracts live in `specs/CONTRACTS.md` (frozen) and
`specs/CONTRACTS_V2.md` (SpacetimeDB + fluid conversation addendum).

## Error envelope

All handled errors return:

```json
{"error": {"code": "...", "message": "..."}}
```

`code` is one of `validation_error`, `consent_denied`, `not_found`,
`conflict`, `illegal_transition`, `provider_error`, `not_configured`,
`unauthorized`. FastAPI request-validation failures return 422 with the
standard `detail` body.

## Authentication

| surface | requirement |
|---|---|
| `/api/events/*` | `X-Dashboard-Token` header **or** `?dashboard_token=` |
| `/api/voice/tools/*`, `/api/voice/webhook` | `X-StillHere-Secret` header |
| everything else | open in this prototype (single-user demo) |

## onboarding

QR signup, phone verification, circle invitations and dual consent. Scanning a QR authorizes nothing; holding an invite link does not prove phone ownership.

| method | path | request | responses |
|---|---|---|---|
| GET | `/api/circle` | query: patient_id | 200, 422 |
| POST | `/api/circle/invite` | `{patient_id: string, contact_name: string, contact_phone_e164: string, relationship_label: string}` | 200, 422 |
| GET | `/api/consents` | query: patient_id | 200, 422 |
| POST | `/api/consents` | `{patient_id: string, contact_id: string, allowed_actions: array, allowed_fields: array, quiet_hours_start?: integer|null, quiet_hours_end?: integer|null, max_per_day?: integer|null}` | 200, 422 |
| POST | `/api/consents/revoke` | `{patient_id: string, contact_id: string}` | 200, 422 |
| GET | `/api/contacts` | query: patient_id | 200, 422 |
| GET | `/api/invite/{token}` | - | 200, 422 |
| POST | `/api/invite/{token}/accept` | `any` | 200, 422 |
| POST | `/api/invite/{token}/decline` | - | 200, 422 |
| POST | `/api/onboarding/preferences` | `{user_id: string, timezone?: string|null, activity_preferences?: array, quiet_hours_start?: integer|null, quiet_hours_end?: integer|null}` | 200, 422 |
| POST | `/api/outreach/activate` | `{user_id: string, active: boolean}` | 200, 422 |
| GET | `/api/qr.png` | query: target? | 200, 422 |
| POST | `/api/signup` | `{phone_e164: string, preferred_channel?: string, adult_attested: boolean, display_name: string}` | 200, 422 |
| POST | `/api/sms/stop` | `{phone_e164: string}` | 200, 422 |
| POST | `/api/verify` | `{phone_e164: string, code: string}` | 200, 422 |

## companion

Companion chat and user-controlled memory with provenance.

| method | path | request | responses |
|---|---|---|---|
| POST | `/api/companion/message` | `{user_id: string, text: string, session_id?: string|null}` | 200, 422 |
| GET | `/api/memories` | query: user_id, audience? | 200, 422 |
| PATCH | `/api/memories/{memory_id}` | `{value?: string|null, visibility?: string|null}` | 200, 422 |
| DELETE | `/api/memories/{memory_id}` | - | 200, 422 |

## demo

Demo orchestration: reset, start outreach, SIMULATED patient approval, plans.

| method | path | request | responses |
|---|---|---|---|
| POST | `/api/demo/reset` | - | 200 |
| POST | `/api/demo/sam-response` | `{plan_id: string, response: string, activity?: string|null, starts_at?: string|null}` | 200, 422 |
| POST | `/api/demo/start` | `any` | 200, 422 |
| GET | `/api/plans` | query: patient_id? | 200, 422 |
| GET | `/api/plans/{plan_id}` | - | 200, 422 |

## events

Live event feed. Requires the dashboard token as the `X-Dashboard-Token` header OR a `dashboard_token` query parameter (EventSource cannot set headers).

| method | path | request | responses |
|---|---|---|---|
| GET | `/api/events/recent` | query: limit?, dashboard_token? | 200, 422 |
| GET | `/api/events/stream` | query: replay?, dashboard_token? | 200, 422 |

## voice

Cartesia webhook tools (require `X-StillHere-Secret`) and the labeled local voice simulator.

| method | path | request | responses |
|---|---|---|---|
| POST | `/api/voice/simulate/end` | `{call_id: string}` | 200, 422 |
| POST | `/api/voice/simulate/turn` | `{call_id: string, text: string}` | 200, 422 |
| POST | `/api/voice/tools/get_support_context` | `{call_id: string}` | 200, 422 |
| POST | `/api/voice/tools/lookup_showtimes` | `{movie: string, date?: string|null}` | 200, 422 |
| POST | `/api/voice/tools/record_friend_response` | `{call_id: string, response: string, activity?: string|null, starts_at?: string|null, note?: string|null}` | 200, 422 |
| POST | `/api/voice/tools/request_patient_approval` | `{call_id: string}` | 200, 422 |
| POST | `/api/voice/webhook` | - | 200, 422 |

## default

Health and capability reporting.

| method | path | request | responses |
|---|---|---|---|
| GET | `/api/status` | - | 200 |
| GET | `/health` | - | 200 |

## SpacetimeDB reducer surface

Coordination state is written through reducers, not HTTP. See
`specs/CONTRACTS_V2.md` §2-§3 and `docs/SPACETIMEDB_ARCHITECTURE.md`.

| reducer | role |
|---|---|
| `start_session` | open a coordination session |
| `set_call_state` | call lifecycle (no phone numbers stored) |
| `propose_or_revise_plan` | insert an immutable proposal version |
| `respond_as_friend` | record the contact's answer to a specific version |
| `record_patient_approval` | record the patient's answer to a specific version |
| `confirm_if_matching` | **the only way a plan can confirm** |
| `revoke_or_cancel` | halt everything for a session |
| `append_sanitized_event` | append to the public event feed |

Called over HTTP as
`POST {SPACETIMEDB_HOST}/v1/database/{SPACETIMEDB_DB}/call/{reducer}` with a
JSON array of positional arguments and a Bearer token.
