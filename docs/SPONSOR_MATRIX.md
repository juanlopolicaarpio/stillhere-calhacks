# Sponsor / Track Matrix

This table states what Still Here actually contributes to each sponsor track,
an honest status for that contribution, and what a judge can verify directly.
It does not assert prize eligibility — that is for judges to decide against
each track's own rules.

| Sponsor / track | What Still Here contributes | Status | What a judge can verify |
|---|---|---|---|
| Cartesia | Managed Agents architecture for outbound PSTN outreach: consent-gated dialing, `dynamic_variables` context injection, four webhook tools, local simulator honestly labeled when no key is present | `implemented (code) / blocked (live call unverified)` | `backend/app/adapters/cartesia_adapter.py`, `backend/app/services/voice.py`, `backend/app/api/voice.py`; `python scripts/discover_cartesia.py` prints the preflight result; `evidence/cartesia_discovery.json` |
| Anthropic | Claude-based companion chat and structured memory extraction, with a deterministic, honestly-labeled fallback when no key is present | `implemented (code) / untested against live API` | `backend/app/adapters/anthropic_adapter.py`; API responses carry `reasoning_mode: "fallback"` when running without a key |
| Twilio | Reserved role as a phone-number provider (importable into Cartesia) and as an SMS channel for onboarding codes; neither is wired up | `planned / not integrated` | `backend/app/config.py` (`twilio_configured`), `.env.example`, `docs/TOOL_TWILIO.md` |
| SpacetimeDB | Considered for live state; the actual implementation is SQLite + Server-Sent Events behind an event-bus seam that could be swapped later | `not integrated` | `backend/app/db.py`, `backend/app/services/events.py`, `backend/app/api/events.py` |
| Fetch.ai (Agentverse / ASI:One) | None. A local `backend/app/agents/orchestrator.py` module exists but is ordinary Python, not an Agentverse agent | `not integrated` | `backend/app/agents/orchestrator.py` — no Fetch.ai import, no ASI:One call |
| Moss | None considered beyond the build plan | `not integrated` | No code, no credentials |
| Factory Droid | None; Claude Code was used as the build tool instead | `not integrated` | No code, no credentials |
| Creao | None considered beyond the build plan | `not integrated` | No code, no credentials |
| Notability | None considered beyond the build plan | `not integrated` | No code, no credentials |
| Meta | Judging/prize track, not an SDK integration; see `docs/TOOL_META.md` for what the submission would need to argue | `not integrated` | No code, no credentials |
| Microsoft | Judging/prize track, not an SDK integration; see `docs/TOOL_MICROSOFT.md` for what the submission would need to argue | `not integrated` | No code, no credentials |
| Photon (photon.codes) | Identified as a strong future fit for SMS/iMessage-based "Text me" onboarding, deliberately deprioritized below the P0 voice path | `verified as a product, not integrated` | `docs/PHOTON_VERIFICATION.md` |
| Claude Code | The entire codebase was authored by Claude Code (Opus 5) as lead engineering agent, using frozen contracts and disjoint file ownership across parallel subagents | `implemented` (development tool) | `specs/CONTRACTS.md`, `docs/TOOL_CLAUDE_CODE.md`, this document itself |

## Cross-cutting evidence any judge can reproduce

- `python -m pytest backend/tests -q` → **113 passed**
- `python scripts/e2e_demo.py` → **49 checks passed, 0 failed**
- `curl http://localhost:8000/api/qr.png` → `200`, `image/png`
- `curl http://localhost:8000/api/status` → capability snapshot showing exactly
  which providers are configured in the current environment
