# Privacy and Safety

This describes what is actually enforced in code, and is explicit about what is
**not** claimed. Still Here is not a clinical product.

## What this is not

- **Not a crisis service.** The companion system prompt states this directly to
  the user: *"You are NOT a therapist, doctor, or crisis service, and you never
  diagnose, assess risk, or infer a clinical state."*
  (`backend/app/adapters/anthropic_adapter.py:43-45`).
- **No clinical monitoring, no crisis prediction.** Nothing in this codebase
  classifies mood, predicts risk, or tracks a clinical trajectory. The only
  "sensitivity" classification that exists is a privacy mechanism — whether a
  memory is safe to repeat to a third party, not a clinical assessment
  (`services/memory.classify_sensitivity`, `memory.py:80-86`).
- **No promise of care-team alerts.** There is no code path that notifies a
  clinician, case manager, or emergency contact based on anything the companion
  detects. The system prompt is explicit: *"Do not promise to alert anyone."*
  (`anthropic_adapter.py:58`).
- **Not therapy.** The companion is a social-reconnection tool: it talks, it
  remembers, and — only with explicit, verified, revocable consent — it helps
  coordinate plans with people the user has chosen.

## Crisis handling

When the fallback reply path (the one actually running in this build, since no
Anthropic key is configured) detects crisis language via `_CRISIS_RE` (`kill
myself`, `end my life`, `suicid*`, `hurt myself`, `want to die`, `self-harm`;
`adapters/anthropic_adapter.py:110-113`), it responds with a fixed, honest
message (`:121-127`):

> "I want to be straight with you: I'm not a crisis service, and I'm not able to
> keep you safe on my own. If you're in immediate danger, please call 911. You
> can also call or text 988, the Suicide & Crisis Lifeline, any time. I'm still
> here with you."

The same instruction is given to the real Claude path via the system prompt
(`:54-58`): surface 988/911, stay warm, never play clinician, never promise to
alert anyone. This is a template response, not a monitored or escalated one —
there is no backend code that does anything else with this detection (no log
flag routed to a human, no automatic contact of emergency services).

## What is enforced server-side (not just described in a prompt)

- **The consent gate.** `services/consent.verify_outreach()` is called
  unconditionally before any provider is touched
  (`services/voice.py:80-92`) — there is no code path to a phone call that
  skips it. See `docs/ONBOARDING.md` for the full gate.
- **The confirmation gate.** `domain.state_machine.evaluate_confirmation()` is
  the only function that can produce `PLAN_CONFIRMED`, and it requires
  identical terms from both parties plus a fresh availability check
  (`domain/state_machine.py:123-168`). No model has a tool that can confirm a
  plan (`agents/tools.py:158-161`).
- **The two memory guards.** Visibility allow-list plus a regex content guard,
  both independent, both covering the contact-facing path
  (`services/memory.shareable_context`, `memory.py:239-307`). See
  `docs/MEMORY.md`.
- **The final outbound guard.** `assert_no_sensitive_leak()` is called on every
  dynamic variable handed to the voice provider and on the confirmation SMS text
  before it's marked sent (`services/voice.py:122-125`,
  `services/plans.py:330-331`) — a last check immediately before anything leaves
  the system.
- **Quiet hours and rate caps.** Checked inside `verify_outreach()`
  (`consent.py:150-168`), per-contact, server-side, with no model-reachable
  override (`override_quiet_hours` is a backend-only kwarg used for rehearsal
  convenience, not exposed through any model tool).
- **STOP.** `handle_stop()` revokes every consent for a phone number
  immediately on receipt, independent of which patient relationship it belongs
  to (`consent.py:190-200`).

## Redaction

`services/events.redact()` (`events.py:41-64`) strips known-sensitive keys
(`phone`, `phone_e164`, `to_number`, `from_number`, `api_key`, `authorization`,
`x-api-key`, `code`, `token`, `secret`) before any event payload is persisted or
streamed, replacing the value with `***<last 4 chars>`. This runs on every event
at the single emission point (`emit()`, called from everywhere events originate)
— there is no second code path that could bypass it.

`services/voice._redact(n)` additionally redacts phone numbers to last-4 before
they're stored in the `calls.to_number_redacted` column (`voice.py:31-32`) — the
full number is never persisted in that column at all, not just redacted on
display.

`api/status` exposes only booleans and non-secret identifiers (`redacted()`,
`config.py:77-90`); it is tested directly for secret leakage
(`test_api_flow.py::test_status_leaks_no_secrets`).

## What is explicitly MOCK and labeled

- **Showtimes.** `services/scheduling.lookup_showtimes()` always returns
  `source: "mock"` with the disclaimer *"MOCK DATA - not real showtimes, nothing
  is booked or paid for"* (`scheduling.py:161-169`). Nothing books, reserves, or
  pays for anything anywhere in this codebase.
- **The demo cast.** Sam Rivera, Josh Tanaka, Maya Chen, Daniel Osei are
  fictional (`services/demo_reset.py:1-14`). Josh's phone number is set to the
  developer's own verified number (`MY_PHONE_E164`) specifically so a live demo
  call reaches a phone the developer controls.
- **Sam's approval in the solo demo.** Always `simulated: true`
  (`api/demo.py:82-150`), rendered with a permanent `SIMULATED SAM APPROVAL`
  badge on the dashboard (`Dashboard.tsx:485-488`) — never presented as a real
  person's response.

## Threat model (what this build defends against vs. does not)

**Defended against:**
- A model (Claude, or Cartesia's Managed Agent) attempting to confirm a plan,
  bypass patient approval, or disclose clinical information mid-call — blocked
  structurally (no tool access) and behaviorally (the local simulator's scripted
  refusals, `services/voice.py:357-379`).
- A contact being called without having opted in, without a verified phone, or
  outside their consented scope/hours/rate.
- A mislabelled memory row leaking clinical content to a contact (content-guard
  defense in depth).
- Replaying a webhook or tool call causing a duplicate state transition or
  duplicate notification (idempotency keys throughout).
- Secrets or phone numbers appearing in logs, events, or API responses.

**Not defended against / out of scope for this build:**
- Account takeover / session hijacking of the patient's own browser — there is
  no authentication on `/signup`, `/onboarding`, `/circle`, etc.; identity is
  carried in browser `localStorage` (`stillhere_user_id`) with no login step.
  This is acceptable for a hackathon demo, not for production.
- A malicious actor who already has a valid dashboard token or webhook secret —
  those are shared secrets (see `docs/SECURITY.md`), not per-user credentials.
- Any attack on the Cartesia or Anthropic infrastructure itself; this build
  trusts those providers' own security.
