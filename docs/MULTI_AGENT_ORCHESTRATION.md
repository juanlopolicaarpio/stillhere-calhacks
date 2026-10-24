# Multi-Agent Orchestration — how this build was actually executed

This describes the real process used to build Still Here across both
contract phases, not an idealized one. It is documented honestly because the
deviations are themselves useful engineering decisions, and because the two
frozen contracts (`specs/CONTRACTS.md`, then `specs/CONTRACTS_V2.md`) record
ground truth about ownership at each point in time.

## Phase 1 — the baseline build (`specs/CONTRACTS.md`)

The original plan was a clean split: separate agents for consent, memory,
voice, planning, frontend, and docs, working in parallel against a frozen
contract. In practice, consent/voice/planner code turned out to be too
interdependent to split safely across agents working concurrently — three
call sites (`services/voice.start_outbound_call`, `services/plans.confirm_plan`,
`agents/orchestrator.py`) all needed the same invariants to hold
simultaneously, and splitting them risked exactly the kind of drift the
state machine and consent gate exist to prevent. The split was collapsed to:
lead owns the entire backend critical path; one agent owns `frontend/**`;
docs agents own `docs/**`. The frontend agent's review of the frozen contract
surfaced five real gaps before this baseline shipped — all fixed. Full
account of those five gaps is preserved below, since they are still true of
the shipped code.

## Phase 2 — the SpacetimeDB + fluid-agent addendum (`specs/CONTRACTS_V2.md`)

The second phase added three substantial pieces of new scope on top of a
working baseline: SpacetimeDB as the authoritative coordination backend, a
Claude-driven tool-use conversation engine replacing regex branching, and a
real outbound PSTN call. The lead froze `specs/CONTRACTS_V2.md` as an
addendum (not a replacement) to the original contract, with an explicit
ownership table (`specs/CONTRACTS_V2.md §7`) assigning disjoint file sets to
agents A (Cartesia), B (SpacetimeDB), C (fluid conversation), D (frontend), E
(QA/security), and F (this docs agent).

### The fan-out, and why it was cut short

A first fan-out of five parallel subagents (A–E) was launched against the
frozen V2 contract. That fan-out was **killed mid-task by an API session
limit** before every agent finished. Rather than discard the partial work,
the lead:

1. **Verified what had actually landed** by reading the partial diffs against
   `specs/CONTRACTS_V2.md`, not trusting agent self-reports.
2. **Integrated the salvageable pieces directly** rather than re-launching a
   fresh fan-out.
3. **Found three real integration gaps by running the system**, not by
   inspection alone:
   - The fluid conversation engine (`backend/app/agents/conversation.py`) had
     been written but was **not wired into** the call-handling path —
     `services/voice.py` was still invoking the old branching logic.
   - **SpacetimeDB was unreachable from the running app** even though the
     module itself published and responded to `spacetime sql` directly —
     the adapter's configuration/discovery path had a gap between what the
     setup script verified and what the running FastAPI process actually
     read at import time.
   - **The two stores modeled plan revisions incompatibly**: SQLite keeps one
     mutable `plans` row whose `version` increments in place; SpacetimeDB's
     `plan_proposals` table is immutable, one row per version. Code written
     against a bare plan id (not `<plan_id>_v<version>`) would silently
     compare the wrong version in the reducer. This is the same bug described
     in `backend/app/services/coordination.py::proposal_ref`'s code comment
     and in `docs/IMPLEMENTATION_STATUS.md` — found once, documented in two
     places on purpose, because it is the kind of bug that reappears if the
     lesson isn't written down next to the code that fixes it.
4. **Fixed all three directly** and re-ran the full test suite before
   continuing, rather than letting a second fan-out build on top of a broken
   integration.

This is the same lesson Phase 1 taught, recurring at larger scope: a frozen
contract defines *what* must be true at the seams, but it does not by itself
catch *whether the seams actually connect* — someone has to run the system.
The contract process worked exactly as intended here: it gave the lead a
precise, falsifiable list of integration points to check once agents stopped
reporting their own completion, rather than a vague "merge and see."

## The 5 contract gaps the frontend agent's Phase 1 review surfaced

All five are fixed in the shipped backend. Evidence that they were real
gaps, not hypothetical:

1. **Missing `GET /api/circle` and `GET /api/contacts`.** The frontend had no
   way to list a patient's invites or resolve a contact id to a display name
   except browser `localStorage`, which breaks across devices/browsers and
   loses data on a cache clear. Fixed: `backend/app/api/onboarding.py`
   (`/api/circle`, with a code comment explaining why it was added; and
   `/api/contacts`).
2. **`EventSource` cannot send custom headers**, so the documented
   `X-Dashboard-Token` header auth is unusable for the one place that
   actually needs live auth — the SSE stream. Fixed: both
   `GET /api/events/recent` and `GET /api/events/stream` accept the token as
   *either* the header *or* a `dashboard_token` query parameter
   (`backend/app/api/events.py`), and
   `frontend/src/hooks/useAgentEvents.ts` documents why.
3. **`Plan.rationale` was a single opaque string**, insufficient for a
   dashboard that needs *why this contact* and *why this activity* as
   separate, structured facts. Fixed: `plans` table gained `why_contact` and
   `why_activity` columns, populated by the orchestrator.
4. **`GET /api/status` shape was undocumented**, so the frontend's honesty
   banner had nothing frozen to code against. Fixed: the endpoint returns
   `config.redacted()` plus `real_call_possible` and a plain-English `notes`
   string, now also carrying `coordination_mode` and `conversation_mode` as
   of the V2 addendum.
5. *(Reported together with the above as one review pass; folded into the
   `/api/circle` fix once the lead traced why the frontend needed it —
   `callable` as a server-computed boolean per invite, rather than three raw
   flags the frontend would otherwise have had to combine itself.)*

## File ownership as it actually stood (V2)

| Area | Owner | Scope |
|---|---|---|
| Shared contracts, config, db, state machine, main, consent/memory/voice services, agents/API routers | **lead** | `specs/**`, `backend/app/config.py`, `db.py`, `main.py`, `domain/**`, `services/**`, `api/**` |
| Cartesia | **agent A** | `backend/app/adapters/cartesia_adapter.py`, `scripts/discover_cartesia.py`, `scripts/setup_cartesia_agent.py`, `scripts/smoke_call.py`, `backend/tests/test_cartesia_adapter.py`, `prompts/OUTREACH_AGENT.md` |
| SpacetimeDB | **agent B** | `spacetime/**`, `backend/app/adapters/spacetimedb_adapter.py`, `scripts/stdb_setup.py`, `backend/tests/test_spacetimedb.py` |
| Fluid conversation | **agent C** | `backend/app/agents/conversation.py`, `backend/app/agents/tools.py`, `backend/app/adapters/anthropic_adapter.py`, `prompts/COMPANION_SYSTEM.md`, `prompts/MEMORY_EXTRACTION.md` |
| Frontend | **agent D** | `frontend/**` |
| QA/security | **agent E** | `backend/tests/test_security.py`, `test_negative_paths.py`, `test_concurrency.py`, `scripts/e2e_demo.py`, `scripts/chaos_check.py`, `docs/PRIVACY_SAFETY.md`, `docs/SECURITY.md` |
| Docs/sponsor | **agent F** (this agent) | `docs/ANTHROPIC_SPONSOR.md`, `MICROSOFT_SPONSOR.md`, `META_SPONSOR.md`, `SPACETIMEDB_SPONSOR.md`, `CARTESIA_SPONSOR.md`, `DEVPOST.md`, `DEMO_RUNBOOK.md`, `IMPLEMENTATION_STATUS.md`, `MULTI_AGENT_ORCHESTRATION.md`, `SPONSOR_MATRIX.md`, `LIVE_PROVIDER_EVIDENCE.md`, `FINAL_HANDOFF.md` |

Note: `specs/CONTRACTS_V2.md §7` originally assigned `docs/CARTESIA_SPONSOR.md`
to agent A and `docs/SPACETIMEDB_SPONSOR.md` to agent B. Because the killed
fan-out meant those two files were never written, the lead reassigned both
to agent F (this agent) as part of the final documentation pass, which is
why the table above differs from the frozen spec on those two lines — a
real, intentional deviation, not an error, recorded here so it isn't
mistaken for drift.

## Why this is the right lesson, not a failure to plan

Both phases produced the same finding: a frozen contract makes the seams
*checkable*, it does not make them *correct by construction*. The five
Phase-1 gaps were caught by an agent actually trying to build against the
contract. The three Phase-2 gaps were caught by the lead actually running the
integrated system after a fan-out was cut short. In both cases the process
surfaced the problem before a judge would have, which is the contract
process working as intended.
