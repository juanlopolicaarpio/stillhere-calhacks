# Devpost Submission Copy

## One-line pitch (locked)

> I built Still Here, an AI that makes it easier for people recovering from
> psychiatric hospitalization to reconnect with the people they love, even when
> depression makes reaching out difficult.

## What it does

Still Here is an opt-in companion for adults after a psychiatric discharge. It
talks with you, remembers what matters — who the important people in your life
are, what you used to do together, what you're comfortable sharing — and, only
with explicit, verified, revocable consent from every person involved, places
a real outbound phone call to plan a small moment of reconnection: a coffee, a
walk, a movie.

It is built around one hard rule: **the model proposes, code and a
transactional database authorize.** No model — not the companion, not the
voice agent on a live call — ever has the ability to confirm a plan or place
an outbound call on its own authority. Those actions live behind a
deterministic state machine and, now, a SpacetimeDB reducer that no
model-reachable tool can bypass.

## What is real vs. simulated (we want to be direct about this)

| Capability | Status |
|---|---|
| Consent gate, plan state machine, memory system with provenance and privacy guards | **Real, fully implemented, tested** — 195 backend tests + a 49-check end-to-end script, both passing |
| Cartesia outbound PSTN voice calling | **Real.** A real phone call was placed on 2026-10-24, accepted by the provider in 1,323 ms, answered by a human, and carried on for 23+ unscripted turns. We're equally direct about the gaps: that call used minimal smoke-test context rather than a real plan, we can't yet confirm the full privacy-hardened prompt was loaded for that specific call, and the single most important safety case — a direct question about whether someone is depressed — was asked on that call but its answer was lost when the provider cleared the transcript buffer. Full account: `docs/LIVE_PROVIDER_EVIDENCE.md`. |
| SpacetimeDB coordination backend | **Real.** SpacetimeDB is the authoritative store for the shared live coordination workflow — session state, call state, plan proposals and their versions, participant responses, and confirmation — enforced by a reducer, `confirm_if_matching`, that will refuse to confirm anything unless both parties agreed to the identical activity and time on the same still-active proposal version. 21 live reducer tests exercise this, including the stale-version, mismatched-terms, and privacy-boundary cases. One thing we have *not* yet visually verified: two independent browser clients staying in sync through SpacetimeDB subscriptions — the frontend code for it exists but hasn't been watched working side-by-side. |
| Claude-powered companion chat and memory extraction | **Real.** Companion replies, memory extraction, and the live-call conversation engine all run a genuine Claude tool-use loop. A regression we found and fixed along the way: the previous keyword-matching version classified "Has he been **ok**ay?" as an acceptance because it matched "ok" as a substring — Claude correctly reads that as a worried health question and discloses nothing. |
| Twilio | Not integrated — a Cartesia-provisioned number carried the real call instead |
| SMS delivery | Not configured; confirmations go through a labeled simulated path |

We'd rather say this plainly than let a judge discover it. Every call row and
every dashboard event carries an explicit `mode`/`simulated` flag, and
`GET /api/status` exposes three honest mode flags — `voice_mode`,
`coordination_mode`, `conversation_mode` — so "is this real" is never
ambiguous in the data.

## How we built it

A lead agent froze a shared contract (`specs/CONTRACTS.md`, later extended by
`specs/CONTRACTS_V2.md`) covering the state machine, every HTTP endpoint
shape, the SpacetimeDB schema, and the conversation-engine safety properties
before implementation began. The original plan was a wide split across
consent/memory/voice/planning/frontend/docs agents working in parallel; the
tightly-coupled backend pieces were collapsed onto the lead for the same
reason twice — once in the baseline build, once again in the SpacetimeDB/
fluid-agent phase, where a first five-way fan-out was cut short by an API
session limit mid-task. The lead verified and integrated the partial output
itself and found three real integration gaps by actually running the
system: the conversation engine wasn't wired into the call path, SpacetimeDB
was unreachable from the running app despite the module itself working, and
the two stores modeled plan revisions incompatibly (one mutable row vs. one
immutable row per version), which silently broke confirmation until fixed.
Full account in `docs/MULTI_AGENT_ORCHESTRATION.md`, including the five
contract gaps a frontend-agent review caught earlier in the build.

## Challenges we ran into

- Making a confirmation gate provably impossible for a model to shortcut —
  now enforced twice: once as a pure function
  (`domain/state_machine.evaluate_confirmation`) and once as a SpacetimeDB
  reducer transaction that checks exact-version agreement from both parties
  before writing anything.
- Getting a live phone call working against a provider whose SDK has no
  `create` method for agents or calls, which meant the Managed Agent itself
  had to be created by hand in the Cartesia Playground before any of our
  code could dial it.
- Being honest about exactly what one real call does and does not prove —
  it's tempting to let "we placed a real call" imply more than it does; we
  wrote `docs/LIVE_PROVIDER_EVIDENCE.md` specifically to keep that claim
  bounded to what was actually observed.
- Keeping two independent data stores (SQLite for private data, SpacetimeDB
  for shared coordination state) from silently disagreeing about which
  version of a plan is current — found and fixed a real bug here.

## What we're proud of

The negative tests are the point, and now there are more of them in more
places. We verify that an unaccepted contact can never be called, that a
mismatched approval can never confirm — in a pure function *and* in a live
database transaction — that revoking consent halts a plan already in flight,
and that a clinical memory wrongly marked shareable still gets caught by a
second, independent guard. And we're proud that when a real call surfaced a
gap in that story — the health-disclosure case whose answer we couldn't
capture — we wrote that down as a gap instead of around it.

## What's next

Register the four webhook tools in the Cartesia Playground (their absence is
why no tool call fired on the live call). Verify the full system prompt is
actually loaded before the next live call. Re-run a live call carrying real
seeded plan context instead of smoke-test variables. Get the health-
disclosure case answered and captured on an actual call. Watch two browser
clients stay in sync through SpacetimeDB side-by-side. Full list in
`docs/FINAL_HANDOFF.md`.
