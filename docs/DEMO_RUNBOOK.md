# Demo Runbook

A 2-minute live demo runbook, a pre-flight checklist, the reset procedure,
and what to do if the real call fails. Written against the actual endpoints
in `backend/app/api/demo.py`, the SpacetimeDB coordination layer, and the
dashboard in `frontend/src/pages/Dashboard.tsx`.

## Pre-flight checklist (run before the judge walks up)

1. **Start SpacetimeDB and publish a clean module.**
   ```
   make stdb-server                              # or: spacetime start --listen-addr 127.0.0.1:3000
   .venv/Scripts/python.exe scripts/stdb_setup.py --fresh   # DESTROYS module data, republishes clean
   ```
   Do the `--fresh` republish before every rehearsal and before the real
   demo. The backend test suite and the live confirmation gate both key
   proposals by exact plan+version — if the module has accumulated state
   from an earlier rehearsal, stale rows can cause a confirmation to be
   correctly-but-confusingly refused. A clean module avoids that entirely.
2. **Start the backend and frontend.**
   ```
   make backend     # :8000
   make frontend    # :5173
   ```
3. **Expose the backend publicly if attempting a real call.**
   ```
   ./tools/cloudflared.exe tunnel --url http://localhost:8000
   ```
   Copy the resulting `https://*.trycloudflare.com` URL into `.env` as
   `PUBLIC_BASE_URL`, then restart the backend so it picks up the new value.
4. **Confirm capability status.**
   ```
   make health
   ```
   Read `voice_mode`, `coordination_mode`, `conversation_mode`, and
   `real_call_possible`. Expect `cartesia_pstn` / `spacetimedb` / `claude` /
   `true` when all three provider keys and a reachable SpacetimeDB module are
   present. If any reads otherwise, the dashboard will say so honestly too —
   plan the narration around whichever flags are actually true.
5. **If attempting a real call**, run the Cartesia preflight first:
   ```
   make preflight     # scripts/discover_cartesia.py
   ```
   This confirms the account can place a call before a judge is watching.
   Confirm `MY_PHONE_E164` is set to a phone you can personally answer.
   **Register the four webhook tools in the Cartesia Playground before
   relying on them** — as of the last live call, no webhook tool had been
   registered, so none fired mid-call; `scripts/setup_cartesia_agent.py`
   prints the exact tool definitions to paste in.
6. **Reset the demo data.**
   ```
   curl -X POST http://localhost:8000/api/demo/reset
   ```
   or click "Reset demo" on the dashboard. Confirm `reset_version` incremented.
7. **Open the dashboard** at `http://localhost:5173/dashboard` and enter the
   `DASHBOARD_TOKEN` value from `.env`. Confirm the live event stream
   indicator is green, and if testing SpacetimeDB sync, confirm the
   SpacetimeDB connection indicator (`useSpacetime().wsConnected`) is green
   too — this specific indicator has not been rehearsed with two browser
   windows open side by side, so check it yourself before relying on it live.
8. **Run the automated checks one last time.**
   ```
   make test && make e2e
   ```
   Expect `195 passed` and `49 passed, 0 failed`. If `make test` shows fewer
   than 195, re-run step 1's `--fresh` republish first — the suite is not
   hermetic against an already-populated SpacetimeDB module (see
   `docs/IMPLEMENTATION_STATUS.md`).

## The 2-minute script

**0:00–0:15 — Frame it.**
Say the locked pitch out loud: *"Still Here is an AI that makes it easier
for people recovering from psychiatric hospitalization to reconnect with the
people they love, even when depression makes reaching out difficult."*

**0:15–0:40 — Show the consent chain, fast.**
On the dashboard, point at the "Sam" panel and the consent indicator. State
plainly: *"Josh had to open his own invite link and accept it — scanning a
QR code never authorizes anything on its own."*

**0:40–1:10 — Start outreach.**
Click "Start outreach." Narrate the dashboard as events land live, including
the `stdb.reducer_called` events proving SpacetimeDB is actually writing the
session and proposal: *"every one of these is a real transaction in
SpacetimeDB, not a local flag."* If placing a real call, this is where the
judge's phone — or a phone you control — rings. Be ready to say honestly, if
asked, that the specific plan context on this call is whatever the
seeded demo data produced, and that the webhook tools may not be registered
yet (see pre-flight step 5) — don't let a silent tool-call gap look like a
bug if it's actually a known, documented limitation.

**1:10–1:40 — The conversation and the guardrails.**
On the call (real or simulated), show: the agent disclosing it's an AI when
asked "who is this," refusing "just book it, don't ask Sam," and giving the
bounded non-disclosure answer to "how is Sam doing." Then have the contact
say yes. Point out the dashboard moving to a pending-approval state — not
confirmed yet.

**1:40–2:00 — Patient approval and confirmation.**
Submit the patient's approval (labelled `SIMULATED SAM APPROVAL` if running
solo — say this out loud). Watch the plan reach confirmed, mirrored live in
both the SpacetimeDB `plan_confirmations` table and the SQLite `plans` row.
Close on: *"nothing here was scripted — the dashboard is reading real events
off a live stream and a live database, not replaying a canned sequence."*

## Reset procedure (between rehearsals or after a failed run)

```
curl -X POST http://localhost:8000/api/demo/reset
```
wipes SQLite application data and reseeds the fictional cast (Sam, Josh,
Maya, Daniel). This does **not** wipe SpacetimeDB — run
`scripts/stdb_setup.py --fresh` separately between rehearsals, or stale
SpacetimeDB rows from the previous run can cause a confusing refusal on the
next confirmation attempt (see pre-flight step 1). Clear the dashboard's
stored plan/call ids by clicking "Reset demo" in the UI itself, not just the
backend reset.

## If the call fails (real mode)

1. **Say it out loud immediately.** *"Looks like the real call didn't
   connect — let's fall back to the local simulator so you can still see the
   exact same logic run."*
2. **Check the dashboard's "Live call status" panel** for the real provider
   error rather than a generic failure message.
3. **Switch to the simulator for the rest of the demo**:
   ```
   curl -X POST http://localhost:8000/api/demo/start -d '{"force_mode":"simulated"}' -H 'Content-Type: application/json'
   ```
4. **Continue narrating against the simulator**, labeling it clearly each
   time — the `SIMULATED` badges do this automatically; don't talk over them.
5. **Do not claim a real call happened if it didn't**, and do not claim more
   about a real call than `docs/LIVE_PROVIDER_EVIDENCE.md` actually supports
   — in particular, do not claim the health-disclosure case has been
   verified live; it has not.

## Known-stable fallback: run the whole thing without dialing anyone

If live telephony is too risky for the venue, the entire demo above works
identically with `force_mode: "simulated"` from the start. `scripts/e2e_demo.py`
is this exact path, scripted and verified to pass end to end (49 checks).

## If asked about SpacetimeDB specifically

Be ready to say exactly what is and isn't verified: the reducer-enforced
confirmation gate is real and tested live (21 passing tests against the
actual running module, not a mock). Two-independent-browser-client live sync
is implemented in the frontend (`frontend/src/hooks/useSpacetime.ts`) but has
not been visually confirmed by the team working side-by-side — say so if
asked, rather than demonstrating it live for the first time in front of a
judge.
