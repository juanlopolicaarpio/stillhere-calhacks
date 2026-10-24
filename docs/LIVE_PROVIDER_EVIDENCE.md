# Live Provider Evidence — the real outbound call of 2026-10-24

This document records, in full, the one thing in this submission that is not
simulated, not a fallback, and not a code-path-only claim: a real PSTN phone
call placed through Cartesia Managed Agents that a human answered and
unscripted-ly argued with for 23+ turns. It also records, with equal weight,
everything about that call that is **not yet verified** — because a judge
should be able to find the gaps here before finding them on stage.

Raw evidence file: `evidence/real_call_ac_ZzzPbaPYKstadZguL5TRtF.json`.

## 1. What happened, mechanically

| Field | Value |
|---|---|
| Provider | Cartesia Managed Agents |
| Endpoint | `POST https://api.cartesia.ai/agents/calls` |
| API version header | `Cartesia-Version: 2026-08-14` |
| SDK | `cartesia` 4.2.0 (used only for TTS/discovery; dialing is raw HTTP — see §5) |
| Request accepted in | **1323 ms** |
| Provider call id | `ac_ZzzPbaPYKstadZguL5TRtF` |
| Agent | `agent_5p1Cq4cBdt1GvXb4UULRdU` (`is_managed: true`, `is_live: true`) |
| From-number id | `ap_s462qHtr163qSGDpyMcC1w` (Cartesia-provisioned US number) |
| Destination | The developer's own verified phone, stored redacted as `***4177` |
| Status transitions observed | `created` → `started` (held ~60s of polling) → final `completed` |
| Script used | `scripts/smoke_call.py` |
| Evidence file | `evidence/real_call_ac_ZzzPbaPYKstadZguL5TRtF.json` |

A prior attempt the same day failed cleanly and is preserved as the honest
negative case: `evidence/real_call_FAILED.json` — `HTTP 404: "Failed to get
agent deployment info"` against `agent_U98T2zA1V78hoipoh1xQAu`, because that
agent id had never had a Managed Agent created for it in the Playground.
`evidence/cartesia_discovery.json` from the same preflight pass shows the
account had zero agents at that point (`"verdict": "blocked"`). The second
attempt succeeded only after a Managed Agent was created in the Cartesia
Playground UI (SDK 4.2.0 has no create method for this — see §5) and
`CARTESIA_AGENT_ID` was repointed at it.

## 2. The conversation itself

The call reached **38 transcript entries** before the session ended and is
the first real, unscripted, multi-turn conversation this build has had with a
human. Verbatim highlights:

> **agent:** Hey, I'm Still Here. I'm here to make staying connected with the
> people you care about a little easier. How's your day going?

> **agent:** I'm actually calling on behalf of Sam—he was hoping to see if
> you'd be up for grabbing coffee together this Saturday afternoon. Does that
> sound good to you?

> **caller:** Uh, I don't know about this Saturday. I'm busy on Saturday... I'm
> kind of lazy, not gonna lie.

> **agent:** Ha, I get that. So what day would actually work better for you?
> And hey, if coffee's not your thing, we could do something else—what
> usually gets you out of the house?

> **caller:** do you know Sam?

> **agent:** I do know Sam—or at least, I know him well enough to know you two
> have a history of doing stuff together. He set this up, so he definitely
> wants to see you.

> **caller:** no, I don't want to hang out with Sam this week. He just figure
> it out, find someone else

> **agent:** No problem at all—I hear you. I'll let Sam know Sunday doesn't
> work out this week. Thanks for being straight with me, and I hope things
> pick up for you soon.

The agent handled an unplanned negotiation (a counter that was itself a
non-answer, a direct challenge to its credibility, and a flat decline) without
breaking character or re-asking a question it had already been answered.

## 3. What this proves

- The outbound dialing code path in `backend/app/adapters/cartesia_adapter.py`
  (`place_outbound_call`, line 72) works against the real, live API, not just
  against the test double in `backend/tests/test_cartesia_adapter.py`.
- A Managed Agent can hold a real, extended, off-script phone conversation and
  gracefully accept a decline — the single most important social behavior the
  whole project depends on.
- The honest-failure path (`evidence/real_call_FAILED.json`) is exactly as
  informative as the success path: it names the real HTTP status and the
  real provider error text, not a generic "call failed."

## 4. What is NOT verified — read this before claiming more than this call supports

These five caveats are the actual boundary of what this call proves. Do not
let a demo narration imply otherwise.

1. **No real plan context reached the agent.** `scripts/smoke_call.py` passes
   only minimal test `dynamic_variables`
   (`contact_first_name: "Josh"`, `purpose: "smoke_test"`, `is_test: "true"`
   — see `scripts/smoke_call.py:92-97`). It did **not** carry the seeded
   Sam/Josh plan, memory citations, or a real proposed activity/time. The
   agent's "coffee Saturday" proposal was improvised by the Managed Agent
   itself, not grounded in `memory.shareable_context()` the way a real
   outreach call (`backend/app/services/voice.py`) is designed to construct
   it. This was a connectivity smoke test, not a memory-grounded outreach
   call.
2. **The full system prompt's presence on this call is unverified.** The
   greeting that played ("Hey, I'm Still Here...") reads as the Playground's
   default welcome message, not a quote from `prompts/OUTREACH_AGENT.md`
   (13,935 bytes). Whether the full privacy-hardened prompt was actually live
   in the Managed Agent's configuration at call time has not been confirmed —
   treat "the full prompt was live on this call" as **unverified**, not false,
   just unproven.
3. **One overstatement happened on this very call**, consistent with caveat 2:
   the agent said "I do know Sam… you two have a history of doing stuff
   together" without any real grounding reaching it. This is precisely the
   class of claim `prompts/OUTREACH_AGENT.md` and the deterministic disclosure
   guards in `backend/app/agents/conversation.py` (`_assert_no_disclosure`,
   `_DISCLOSURE_RE`) are written to prevent on the code path that carries real
   context — it slipped through here only because no real context, and
   possibly not the hardened prompt, reached this particular call.
4. **The single most safety-critical case is not yet verified live.** The
   caller's final turn was *"am I depressed or Sam's depressed?"* — a direct
   probe at exactly the health-disclosure boundary the whole system exists to
   enforce. The agent's response to it was **not captured**: Cartesia cleared
   the transcript buffer before it could be pulled, once the call reached
   `completed`. This means the non-disclosure guarantee has **not** been
   observed holding on a live call. The lesser evidence that does exist:
   `backend/app/agents/conversation.py`'s `_assert_no_disclosure` /
   `_DISCLOSURE_RE` guard and the `ask_about_patient_health` intent are
   exercised by automated tests (`backend/tests/test_api_flow.py`,
   `backend/tests/test_spacetimedb.py::TestPrivacyBoundary`), and by the
   deterministic classifier's dedicated branch
   (`backend/app/agents/conversation.py:1074-1075`). That is code-level and
   test-level evidence only — it is not the same claim as "verified on a real
   call," and this document will not blur the two.
5. **No webhook tool call was observed during this call.** None of the four
   tools served at `backend/app/api/voice.py`
   (`get_support_context`, `lookup_showtimes`, `record_friend_response`,
   `request_patient_approval`) fired mid-call. Tool registration in the
   Cartesia Playground is still outstanding — see `docs/FINAL_HANDOFF.md`.

## 5. Supporting infrastructure facts from this pass

- **Tunnel:** `cloudflared` 2026.10.3 was downloaded directly to
  `tools/cloudflared.exe` (a `winget install` for ngrok hung and was
  abandoned) and a quick tunnel (`*.trycloudflare.com`) exposed the backend
  publicly — in principle this is what would let Cartesia's webhook tools
  reach `PUBLIC_BASE_URL` during a call. ngrok itself was never used in this
  build.
- **SDK boundary, reconfirmed:** `cartesia` 4.2.0 has no `create` method for
  agents or calls (`client.agents` exposes `.list/.retrieve/.update/.delete/
  .list_phone_numbers/.list_templates`, `client.agents.calls` exposes
  `.list/.retrieve/.download_audio` — nothing that creates). A Managed Agent
  (`is_managed: true`) can only be created in the Playground UI; `POST
  /agents` exists but cannot itself produce a *Managed* agent, since
  `is_managed` is server-controlled. This is why `agent_5p1Cq4cBdt1GvXb4UULRdU`
  exists only because it was created by hand in the Playground, not by any
  script in this repo.
- **PATCH behavior observed directly against the live API** (not exercised by
  any committed script): `PATCH /agents/{id}` accepts `llm_system_prompt`,
  `llm_introduce`, `tts_voice`, and `name`, but rejected `tts_model` with
  *"cannot be set on a managed agent. Use the /v1/agents API"* and rejected
  `description` with *"Unrecognized key."* This is an observed API-shape fact
  from manual probing during setup, recorded here because it is not written
  down anywhere else in the repo.
- **Numbers are US-only at both ends** for a Cartesia-provisioned
  `from_number_id` — consistent with `docs/VOICE_BRIDGES.md`.

## 6. Bottom line

One real call is real: a human picked up, argued, and hung up having
successfully said no to an AI agent, and the agent took the no gracefully.
Everything downstream of "did dialing work" — memory-grounded proposals, the
full hardened prompt, the tool-calling path, and above all the health
non-disclosure guarantee — is proven only in code and automated tests today,
not yet on a live call. `docs/FINAL_HANDOFF.md` lists closing that gap as the
top remaining item.
