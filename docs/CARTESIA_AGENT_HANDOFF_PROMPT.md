# Handoff prompt — register Still Here's webhook tools in Cartesia

Give everything below to an AI that has access to the Cartesia account (or
browser access to play.cartesia.ai). It contains every value needed. Copy from
`--- BEGIN PROMPT ---` to `--- END PROMPT ---`.

Regenerate this file whenever the tunnel URL changes:
`python scripts/print_tools.py` prints the live values.

---

--- BEGIN PROMPT ---

You have access to my Cartesia account. I need you to finish configuring one
Managed Agent by registering five webhook tools. Everything else about the
agent is already set up correctly — do not change the prompt, the voice, or the
phone number.

## Context: what is wrong and why it matters

The agent can hold a phone conversation, but it has **no tools**, so it has no
way to send anything back to my server. Right now a call connects, the agent
talks sensibly, the person on the phone says "yes, that sounds good" — and that
fact dies inside Cartesia. My backend never learns it. Nothing is recorded, no
plan advances, nothing appears on my dashboard.

Confirmed state: `webhook_id` is `null` and no tools are registered. My server
logged **zero** inbound requests from Cartesia during a real completed call.

Registering these five tools is the only remaining step.

## The agent

| field | value |
|---|---|
| Agent ID | `agent_5p1Cq4cBdt1GvXb4UULRdU` |
| Agent name | `still-here` |
| Phone number | `+18458427399` (number ID `ap_s462qHtr163qSGDpyMcC1w`) |
| `is_managed` | `true` |
| `is_live` | `true` |
| API version header | `Cartesia-Version: 2026-08-14` |

Do **not** create a new agent. Do not detach the phone number. Do not modify
`llm_system_prompt` (11,423 chars) or `llm_introduce` (265 chars) — both are
already correct and contain privacy rules that must not be edited.

## Shared configuration for all five tools

- Method: **POST**
- Type: **webhook tool** (a request Cartesia sends to my server — not a client
  tool and not a system tool)
- Required header on every one:
  `X-StillHere-Secret: dev-only-change-me`
  Without this header my server returns **401** and the tool silently fails.
- Base URL: `https://midwest-york-smaller-forth.trycloudflare.com`
- Content type: `application/json`
- Execution mode: **immediate** (not async) — the agent needs the result inside
  the same conversational turn.

`{{session_id}}` and `{{caller_number}}` below are Cartesia template variables.
If your Playground uses different names for "this call's identifier" and "the
caller's phone number", substitute the correct ones and tell me what they were.

---

### Tool 1 — `record_friend_response` (MOST IMPORTANT)

This is the one that makes the product work. Without it, agreeing on the phone
has no effect anywhere.

- URL: `https://midwest-york-smaller-forth.trycloudflare.com/api/voice/tools/record_friend_response`
- Description for the agent:
  > Record the friend's answer about the proposed plan. Call this as soon as
  > they accept, decline, or suggest different terms. Required — nothing is
  > recorded unless you call this.
- Parameters:
  | name | type | required | notes |
  |---|---|---|---|
  | `call_id` | string | yes | pass `{{session_id}}` |
  | `response` | string | yes | **must be exactly** `accepted`, `declined`, or `counterproposed` |
  | `activity` | string | no | only when they propose a different activity, e.g. `Coffee` |
  | `starts_at` | string | no | only when they propose a different time; ISO 8601, e.g. `2026-11-02T11:00:00+00:00` |
  | `note` | string | no | short free-text context |
- Returns: `{"plan_id": "...", "state": "..."}`

Any `response` value other than those three exact strings is rejected with 422.

### Tool 2 — `get_support_context`

- URL: `https://midwest-york-smaller-forth.trycloudflare.com/api/voice/tools/get_support_context`
- Description for the agent:
  > Look up what you are permitted to say about the person you are calling on
  > behalf of. Call this when asked "why me?", "why that activity?", or "what
  > has he been up to?". Only ever use what this returns.
- Parameters:
  | name | type | required | notes |
  |---|---|---|---|
  | `call_id` | string | yes | pass `{{session_id}}` |
- Returns `shared_interests`, `relationship`, `boundaries`, `citations`, and
  `withheld_private_memories`.

### Tool 3 — `request_patient_approval`

- URL: `https://midwest-york-smaller-forth.trycloudflare.com/api/voice/tools/request_patient_approval`
- Description for the agent:
  > Ask the person you are calling on behalf of to approve the plan. Call this
  > after the friend accepts. You cannot confirm a plan yourself.
- Parameters:
  | name | type | required | notes |
  |---|---|---|---|
  | `call_id` | string | yes | pass `{{session_id}}` |

### Tool 4 — `lookup_showtimes`

- URL: `https://midwest-york-smaller-forth.trycloudflare.com/api/voice/tools/lookup_showtimes`
- Description for the agent:
  > Look up illustrative options for an activity. Results are illustrative
  > only — never describe anything as booked or reserved.
- Parameters:
  | name | type | required | notes |
  |---|---|---|---|
  | `movie` | string | yes | activity or title, e.g. `Marvel` or `coffee` |
  | `date` | string | no | ISO date |

### Tool 5 — `identify_caller` (inbound only)

The same number receives calls. On an inbound call the agent does not know who
is ringing until it asks.

- URL: `https://midwest-york-smaller-forth.trycloudflare.com/api/inbound/identify`
- Description for the agent:
  > When you ANSWER an incoming call, call this first to find out who is
  > calling and what you are allowed to discuss with them. Use the `greeting`
  > it returns. Never guess a name.
- Parameters:
  | name | type | required | notes |
  |---|---|---|---|
  | `from_number` | string | yes | pass `{{caller_number}}` — the caller's number in E.164 |
  | `call_id` | string | no | pass `{{session_id}}` |
- Returns `known_caller`, `role`, `first_name`, `greeting`, `context`, and
  `may_discuss_patient_health`. **Honour `may_discuss_patient_health: false`
  absolutely** — it is false for every friend.

---

## Also, if the Playground offers a call-status webhook

Subscribe the agent to call lifecycle events:

- URL: `https://midwest-york-smaller-forth.trycloudflare.com/api/voice/webhook`
- Method: POST
- Header: `X-StillHere-Secret: dev-only-change-me`
- Events: ringing, answered, completed, failed, no-answer, busy, voicemail —
  whatever the subscription offers.

If creating this requires a `secret` field, use `dev-only-change-me`.

## Verify before you report back

1. Re-read the agent and confirm five tools are attached.
2. Confirm every tool carries the `X-StillHere-Secret` header. A tool without
   it returns 401 and fails silently, which is indistinguishable from having no
   tool at all.
3. If you can trigger a test call, confirm my server receives POSTs to
   `/api/voice/tools/*` during it.

## Report back with

- Which of the five registered successfully, and which failed with what error.
- Whether you did it via API or the web UI. If via API, the **exact endpoint
  and request body that worked** — I probed `GET/POST /agents/{id}/tools`,
  `/agents/{id}/webhook-tools` and `PATCH /agents/{id}` with a `tools` array
  and all returned 404 or `Unrecognized key: "tools"`, so I concluded it is
  UI-only. If you find a working API route, that is valuable and I want it.
- The real names of the template variables for call id and caller number, if
  they differ from `{{session_id}}` / `{{caller_number}}`.
- Anything in the Playground that contradicts the above.

Do not change the system prompt, the welcome message, the voice, the phone
number assignment, or create any new agent. Only add tools.

--- END PROMPT ---

---

## If the tunnel URL changed

Every URL above embeds `https://midwest-york-smaller-forth.trycloudflare.com`.
Cloudflare quick tunnels are ephemeral, so this dies on restart. Regenerate
with:

```bash
python go.py up          # starts a tunnel, writes PUBLIC_BASE_URL into .env
python scripts/print_tools.py   # prints the five tools with the new URL
```

Then hand over the refreshed values. A permanent URL would avoid this — a named
Cloudflare tunnel or any stable public host — and is worth doing before a real
demo.
