# specs/AGENT_TOOLS.md

Implemented in `backend/app/agents/tools.py` (`TOOL_SPECS`, `HANDLERS`, `dispatch`) and
exposed to the live phone agent as webhook routes in `backend/app/api/voice.py`.

Both consumers — Claude (Anthropic tool-use format) and the Cartesia Managed Agent
(webhook tools) — funnel into the **same handlers**, so each capability has exactly one
implementation and one place where validation and consent are enforced.

## Tools available to a model

| tool | purpose | can it change state? |
|---|---|---|
| `get_support_context` | consent-filtered shared context + memory citations | no |
| `verify_consent` | is this outreach action currently permitted? | no |
| `find_free_slots` | shared availability windows | no |
| `suggest_activity` | memory-grounded proposal, honours boundaries | no |
| `lookup_showtimes` | **mock** options, `source: "mock"` | no |
| `record_friend_response` | record the contact's answer | yes — bounded |
| `request_patient_approval` | ask the patient to decide | emits event only |
| `store_preference_feedback` | persist a stated preference/boundary | creates memory |

## Tools deliberately NOT available to any model

```python
MODEL_FORBIDDEN_TOOLS = {"confirm_plan", "send_confirmation", "start_outbound_call"}
```

`dispatch()` refuses these, emits a `safety.blocked` event, and raises `ToolRefused`.
Confirming a plan, sending confirmations, and dialling a phone are backend-authorized
only. **The model has no tool that can confirm a plan or place a call.**

This is the single most important line in the design: the worst thing the agent can do
by hallucinating a tool call is record a friend's answer. It cannot commit the patient
to anything, and it cannot phone anyone.

## Bounded authority of `record_friend_response`

`"accepted"` advances only as far as `PATIENT_APPROVAL_PENDING`. It can never reach
`PLAN_CONFIRMED` — the state machine forbids the transition and the confirmation gate
would refuse anyway. `"counterproposed"` creates a new plan version and **clears both
approvals**.

## Guarantees every handler provides

1. **Typed input.** Pydantic models on the webhook routes; `input_schema` for Claude.
2. **One real event per call.** `dispatch()` emits `call.tool_invoked` with the tool
   name, status and measured `elapsed_ms`. Failures emit too, then re-raise.
3. **Idempotency.** Webhook tools key on `sha256(call_id + tool + sorted payload)` and
   return the first result on replay, so provider retries cannot double-advance state.
4. **No consent widening.** Handlers read consent; none grants it.
5. **Authentication.** Every webhook tool requires `X-StillHere-Secret`, compared with
   `secrets.compare_digest`. Missing or wrong ⇒ 401.

## Privacy boundary

`get_support_context` returns only `memory.shareable_context()` output: rows whose
visibility is `shareable_with_contact` or `public_demo_safe`, each additionally screened
by a regex content guard. It reports `withheld_private_memories` so the dashboard can
show that information was deliberately held back.

Before a provider request is sent, `voice.start_outbound_call()` runs
`memory.assert_no_sensitive_leak()` over **every** `dynamic_variables` value. A clinical
term anywhere in the outbound payload raises and the call does not happen.

## Webhook route mapping

| tool | route |
|---|---|
| `get_support_context` | `POST /api/voice/tools/get_support_context` |
| `lookup_showtimes` | `POST /api/voice/tools/lookup_showtimes` |
| `record_friend_response` | `POST /api/voice/tools/record_friend_response` |
| `request_patient_approval` | `POST /api/voice/tools/request_patient_approval` |
| call lifecycle | `POST /api/voice/webhook` |

`_call_or_404()` accepts either our `call_id` or Cartesia's `provider_call_id`, so the
agent can pass whichever identifier it holds.

Agent-side configuration (system prompt, tool bodies, headers) is in
`prompts/OUTREACH_AGENT.md`; `scripts/setup_cartesia_agent.py` prints it with your real
`PUBLIC_BASE_URL` substituted.
