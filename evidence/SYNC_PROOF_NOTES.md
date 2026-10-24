# Two-client SpacetimeDB sync proof — Agent K

## What this proves

specs/CONTRACTS_V3.md §6 requires: "two independent browser contexts, subscribed
to the SpacetimeDB module, both updating when state changes — with no polling
and no page reload."

## What was actually done

The Chrome browser extension needed for a literal two-window screenshot was
**not connected** in this environment — `mcp__claude-in-chrome__list_connected_browsers`
returned `[]` when checked. Per the task's own fallback instruction ("capture
evidence another way — e.g. two Node/WebSocket subscriber processes printing
live row updates"), this was verified with two independent Node.js processes
instead, using the **exact same client SDK and generated bindings** that
`frontend/src/hooks/useSpacetime.ts` uses in the browser
(`spacetimedb` npm package, `DbConnection.builder()`,
`connection.subscriptionBuilder().subscribe(...)`, `table.onInsert`/`onUpdate`
row callbacks). This is not a simulation of the SDK — it is the SDK, run twice,
independently, against the live `stillhere` module on `ws://127.0.0.1:3000`.

Script: `frontend/scripts/sync_proof_subscriber.ts`. Run with:

```
node --experimental-strip-types --experimental-loader ./scripts/ts-ext-resolve-hook.mjs \
  scripts/sync_proof_subscriber.ts A   # and, in a second process, "B"
```

(`scripts/ts-ext-resolve-hook.mjs` only resolves the generated bindings'
extensionless relative imports under plain Node; it does not change program
behavior.)

## Timeline observed (raw logs: `sync_proof_client_A.log`, `sync_proof_client_B.log`)

1. **19:22:50.748Z** — both client A and client B start, independently, each
   opening its own WebSocket connection and calling
   `conn.subscriptionBuilder().subscribe([...])` for `demo_presence`,
   `plan_proposals`, and `participant_responses`.
2. **19:22:50.821Z** — both connect (different SpacetimeDB identities,
   `c2007f2a...` for A, `c2008650...` for B — genuinely separate clients, not
   the same socket).
3. **19:22:50.844–845Z** — both receive their initial subscription snapshot
   (a pre-existing `demo_presence` row and a pre-existing `plan_proposals`
   v1 row with its two `participant_responses`), within 1ms of each other.
4. **19:23:04.617Z** — a **third, separate** process calls the
   `upsert_demo_presence` reducer over plain HTTP
   (`POST /v1/database/stillhere/call/upsert_demo_presence`) with a brand new
   `connectionId`/`label` pair that neither client A nor B had ever seen.
5. **19:23:04.755Z (B) / 19:23:04.759Z (A)** — ~140ms later, **both** client A
   and client B print a `demo_presence INSERT` for that exact new row,
   **4ms apart from each other**, purely from their `onInsert` callback firing
   — neither process polls, re-queries, or restarts.
6. **19:23:16–19:23:19Z** — `POST /api/demo/reset` then `POST /api/demo/start`
   (real backend, real reducer calls `reset_coordination` then
   `start_session`/`propose_or_revise_plan`) produced a new plan `plan_0ce...`
   version 1. Both A (19:23:19.359Z) and B (19:23:19.362Z) printed the insert
   3ms apart.
7. **19:23:28.868Z** — a further `plan_proposals` insert (`plan_783...`)
   appeared on **both** clients at the identical millisecond, caused by
   independent activity elsewhere in this shared demo environment (other
   agents were working against the same live backend/SpacetimeDB instance at
   the same time) — itself further confirmation that this is a live,
   shared, multi-actor pub/sub feed rather than anything scripted by this
   proof.

Every row printed in both logs is byte-for-byte identical between A and B,
in the same order, within single-digit milliseconds of each other, with
**zero polling** (no interval, no refetch — only `onInsert`/`onUpdate`
callbacks registered once at startup) and **zero reload** (both are the same
long-running process from start to finish).

## How to repeat this visually in a browser

Navigate to `/sync-proof` in two separate browser windows (same frontend dev
server). Each window independently opens its own SpacetimeDB WebSocket via
`frontend/src/hooks/useSpacetime.ts`. Type a label and click "I'm here" in one
window; the new row appears in the other window's presence list with no
refresh. Driving a plan counterproposal from the judge dashboard
(`/dashboard`) in one window shows the new proposal version and its
revision history appear live in the other window's "Live plan proposals &
revision history" panel.

## Status for this claim (specs/CONTRACTS_V3.md §1 vocabulary)

**`externally_verified`** — verified against the real, independently-running
`stillhere` SpacetimeDB module (not mocked, not an in-memory stub), from two
separate OS processes with separate identities, with evidence captured to
this directory. The browser-visual variant of this same proof was not
performed (no Chrome extension connection available in this environment);
the dashboard note above documents a judge or teammate can perform that
visual check themselves at `/sync-proof` in under a minute.
