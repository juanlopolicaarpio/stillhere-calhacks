# Multi-Agent Orchestration — how this build was actually executed

This describes the real process used to build Still Here, not the originally
planned one. It is documented honestly because the deviation itself is a useful
engineering decision, and because `specs/CONTRACTS.md` records the collapsed
ownership split as ground truth (`specs/CONTRACTS.md §8`).

## What was planned vs. what happened

The original plan was a clean A–F split: separate agents for consent, memory,
voice, planning, frontend, and docs, working in parallel against a frozen
contract.

In practice, the consent, voice, and planner code turned out to be too
interdependent to split safely across agents working concurrently:

- `services/voice.start_outbound_call()` has to call `consent.verify_outreach()`
  unconditionally before touching a provider (`services/voice.py:80-92`) — the
  voice owner and consent owner would be editing the same invariant from two
  sides.
- `services/plans.confirm_plan()` is the single authorization point
  (`services/plans.py:234-301`) and is called from `api/demo.py`, from
  `api/voice.py` tool handlers, and indirectly from `agents/orchestrator.py` —
  three call sites that all need the same guarantees to hold simultaneously.
- The planner (`agents/orchestrator.py`) reads memory visibility rules that the
  memory owner would also be changing.

Splitting these into separately-owned files risked exactly the kind of drift the
state machine and consent gate are designed to prevent (two code paths
implementing "is this allowed" slightly differently). So the split was
collapsed:

| Area | Owner | Files |
|---|---|---|
| Shared contracts, config, db, state machine, main | **lead** | `config.py`, `db.py`, `domain/*`, `main.py`, `specs/*` |
| Consent / memory / voice services, adapters, agents, API routers | **lead** | `services/*`, `adapters/*`, `agents/*`, `api/*` |
| Frontend | **agent E** | `frontend/**` only |
| Docs | **agent F** (this agent) | `docs/**`, `README.md` only |
| Tests | **lead + agent F** | `backend/tests/**` |

The lead built the entire interdependent backend critical path directly, and owned
every file two agents might otherwise have touched. Parallelism was preserved only
where the interface was genuinely narrow and one-directional: the frontend consumes
a frozen HTTP contract and nothing else; docs describe what already exists and
write to nothing the backend or frontend read.

## The actual process

1. **Freeze the contract.** The lead wrote `specs/CONTRACTS.md` — ground rules,
   visibility ladder, state machine, every HTTP endpoint shape, the event
   vocabulary, and the frontend route list — and froze it
   ("frozen 2026-10-24 by the lead agent... Implementation agents must code
   against this file and must not change it", `specs/CONTRACTS.md:3-5`).
2. **Lead builds backend.** Config, db, domain/state_machine, all services,
   adapters, agents, and API routers, directly against the frozen contract.
3. **Parallel subagents build against the frozen contract, not against each
   other.** The frontend agent built `frontend/**` purely from
   `specs/CONTRACTS.md` — `frontend/src/api/client.ts` opens with an explicit
   instruction to future editors: *"Do not invent endpoints or rename fields —
   this file is the frozen contract surface for the frontend. If something here
   looks wrong, it should be reported to the lead, not quietly changed"*
   (`client.ts:1-4`). Docs agents (this one, plus provider-specific doc owners)
   read the finished backend and describe it.
4. **The frontend agent's review surfaced real contract gaps**, reported to the
   lead rather than worked around locally (see below).
5. **The lead fixed the gaps** in the backend and updated the frozen contract,
   and the frontend agent's code was updated to match.

## The 5 contract gaps the frontend agent's review surfaced

All five are now fixed in the shipped backend. Evidence that they were real gaps,
not hypothetical:

1. **Missing `GET /api/circle` and `GET /api/contacts`.** The frontend had no way
   to list a patient's invites or resolve a contact id to a display name except
   browser `localStorage`, which breaks across devices/browsers and loses data on
   a cache clear. Fixed: `backend/app/api/onboarding.py:280-320` (`/api/circle`,
   with the code comment "Added after the frontend correctly reported that invites
   were otherwise only recoverable from browser localStorage") and `:323-341`
   (`/api/contacts`).
2. **`EventSource` cannot send custom headers**, so the documented
   `X-Dashboard-Token` header auth (`specs/CONTRACTS.md §7`) is unusable for the
   one place that actually needs live auth — the SSE stream. Fixed: both
   `GET /api/events/recent` and `GET /api/events/stream` now accept the token as
   *either* the header *or* a `dashboard_token` query parameter
   (`backend/app/api/events.py:26-34`, `:37-78`), and
   `frontend/src/hooks/useAgentEvents.ts:20-23,36-39` documents exactly why the
   query-param path exists and uses it.
3. **`Plan.rationale` was a single opaque string**, insufficient for a dashboard
   that needs to show *why this contact* and *why this activity* as separate,
   structured facts. Fixed: `plans` table gained `why_contact` and `why_activity`
   columns (`backend/app/db.py:126-127`), populated by the orchestrator
   (`agents/orchestrator.py:285-286`) and asserted on in
   `test_api_flow.py::test_plan_exposes_structured_rationale`.
4. **`GET /api/status` shape was undocumented**, so the frontend's honesty banner
   (which must say plainly whether a real phone call is possible) had nothing
   frozen to code against. Fixed: `backend/app/main.py:93-110` returns
   `config.redacted()` plus `real_call_possible` and a plain-English `notes`
   string, and this shape is now what `frontend/src/api/client.ts:113-116`
   (`StatusResponse`) is typed against.
5. *(Reported together with the above as one review pass; the fifth gap folded
   into the `/api/circle` fix once the lead traced why the frontend needed it —
   `callable` as a server-computed boolean per invite, rather than three raw
   flags the frontend would otherwise have had to combine itself: `status ==
   "accepted" AND phone_verified`, `onboarding.py:317`.)*

## Why this is the right lesson, not a failure to plan

The collapse from a six-way split to lead-owned-backend + delegated-frontend/docs
is the kind of decision the frozen-contract process is supposed to make visible
early rather than discover through merge conflicts. The contract stayed frozen;
the *team topology* around it adapted once the actual coupling in the code became
clear. The five gaps were caught by a frontend agent actually trying to build
against the contract — which is the contract process working as intended, not
failing.
