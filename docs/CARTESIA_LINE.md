# Cartesia Line SDK — Deliberately Not Integrated

## 1. Status

`not integrated`. This is a deliberate architectural decision made before any
code was written, not an oversight or a dropped feature.

## 2. Role in Still Here

None. The build plan originally listed the Line SDK as a fallback transport
for live voice. It was dropped before implementation began.

## 3. Credentials required

None — no code paths reference the Line SDK.

## 4. SDK / package / version as actually verified

Not installed. Not referenced anywhere in `backend/`.

## 5. Setup steps

None planned. If a future need arises, re-evaluate against Cartesia's current
hosting policy first (see below) before writing any code against it.

## 6. Actual implemented API calls

None.

## 7. How to test

Not applicable — there is nothing to test.

## 8. Known limitations / rationale

Cal Hacks guidance and Cartesia's own documentation state that Managed Agents,
not the Line SDK, are the supported path going forward: **Cartesia stops
hosting Line SDK agents on 2026-12-01.** Building the demo's real-call path on
a transport with a scheduled sunset six weeks after this hackathon would be a
poor foundation, so the "Line SDK fallback" item in the original build plan
was removed in favor of Managed Agents end to end
(`backend/app/adapters/cartesia_adapter.py`, `backend/app/services/voice.py`).
This also means no Line-specific multi-agent handoff features (see
`docs/CARTESIA_CREATIVE_FEATURES.md`) are available or planned.
