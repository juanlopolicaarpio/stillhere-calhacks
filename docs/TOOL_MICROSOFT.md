# Microsoft

## 1. Status

`not integrated`. Zero code, zero credentials. This is a judging/prize
track, not an SDK integration, so "not integrated" describes the absence of
any Microsoft product (Azure, Teams, Copilot Studio, etc.) in the codebase,
not a gap relative to a planned feature.

## 2. Role in Still Here

No Microsoft product or SDK is used anywhere in this build. If Still Here is
entered against a Microsoft-sponsored track, the submission needs to make an
argument about fit (e.g., Azure deployment, Teams-based caregiver dashboard),
not point at integrated code.

## 3. Credentials required

None obtained. No Azure/Microsoft env var exists in `backend/app/config.py`
or `.env.example`.

## 4. SDK / package / version as actually verified

Not installed. No Azure SDK, Microsoft Graph client, or Teams Bot Framework
package appears in `backend/requirements.txt` or `frontend/package.json`.

## 5. Setup steps

Not applicable — nothing is integrated.

## 6. Actual implemented API calls

None.

## 7. How to test

Not applicable.

## 8. What the submission would need to argue instead of pretending code exists

- Be explicit to judges that this is a track-fit argument, not a code
  integration: no Microsoft SDK call exists anywhere in `backend/` or
  `frontend/`.
- A plausible argument, if made, would be about deployment target (e.g.,
  Azure hosting for the FastAPI backend) or about a caregiver-facing
  dashboard surfaced through Teams instead of (or alongside) the web
  dashboard already built (`frontend/src/pages/Dashboard.tsx`, owned by
  another agent). Neither has been attempted or evaluated.
- Do not claim any Azure OpenAI or Copilot Studio integration — the only
  LLM integration in this codebase is Anthropic's Claude API (see
  `docs/TOOL_ANTHROPIC.md`).
