# Fetch.ai (Agentverse / ASI:One)

## 1. Status

`not integrated`. Zero code, zero credentials.

## 2. Role in Still Here

Would have been used, per the build plan, to register Still Here's planning
logic as an Agentverse agent and/or to route reasoning through ASI:One. No
such registration or routing exists.

## 3. Credentials required

None obtained.

## 4. SDK / package / version as actually verified

Not installed. No `uagents`, `fetchai`, or Agentverse/ASI:One package appears
anywhere in `backend/requirements.txt`.

## 5. Setup steps

Not started.

## 6. Actual implemented API calls

None. `backend/app/agents/orchestrator.py` and `backend/app/agents/tools.py`
exist and are named "agents," but they are ordinary local Python modules —
`identify_opportunity()`, `score_contact()`, `choose_activity()` — with no
Agentverse registration, no ASI:One call, and no Fetch.ai import anywhere in
either file. **Naming local Python classes or modules "agents" is not a
Fetch.ai integration**, and nothing in this repo should be presented as one.

## 7. How to test

Not applicable — there is nothing Fetch.ai-related to test. Confirm the
negative by grepping `backend/` for `uagents`, `agentverse`, or `asi1`: no
matches.

## 8. Known limitations / rationale

Correctly deprioritized: the build plan's stop rule required finishing P0
(real call, dual consent, memory, planner, dashboard, tests) before any
additional sponsor integration, and P0 consumed the available time. Actually
integrating Fetch.ai would require wrapping the planner in a `uagents.Agent`,
registering it on Agentverse, and/or routing a reasoning call through
ASI:One — none of which has been attempted.
