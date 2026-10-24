# Microsoft — Sponsor Submission

## Status: `not integrated`

This is a judging/prize track, not an SDK integration. No Microsoft product
— Azure, Teams, Copilot Studio, the Bot Framework, or anything else — is
used anywhere in this codebase. Zero code, zero credentials. If Still Here
is entered against a Microsoft-sponsored track, the submission needs to
make an explicit argument about fit, not point at integrated code, and this
document keeps that argument separate from any code claim.

**We do not assert prize eligibility here — track rules and this track's own
judging criteria must be confirmed at the event.**

## The track-fit argument, stated honestly

Two plausible arguments exist, neither attempted or evaluated:

1. **Deployment target.** The FastAPI backend has no Azure-specific code
   today; it runs locally against SQLite and a local SpacetimeDB module.
   Azure hosting would require real deployment work (container or app
   service configuration, a managed Postgres/SQL target to replace the
   SQLite dev store, secrets management) that has not been started.
2. **A caregiver-facing surface in Teams instead of, or alongside, the
   existing web dashboard** (`frontend/src/pages/Dashboard.tsx`, owned by
   another agent in this build). This would need a Teams Bot Framework
   integration from scratch; none exists.

Do not claim any Azure OpenAI or Copilot Studio integration anywhere in this
submission — the only LLM integration in this codebase is Anthropic's Claude
API, see `docs/ANTHROPIC_SPONSOR.md`.

## What a judge can verify

Grep `backend/` and `frontend/` for any Azure SDK, Microsoft Graph client,
or Bot Framework import and confirm there are none. No Azure/Microsoft
environment variable exists in `backend/app/config.py` or `.env.example`.
