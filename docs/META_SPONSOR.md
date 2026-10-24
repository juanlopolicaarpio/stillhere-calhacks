# Meta — Sponsor Submission

## Status: `not integrated`

This is a judging/prize track, not an SDK integration. No Meta product —
Llama, the WhatsApp Business API, or anything else — is used anywhere in
this codebase. Zero code, zero credentials. If Still Here is entered
against a Meta-sponsored track, the submission needs to make an explicit
argument about fit, not point at integrated code, and this document keeps
that argument separate from any code claim.

**We do not assert prize eligibility here — track rules and this track's own
judging criteria must be confirmed at the event.**

## The track-fit argument, stated honestly

Still Here's architecture already separates "channel" (today: voice, via
Cartesia Managed Agents) from "planning/consent logic" (channel-agnostic,
living in `backend/app/agents/orchestrator.py` and `backend/app/services/`).
In principle, a WhatsApp Business API channel could be added alongside
Cartesia the same way `docs/PHOTON_VERIFICATION.md` proposes an SMS/iMessage
channel for contacts who'd rather not take a phone call. This is an
architectural claim about how the system is shaped, not a demonstrated one —
no WhatsApp code has been written, and no evaluation of the WhatsApp
Business API against this use case has been attempted.

On the model side: do not claim Llama was evaluated as a backend for the
companion or the conversation engine. The only LLM integration anywhere in
this codebase is Anthropic's Claude API — see `docs/ANTHROPIC_SPONSOR.md`.
No comparison or swap-in to Llama was attempted, and none is implied by
anything else in this repository.

## What a judge can verify

Grep `backend/` and `frontend/` for any Meta-related import (WhatsApp
Business API client, Llama inference client, etc.) and confirm there are
none. No Meta-related environment variable exists in
`backend/app/config.py` or `.env.example`.
