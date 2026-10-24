# Final Handoff — what remains

This is the exact, prioritized list of what is still open after the
SpacetimeDB + fluid-agent build, written so the next session (or the next
agent) does not have to rediscover it. Each item names the file(s) involved
and why it matters, not just what to do.

## 1. Register the four webhook tools in the Cartesia Playground

**Why it matters:** no tool call fired during the one real live call placed
so far (`docs/LIVE_PROVIDER_EVIDENCE.md §4.5`). The code that serves them
(`backend/app/api/voice.py`: `get_support_context`, `lookup_showtimes`,
`record_friend_response`, `request_patient_approval`) is implemented and
tested, but a Managed Agent cannot call a tool it was never told exists.

**Action:** run `python scripts/setup_cartesia_agent.py`, copy the four tool
definitions it prints (each with the correct `PUBLIC_BASE_URL` and
`X-StillHere-Secret` header), and paste them into the agent's tool
configuration in the Playground.

## 2. Verify the full system prompt is actually live on a call

**Why it matters:** the greeting heard on the one real call sounded like the
Playground's default welcome message, not a line from
`prompts/OUTREACH_AGENT.md` (13,935 bytes). Whether the full
privacy-hardened prompt was loaded for that call is unverified, and the one
overstatement the agent made on that call ("I do know Sam... you two have a
history") is exactly the kind of thing that prompt is written to prevent.

**Action:** `PATCH /agents/{id}` with `llm_system_prompt` set to the full
contents of `prompts/OUTREACH_AGENT.md`, then confirm via `GET /agents/{id}`
(or the Playground UI) that the stored value matches before placing the
next call.

## 3. Re-run a live call carrying real seeded plan context

**Why it matters:** `scripts/smoke_call.py` passes only
`{contact_first_name, purpose: "smoke_test", is_test: "true"}` as
`dynamic_variables` — not the real Sam/Josh plan, memory citations, or
proposed activity/time. The agent's "coffee Saturday" proposal on the live
call was improvised, not memory-grounded.

**Action:** either extend `smoke_call.py` to accept a real plan id and build
`dynamic_variables` from `memory.shareable_context()` the way
`services/voice.py`'s real outreach path is designed to, or trigger the call
through `/api/demo/start` against a real seeded plan instead of the smoke
script.

## 4. Verify the health-disclosure case on a live call

**Why it matters:** this is the single most safety-critical behavior in the
whole system. The one live call that came close — the caller asked "am I
depressed or Sam's depressed?" — had its agent response lost when Cartesia
cleared the transcript buffer before it could be pulled. The non-disclosure
guarantee is proven in code and in automated tests
(`backend/app/agents/conversation.py::_assert_no_disclosure`,
`backend/tests/test_api_flow.py`) but has never been *observed holding* on
an actual phone call.

**Action:** place a call, deliberately ask a health-probing question mid-call
(have a second person play the "friend" role if needed), and capture the
agent's response before the call reaches a terminal status — poll
`GET /agents/calls/{id}` and pull the transcript promptly, or log
`call.turn` events as they happen through the configured webhook rather than
relying on fetching it after the fact.

## 5. Visually verify two-client SpacetimeDB sync

**Why it matters:** `frontend/src/hooks/useSpacetime.ts` and
`frontend/src/spacetime_bindings/` subscribe to all eight public tables, but
no one has opened two browser windows side by side and watched a change in
one appear in the other with no reload. This is one of the explicit
acceptance criteria in `specs/CONTRACTS_V2.md §8` ("Two independent
subscribed clients update with no polling or reload") and is currently
unverified, not failing — just unobserved.

**Action:** open the dashboard in two browser profiles/windows, drive a plan
through `/api/demo/start` → approval → confirmation in one, and watch the
`plan_proposals` / `participant_responses` / `plan_confirmations` panels
update live in the other with no manual refresh.

## 6. Fix the test suite's non-hermeticity against the live SpacetimeDB module

**Why it matters:** found and reproduced during this documentation pass.
`python -m pytest backend/tests -q` against an already-populated SpacetimeDB
module produced 5 failures out of 195 (each passes individually); a clean
`--fresh` republish reproduced 195/195 reliably. The root cause:
`backend/tests/conftest.py`'s `fresh_db` fixture wipes the per-test SQLite
file but has no equivalent reset for the persistent external SpacetimeDB
module, so deterministic test/demo ids can collide with rows left over from
a prior manual run or test session.

**Action:** either (a) have the test suite publish its own isolated
SpacetimeDB database name per run (e.g. a `conftest.py` fixture that
publishes `stillhere_test_<uuid>` and tears it down after), or (b) make
`fresh_db` call `scripts/stdb_setup.py --fresh`-equivalent logic at session
start so the module is wiped once per test session rather than never. Option
(a) is safer for CI; option (b) is simpler and sufficient for local/demo use.
Until this is fixed, always republish the module with `--fresh` immediately
before any test run or rehearsal that must show a clean 195/195.

## Priority order for a judge-facing demo

If time is short before presenting, do items 1, 2, and 6 first (webhook
tools and prompt verification directly affect what a real call looks like
on stage; item 6 directly affects whether `make test` shows the number this
submission claims). Items 3, 4, and 5 strengthen the evidence base but are
not required for the demo script in `docs/DEMO_RUNBOOK.md` to run
successfully end to end.
