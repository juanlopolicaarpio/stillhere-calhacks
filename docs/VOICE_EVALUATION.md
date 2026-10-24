# Voice Evaluation

## 1. Status

`simulated`. No live call has been placed, so no real-call latency or audio
quality numbers exist. Everything measurable today comes from the local
simulator (`backend/app/services/voice.py`) and the test suite.

## 2. Role in Still Here

This document defines the metrics that WOULD be measured once a real
Cartesia call is placed, and records what branch coverage the simulator and
tests actually exercise today, so demo-day claims about "latency" or
"accuracy" are never invented.

## 3. Credentials required

None to read this document. A live measurement would require
`CARTESIA_API_KEY`, `CARTESIA_AGENT_ID`, `CARTESIA_FROM_NUMBER_ID` — see
`docs/TOOL_CARTESIA.md`.

## 4. SDK / package / version as actually verified

`cartesia` 4.2.0 (see `docs/TOOL_CARTESIA.md`). No telemetry library is
installed or used for latency measurement; any future measurement would read
timestamps already recorded on the `calls` table (`requested_at`,
`answered_at`, `ended_at` — `backend/app/db.py`) and the Cartesia call-status
webhook payloads (`backend/app/api/voice.py:238`).

## 5. Setup steps

Not applicable until a real call exists. Once `scripts/smoke_call.py`
succeeds, timestamps it writes to `evidence/real_call_<id>.json` combined
with the webhook-driven `calls` row updates would be the first real data
points.

## 6. Actual implemented API calls

None specific to evaluation. See `docs/TOOL_CARTESIA.md` for the call-placing
calls themselves.

## Metrics that WOULD be measured on a real call

| Metric | Definition | Source once live |
|---|---|---|
| Time-to-first-audio | Time from `place_outbound_call()` returning `agent_call_id` to the first `call.answered` webhook | `calls.requested_at` → `calls.answered_at` |
| Turn latency | Time from the contact finishing speaking to the agent's audio response starting | Would require the provider's own turn telemetry; not exposed by any API call verified in this build |
| Interruption recovery | Whether the agent correctly stops speaking and resumes context after a mid-sentence interruption | Would require listening to recorded call audio via `client.agents.calls.download_audio()` |
| Correct tool selection | Whether the agent invokes the right webhook tool (`get_support_context`, `lookup_showtimes`, `record_friend_response`, `request_patient_approval`) for a given utterance | `call.tool_invoked` events already logged by `backend/app/api/voice.py` — this part works identically live or simulated |
| Branch coverage | Fraction of the negotiation branches below actually exercised in a call | See table below |

**No live-call latency numbers exist yet because no call was placed.** Any
number quoted for time-to-first-audio or turn latency before a real call has
been run and its webhook timestamps captured would be fabricated.

## Branch matrix covered by the local simulator and tests

| Branch | Simulator trigger | Covered by |
|---|---|---|
| Yes (accept) | `_YES` regex | `backend/app/services/voice.py:484`; tests + `scripts/e2e_demo.py` |
| No (decline) | `_NO` regex | `backend/app/services/voice.py:467` |
| Different day | `_requested_weekday()` | `backend/app/services/voice.py:417` |
| Different activity (coffee) | `_COFFEE` regex | `backend/app/services/voice.py:387` |
| "Who is this?" | `_WHO` regex | `backend/app/services/voice.py:350` |
| "Why Marvel?" / "why me?" | `_WHY` regex | `backend/app/services/voice.py:381` |
| "How is Sam doing?" (must refuse) | `_HOWIS` regex, emits `safety.blocked` | `backend/app/services/voice.py:369` |
| "Just book it without asking Sam" (must refuse) | `_BOOK_WITHOUT` regex, emits `safety.blocked` | `backend/app/services/voice.py:357` |
| Interruption | `_INTERRUPT` regex | `backend/app/services/voice.py:508` |
| Silence | Not modeled as a distinct simulator branch; a non-matching turn falls through to the generic "did I get that right?" reply at `backend/app/services/voice.py:511` | partial |
| Voicemail | Provider webhook status mapping `voicemail` → `call.ended`, `status="voicemail"`, plus a `safety.blocked` event because no conversation took place | `backend/app/api/voice.py:264` (mapping table), `:294` (safety.blocked) |
| Busy | Provider webhook status mapping `busy` → `call.ended`, `status="busy"` | `backend/app/api/voice.py:264` |
| No-answer | Provider webhook status mapping `no_answer` → `call.ended`, `status="no_answer"` | `backend/app/api/voice.py:264` |
| Duplicate webhook | `_idem()` / `_remember()` return the cached result for a repeated `(call_id, tool, payload)` | `backend/app/api/voice.py:40` |

## 7. How to test

- `python -m pytest backend/tests -q` → 113 passed.
- `python scripts/e2e_demo.py` → 49 checks passed, 0 failed.
- Manually drive individual branches via `POST /api/voice/simulate/turn`.

## 8. Known limitations

- No real latency, interruption-recovery, or audio-quality data exists.
- "Silence" is not a distinct branch in the simulator; it is not
  distinguishable from an unrecognized utterance today.
- Voicemail/busy/no-answer are only exercised through the provider webhook
  code path, which has never received a real webhook call from Cartesia.
