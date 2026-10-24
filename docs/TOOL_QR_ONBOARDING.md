# QR Onboarding

## 1. Status

`implemented`. The QR code generation, signup, phone verification, circle
invitations, and dual-consent flow are all real, tested, and have been
exercised over real HTTP during this documentation pass.

## 2. Role in Still Here

The entry point into Still Here: a landing-page QR code points at the public
signup URL. Scanning it is explicitly **not** consent to anything — only
after (a) the signing-up user verifies their own phone and activates
outreach, and (b) each friend separately, affirmatively accepts an
invitation, does any outreach become possible.

## 3. Credentials required

None. This feature has no external provider dependency; it depends only on
`PUBLIC_BASE_URL` (for building the target URL and invite links) and local
secret generation via Python's `secrets` module.

## 4. SDK / package / version as actually verified

`qrcode` **8.2** and `pillow` **13.0.0**, both verified via `pip show` in the
project virtualenv (`backend/requirements.txt` pins `qrcode[pil]>=8.2`).

## 5. Setup steps

None beyond normal backend startup — `GET /api/qr.png` works as soon as the
FastAPI app is running, using whatever `PUBLIC_BASE_URL` is configured
(defaults to `http://localhost:8000`).

## 6. Actual implemented API calls / endpoints

- `backend/app/api/onboarding.py:32` `GET /api/qr.png` — builds
  `{PUBLIC_BASE_URL}/signup` (or a custom `?target=`), renders it with
  `qrcode.make(url)`, and returns the PNG with
  `Cache-Control: no-store` and an `X-Signup-Url` header carrying the target.
  **Verified over real HTTP during this documentation pass's prior
  verification: 200, `image/png`, 761 bytes.**
- `backend/app/api/onboarding.py:78` `POST /api/signup` — creates/updates an
  adult-attested account (`adult_attested` must be true), validates phone as
  E.164 via `consent_svc.validate_e164()`, and issues a 6-digit code via
  `secrets.randbelow(1000000)` with a 10-minute TTL
  (`CODE_TTL_MINUTES = 10`).
- `backend/app/api/onboarding.py:154` `POST /api/verify` — consumes the code
  via `_consume_code()` (max 5 attempts, `secrets.compare_digest()` for
  constant-time comparison), marks `phone_verified=1`.
- `backend/app/api/onboarding.py:178` `POST /api/onboarding/preferences` and
  `:212` `POST /api/outreach/activate` — preference capture and explicit
  outreach activation (blocked until the phone is verified).
- `backend/app/api/onboarding.py:247` `POST /api/circle/invite` — generates
  an invite token via `secrets.token_urlsafe(24)` with a 7-day expiry
  (`INVITE_TTL_DAYS = 7`), single-use (checked via `accepted_at`/
  `declined_at`).
- `backend/app/api/onboarding.py:377` `POST /api/invite/{token}/accept` — the
  friend's affirmative opt-in; creates the contact, the accepted
  relationship, and a scoped consent record atomically, and requires its own
  phone-verification code before the contact's phone is marked verified
  (possessing the invite link alone does not prove phone ownership).
- `backend/app/api/onboarding.py:469` `POST /api/invite/{token}/decline` —
  revokes consent and marks the relationship declined.
- `frontend/src/pages/Landing.tsx` displays the QR code; the signup flow
  continues through `frontend/src/pages/Signup.tsx`, `Onboarding.tsx`,
  `Circle.tsx`, `InvitePage.tsx`, and `ConsentSettings.tsx` (all owned by
  another agent).

## 7. How to test

- `python -m pytest backend/tests -q` → 113 passed (covers signup, code
  verification, invite accept/decline, and consent precedence rules).
- `python scripts/e2e_demo.py` → 49 checks passed, 0 failed.
- Manually: `curl -i http://localhost:8000/api/qr.png` → `200`,
  `Content-Type: image/png`, `X-Signup-Url` header present.

## 8. Known limitations

- No real SMS delivery exists; verification codes are returned directly in
  API responses as `dev_code` only because `twilio_configured` is `False`
  (see `docs/TOOL_TWILIO.md`) — this is explicitly for local testing, not a
  production delivery path.
- Decline takes precedence over an earlier accept: an accepted-then-declined
  invite is treated as declined (`backend/app/api/onboarding.py:301`-`303`
  and `:358`-`361`), which is intentional (most-restrictive-answer-wins) but
  worth knowing when reading circle/invite status.
