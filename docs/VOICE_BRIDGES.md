# Voice Transport Bridges — Options Compared

## 1. Status

`implemented (code) / blocked (live call unverified)` for the chosen option
(Cartesia Managed Agents); every other option on this page is
`not integrated`.

## 2. Role in Still Here

This document compares the transport options considered for getting a real
voice call to a contact's phone, and records why Cartesia Managed Agents was
chosen over the alternatives.

## 3. Credentials required

Only the chosen path (Cartesia) has reserved env vars in this build; see
`docs/TOOL_CARTESIA.md`.

## 4. SDK / package / version as actually verified

`cartesia` 4.2.0. No other transport SDK (Twilio, Telnyx/Photon, a custom SIP
stack) is installed.

## 5. Setup steps

See `docs/PROVIDER_STATUS.md` for the Cartesia flip-to-live sequence.

## 6. Actual implemented API calls

Only the Cartesia path has implemented calls; see `docs/TOOL_CARTESIA.md`.

## Comparison

| Option | Status | Why / why not |
|---|---|---|
| **Cartesia Managed Agents (chosen)** | `implemented (code) / blocked (live call unverified)` | Cartesia hosts listening, turn-taking, interruption handling, tool calls, and telephony around the LLM of choice. Fastest path to a real call that also supports mid-call negotiation via webhook tools. |
| Cartesia-provisioned number | Available to the chosen path, not yet used (no key) | Simplest number acquisition, but **US-only** for both origin and destination numbers. |
| Imported Twilio number | `planned / not integrated` | Documented fallback if Cartesia cannot provision a suitable US number; would require a Twilio account and importing the number into Cartesia. Not needed yet since no number has been provisioned at all. |
| Own SIP carrier | `not integrated` | Third way to attach a number to a Managed Agent, per Cartesia's docs. Not pursued — adds operational complexity with no benefit over Cartesia-provisioned or imported numbers for a demo. |
| Photon (photon.codes) / Telnyx | `verified as a product, not integrated` | A real platform (Vercel-backed, Telnyx-powered under the hood) for putting agents into iMessage/WhatsApp/Telegram/SMS/RCS/email/voice, with SIP-compatible calling connections (e.g., LiveKit, Retell). Strong future fit for the "Text me" onboarding branch, but not the fastest path to a live voice call. Deprioritized to P2. See `docs/PHOTON_VERIFICATION.md`. |
| Custom Twilio media bridge | `not integrated` (explicitly rejected) | The build plan explicitly said not to build a custom Twilio media bridge unless Managed Agents could not work. Managed Agents were judged sufficient, so no bridge was built. This avoids reimplementing turn-taking, interruption handling, and STT/TTS plumbing that Cartesia already provides. |

## Constraint carried into the chosen design

Cartesia-provisioned phone numbers are **US-only**, for both the origin
(agent) number and the destination number being dialed. This is accepted as
a known limitation for the demo; a non-US contact would require the imported-
number or own-SIP-carrier paths, neither of which is implemented.

## 7. How to test

Not applicable beyond what is already covered in `docs/TOOL_CARTESIA.md` —
this document is a comparison, not a runnable component.

## 8. Known limitations

- Only the Cartesia path has any code behind it; everything else in this
  comparison is evaluation only.
- The US-only constraint on Cartesia-provisioned numbers has not been worked
  around.
