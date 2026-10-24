# Meta

## 1. Status

`not integrated`. Zero code, zero credentials. This is a judging/prize
track, not an SDK integration, so "not integrated" describes the absence of
any Meta product (Llama, WhatsApp Business API, etc.) in the codebase, not a
gap relative to a planned feature.

## 2. Role in Still Here

No Meta product or SDK is used anywhere in this build. If Still Here is
entered against a Meta-sponsored track, the submission needs to make an
argument about fit (e.g., a hypothetical WhatsApp-based outreach channel, or
running on Llama instead of Claude), not point at integrated code.

## 3. Credentials required

None obtained. No Meta-related env var exists in `backend/app/config.py` or
`.env.example`.

## 4. SDK / package / version as actually verified

Not installed. No Meta SDK (e.g., WhatsApp Business API client, Llama
inference client) appears in `backend/requirements.txt` or
`frontend/package.json`.

## 5. Setup steps

Not applicable — nothing is integrated.

## 6. Actual implemented API calls

None.

## 7. How to test

Not applicable.

## 8. What the submission would need to argue instead of pretending code exists

- Be explicit to judges that this is a track-fit argument, not a code
  integration: no Meta SDK call exists anywhere in `backend/` or
  `frontend/`.
- A plausible argument, if made, would be: Still Here's architecture already
  separates "channel" (voice today, via Cartesia) from "planning/consent
  logic" (channel-agnostic, in `backend/app/agents/orchestrator.py` and
  `backend/app/services/`), so a WhatsApp Business API channel could in
  principle be added alongside Cartesia the same way Photon is proposed as a
  future SMS/iMessage channel (see `docs/PHOTON_VERIFICATION.md`). This is
  an architectural claim, not a demonstrated one.
- Do not claim Llama was evaluated as a model backend — `anthropic_adapter.py`
  is the only LLM integration in this codebase (see `docs/TOOL_ANTHROPIC.md`),
  and no comparison or swap-in was attempted.
