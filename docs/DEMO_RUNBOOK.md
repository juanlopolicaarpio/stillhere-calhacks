# Demo Runbook

A 2-minute live demo runbook, a pre-flight checklist, the reset procedure, and
what to do if the real call fails. Written against the actual endpoints in
`backend/app/api/demo.py` and the dashboard in `frontend/src/pages/Dashboard.tsx`.

## Pre-flight checklist (run before the judge walks up)

1. **Backend and frontend are running.**
   ```
   make backend     # :8000
   make frontend     # :5173
   ```
2. **Confirm capability status.**
   ```
   make health
   ```
   Read the `voice_mode` and `real_call_possible` fields. If `CARTESIA_API_KEY`,
   `CARTESIA_AGENT_ID`, and `CARTESIA_FROM_NUMBER_ID` are all set,
   `voice_mode` will read `cartesia_pstn`. **In this build none of those are
   configured, so expect `simulated` and plan the demo around that honestly —
   see "If the real call isn't available" below.**
3. **If attempting a real call**, run `make preflight` first
   (`scripts/discover_cartesia.py`) to verify the Cartesia account can actually
   place a call before the judge is watching, and confirm `PUBLIC_BASE_URL`
   points at a public HTTPS tunnel (ngrok/cloudflared) that Cartesia's webhook
   tools can reach. Set `MY_PHONE_E164` to a phone you can personally answer.
4. **Reset the demo data.**
   ```
   curl -X POST http://localhost:8000/api/demo/reset
   ```
   or click "Reset demo" on the dashboard. Confirm the response's
   `reset_version` incremented.
5. **Open the dashboard** at `http://localhost:5173/dashboard` and enter the
   `DASHBOARD_TOKEN` value from `.env`. Confirm the "Live event stream
   connected" indicator is green before starting.
6. **Run the automated checks one last time** if there's time:
   ```
   make test && make e2e
   ```
   A clean `113 passed` / `49 passed, 0 failed` right before presenting is the
   cheapest insurance against last-minute drift.

## The 2-minute script

**0:00–0:15 — Frame it.**
Say the locked pitch out loud: *"Still Here is an AI that makes it easier for
people recovering from psychiatric hospitalization to reconnect with the
people they love, even when depression makes reaching out difficult."*
Show the landing page QR code — note on camera that scanning it only opens
sign-up, it does not contact anyone.

**0:15–0:40 — Show the consent chain, fast.**
On the dashboard, point at the "Sam" panel and the consent indicator. State
plainly: *"Josh had to open his own invite link and accept it — the patient
scanning a QR code never authorizes anything on its own."* If time allows,
flip briefly to `/circle` to show the real invite list (`GET /api/circle`),
not a mockup.

**0:40–1:10 — Start outreach.**
Click "Start outreach" (`POST /api/demo/start`). Narrate the dashboard as
events land live: *"the planner just chose Josh and 'a Marvel movie' —
not because it's hardcoded, but because those are literally what Sam told the
companion earlier."* Point at "Why this plan?" and the cited memories panel.
If running in `simulated` mode, say so directly: *"this mode is labeled
SIMULATED because no real phone call is configured for this run — the dialing
code is real and targets Cartesia's live API, we just don't have a key loaded
right now."* If running a real call, this is where the judge's phone rings.

**1:10–1:40 — The conversation and the guardrails.**
Either on the real call or via the simulator, show: the agent disclosing it's
an AI when asked "who is this", refusing "just book it, don't ask Sam", and
refusing to disclose "how is Sam doing". Then have the contact say yes. Point
out the dashboard moving to `PATIENT_APPROVAL_PENDING` — not confirmed yet.

**1:40–2:00 — Patient approval and confirmation.**
Use the "Sam approval simulator" panel (explicitly labeled `SIMULATED SAM
APPROVAL` — say this out loud, don't let it read as a real second person) to
submit "accepted". Watch the plan reach `PLAN_CONFIRMED` → `NOTIFICATIONS_SENT`
→ `COMPLETE` live on the dashboard. Close on: *"nothing here was scripted for
the demo — every one of those events is the backend actually doing the thing,
and the dashboard is reading them off a live stream, not replaying a canned
sequence."*

## Reset procedure (between rehearsals or after a failed run)

```
curl -X POST http://localhost:8000/api/demo/reset
```
or `make reset`. This wipes all application data (`db.wipe_all()`) and
reseeds the fictional cast (Sam, Josh, Maya, Daniel) with fresh memories,
availability, and consent records (`services/demo_reset.reset()`). Safe to run
repeatedly — `reset_version` increments each time so you can confirm it
actually ran. Clear the dashboard's stored `planId`/`callId` by clicking
"Reset demo" in the UI itself (it clears local state alongside the backend
call, `Dashboard.tsx:158-175`), not just the backend reset — otherwise the
dashboard will keep pointing at a plan id that no longer exists.

## If the call fails (real mode)

1. **Say it out loud immediately.** Do not let a silent failure look like a
   working demo. Something like: *"Looks like the real call didn't connect —
   let's fall back to the local simulator so you can still see the exact same
   logic run."*
2. **Check the dashboard's "Live call status" panel** — a `call.failed` event
   will show the real reason (provider rejection, no answer, etc.) rather than
   a generic error, because `services/voice._dial_real()` logs the provider's
   actual error message (`services/voice.py:206-220`).
3. **Switch to the simulator for the rest of the demo**:
   ```
   curl -X POST http://localhost:8000/api/demo/start -d '{"force_mode":"simulated"}' -H 'Content-Type: application/json'
   ```
   or restart from the dashboard with `force_mode` left unset (it defaults to
   whatever `voice_mode()` resolves to, which is `simulated` without a working
   Cartesia config).
4. **Continue narrating against the simulator**, labeling it clearly each
   time: *"this is the local simulator — same consent gate, same state
   machine, same confirmation logic, just no real telephony underneath it
   right now."* The `SIMULATED` badges on the dashboard do this labeling
   automatically; just don't talk over them as if they weren't there.
5. **Do not claim a real call happened if it didn't.** The whole point of the
   `mode`/`simulated` flags throughout this system is that this distinction is
   never ambiguous in the data — don't make it ambiguous in the narration
   either.

## Known-stable fallback: run the whole thing without dialing anyone

If live telephony is too risky for the venue (bad signal, no time to tunnel),
the entire demo above works identically with `force_mode: "simulated"` from
the start — every state transition, every guardrail, and every dashboard panel
behaves the same way, just honestly labeled. `scripts/e2e_demo.py` is this
exact path, scripted and verified to pass end to end; walking through the
dashboard live while narrating is the demo version of that script.
