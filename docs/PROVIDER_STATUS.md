# Provider Status — Master Table

Last updated: 2026-10-24 (UTC).

> **No real outbound PSTN call has been placed or verified, because no Cartesia
> API key was supplied to this build.** The voice path runs a clearly-labeled
> local simulator (`config.voice_mode() == "simulated"`); every row and event
> produced by it carries `simulated: true`. Nothing in this repository claims a
> live call occurred.

## Master table

| Provider | Status | SDK / package (verified) | Credentials present? | Verified requests | Demo readiness | Timestamp |
|---|---|---|---|---|---|---|
| Cartesia | `implemented (code) / blocked (live call unverified)` | `cartesia` 4.2.0 | No (`CARTESIA_API_KEY` unset) | Preflight run only (`evidence/cartesia_discovery.json`, `api_key_present: false`) | Simulator only; flips to live with a key | 2026-10-24 |
| Cartesia Line SDK | `not integrated` (deliberate) | n/a | n/a | n/a | n/a — architecture decision, see `docs/CARTESIA_LINE.md` | 2026-10-24 |
| Photon (photon.codes) | `verified as a product, not integrated` | n/a | No | None | P2, not in current demo | 2026-10-24 |
| Anthropic | `implemented (code) / untested against live API` | `anthropic` 2.3.0 | No (`ANTHROPIC_API_KEY` unset) | None against the live API; fallback path exercised by 113 passing tests | Runs on deterministic fallback, labeled `reasoning_mode: "fallback"` | 2026-10-24 |
| Claude Code | `implemented` (development tool, no runtime dependency) | Claude Code CLI, Opus 5 | n/a | n/a | Already "deployed" — it built the repo | 2026-10-24 |
| Twilio | `planned / not integrated` | not installed | No | None | `twilio_configured=False`; dev codes returned in API responses instead of SMS | 2026-10-24 |
| SpacetimeDB | `not integrated` | not installed | No | None | SQLite + SSE used instead; adapter seam documented | 2026-10-24 |
| Moss | `not integrated` | not installed | No | None | No code | 2026-10-24 |
| Fetch.ai (Agentverse / ASI:One) | `not integrated` | not installed | No | None | Local Python classes named "agents" are NOT a Fetch.ai integration | 2026-10-24 |
| Factory Droid | `not integrated` | not installed | No | None | No code | 2026-10-24 |
| Creao | `not integrated` | not installed | No | None | No code | 2026-10-24 |
| Notability | `not integrated` | not installed | No | None | No code | 2026-10-24 |
| Meta | `not integrated` | n/a (judging track, not an SDK) | No | None | Argument only, see `docs/TOOL_META.md` | 2026-10-24 |
| Microsoft | `not integrated` | n/a (judging track, not an SDK) | No | None | Argument only, see `docs/TOOL_MICROSOFT.md` | 2026-10-24 |
| QR onboarding (`qrcode` + Pillow) | `implemented` | `qrcode` 8.2, `pillow` 13.0.0 | n/a | Verified over real HTTP: `GET /api/qr.png` → 200, `image/png`, 761 bytes | Fully demoable end to end | 2026-10-24 |

## Test evidence

- `python -m pytest backend/tests -q` → **113 passed**
- `python scripts/e2e_demo.py` → **49 checks passed, 0 failed**

Both were re-run during this documentation pass and reproduced these exact
numbers (`.venv/Scripts/python.exe -m pytest backend/tests -q` → `113 passed in
16.27s`; `.venv/Scripts/python.exe scripts/e2e_demo.py` → `RESULT: 49 passed, 0
failed`).

## Exact steps to flip Cartesia from simulated to live

1. Create a key at `https://play.cartesia.ai/keys` and set `CARTESIA_API_KEY` in `.env`.
2. Create a Managed Agent in the Cartesia Playground and paste the system
   prompt from `prompts/OUTREACH_AGENT.md` into it.
3. Provision or import a US phone number for the agent; record the resulting
   phone-number id as `CARTESIA_FROM_NUMBER_ID` and the agent id as
   `CARTESIA_AGENT_ID`.
4. Expose the backend publicly (`ngrok` or `cloudflared`) and set
   `PUBLIC_BASE_URL` in `.env` to that public URL.
5. Register the four webhook tools (`get_support_context`,
   `lookup_showtimes`, `record_friend_response`, `request_patient_approval`)
   in the Playground, each pointed at `{PUBLIC_BASE_URL}/api/voice/tools/...`
   and sending the `X-StillHere-Secret` header equal to `WEBHOOK_SECRET`.
6. Run `python scripts/discover_cartesia.py` to verify the account, then
   `python scripts/smoke_call.py` to place one real outbound call and write
   `evidence/real_call_<id>.json`.

Until all six steps are done, `config.cartesia_call_ready` is `False`,
`config.voice_mode()` returns `"simulated"`, and every call in the system is
routed to the local simulator in `backend/app/services/voice.py`.

## Known inconsistencies found while writing this documentation

None found in the provider-facing code paths checked (`backend/app/config.py`,
`backend/app/adapters/cartesia_adapter.py`,
`backend/app/adapters/anthropic_adapter.py`,
`backend/app/api/onboarding.py`, `backend/app/api/voice.py`,
`backend/app/api/demo.py`) against the verified ground truth supplied for this
task. Versions re-verified directly against the project virtualenv
(`.venv/Scripts/python.exe -m pip show`): `cartesia 4.2.0`, `anthropic 2.3.0`,
`qrcode 8.2`, `pillow 13.0.0`, `httpx 0.28.1`, `sse-starlette 3.5.0`. Test
counts re-verified by direct execution and match exactly.
