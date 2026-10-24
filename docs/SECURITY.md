# Security

What is actually implemented, verified against `backend/app/config.py`,
`backend/app/api/*.py`, and `backend/app/services/events.py`.

## Secrets handling

`config.py:1-11` loads `.env` from the repo root via `python-dotenv`; every
value is passed through `_clean()` (`:14-18`), which strips whitespace and
accidental surrounding quotes — a common source of "it works locally but not in
CI" bugs with copy-pasted keys.

Secrets held in `Settings`: `cartesia_api_key`, `anthropic_api_key`,
`webhook_secret`, `dashboard_token`, `twilio_auth_token`. None of these are ever
written to a log line or returned by any API response. `Settings.redacted()`
(`config.py:77-90`) is the **only** method that exposes capability state
externally (via `GET /api/status`), and it returns booleans
(`cartesia_configured`, `anthropic_configured`, `twilio_configured`) and
non-secret identifiers (`cartesia_agent_id_present` — a bool, not the id itself)
— never a key value. Directly tested:
`test_api_flow.py::test_status_leaks_no_secrets` asserts `sk_`, `sk-ant`,
`bearer `, and `auth_token` never appear in the `/api/status` response body.

Default dev values (`webhook_secret: "dev-only-change-me"`,
`dashboard_token: "dev-dashboard-token"`, `config.py:40-41`) are placeholders
meant to be overridden in `.env` for any real deployment — they are intentionally
obvious strings, not meant to be secure defaults.

## Webhook authentication

`POST /api/voice/tools/*` and `POST /api/voice/webhook` require header
`X-StillHere-Secret` matching `WEBHOOK_SECRET`, compared with
`secrets.compare_digest` (constant-time, avoids timing side-channels)
(`api/voice.py:29-37`). Missing or wrong secret → 401 with the standard error
envelope. This is tested directly:
`test_api_flow.py::TestVoiceTools::test_tools_require_secret` and
`test_tools_reject_wrong_secret`.

This is a single shared secret, not a per-caller credential — adequate for a
hackathon integration with one external provider, not for a multi-tenant
production system.

## Dashboard authentication

`X-Dashboard-Token` header, or `dashboard_token` query parameter (added because
the browser's native `EventSource` cannot set custom headers — see
`docs/DASHBOARD.md`), compared with `secrets.compare_digest`
(`api/events.py:26-34`). Every `/api/events/*` route requires it; there is no
unauthenticated path to the event feed. Also a shared secret, same caveat as
above.

## Token/code expiry

- Verification codes (`verification_codes` table): 10-minute TTL
  (`CODE_TTL_MINUTES = 10`, `onboarding.py:25`), max 5 consumption attempts
  (`MAX_CODE_ATTEMPTS = 5`), single-use (`consumed_at` set on success,
  `_consume_code()`, `:135-151`).
- Invitation tokens: 7-day TTL (`INVITE_TTL_DAYS = 7`, `:24`), single-use
  (`accept`/`decline` both check `accepted_at`/`declined_at` before proceeding,
  `:382-386`), generated with `secrets.token_urlsafe(24)` (`:254`) — 192 bits of
  randomness, not guessable.
- No session/JWT expiry exists for the patient's own browser identity, because
  there is no login system at all (see Threat Model in
  `docs/PRIVACY_SAFETY.md`) — identity lives in `localStorage`
  (`frontend/src/hooks/useLocalStorage.ts`) with no expiry.

## Rate limits

The only rate limiting implemented is the consent-level outreach cap:
`max_calls_per_contact_per_day` (default 2, `config.py:53`), enforced inside
`verify_outreach()` by counting `outreach_log` rows in the trailing 24 hours
(`consent.py:159-168`). There is no general API rate limiting (no
per-IP/per-token throttle on any endpoint) — acceptable for a judged demo, not
for a public deployment.

## Idempotency

Three independent idempotency mechanisms, all backed by the `idempotency` table:

1. **Call dialing** — `calls.idempotency_key UNIQUE`; a duplicate dial request
   for the same key returns the existing call row instead of placing a second
   call (`services/voice.py:47-64`).
2. **Plan confirmation** — `confirm_plan(plan_id, idempotency_key)` caches the
   full result under `scope='confirm_plan'`; a replay returns byte-identical
   output without re-evaluating or re-emitting a `plan.confirmed` event
   (`services/plans.py:234-251,295-300`). Refusals are deliberately **not**
   cached, since a caller may legitimately retry after obtaining a missing
   approval (`:270-273`).
3. **Voice tool calls** — keyed over `(call_id, tool, sha256(payload))`
   (`api/voice.py:40-45`); a duplicate `record_friend_response` from a retrying
   provider returns the first result without re-transitioning plan state
   (verified: `test_api_flow.py::test_record_response_is_idempotent`).
4. **Notification send** — `send_confirmation()` is keyed on the plan id and
   will not double-notify (`services/plans.py:303-356`).

## Input validation

- Phone numbers: `validate_e164()` enforces `^\+[1-9]\d{7,14}$` after stripping
  spaces/dashes (`consent.py:17-28`); used as a Pydantic `field_validator` on
  every endpoint that accepts a phone number (signup, circle invite, SMS stop).
- Request bodies: Pydantic models throughout with explicit length/range bounds
  (e.g. `display_name` max 80 chars, `quiet_hours_start/end` constrained 0–23,
  `max_per_day` constrained 0–50) — malformed input 422s before any handler
  logic runs.
- Memory kind/visibility: `store_memory()` rejects unknown `kind` or
  `visibility` values with `MemoryError_` rather than silently accepting
  garbage (`memory.py:101-106`).

## Transport

No TLS termination is implemented in this codebase — `PUBLIC_BASE_URL` defaults
to `http://localhost:8000` (`config.py:39`). A real deployment would sit this
behind a TLS-terminating reverse proxy (see `docs/DEPLOYMENT.md`); CORS is
currently locked to `http://localhost:5173`/`127.0.0.1:5173`
(`main.py:39-45`) for local dev only.

## What is NOT implemented (be direct about this)

- No authentication/authorization system for patient or contact identity
  beyond phone-number verification and browser `localStorage`.
- No encryption at rest for `stillhere.db` — it is a plain SQLite file.
- No audit log beyond the `agent_events` table (which is for demo
  transparency, not security auditing, though it does serve both purposes
  informally).
- No per-IP rate limiting or WAF-style protection at the application layer.
