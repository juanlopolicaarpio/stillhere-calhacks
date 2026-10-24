# Photon — Verification

## 1. Status

`verified as a product, not integrated`. Research confirms what Photon
actually is; no code or credentials exist in this build.

## 2. Role in Still Here

Potential future transport for the "Text me" branch of QR onboarding
(`docs/TOOL_QR_ONBOARDING.md`), where a contact could be reached over
iMessage/SMS rather than only by voice call. Not used today.

## 3. Credentials required

None — not integrated. If pursued later, Photon would require its own API
key/account; no env var is reserved for it in `backend/app/config.py`.

## 4. SDK / package / version as actually verified

Not installed. No package evaluated or pinned.

## 5. Setup steps

None taken. If pursued: evaluate Photon's API for number provisioning and
SIP-compatible calling-connection setup (e.g., to LiveKit or Retell), then
decide whether it replaces or supplements the Cartesia voice path for
non-voice channels.

## 6. Actual implemented API calls

None.

## 7. How to test

Not applicable — nothing is implemented.

## 8. Research finding and assessment

The user mentioned "Photon." Verified identity: this is **photon.codes**, a
Vercel-backed platform (raised $4.5M seed, co-led by Gradient and A*) that
puts AI agents into iMessage, WhatsApp, Telegram, SMS, RCS, email, and voice.
Its messaging/voice layer is powered by Telnyx underneath; it provisions
phone numbers in near real time and supports calling connections to
SIP-compatible platforms such as LiveKit and Retell, so a single number can
serve both iMessage and calls.

This is explicitly **not** the Photon game-engine voice SDK — that is a
different product entirely and was ruled out during research.

Assessment: Photon is a strong future fit for the "Text me" branch of QR
onboarding (a contact who prefers SMS/iMessage over a phone call), but it is
not the fastest path to a live voice call — Cartesia Managed Agents is.
Photon was correctly deprioritized to P2 behind the P0 items (real call, dual
consent, memory, planner, dashboard, tests). No code, no credentials, not
integrated.
