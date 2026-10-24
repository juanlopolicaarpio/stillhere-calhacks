# Twilio

## 1. Status

`planned / not integrated`. No SDK is installed and no credentials are
configured; `config.twilio_configured` is `False` in every environment this
build has run in.

## 2. Role in Still Here

Two possible roles were reserved, neither implemented:

1. Phone-number provider whose number is imported into Cartesia — the
   documented fallback transport if Cartesia cannot provision a suitable US
   number itself (see `docs/VOICE_BRIDGES.md`).
2. SMS delivery channel for onboarding verification codes and plan
   confirmation notifications.

## 3. Credentials required

| Env var | Purpose |
|---|---|
| `TWILIO_ACCOUNT_SID` | Account identifier |
| `TWILIO_AUTH_TOKEN` | Auth |
| `TWILIO_FROM_NUMBER` | Sending number for SMS |

All three are reserved in `.env.example` and `backend/app/config.py` but
unset.

## 4. SDK / package / version as actually verified

Not installed. Not listed in `backend/requirements.txt`.

## 5. Setup steps

Not started. If pursued: install the `twilio` package, set the three env
vars above, and implement an SMS-sending adapter plus replace the
dev-code-in-response behavior described below with real delivery.

## 6. Actual implemented API calls

None. The only code that references Twilio's absence is the capability flag
`config.Settings.twilio_configured` (`backend/app/config.py:70`) and its
consumers:

- `backend/app/api/onboarding.py:118` — `signup()` returns `dev_code` and a
  `dev_note` in the API response instead of sending an SMS, specifically
  `if not s.twilio_configured`.
- `backend/app/api/onboarding.py:464` — `accept_invite()` does the same for
  the contact-side verification code.
- `backend/app/services/consent.py` / notification code treats delivery as a
  `simulated` event when no SMS provider is configured (confirmations are
  emitted as `simulated` notification events rather than real texts).

## 7. How to test

Confirm the current (unintegrated) state: `GET /api/status` →
`twilio_configured: false`. Confirm the fallback behavior: `POST /api/signup`
returns a `dev_code` field in its JSON response, which would not happen if
Twilio were configured.

## 8. Known limitations

- No SMS has ever been sent by this build.
- The build plan explicitly says not to build a custom Twilio media bridge
  for voice unless Cartesia Managed Agents could not work — Managed Agents
  were sufficient, so no such bridge was built (see `docs/VOICE_BRIDGES.md`).
- Verification codes being returned directly in API responses (`dev_code`)
  is explicitly for local testing only and is not a production-safe pattern;
  it exists solely because no SMS provider is configured.
