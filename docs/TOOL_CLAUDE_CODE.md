# Claude Code

## 1. Status

`implemented` (development tool). This repository was built by Claude Code
acting as the lead engineering agent; it has no runtime dependency in the
shipped application.

## 2. Role in Still Here

Claude Code (running Claude Opus 5) was the tool used to design, implement,
and test this entire codebase, using a multi-agent workflow: contracts were
frozen first, then implementation work was split across subagents with
disjoint file ownership, with the lead agent owning shared files and
performing merges. It does not run as part of the deployed Still Here
application — it is purely a build-time tool.

## 3. Credentials required

None at runtime. Claude Code itself required its own Anthropic access during
development; that is separate from the `ANTHROPIC_API_KEY` reserved for the
Still Here companion/memory features at runtime (see
`docs/TOOL_ANTHROPIC.md`).

## 4. SDK / package / version as actually verified

Claude Code CLI, model Opus 5. Not a package dependency of `backend/` or
`frontend/` — it does not appear in `backend/requirements.txt` or
`frontend/package.json`.

## 5. Setup steps

Not applicable to running the shipped app. For context on how it was used:
`specs/CONTRACTS.md` was authored and frozen first ("Status: frozen
2026-10-24 by the lead agent. Owned by the lead only. Implementation agents
must code against this file and must not change it."), and that file records
the verified toolchain versions used throughout the build (Python 3.14.2,
Node 24.12.0, fastapi 0.162.0, pydantic 2.14.1, cartesia 4.2.0, anthropic
2.3.0, httpx 0.28.1, pytest 9.1.1).

## 6. Actual implemented API calls

None — this is a build-time tool, not a runtime integration. The artifact of
its work is the rest of this repository.

## 7. How to test

Not applicable at runtime. The quality of its output is what the test suite
measures: `python -m pytest backend/tests -q` → 113 passed;
`python scripts/e2e_demo.py` → 49 checks passed, 0 failed.

## 8. Known limitations

- Claude Code's involvement is a development-process fact, not a feature of
  the running product, and should not be presented to judges as a sponsor
  SDK integration in the same sense as Cartesia or Anthropic's API.
- The orchestration pattern used (frozen contracts, disjoint file ownership,
  lead-owned merges) is documented in `specs/CONTRACTS.md` and
  `docs/MULTI_AGENT_ORCHESTRATION.md` (owned by another agent); this document
  only covers Claude Code's role as a tool, not the orchestration design
  itself.
