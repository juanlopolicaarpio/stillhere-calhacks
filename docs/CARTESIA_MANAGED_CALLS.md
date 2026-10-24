# Cartesia Managed Agents — Outbound Call Path

## 1. Status

`implemented (code) / blocked (live call unverified)`. Every code path that a
real call would exercise exists and is covered by tests; the live call itself
has not been attempted because no `CARTESIA_API_KEY` was supplied.

## 2. Role in Still Here

Managed Agents handle listening, turn-taking, interruptions, tool calls,
knowledge retrieval, and telephony around the LLM of choice. Still Here uses
this to let the agent conduct a real negotiation call with a consenting
contact: propose an activity and time, handle acceptance / decline / counter-
proposal, answer "why me?" with a memory-cited reason, and hand off to the
patient for final approval — never confirming anything on its own authority.

## 3. Credentials required

`CARTESIA_API_KEY`, `CARTESIA_AGENT_ID`, `CARTESIA_FROM_NUMBER_ID`,
`CARTESIA_VERSION` (defaults to `2026-08-14`), `WEBHOOK_SECRET`,
`PUBLIC_BASE_URL`. See `docs/TOOL_CARTESIA.md` for the full table. None are
set in this build.

## 4. SDK / package / version as actually verified

`cartesia` 4.2.0 (verified via `pip show`). The SDK's `client.agents`
namespace has `.list()`, `.retrieve()`, `.update()`, `.delete()`,
`.list_phone_numbers()`, `.list_templates()`, and `.calls.list/.retrieve/
.download_audio()` — but **no create method for agents or outbound calls**.
Outbound dialing is implemented over raw HTTP instead.

## 5. Setup steps

1. Create the Managed Agent in the Cartesia Playground (not via SDK — no
   create method exists). Paste the system prompt from
   `prompts/OUTREACH_AGENT.md`.
2. Attach a phone number: Cartesia-provisioned (US-only), imported from
   Twilio, or your own SIP carrier. Record the resulting phone-number id as
   `CARTESIA_FROM_NUMBER_ID`.
3. Register the four webhook tools in the Playground, each calling back into
   `{PUBLIC_BASE_URL}/api/voice/tools/...` with header
   `X-StillHere-Secret: {WEBHOOK_SECRET}` (see `scripts/setup_cartesia_agent.py`
   for the exact tool definitions it prints).
4. Point the Cartesia call-status webhook at
   `{PUBLIC_BASE_URL}/api/voice/webhook`.
5. Set `.env` with the resulting ids and run
   `python scripts/discover_cartesia.py` to confirm, then
   `python scripts/smoke_call.py` to place one real call.

## 6. Actual implemented API calls

- `POST https://api.cartesia.ai/agents/calls` —
  `backend/app/adapters/cartesia_adapter.py:72` (`place_outbound_call`).
  Request body: `{agent_id, from_number_id, ringing_timeout_seconds,
  outbound_calls:[{to_number, dynamic_variables}]}`. Response:
  `{calls:[{number, agent_call_id}]}` or `{calls:[{number, error}]}` — the
  adapter checks the body, not just the HTTP status, per the documented
  warning "do not rely on response status alone."
- `GET https://api.cartesia.ai/agents/calls/{call_id}` —
  `backend/app/adapters/cartesia_adapter.py:179` (`get_call`).
- Webhook tools served to Cartesia: `backend/app/api/voice.py` lines 89
  (`get_support_context`), 124 (`lookup_showtimes`), 156
  (`record_friend_response`), 213 (`request_patient_approval`), each
  authenticated by `_auth()` at `backend/app/api/voice.py:29` and made
  idempotent by `_idem()`/`_remember()` at lines 40–54.
- Provider call-status webhook: `backend/app/api/voice.py:238`
  (`provider_webhook`), maps Cartesia's `status`/`event` field onto the local
  `calls` row and emits a `call.ringing` / `call.answered` / `call.ended`
  event.

## 7. How to test

- `python -m pytest backend/tests -q` → 113 passed (covers the consent gate,
  webhook auth, idempotency, and state transitions around this path without
  touching the network).
- `python scripts/e2e_demo.py` → 49 checks passed, 0 failed (drives the whole
  flow through the local simulator).
- `python scripts/discover_cartesia.py` then `python scripts/smoke_call.py` —
  the only two scripts that touch the real network, and only once credentials
  exist.

## 8. Known limitations

- Dialing all numbers in a single request happens immediately; destinations
  beyond the account's concurrency limit are rejected, not queued.
- The Line SDK is explicitly not used here — see `docs/CARTESIA_LINE.md`.
- No live call has been placed or verified in this build.
