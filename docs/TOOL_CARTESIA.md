# Cartesia

## 1. Status

`implemented (code) / blocked (live call unverified)`. The adapter, consent
gate, webhook tools, and call-lifecycle handling are all written and exercised
by 113 passing tests and a 49-check e2e script, but no `CARTESIA_API_KEY` was
ever supplied to this build, so no real outbound PSTN call has been attempted
or verified. `config.voice_mode()` returns `"simulated"` in this environment.

## 2. Role in Still Here

Cartesia Managed Agents is the only path to a real outbound phone call. It
would call a consenting contact on the patient's behalf, carry on the
negotiation (accept / decline / counter-propose a day or activity / ask "why
me?"), and report results back to Still Here through webhook tools and a
provider status webhook — all without private clinical memory ever leaving
the backend (only `dynamic_variables` built from `memory.shareable_context()`
reach the call).

## 3. Credentials required

| Env var | Purpose |
|---|---|
| `CARTESIA_API_KEY` | Auth for both REST calls and the SDK |
| `CARTESIA_VERSION` | API version header (defaults to `2026-08-14`) |
| `CARTESIA_AGENT_ID` | The Managed Agent to dial from |
| `CARTESIA_FROM_NUMBER_ID` | The account's phone-number id (not the number itself) |
| `CARTESIA_VOICE_ID` | Sonic voice id (defaults to `a0e99841-438c-4a64-b679-ae501e7d6091`) |
| `CARTESIA_TTS_MODEL` | Defaults to `sonic-3.6` |
| `WEBHOOK_SECRET` | Shared secret the four webhook tools and the provider webhook must present as `X-StillHere-Secret` |
| `PUBLIC_BASE_URL` | Public URL Cartesia calls back into |

None of these are set in this build's environment.

## 4. SDK / package / version as actually verified

`cartesia` **4.2.0**, verified via `pip show cartesia` in the project
virtualenv (confirmed again during this documentation pass:
`.venv/Scripts/python.exe -m pip show cartesia` → `Version: 4.2.0`). Python
3.14.2 (3.14.0 in the current shell). Base URL `https://api.cartesia.ai`.
Default `cartesia-version` header sent by the SDK: `2026-08-14`.

SDK surface actually verified by introspection: `client.agents` →
`.list()`, `.retrieve(agent_id)`,
`.update(agent_id, name=, description=, tts_voice=, tts_language=)`,
`.delete()`, `.list_phone_numbers(agent_id)`, `.list_templates()`,
`.calls.list(agent_id=)`, `.calls.retrieve(call_id)`,
`.calls.download_audio()`, `.deployments.list/retrieve`, `.metrics`. Also
`client.tts`, `client.stt`, `client.voices`, `client.voice_changer`,
`client.datasets`, `client.fine_tunes`, `client.pronunciation_dicts`,
`client.access_token`.

**Critical finding:** SDK 4.2.0 exposes no `create` method for agents or for
outbound calls. Agent creation and webhook-tool registration must be done in
the Cartesia Playground UI. Outbound dialing is therefore done over raw HTTP
in this repo, not through the SDK's agent namespace.

## 5. Setup steps

See `docs/PROVIDER_STATUS.md`, "Exact steps to flip Cartesia from simulated to
live," for the full six-step sequence (create key, create agent in Playground,
provision/import a number, expose the backend publicly, register webhook
tools, run the preflight and smoke scripts).

## 6. Actual implemented API calls

- `backend/app/adapters/cartesia_adapter.py:72` `place_outbound_call()` —
  `POST {cartesia_base_url}/agents/calls` with headers `X-API-Key`,
  `Authorization: Bearer <key>` (sent in addition to `X-API-Key` in case
  either gateway path is in effect), `Cartesia-Version`. Body:
  `{agent_id, from_number_id, ringing_timeout_seconds, outbound_calls:[{to_number, dynamic_variables}]}`.
- `backend/app/adapters/cartesia_adapter.py:179` `get_call()` —
  `GET {cartesia_base_url}/agents/calls/{call_id}`.
- `backend/app/adapters/cartesia_adapter.py:194` `list_agents()` —
  `GET {cartesia_base_url}/agents`.
- `backend/app/adapters/cartesia_adapter.py:207` `list_phone_numbers()` —
  tries `GET {cartesia_base_url}/agents/phone-numbers`, then
  `/agents/phone_numbers`, then the deprecated per-agent endpoint.
- `backend/app/adapters/cartesia_adapter.py:234` `synthesize_wav()` — official
  SDK call `client.tts.generate(model_id=..., transcript=..., voice=...,
  output_format={...})` then `.write_to_file(path)`.
- `backend/app/services/voice.py:196` `_dial_real()` calls
  `place_outbound_call()` only after consent is verified
  (`backend/app/services/voice.py:81`) and only when
  `config.voice_mode() == "cartesia_pstn"`.
- `backend/app/api/voice.py` exposes the four webhook tools
  (`/api/voice/tools/get_support_context`, `/lookup_showtimes`,
  `/record_friend_response`, `/request_patient_approval`) that a live Managed
  Agent would call back into, plus `/api/voice/webhook` for provider call
  status callbacks (`backend/app/api/voice.py:238`).

None of the above have been exercised against the real Cartesia API in this
build — only the preflight script has run, and it exited at the missing-key
check (`evidence/cartesia_discovery.json`: `"api_key_present": false`).

## 7. How to test

- Unit/integration (no provider): `python -m pytest backend/tests -q` → 113 passed.
- Scripted end-to-end simulator run: `python scripts/e2e_demo.py` → 49 checks passed, 0 failed.
- Preflight against the real account (requires a key):
  `python scripts/discover_cartesia.py`.
- One real outbound call (requires a key, agent, and number):
  `python scripts/smoke_call.py [+1...]`, which writes
  `evidence/real_call_<id>.json` with the provider's own status transitions.

## 8. Known limitations

- No real call has ever been placed in this environment; `cartesia_call_ready`
  is `False` and will remain so until all three of `CARTESIA_API_KEY`,
  `CARTESIA_AGENT_ID`, and `CARTESIA_FROM_NUMBER_ID` are set.
- The SDK has no `create` method for agents or calls, so agent setup is
  necessarily a manual Playground step this repo cannot automate.
- Cartesia-provisioned phone numbers are US-only (both origin and
  destination); see `docs/VOICE_BRIDGES.md`.
- The provider API documents that a call's success/failure can appear inside
  a 200 response body (`calls[].error` vs `calls[].agent_call_id`), so status
  codes alone are not trustworthy — `place_outbound_call()` checks the body.
- Voice cloning, multilingual outreach, voice preview, and Line SDK multi-agent
  handoffs are not built; see `docs/CARTESIA_CREATIVE_FEATURES.md`.
