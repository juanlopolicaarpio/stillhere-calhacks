# SpacetimeDB — Sponsor Submission

## Status: `implemented`

SpacetimeDB is the authoritative transactional backend for Still Here's
shared live coordination workflow — not a parallel store, not a demo-only
add-on. The confirmation gate that the entire product's safety story depends
on (both parties must agree to the identical activity and time before
anything is final) is enforced inside a SpacetimeDB reducer, and the
pre-existing SQLite path now mirrors that outcome rather than deciding it
independently.

## What's actually built

| Piece | Detail |
|---|---|
| Module | TypeScript (`spacetimedb` npm 2.13.0, CLI 2.13.0, V8 runtime — chosen because `cargo`/`dotnet` are not installed on this machine but Node is), published locally as database `stillhere` at `spacetime/spacetimedb` |
| Tables (public) | `connection_sessions`, `call_sessions`, `plan_proposals` (immutable, one row per version), `participant_responses`, `consent_state` (sanitized booleans only), `agent_events` (pre-redacted), `plan_confirmations`, `demo_presence` |
| Table (private) | `service_principals` — identities permitted to call privileged reducers, never subscribable |
| Reducers | `start_session`, `set_call_state`, `propose_or_revise_plan`, `respond_as_friend`, `record_patient_approval`, `confirm_if_matching`, `revoke_or_cancel`, `set_consent_state`, `append_sanitized_event`, `upsert_demo_presence` |
| Python bridge | `backend/app/services/coordination.py` — every shared-state write goes through a reducer call; `confirm_plan()` treats `confirm_if_matching`'s outcome as authoritative and writes no local confirmation on refusal |
| Frontend bridge | `frontend/src/hooks/useSpacetime.ts` + generated bindings in `frontend/src/spacetime_bindings/` subscribe to all eight public tables and a presence reducer |

## The confirmation invariant, specifically

`confirm_if_matching(proposalId, version, idempotencyKey)` confirms only if,
inside one transaction: the proposal exists and is still `active` (not
superseded by a later revision); the passed `version` matches exactly; a
`contact`-role response and a `patient`-role response both exist for that
exact proposal+version; both responses' activity and start time match the
proposal's and each other's; consent for the session has not been revoked;
and no confirmation already exists for this proposal (idempotent replay of
the same key returns success without a second write; a different key for an
already-confirmed proposal is rejected, not silently re-confirmed). Any
failure returns one of nine machine-readable reasons: `no_proposal`,
`stale_version`, `superseded`, `missing_contact_approval`,
`missing_patient_approval`, `activity_mismatch`, `time_mismatch`,
`consent_revoked`, `already_confirmed`.

**21 live reducer tests** exercise this against the actual running module,
not a mock — `backend/tests/test_spacetimedb.py`, including
`TestConfirmationInvariant` (lines 140-228: every rejection reason, plus
idempotent replay and revoked-consent cases) and `TestPrivacyBoundary`
(lines 230-287: asserting no clinical language and no full phone number ever
appears in a public table). Observed live during this build: a proposal
moved from `v1 active=false ("a Marvel movie")` to `v2 active=true
("Coffee")` after a revision, both parties approved v2 specifically, and the
reducer returned `CONFIRMED BY REDUCER: v2 "Coffee"` — v1's now-stale
approvals could not have confirmed anything, by construction.

## A real bug this build found and fixed

SQLite keeps one mutable `plans` row whose `version` column increments in
place; SpacetimeDB's `plan_proposals` table is immutable — a revision
inserts a new row. The bridge function `coordination.py::proposal_ref`
derives a stable key, `<plan_id>_v<version>`, specifically because the
original code reused the bare plan id: that left the SpacetimeDB side
pointed at v1 forever while SQLite moved on to v2, so the reducer compared
the wrong terms and refused to confirm a plan that should have confirmed.
Found by running the integrated system, not by code review, and documented
at the point of the fix so it doesn't silently reappear.

## Honest degradation

`config.Settings.coordination_mode()` returns `"spacetimedb"` only when the
adapter is configured **and** a live health probe succeeds; otherwise
`"sqlite_fallback"`. `GET /api/status` exposes both `coordination_mode` and
`spacetimedb_reachable`. `services/coordination.py::_call()` distinguishes
"SpacetimeDB unreachable" (fallback continues, `stdb.unavailable` event)
from "SpacetimeDB ran and refused" (a real rejection, `stdb.reducer_rejected`
event) — these are never conflated, because they mean different things to a
judge watching the dashboard.

## What is not yet verified

Two independent SpacetimeDB-subscribed browser clients staying in sync with
no polling or reload — the code for it exists
(`frontend/src/hooks/useSpacetime.ts`, `frontend/src/spacetime_bindings/`)
but has not been visually confirmed working side-by-side. Treat this
specific claim as `planned`, not demonstrated.

## A test-suite caveat found during this documentation pass

Running the full backend test suite against an already-populated live
SpacetimeDB module (one that has accumulated rows from prior manual runs)
produces 5 failures out of 195 — each passes individually, and the cause is
deterministic test/demo ids colliding with stale rows left in the module,
which `confirm_if_matching` then correctly-but-confusingly refuses.
Republishing with `spacetime/../scripts/stdb_setup.py --fresh` first
reproduces a clean 195/195 every time this was tried. This means the
module's confirmation logic is behaving exactly as designed — the gap is
that the test suite isn't yet hermetic against the module's external,
persistent state. Recorded in `docs/FINAL_HANDOFF.md`.

## How to test

```
spacetime start --listen-addr 127.0.0.1:3000
python scripts/stdb_setup.py --fresh
python -m pytest backend/tests/test_spacetimedb.py -q   # 21 passed
spacetime sql --server local stillhere "SELECT * FROM plan_confirmations"
```
