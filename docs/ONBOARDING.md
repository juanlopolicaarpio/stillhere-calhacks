# Onboarding and Consent Flow

This is what the code actually does, end to end, from `backend/app/api/onboarding.py`
and the corresponding `frontend/src/pages/*.tsx`.

## The core rule

**Scanning a QR code is not consent to anything.** It opens a sign-up page. Nothing
is contacted until:

1. the person who scanned it verifies their own phone number and activates outreach,
   **and**
2. each individual friend in their circle separately opens their own invite link and
   affirmatively accepts it, **and**
3. that friend's phone number is independently verified (holding the invite link
   does not prove phone ownership).

Every one of these is enforced server-side, not just described in the UI copy.

## Step by step

### 1. QR → signup page

`GET /api/qr.png` (`backend/app/api/onboarding.py:32-46`) renders a QR pointing at
`PUBLIC_BASE_URL/signup`. The landing page explicitly tells the user: *"Scanning
this code only opens the sign-up page on your phone. It does not authorize any
call, text, or outreach of any kind"* (`frontend/src/pages/Landing.tsx:66-68`).

### 2. Signup (`POST /api/signup`)

`SignupBody` requires `phone_e164`, `preferred_channel` (`call`|`text`),
`adult_attested: bool`, `display_name` (`onboarding.py:52-62`).
- `adult_attested` must be `true` or the request is rejected (`:82-83`) — enforced
  server-side, not just a UI checkbox.
- A 6-digit numeric code is generated and stored in `verification_codes` with a
  10-minute expiry (`CODE_TTL_MINUTES = 10`, `:25`, `_issue_code`, `:64-75`).
- The user row is created with `outreach_active=0` and `phone_verified=0` (`:93-99`)
  — nothing is active yet.
- `dev_code` is returned in the response **only when no SMS provider (Twilio) is
  configured** (`:118-122`), which is the case in this build — so local testing is
  possible without a live SMS integration, and the contract guarantees this field is
  absent whenever Twilio is actually live.

### 3. Verify (`POST /api/verify`)

Consumes the code with `secrets.compare_digest` (constant-time comparison,
`:148`), enforces expiry and a max of 5 attempts (`MAX_CODE_ATTEMPTS = 5`, `:143`).
On success, `phone_verified` flips to `1` (`:161`). This is the only way
`phone_verified` becomes true for the patient.

### 4. Preferences (`POST /api/onboarding/preferences`)

Optional timezone and activity preferences, stored as `shareable_with_contact`
memories with provenance `"stated during onboarding"` (`:189-196`). Does not touch
consent or outreach state.

### 5. Build the circle (`POST /api/circle/invite`)

For each friend, the patient creates an invitation: `contact_name`,
`contact_phone_e164`, `relationship_label`. A random URL-safe token is generated
(`secrets.token_urlsafe(24)`, `:254`), stored in `invitations` with a 7-day expiry
(`INVITE_TTL_DAYS = 7`, `:24`). **No relationship, consent, or user row exists yet
for the contact at this point** — the invite is just a pending offer.

`GET /api/circle` (`:280-320`) lists every invite the patient sent and its real
status (`pending`/`accepted`/`declined`), with `callable` computed server-side as
`status == "accepted" AND phone_verified` (`:317`) — the UI never has to compute
this itself from raw flags.

### 6. The friend's own invite page (`GET /api/invite/{token}`, `POST .../accept`)

The contact-facing invite page (`frontend/src/pages/InvitePage.tsx`) is loaded by
whoever holds the link — the backend deliberately exposes **no clinical context
whatsoever** at this endpoint (`onboarding.py:353-370`; verified by
`test_api_flow.py::test_invite_page_leaks_no_clinical_context`).

The friend must click **Accept** or **Decline**:

- **Accept** (`POST /api/invite/{token}/accept`, `:377-466`) creates the contact's
  user row (if new), an `accepted` relationship, and a scoped `consents` row — in
  one atomic step. This is the friend's affirmative, individual opt-in; nothing
  about the patient's earlier QR scan or signup grants this.
- **Critically: accepting does not verify the phone.** The code comment is explicit
  about why (`:388-391`):

  > "Phone ownership is proven by an SMS code, NOT by holding the invite link. The
  > link is shown in the UI, so possessing it proves nothing about the phone."

  Unless the request includes a valid code, `phone_verified` stays `0`
  (`code_ok` gate, `:392-394`), and `consent.verify_outreach()` will refuse to dial
  this contact even though the relationship is `accepted`
  (proven by `test_api_flow.py::test_unverified_contact_cannot_be_called`). The
  response flags `verification_required: true` and `phone_verified: false` so the UI
  can prompt for the code, and — only when no SMS provider is configured —
  returns `dev_code` for local testing (`:456-466`).
- **Decline** (`POST /api/invite/{token}/decline`) marks the relationship `declined`
  and revokes any consent that might already exist (`:469-484`). A decline always
  wins over an earlier accept if both happen (`:301-307`, `:358-363`) — the most
  restrictive answer governs.

### 7. Activation (`POST /api/outreach/activate`)

The patient must separately turn outreach on. This requires `phone_verified` to
already be true (`:220-221`) — you cannot activate before your own phone is
confirmed. Activating does **not** immediately contact anyone; the UI says so
explicitly (`frontend/src/pages/Circle.tsx:209-211`), and the planner
(`agents/orchestrator.identify_opportunity`) still requires, per contact, that the
full consent gate in `services/consent.verify_outreach()` passes before any plan or
call is attempted.

## What must be true before Josh (or anyone) can actually be called

All of the following, checked in order by `consent.verify_outreach()`
(`services/consent.py:93-179`):

1. Patient has activated outreach (`outreach_active = 1`).
2. The contact's relationship status is `accepted` (their own doing, not the
   patient's).
3. For voice/SMS, the contact's `phone_verified = 1` (proven by a code, not a link).
4. An active `consents` row exists permitting the specific action (`voice_call` or
   `sms`).
5. Current time is outside the consent's quiet hours.
6. The contact hasn't been reached more than `max_per_day` times in the last 24h.

Any single failure blocks the call — there is no override reachable from a model.

## Revocation at any time

- `POST /api/consents/revoke` — immediately halts any in-flight plan for that pair
  (`onboarding.py:566-588`; `services/plans.halt_for_revoked_consent`), reachable
  from the patient's own Consent Settings page
  (`frontend/src/pages/ConsentSettings.tsx`).
- `POST /api/sms/stop` — the inbound STOP keyword path. Revokes **every** consent
  for that phone number, regardless of which patient relationship it belongs to
  (`consent.handle_stop`, `services/consent.py:190-200`).
