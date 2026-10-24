# Cartesia — Sponsor Submission

## Status: `implemented`, one real outbound PSTN call verified live, with documented caveats

A real phone call was placed through Cartesia Managed Agents on 2026-10-24,
accepted by the provider in 1,323 ms, answered by a human, and carried on
for a 23+ turn unscripted conversation before completing. Full transcript,
evidence file, and an unflinching account of what that call does and does
not prove: `docs/LIVE_PROVIDER_EVIDENCE.md`. This document maps that work
and the surrounding code to Cartesia's own published judging rubric.

**We do not assert prize eligibility here — track rules must be confirmed at
the event. This document maps contributions to the rubric's categories; it
is not a claim of qualification.**

## Rubric mapping

Cartesia's published rubric: problem/originality 25%, use of Cartesia /
voice quality 25%, execution 25%, impact & responsibility 25%.

| Category | What we built | Evidence |
|---|---|---|
| Problem / originality | A consent-gated outbound call that negotiates a real plan with a friend on behalf of someone who finds reaching out hard right now — acceptance, decline, counter-proposal, "why me?", and a graceful opt-out are all live-call behaviors, not scripted branches | `prompts/OUTREACH_AGENT.md`; the live call itself handled an unplanned decline gracefully (`docs/LIVE_PROVIDER_EVIDENCE.md §2`) |
| Use of Cartesia / voice quality | Built on Managed Agents end-to-end (deliberately not the Line SDK, which Cartesia stops hosting 2026-12-01 — see `docs/CARTESIA_LINE.md`); `dynamic_variables` carry permissioned context without exposing clinical data to the model; four webhook tools defined for mid-call context, showtime lookup, response recording, and patient-approval handoff | `backend/app/adapters/cartesia_adapter.py`; `backend/app/api/voice.py`; `scripts/setup_cartesia_agent.py` prints the exact tool definitions |
| Execution | A real call was placed and held for ~60 seconds of live conversation against the production API, not a sandbox; 46 adapter tests plus the full consent/state-machine test suite pass against the code path that call exercised | `evidence/real_call_ac_ZzzPbaPYKstadZguL5TRtF.json`; `backend/tests/test_cartesia_adapter.py` (46 tests); `python -m pytest backend/tests -q` → 195 passed |
| Impact & responsibility | No model-reachable tool can confirm a plan or place a call (`MODEL_FORBIDDEN_TOOLS`); every webhook tool is authenticated and idempotent; the call that happened handled a direct decline with no pressure applied, matching the design intent exactly | `backend/app/agents/tools.py`; `backend/app/api/voice.py` (`_auth`, `_idem`); the live transcript itself |

## SDK / API facts verified directly against the live account

- `cartesia` 4.2.0. `client.agents` exposes `.list/.retrieve/.update/.delete/
  .list_phone_numbers/.list_templates`; `client.agents.calls` exposes
  `.list/.retrieve/.download_audio`. **No create method exists for agents or
  for outbound calls.** A Managed Agent (`is_managed: true`) can only be
  created in the Cartesia Playground UI — `POST /agents` exists but
  `is_managed` is server-controlled.
- Outbound dialing is therefore raw HTTP: `POST
  https://api.cartesia.ai/agents/calls`, header `Cartesia-Version:
  2026-08-14`, body `{agent_id, from_number_id, ringing_timeout_seconds,
  outbound_calls:[{to_number, dynamic_variables}]}`
  (`backend/app/adapters/cartesia_adapter.py::place_outbound_call`, line 72).
- `PATCH /agents/{id}` accepts `llm_system_prompt`, `llm_introduce`,
  `tts_voice`, `name`; it rejects `tts_model` ("cannot be set on a managed
  agent. Use the /v1/agents API") and `description` ("Unrecognized key") —
  observed directly during setup, not currently automated by any script.
- Cartesia-provisioned phone numbers are US-only at both ends.
- Cartesia offers no cryptographic webhook signature verification — only a
  plain shared-secret header, matched by this repo's own
  `X-StillHere-Secret` scheme (see the detailed finding in
  `backend/app/adapters/cartesia_adapter.py`'s module docstring).

## What the live call proved, and what it did not

Proved: the dialing path works against the real API; a Managed Agent can
hold an extended, unscripted, somewhat adversarial conversation and end it
gracefully on a decline.

Did not prove, and we say so plainly: the agent on that call had only
minimal smoke-test `dynamic_variables`, not real seeded plan context, so
its proposal was improvised rather than memory-grounded; whether the full
privacy-hardened `prompts/OUTREACH_AGENT.md` prompt was actually loaded for
that call is unverified; no webhook tool fired, meaning tool registration in
the Playground is still outstanding; and the call's one direct probe at the
health-disclosure boundary ("am I depressed or Sam's depressed?") had its
agent response lost before capture. Full account: `docs/LIVE_PROVIDER_EVIDENCE.md §4`.

## How to test

```
python -m pytest backend/tests/test_cartesia_adapter.py -q   # 46 passed
python scripts/discover_cartesia.py                           # live account preflight
python scripts/smoke_call.py                                  # places a NEW real call
```
