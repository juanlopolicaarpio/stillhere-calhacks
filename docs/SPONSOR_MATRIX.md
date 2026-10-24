# Sponsor / Track Matrix

This table states what Still Here actually contributes to each sponsor
track, an honest status for that contribution, and what a judge can verify
directly. It does not assert prize eligibility — track rules must be
confirmed at the event.

| Sponsor / track | What Still Here contributes | Status | What a judge can verify |
|---|---|---|---|
| **Cartesia** | Managed Agents outbound PSTN calling: consent-gated dialing, `dynamic_variables` context injection, four webhook tools, a real completed call with a 23+ turn unscripted human conversation | `implemented (live call verified, with documented caveats)` | `evidence/real_call_ac_ZzzPbaPYKstadZguL5TRtF.json`; `docs/LIVE_PROVIDER_EVIDENCE.md`; `backend/app/adapters/cartesia_adapter.py`; `python scripts/smoke_call.py` reproduces the path (will place a new real call) |
| **SpacetimeDB** | Authoritative transactional backend for shared live coordination (sessions, call state, immutable plan-proposal versions, participant responses, consent snapshots, confirmations), enforced through a reducer no client can bypass | `implemented` | `spacetime/spacetimedb/src/schema.ts` and `index.ts`; `backend/app/services/coordination.py`; `backend/tests/test_spacetimedb.py` (21 tests); `python -m pytest backend/tests/test_spacetimedb.py -q` |
| **Anthropic** | Claude-driven companion chat, structured memory extraction, and a genuine tool-use conversation engine for live calls, each with an honestly-labeled deterministic fallback | `implemented` | `backend/app/adapters/anthropic_adapter.py`; `backend/app/agents/conversation.py`; `GET /api/status` → `conversation_mode: "claude"` in this environment |
| **Claude Code** | The entire codebase was authored by Claude Code (Opus 5) as lead engineering agent, using two frozen contract documents and disjoint file ownership across parallel subagents, including recovering from a fan-out killed by an API session limit | `implemented` (development tool) | `specs/CONTRACTS.md`, `specs/CONTRACTS_V2.md`, `docs/MULTI_AGENT_ORCHESTRATION.md`, `docs/TOOL_CLAUDE_CODE.md` |
| Twilio | Reserved role as a phone-number provider and SMS channel; a Cartesia-provisioned number was used instead for the live call, so this was never needed | `not integrated` | `backend/app/config.py` (`twilio_configured`), `docs/TOOL_TWILIO.md` |
| Photon (photon.codes) | Identified as a strong future fit for SMS/iMessage "Text me" onboarding; correctly deprioritized below the P0 voice path | `verified as a product, not integrated` | `docs/PHOTON_VERIFICATION.md` |
| Fetch.ai (Agentverse / ASI:One) | None. `backend/app/agents/orchestrator.py` is ordinary Python, not an Agentverse agent | `not integrated` | No Fetch.ai import anywhere in `backend/` |
| Moss, Factory Droid, Creao, Notability | None considered beyond the build plan | `not integrated` | No code, no credentials |
| **Meta** | Judging/prize track, not an SDK integration; see `docs/META_SPONSOR.md` for the track-fit argument the submission would need to make | `not integrated` | No code, no credentials |
| **Microsoft** | Judging/prize track, not an SDK integration; see `docs/MICROSOFT_SPONSOR.md` for the track-fit argument the submission would need to make | `not integrated` | No code, no credentials |

## Cross-cutting evidence any judge can reproduce

```
spacetime start --listen-addr 127.0.0.1:3000   # if not already running
python scripts/stdb_setup.py --fresh           # publish a clean module (see note below)
python -m pytest backend/tests -q              # -> 195 passed
python scripts/e2e_demo.py                     # -> 49 checks passed, 0 failed
cd frontend && npm run build                   # -> clean, zero TypeScript errors
curl http://localhost:8000/api/status          # capability snapshot: voice_mode,
                                                #   coordination_mode, conversation_mode
```

**Note on reproducing the 195-passed number:** the backend test suite is not
currently hermetic against an already-populated live SpacetimeDB module — if
the module has accumulated state from prior manual runs (demo clicks, a real
call, earlier test runs), 5 of 195 tests can fail on deterministic-id
collisions even though each passes individually. Republishing with `--fresh`
first reproduces a clean 195/195 every time this was tried during this
documentation pass. See `docs/IMPLEMENTATION_STATUS.md` and
`docs/FINAL_HANDOFF.md`.

## Per-sponsor detail

Cartesia and SpacetimeDB each have a dedicated document mapping the track's
own published judging criteria to specific code and evidence:
`docs/CARTESIA_SPONSOR.md`, `docs/SPACETIMEDB_SPONSOR.md`,
`docs/ANTHROPIC_SPONSOR.md`, `docs/META_SPONSOR.md`,
`docs/MICROSOFT_SPONSOR.md`.
