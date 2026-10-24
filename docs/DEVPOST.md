# Devpost Submission Copy

## One-line pitch (locked)

> I built Still Here, an AI that makes it easier for people recovering from
> psychiatric hospitalization to reconnect with the people they love, even when
> depression makes reaching out difficult.

## What it does

Still Here is an opt-in companion for adults after a psychiatric discharge. It
talks with you, remembers what matters — who the important people in your life
are, what you used to do together, what you're comfortable sharing — and, only
with explicit, verified, revocable consent from every person involved, helps
plan small moments of reconnection: a call to an old friend, a coffee, a movie.

It is built around one hard rule: **Claude proposes, code authorizes.** No
model — not the companion, not the voice agent on a live call — ever has the
ability to confirm a plan or place an outbound call. Those actions live behind
a deterministic state machine and a consent gate that no model-reachable tool
can bypass.

## What is real vs. simulated (we want to be direct about this)

| Capability | Status |
|---|---|
| Consent gate, plan state machine, memory system with provenance and privacy guards | **Real, fully implemented, tested** — 113 backend tests + a 49-check end-to-end script, both passing against the actual running app |
| The connection planner (who to reach out to, what to propose) | **Real** — scores real stored memories and real consent state; nothing about "Josh" or "a Marvel movie" is hardcoded, it falls out of what Sam actually told the companion |
| Cartesia outbound voice calling | **Code is real**, written against Cartesia's verified live API (`POST /agents/calls`, Managed Agents). **We did not have an API key for this submission**, so no real phone call has been placed — the system honestly reports `voice_mode: "simulated"` and runs an equivalent local simulator with the identical consent gate, state machine, and confirmation logic underneath it |
| Claude-powered companion chat and memory extraction | **Code is real**, written against the Anthropic API (`claude-opus-5`). **We did not have an API key for this submission**, so every reply in this build ran a deterministic rule-based fallback, honestly labeled `reasoning_mode: "fallback"` in the response and on screen |
| SMS delivery (Twilio) | Not configured; confirmations are sent through a labeled simulated path |
| SpacetimeDB (originally planned for live state sync) | Not implemented — we used SQLite + Server-Sent Events instead, which met the actual requirement (a judge's dashboard that updates live off only real backend events) without adding a second database/runtime |

We'd rather say this plainly than let a judge discover it. The architecture is
built so that "is this real" is never ambiguous in the data: every call row and
every dashboard event carries an explicit `mode`/`simulated` flag, and the
dashboard renders a `SIMULATED` badge directly off it.

## How we built it

A lead agent froze a shared contract (`specs/CONTRACTS.md`) covering the state
machine, every HTTP endpoint shape, the visibility ladder, and the event
vocabulary before any implementation began. The original plan was a six-way
split across consent/memory/voice/planning/frontend/docs agents working in
parallel. In practice, the consent, voice, and planning code turned out to be
too tightly coupled to split safely — they all share the same invariants
(nothing bypasses the consent gate, nothing confirms without identical
double-approval) — so the lead built that entire backend critical path
directly, while a frontend agent and documentation agents worked in parallel
against the frozen contract. The frontend agent's review of that contract
caught five real gaps (missing circle/contacts list endpoints, the fact that
browser `EventSource` can't send custom headers so SSE auth needed a
query-param fallback, an under-specified plan rationale field, and an
undocumented status endpoint shape) — all fixed before this submission. Full
account in `docs/MULTI_AGENT_ORCHESTRATION.md`.

## Challenges we ran into

- Designing a confirmation gate that is provably impossible for a model to
  shortcut, not just prompted against — ended up as a pure-function state
  machine (`domain/state_machine.evaluate_confirmation`) that requires both
  parties to have agreed to *the exact same* activity and start time, with no
  tool exposed to any model that can write the confirmed state.
- Making sure a clinical fact that's mislabelled as shareable still can't
  reach a contact — solved with two independent guards (a visibility
  allow-list and a separate regex content filter) that don't trust each other.
- Being honest about provider integration without an API key in hand for
  either Cartesia or Anthropic: every adapter has a deterministic, clearly
  labeled fallback that does real work (not a stub), so the demo is still
  meaningfully functional and the labeling is never misleading about what ran.

## What we're proud of

The negative tests are the point. We didn't just verify the happy path — we
verified that an unaccepted contact can never be called, that a mismatched
approval can never confirm, that revoking consent halts a plan already in
flight, and that a clinical memory wrongly marked shareable still gets caught
by a second, independent guard. All of that is executable and currently
passing, not aspirational.

## What's next

Load the real API keys and run the exact same test suite and demo path
against them — the code doesn't change, only the configuration does. Beyond
that: a real authentication system for the patient's own session, TLS and a
production datastore for anything beyond a hackathon demo, and SMS delivery
via Twilio so confirmations aren't limited to the simulated path.
