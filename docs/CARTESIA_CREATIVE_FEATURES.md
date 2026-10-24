# Cartesia Creative Features — What Is and Isn't Built

## 1. Status

`implemented (code) / blocked (live call unverified)` for the features listed
as built below, matching the overall Cartesia status in
`docs/TOOL_CARTESIA.md`. Everything in "Not built" is exactly that: no code,
no credentials.

## 2. Role in Still Here

This document separates the creative/negotiation behavior Still Here actually
implements from features that were explicitly scoped out, so no one mistakes
a design idea for a shipped capability.

## 3. Credentials required

Same as `docs/TOOL_CARTESIA.md`: `CARTESIA_API_KEY`, `CARTESIA_AGENT_ID`,
`CARTESIA_FROM_NUMBER_ID`. None set in this build.

## 4. SDK / package / version as actually verified

`cartesia` 4.2.0 (see `docs/TOOL_CARTESIA.md` for the full verification).

## 5. Setup steps

See `docs/PROVIDER_STATUS.md` for the six-step flip-to-live sequence. The
features below are wired through the same webhook tools and `dynamic_variables`
mechanism already in place; nothing further is needed once a live call works.

## 6. What IS built

- **Real-time negotiation.** The contact can interrupt, decline, propose a
  different day, propose a different activity (coffee is the implemented
  example), or ask "why me?" / "who is this?" — all handled without the agent
  restarting the conversation, implemented identically for the local
  simulator and the Managed Agent's webhook tools:
  - "why me?" → `get_support_context` tool
    (`backend/app/api/voice.py:89`) / simulator branch
    `backend/app/services/voice.py:381` (`_WHY`).
  - "who is this?" → simulator branch `backend/app/services/voice.py:350`
    (`_WHO`); live call would get the equivalent answer from the agent's
    system prompt in `prompts/OUTREACH_AGENT.md`.
  - Different day → `record_friend_response(response="counterproposed")`
    (`backend/app/api/voice.py:156`) / simulator branch
    `backend/app/services/voice.py:417` (`_requested_weekday`), which
    re-plans by calling `plans.counterpropose()` and looping the state
    machine back to `CANDIDATE_PLAN_CREATED` without ending the call.
  - Different activity (coffee) → simulator branch
    `backend/app/services/voice.py:387` (`_COFFEE`), which calls
    `scheduling.lookup_showtimes("coffee")` and `plans.counterpropose()`.
  - Refusal to book without the patient's say-so → simulator branch
    `backend/app/services/voice.py:357` (`_BOOK_WITHOUT`), which emits a
    `safety.blocked` event rather than advancing the plan.
  - Refusal to disclose the patient's health status → simulator branch
    `backend/app/services/voice.py:369` (`_HOWIS`), also emitting
    `safety.blocked`.
- **Memory-cited rationale on the dashboard.** Every plan carries
  `why_contact` / `why_activity` strings built from actual memory rows
  (`backend/app/agents/orchestrator.py:282`), and `memory.retrieved` /
  `call.tool_invoked` events carry the specific `memory_id`s cited
  (`backend/app/services/voice.py:157`, `backend/app/api/voice.py:111`), so a
  judge can trace "why Marvel?" back to a specific stored memory row.
- **`dynamic_variables` as the consent boundary.** `voice.py:109` builds the
  per-call `dynamic_variables` dict exclusively from
  `memory.shareable_context()`, and `memory.assert_no_sensitive_leak()` is
  run over every string value before it is sent (`voice.py:123`) — this is
  the mechanism, not just a convention, that keeps private clinical memory
  off the call.

## 7. How to test

- `python -m pytest backend/tests -q` → 113 passed, including branch coverage
  for each negotiation path above.
- `python scripts/e2e_demo.py` → 49 checks passed, 0 failed.
- Manually: `POST /api/voice/simulate/turn` with text like `"coffee instead"`,
  `"why marvel?"`, `"how is sam doing"`, or `"just book it without asking
  sam"` against a call created by `POST /api/demo/start`.

## 8. What is NOT built

- **Voice cloning.** The build plan explicitly said not to touch cloning
  until a real call works end to end. No code exists for it. Any future work
  here must be opt-in, disclosed to the person being cloned, revocable, and
  never used to make a call sound like it came from the patient themselves —
  i.e., never used to impersonate the patient to a third party.
- **Multilingual outreach.** Not implemented; `CARTESIA_TTS_MODEL` and voice
  selection are fixed to a single English voice/model in
  `backend/app/config.py`.
- **Voice preview** (letting the patient hear the agent's voice before
  authorizing a call). `synthesize_wav()` exists in the adapter as a
  general-purpose TTS call but is not wired into any onboarding or consent
  screen.
- **Line SDK multi-agent handoffs.** Not applicable — the Line SDK itself is
  not used; see `docs/CARTESIA_LINE.md`.
