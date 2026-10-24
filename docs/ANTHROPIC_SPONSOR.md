# Anthropic / Claude — Sponsor Submission

## Status: `implemented`

Three real, distinct uses of the Anthropic API exist in this codebase, each
with an honestly-labeled deterministic fallback for when no key is present.
All three were observed running the live Claude path in this environment.

## Where Claude is actually used

| Use | File | Observed mode |
|---|---|---|
| Companion chat replies | `backend/app/adapters/anthropic_adapter.py::companion_reply()` | `reasoning_mode: "claude"` |
| Structured memory extraction (fact/preference/relationship/boundary, with provenance) | `backend/app/adapters/anthropic_adapter.py::extract_memories()` | `extraction_mode: "claude"` |
| Live-call tool-use conversation engine | `backend/app/agents/conversation.py::_run_claude()` | `conversation_mode: "claude"` |

SDK: `anthropic` 2.3.0. Model: `claude-opus-5`. All three functions degrade
to a deterministic, honestly-labeled fallback (`"fallback"` /
`"keyword_fallback"`) on any exception or when `ANTHROPIC_API_KEY` is unset
— `GET /api/status` reports which mode is actually in effect; nothing in the
UI or docs may claim more than that flag says.

## The conversation engine, specifically

`backend/app/agents/conversation.py::interpret_turn` runs a genuine
Anthropic tool-use loop: `client.messages.create(tools=[...])`, dispatches
any `tool_use` blocks through `agents/tools.py::dispatch`, feeds
`tool_result` blocks back in, and repeats until Claude calls the one output
tool, `respond_to_contact`, with a reply and a structured intent label. This
is not a single completion call — it is an agentic loop with real tool
execution in the middle, capped at `MAX_TOOL_ITERATIONS = 5`.

Claude is deliberately given **no tool that can change authoritative
state**. `agents/tools.py::MODEL_FORBIDDEN_TOOLS = {"confirm_plan",
"send_confirmation", "start_outbound_call"}` — if the model emits a `tool_use`
for one of these (whether by its own initiative or because a caller tried to
manipulate it into doing so), `dispatch()` is still the single enforcement
point that refuses it and emits a `safety.blocked` event. Four safety
properties are then enforced in code regardless of what the model says,
checked against every reply before it can reach a contact:

1. **No health disclosure.** Claude's context is built from
   `memory.shareable_context()` only — private clinical rows are never in
   its context window. Every outgoing reply is independently re-screened by
   `memory.assert_no_sensitive_leak()` plus a wider `_DISCLOSURE_RE` pattern
   set (`conversation.py`, lines ~137-147) covering both explicit clinical
   terms and softer inferences ("not doing great", "going through a lot").
2. **No invented availability.** Every proposed time is checked against
   `scheduling.find_free_slots` / `slot_covers`; an ungrounded time (even one
   mentioned only in prose, not in the structured slot) is caught by
   `_hours_mentioned()` and the reply is rewritten.
3. **No third-party disclosure.** `ask_add_person` replies are required to
   contain an explicit "needs their own permission" clause or they are
   replaced with `SAFE_ADD_PERSON_REPLY`.
4. **No state change.** `interpret_turn()` itself records nothing and
   confirms nothing; it returns an intent and slots, and the backend decides
   what to do with them.

## A real regression this build found and fixed

The previous implementation used keyword/regex branching for intent
classification, which matched the substring "ok" inside "Has he been
**ok**ay?" and classified a worried health question as an acceptance. With
Claude in the loop, that same utterance correctly classifies as
`ask_about_patient_health`, discloses nothing, and advances no plan state.
This is documented in code at `backend/app/agents/conversation.py`'s module
docstring and is exactly the class of bug a real-conversation-shaped
evaluation (rather than scripted branches) is supposed to catch.

## What is not yet verified

- Whether Claude's conversation engine, specifically, has handled a live
  phone call end-to-end is unverified — the one real PSTN call placed so far
  (`docs/LIVE_PROVIDER_EVIDENCE.md`) was a Cartesia Managed Agent
  conversation, not a call routed through `interpret_turn()`; whether the
  full system prompt was even loaded on that call is itself unverified (see
  caveat 2 in `docs/LIVE_PROVIDER_EVIDENCE.md`).
- The health-disclosure guarantee is proven by automated tests
  (`backend/tests/test_api_flow.py`, the `_DISCLOSURE_RE` guard) but has
  **not** been observed holding on an actual live call — the one call that
  asked a related question ("am I depressed or Sam's depressed?") had its
  answer lost to a cleared transcript buffer before it could be captured.

## How to test

```
python -m pytest backend/tests -q      # 195 passed
python scripts/e2e_demo.py             # 49 passed, 0 failed
curl http://localhost:8000/api/status  # conversation_mode, anthropic_configured
```
