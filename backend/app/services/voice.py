"""Outbound voice. The ONLY way to reach a contact by phone.

`start_outbound_call()` enforces, in order, before any provider is touched:
  1. E.164 validation
  2. consent (`consent.verify_outreach`) - opt-in, verified phone, quiet hours, rate cap
  3. idempotency (no duplicate dials for the same key)
Only then does it either place a REAL Cartesia call or open a SIMULATED session,
and it records which one happened in `calls.mode`.

The context handed to the provider comes exclusively from
`memory.shareable_context()`, so private clinical memory cannot reach a friend.
"""
from __future__ import annotations

import json
import re

from typing import Any

from ..adapters import cartesia_adapter as ca
from ..config import get_settings
from ..db import ex, new_id, now_iso, q1
from ..domain.state_machine import PlanState
from . import consent as consent_svc
from . import events, memory, plans, scheduling


class VoiceError(Exception):
    pass


def _redact(n: str) -> str:
    return f"***{n[-4:]}" if n and len(n) >= 4 else "***"


def _speakable(text: str, patient_first: str, contact_first: str) -> str:
    """Rewrite a stored memory into something natural to say ON the call.

    Memories are stored in the third person for auditability
    ("Sam and Josh watch Marvel movies together"), which reads wrong when the
    agent speaks it to Josh. This converts the pair into second person
    ("you and Sam watch Marvel movies together") without touching the stored
    row, so provenance stays intact while the spoken line sounds human.
    """
    out = (text or "").strip()
    if not out:
        return out
    for a, b in (
        (f"{patient_first} and {contact_first}", f"you and {patient_first}"),
        (f"{contact_first} and {patient_first}", f"you and {patient_first}"),
    ):
        out = out.replace(a, b)
    # "Josh is Sam's best friend" -> "you are Sam's best friend"
    if out.startswith(f"{contact_first} is "):
        out = "you are " + out[len(contact_first) + 4:]
    return out


def _quiet_gap(patient_id: str, contact_id: str) -> tuple[str, int | None]:
    """How long since these two were actually in touch.

    Returns a speakable phrase and the raw day count. The phrase is derived
    from `relationships.last_contact_at`, so "it has been a while" is a fact the
    agent reads rather than a sentiment it invents. Returns ("", None) when we
    genuinely do not know, and the opening then omits the claim entirely.
    """
    from datetime import datetime, timezone

    row = q1(
        "SELECT last_contact_at FROM relationships WHERE patient_id=? AND contact_id=?",
        (patient_id, contact_id),
    )
    raw = row["last_contact_at"] if row else None
    if not raw:
        return "", None
    try:
        then = datetime.fromisoformat(raw)
    except (TypeError, ValueError):
        return "", None
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    days = (datetime.now(timezone.utc) - then).days
    if days < 7:
        return "", days          # not a gap worth remarking on
    if days < 14:
        return "a couple of weeks", days
    if days < 60:
        return f"about {round(days / 7)} weeks", days
    return f"about {round(days / 30)} months", days


def _pick_shared_interest(
    interests: list[str], activity: str, patient_first: str, contact_first: str
) -> str:
    """Choose the ONE shared memory that actually justifies this proposal.

    Reading every stored interest aloud sounds like a database dump and buries
    the reason for the call. Prefer the memory whose words overlap the proposed
    activity, so the opening line explains itself.
    """
    if not interests:
        return ""
    act_words = {w for w in re.split(r"\W+", (activity or "").lower()) if len(w) > 3}
    best = max(
        interests,
        key=lambda m: len(act_words & {
            w for w in re.split(r"\W+", m.lower()) if len(w) > 3
        }),
    )
    return _noun_phrase(best, patient_first, contact_first)


# Verb phrases that open a stored memory, longest first so "used to watch"
# wins over "watch".
_LEAD_VERBS = (
    "used to go to ", "used to watch ", "used to grab ", "used to get ",
    "used to do ", "like to ", "go to ", "watch ", "grab ", "get ", "do ",
)


def _noun_phrase(memory_text: str, patient_first: str, contact_first: str) -> str:
    """Reduce a stored memory to the THING they did together.

    "Sam and Josh watch Marvel movies together on opening weekend"
        -> "Marvel movies together on opening weekend"

    The opening line already supplies its own subject and verb ("You two used
    to do ..."), so injecting a complete sentence produced
    "used to do you and Sam watch Marvel movies". Stripping the memory's own
    subject and verb leaves a fragment that slots in cleanly.
    """
    out = (memory_text or "").strip()
    if not out:
        return out
    # Drop the subject, in either name order.
    for subj in (
        f"{patient_first} and {contact_first} ",
        f"{contact_first} and {patient_first} ",
        f"you and {patient_first} ",
    ):
        if out.lower().startswith(subj.lower()):
            out = out[len(subj):]
            break
    low = out.lower()
    for verb in _LEAD_VERBS:
        if low.startswith(verb):
            out = out[len(verb):]
            break
    return out.strip()


def start_outbound_call(
    patient_id: str,
    contact_id: str,
    plan_id: str,
    *,
    session_id: str,
    idempotency_key: str,
    force_mode: str | None = None,
    override_quiet_hours: bool = False,
) -> dict[str, Any]:
    s = get_settings()

    # --- idempotency: never dial the same person twice for one key ---
    existing = q1("SELECT * FROM calls WHERE idempotency_key=?", (idempotency_key,))
    if existing is not None:
        events.emit(
            "call.requested",
            "Duplicate call suppressed (idempotency key already used)",
            plan_id=plan_id,
            call_id=existing["id"],
            session_id=session_id,
            payload={"idempotency_key": idempotency_key, "suppressed": True},
        )
        return {
            "call_id": existing["id"],
            "mode": existing["mode"],
            "status": existing["status"],
            "provider_call_id": existing["provider_call_id"],
            "duplicate": True,
        }

    contact = q1("SELECT * FROM users WHERE id=?", (contact_id,))
    if contact is None:
        raise VoiceError(f"unknown contact {contact_id!r}")

    # --- 1. phone validation ---
    try:
        to_number = consent_svc.validate_e164(contact["phone_e164"] or "")
    except consent_svc.ConsentError as e:
        events.emit(
            "call.failed", f"Invalid contact phone number: {e}",
            plan_id=plan_id, session_id=session_id, payload={"reason": str(e)},
        )
        raise VoiceError(str(e)) from e

    # --- 2. consent gate (hard) ---
    decision = consent_svc.verify_outreach(
        patient_id, contact_id, "voice_call", override_quiet_hours=override_quiet_hours
    )
    if not decision.allowed:
        events.emit(
            "consent.denied",
            f"Outbound call BLOCKED: {decision.reason}",
            plan_id=plan_id,
            session_id=session_id,
            payload={"reason": decision.reason, "contact_id": contact_id},
        )
        raise VoiceError(f"consent denied: {decision.reason}")

    events.emit(
        "consent.verified",
        f"Consent verified for voice_call to {contact['display_name']}",
        plan_id=plan_id,
        session_id=session_id,
        payload=decision.scope,
    )

    # --- 3. build contact-safe context (private memory cannot pass this) ---
    ctx = memory.shareable_context(
        patient_id, contact_id, decision.scope.get("allowed_fields", [])
    )
    plan = plans.get_plan(plan_id)
    patient = q1("SELECT * FROM users WHERE id=?", (patient_id,))

    contact_first = contact["display_name"].split()[0]
    patient_first = (patient["display_name"] if patient else "your friend").split()[0]

    dynamic_variables = {
        "contact_first_name": contact_first,
        "patient_first_name": patient_first,
        "proposed_activity": plan["activity"],
        "proposed_time_human": _humanize(plan["starts_at"]),
        "proposed_location": plan["location"] or "",
        # Speech-ready, second-person, and narrowed to the ONE memory that
        # justifies this proposal. See _pick_shared_interest().
        "shared_interests": _pick_shared_interest(
            ctx["shared_interests"], plan["activity"],
            patient_first, contact_first,
        ),
        "relationship": _speakable(
            ctx["relationship"] or "a friend", patient_first, contact_first
        ),
        "boundaries": _speakable(
            "; ".join(ctx["boundaries"][:1]), patient_first, contact_first
        ),
        # Real computed gap since they were last in touch, so the agent's own
        # reason for calling is grounded. Empty string when unknown.
        "time_since_contact": _quiet_gap(patient_id, contact_id)[0],
        "plan_id": plan_id,
        "session_id": session_id,
    }

    # Defence in depth: nothing clinical may enter the provider payload.
    for k, v in dynamic_variables.items():
        if isinstance(v, str) and v:
            memory.assert_no_sensitive_leak(v)

    mode = force_mode or s.voice_mode()
    call_id = new_id("call")

    ex(
        "INSERT INTO calls (id, plan_id, contact_id, session_id, provider,"
        " to_number_redacted, status, mode, idempotency_key, requested_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            call_id, plan_id, contact_id, session_id,
            "cartesia" if mode == "cartesia_pstn" else "simulated",
            _redact(to_number), "requested", mode, idempotency_key, now_iso(),
        ),
    )
    consent_svc.record_outreach(patient_id, contact_id, "voice_call")

    events.emit(
        "call.requested",
        f"Outbound call requested to {contact['display_name']} "
        f"({'REAL PSTN via Cartesia' if mode == 'cartesia_pstn' else 'LOCAL SIMULATOR'})",
        plan_id=plan_id,
        call_id=call_id,
        session_id=session_id,
        payload={
            "mode": mode,
            "to_number": to_number,  # redacted by events.redact()
            "memory_citations": [c["memory_id"] for c in ctx["citations"]],
            "withheld_memories": ctx["blocked_count"],
        },
        simulated=(mode != "cartesia_pstn"),
    )
    events.emit(
        "memory.retrieved",
        f"{len(ctx['citations'])} shareable memories loaded for the call; "
        f"{ctx['withheld_by_visibility']} withheld as private"
        + (
            f"; {ctx['blocked_by_content_guard']} blocked by the content guard"
            if ctx["blocked_by_content_guard"] else ""
        ),
        plan_id=plan_id,
        call_id=call_id,
        session_id=session_id,
        payload={
            "citations": ctx["citations"],
            "withheld_total": ctx["blocked_count"],
            "withheld_by_visibility": ctx["withheld_by_visibility"],
            "blocked_by_content_guard": ctx["blocked_by_content_guard"],
        },
    )

    plans.set_state(
        plan_id, PlanState.CONTACT_IN_PROGRESS,
        reason=f"calling via {mode}", session_id=session_id,
        simulated=(mode != "cartesia_pstn"),
    )

    # --- 4. dial ---
    if mode == "cartesia_pstn":
        return _dial_real(call_id, to_number, dynamic_variables, plan_id, session_id)
    return _open_simulated(call_id, plan_id, session_id, dynamic_variables)


def _dial_real(
    call_id: str,
    to_number: str,
    dynamic_variables: dict[str, Any],
    plan_id: str,
    session_id: str,
) -> dict[str, Any]:
    try:
        result = ca.place_outbound_call(to_number, dynamic_variables=dynamic_variables)
    except ca.CartesiaNotConfigured as e:
        ex("UPDATE calls SET status='failed', error=? WHERE id=?", (str(e), call_id))
        events.emit(
            "call.failed", f"Cartesia not configured: {e}",
            plan_id=plan_id, call_id=call_id, session_id=session_id,
            payload={"reason": str(e)},
        )
        raise VoiceError(str(e)) from e

    if not result.ok:
        ex(
            "UPDATE calls SET status='failed', error=?, raw_provider_response=? WHERE id=?",
            (result.error, json.dumps(result.raw)[:4000], call_id),
        )
        events.emit(
            "call.failed",
            f"Provider rejected the call: {result.error}",
            plan_id=plan_id, call_id=call_id, session_id=session_id,
            payload={"error": result.error, "http_status": result.http_status},
        )
        return {
            "call_id": call_id, "mode": "cartesia_pstn", "status": "failed",
            "error": result.error, "provider_call_id": None,
        }

    ex(
        "UPDATE calls SET status='ringing', provider_call_id=?, raw_provider_response=?"
        " WHERE id=?",
        (result.provider_call_id, json.dumps(result.raw)[:4000], call_id),
    )
    events.emit(
        "call.accepted_by_provider",
        f"Cartesia accepted the call (provider id {result.provider_call_id})",
        plan_id=plan_id, call_id=call_id, session_id=session_id,
        payload={"provider_call_id": result.provider_call_id, "real_pstn": True},
    )
    return {
        "call_id": call_id, "mode": "cartesia_pstn", "status": "ringing",
        "provider_call_id": result.provider_call_id, "error": None,
    }


# --------------------------------------------------------------------------
# Local simulator - the honest fallback, and the scripted e2e test adapter
# --------------------------------------------------------------------------

# Must stay consistent with the Cartesia agent's `llm_introduce` (see
# prompts/OUTREACH_AGENT.md). Still Here opens with its OWN observation - the
# computed quiet gap, the real shared free time, and the one stored memory that
# justifies the proposal. It never says "on behalf of" or "Sam set this up":
# that framing makes it sound like the patient outsourced caring about his
# friend, which is the opposite of the point.
OPENING_TEMPLATE = (
    "Hey {contact_first_name}, it's Still Here. It's been {time_since_contact} "
    "since you and {patient_first_name} were in the same room, and I saw you're "
    "both free {proposed_time_human}. You two used to do {shared_interests}. "
    "Want me to put something together?"
)

#: Used when `relationships.last_contact_at` is unknown, so the agent never
#: asserts a gap it cannot compute.
OPENING_TEMPLATE_NO_GAP = (
    "Hey {contact_first_name}, it's Still Here. I saw you and "
    "{patient_first_name} are both free {proposed_time_human}, and you two used "
    "to do {shared_interests}. Want me to put something together?"
)


def _humanize(iso: str) -> str:
    """'2026-10-25T19:10:00' -> 'on Sunday at 7:10 PM'. Platform-portable:
    %-I (glibc) and %#I (Windows) are both non-standard, so hour is formatted
    by hand.
    """
    from datetime import datetime

    try:
        d = datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return iso
    hour = d.hour % 12 or 12
    ampm = "AM" if d.hour < 12 else "PM"
    return f"on {d.strftime('%A')} at {hour}:{d.minute:02d} {ampm}"


def _open_simulated(
    call_id: str, plan_id: str, session_id: str, dv: dict[str, Any]
) -> dict[str, Any]:
    filled = {
        **dv,
        "shared_interests": dv.get("shared_interests") or "the same things",
    }
    template = (
        OPENING_TEMPLATE if filled.get("time_since_contact")
        else OPENING_TEMPLATE_NO_GAP
    )
    opening = template.format(**filled)
    ex(
        "UPDATE calls SET status='answered', answered_at=? WHERE id=?",
        (now_iso(), call_id),
    )
    events.emit(
        "call.answered",
        "SIMULATED call session opened (no real phone call was placed)",
        plan_id=plan_id, call_id=call_id, session_id=session_id,
        payload={"mode": "simulated"}, simulated=True,
    )
    events.emit(
        "call.turn", f"agent: {opening}",
        plan_id=plan_id, call_id=call_id, session_id=session_id,
        payload={"speaker": "agent", "text": opening}, simulated=True,
    )
    return {
        "call_id": call_id, "mode": "simulated", "status": "answered",
        "provider_call_id": None, "opening": opening, "error": None,
    }


def simulate_turn(call_id: str, text: str) -> dict[str, Any]:
    """Handle one conversational turn.

    Interpretation is delegated to `agents.conversation.interpret_turn()`, which
    uses Claude with tools when a key is configured and a labelled deterministic
    classifier otherwise. THIS function owns everything authoritative: it maps
    the returned intent onto real state transitions.

    That split is the whole safety design. The model decides what the caller
    *meant*; this code decides what the system is allowed to *do*. The model
    cannot record an approval, confirm a plan, or dial anyone - it has no tool
    for any of those (see `agents.tools.MODEL_FORBIDDEN_TOOLS`).

    Mirrors the branch behaviour the Managed Agent prompt implements over a real
    phone, so the same state changes are exercised in tests and in the simulator.
    """
    from ..agents import conversation

    call = q1("SELECT * FROM calls WHERE id=?", (call_id,))
    if call is None:
        raise VoiceError(f"unknown call {call_id!r}")
    plan_id = call["plan_id"]
    session_id = call["session_id"]
    contact_id = call["contact_id"]
    plan = plans.get_plan(plan_id)

    events.emit(
        "call.turn", f"contact: {text}",
        plan_id=plan_id, call_id=call_id, session_id=session_id,
        payload={"speaker": "contact", "text": text}, simulated=True,
    )

    result = conversation.interpret_turn(
        text,
        call_id=call_id,
        conversation=conversation.history_for_call(call_id),
        context=conversation.context_for_call(call_id),
    )
    intent = result.intent
    slots = result.slots or {}

    events.emit(
        "conversation.interpreted",
        f"intent={intent} ({result.mode})",
        plan_id=plan_id, call_id=call_id, session_id=session_id,
        payload={
            "intent": intent,
            "slots": slots,
            "mode": result.mode,
            "tools": result.tool_names,
            "needs_clarification": result.needs_clarification,
        },
        simulated=True,
    )

    def _present(activity: str | None) -> str | None:
        """Tidy a spoken activity phrase for display.

        The engine extracts activities from natural speech, so it yields
        "coffee" where a plan label should read "Coffee" - this string is shown
        on the dashboard and in the confirmation message. Leading articles are
        preserved ("a Marvel movie") since they read correctly in a sentence.
        """
        if not activity:
            return activity
        a = activity.strip()
        if not a or a.split()[0].lower() in {"a", "an", "the"}:
            return a
        return a[0].upper() + a[1:]

    def _revise(activity=None, starts_at=None, reason="contact proposed different terms"):
        """Record a counterproposal, re-plan, and stay on the call."""
        activity = _present(activity)
        plans.record_response(
            plan_id, contact_id, "counterproposed",
            activity=activity, starts_at=starts_at,
            simulated=True, session_id=session_id,
        )
        plans.set_state(plan_id, PlanState.FRIEND_COUNTERPROPOSED,
                        reason=reason, session_id=session_id, simulated=True)
        plans.counterpropose(plan_id, activity=activity, starts_at=starts_at,
                             session_id=session_id)
        events.emit(
            "approval.invalidated",
            "Terms changed, so prior approvals were cleared",
            plan_id=plan_id, call_id=call_id, session_id=session_id,
            payload={"activity": activity, "starts_at": starts_at}, simulated=True,
        )
        plans.set_state(plan_id, PlanState.CANDIDATE_PLAN_CREATED,
                        reason="re-planned on counterproposal",
                        session_id=session_id, simulated=True)
        # Still mid-call, so a following "yes" remains a legal transition.
        plans.set_state(plan_id, PlanState.CONTACT_IN_PROGRESS,
                        reason="still on the call with the new terms",
                        session_id=session_id, simulated=True)

    if intent == "accept":
        plans.record_response(plan_id, contact_id, "accepted",
                              simulated=True, session_id=session_id)
        plans.set_state(plan_id, PlanState.FRIEND_ACCEPTED,
                        reason="contact accepted", session_id=session_id, simulated=True)
        plans.set_state(plan_id, PlanState.PATIENT_APPROVAL_PENDING,
                        reason="awaiting patient approval",
                        session_id=session_id, simulated=True)
        events.emit(
            "patient.approval_requested",
            "Asking Sam to approve the plan before anything is final",
            plan_id=plan_id, call_id=call_id, session_id=session_id,
            payload={"activity": plan["activity"], "starts_at": plan["starts_at"]},
        )

    elif intent == "decline":
        plans.record_response(plan_id, contact_id, "declined",
                              simulated=True, session_id=session_id)
        plans.set_state(plan_id, PlanState.FRIEND_DECLINED,
                        reason="contact declined", session_id=session_id, simulated=True)
        plans.set_state(plan_id, PlanState.COMPLETE,
                        reason="attempt ended after decline",
                        session_id=session_id, simulated=True)

    elif intent == "request_stop_contact":
        # An explicit opt-out outranks everything else in flight.
        from . import consent as consent_mod

        plans.record_response(plan_id, contact_id, "declined",
                              simulated=True, session_id=session_id)
        consent_mod.revoke_consent(plan["patient_id"], contact_id)
        events.emit(
            "consent.revoked",
            "Contact asked not to be called again; consent revoked immediately",
            plan_id=plan_id, call_id=call_id, session_id=session_id,
            payload={"contact_id": contact_id, "source": "on_call_request"},
        )
        plans.halt_for_revoked_consent(
            plan_id, "contact requested no further contact", session_id=session_id
        )

    elif intent in ("counter_time", "counter_activity", "counter_both"):
        # Agreeing to terms that are ALREADY the plan is acceptance, not a
        # counterproposal. Claude re-states the agreed terms when the caller
        # says "yeah, let's do that" right after a revision; treating that as a
        # fresh counter bumped the version and silently discarded the yes.
        # Deterministic check, so the model cannot talk its way into either
        # outcome.
        want_activity = _present(slots.get("activity")) or plan["activity"]
        want_start = slots.get("starts_at") or plan["starts_at"]
        if (
            want_activity.strip().lower() == plan["activity"].strip().lower()
            and str(want_start) == str(plan["starts_at"])
        ):
            plans.record_response(plan_id, contact_id, "accepted",
                                  simulated=True, session_id=session_id)
            plans.set_state(plan_id, PlanState.FRIEND_ACCEPTED,
                            reason="contact agreed to the current terms",
                            session_id=session_id, simulated=True)
            plans.set_state(plan_id, PlanState.PATIENT_APPROVAL_PENDING,
                            reason="awaiting patient approval",
                            session_id=session_id, simulated=True)
            events.emit(
                "patient.approval_requested",
                "Asking Sam to approve the plan before anything is final",
                plan_id=plan_id, call_id=call_id, session_id=session_id,
                payload={"activity": plan["activity"], "starts_at": plan["starts_at"],
                         "note": "restated current terms, read as acceptance"},
            )
            intent = "accept"
        else:
            _revise(
                activity=slots.get("activity"),
                starts_at=slots.get("starts_at"),
                reason=f"contact proposed different terms ({intent})",
            )

    # maybe_defer, ask_*, interrupt, silence, out_of_scope, clarification_needed
    # and unknown deliberately change NO state. A hedge is not an acceptance.

    reply = result.reply
    # Final outbound guard: a contact-facing utterance must stay clinically clean.
    try:
        memory.assert_no_sensitive_leak(reply)
    except memory.MemoryError_:
        events.emit(
            "safety.blocked",
            "Replaced a reply that contained sensitive language",
            plan_id=plan_id, call_id=call_id, session_id=session_id,
            payload={"intent": intent}, simulated=True,
        )
        reply = (
            "That's really Sam's to share, and he would want to tell you himself. "
            "Shall I go ahead and set this up?"
        )

    events.emit(
        "call.turn", f"agent: {reply}",
        plan_id=plan_id, call_id=call_id, session_id=session_id,
        payload={"speaker": "agent", "text": reply, "intent": intent,
                 "tools": result.tool_names},
        simulated=True,
    )

    return {
        "reply": reply,
        "intent": intent,
        "slots": slots,
        "mode": result.mode,
        "tool_calls": result.tool_names,
        "needs_clarification": result.needs_clarification,
        "state": plans.get_plan(plan_id)["status"],
        "simulated": True,
    }


def end_call(call_id: str, status: str = "completed", error: str | None = None) -> None:
    call = q1("SELECT * FROM calls WHERE id=?", (call_id,))
    if call is None:
        return
    ex(
        "UPDATE calls SET status=?, ended_at=?, error=? WHERE id=?",
        (status, now_iso(), error, call_id),
    )
    events.emit(
        "call.ended", f"Call ended: {status}" + (f" ({error})" if error else ""),
        plan_id=call["plan_id"], call_id=call_id, session_id=call["session_id"],
        payload={"status": status, "error": error},
        simulated=(call["mode"] != "cartesia_pstn"),
    )
