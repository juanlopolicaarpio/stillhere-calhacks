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

    dynamic_variables = {
        "contact_first_name": contact["display_name"].split()[0],
        "patient_first_name": (patient["display_name"] if patient else "your friend").split()[0],
        "proposed_activity": plan["activity"],
        "proposed_time_human": _humanize(plan["starts_at"]),
        "proposed_location": plan["location"] or "",
        "shared_interests": ", ".join(ctx["shared_interests"][:3]),
        "relationship": ctx["relationship"] or "friend",
        "boundaries": "; ".join(ctx["boundaries"][:2]),
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

OPENING_TEMPLATE = (
    "Hey {contact_first_name}! This is Still Here - I'm an AI assistant calling "
    "on behalf of {patient_first_name}. Is now an okay time? "
    "{patient_first_name} has been wanting to see you, and I remembered you two "
    "are into {shared_interests}. There's {proposed_activity} "
    "{proposed_time_human}. Would you be up for it?"
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
    opening = OPENING_TEMPLATE.format(**{**dv, "shared_interests": dv.get("shared_interests") or "the same things"})
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


_YES = re.compile(r"\b(yes|yeah|yep|sure|sounds good|i'?m in|let'?s do it|okay|ok|down)\b", re.I)
_NO = re.compile(r"\b(no|nope|can'?t|cannot|busy|not this week|pass|rather not)\b", re.I)
_WEEKDAY_RE = re.compile(
    r"\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", re.I
)


def _requested_weekday(text: str, current_start: str) -> tuple[str, int] | None:
    """Detect a contact naming a different day than the one proposed.

    Returns (day_name, weekday_index), or None when no day is named or the day
    named is the one already proposed.
    """
    m = _WEEKDAY_RE.search(text or "")
    if not m:
        return None
    name = m.group(1).lower()
    weekday = scheduling.WEEKDAYS[name]
    try:
        from datetime import datetime

        if datetime.fromisoformat(current_start).weekday() == weekday:
            return None  # they are agreeing to the proposed day, not changing it
    except (TypeError, ValueError):
        pass
    return name, weekday
_COFFEE = re.compile(r"\bcoffee\b", re.I)
_WHO = re.compile(r"\b(who are you|what is this|who is this|are you a (bot|robot|real))\b", re.I)
_WHY = re.compile(r"\bwhy (marvel|that|this)\b", re.I)
_HOWIS = re.compile(r"\bhow (is|'?s) \w+ (doing|been)\b", re.I)
_BOOK_WITHOUT = re.compile(r"\b(just book|book it|don'?t ask|without asking)\b", re.I)
_INTERRUPT = re.compile(r"\b(wait|hold on|hang on|stop)\b", re.I)


def simulate_turn(call_id: str, text: str) -> dict[str, Any]:
    """Handle one simulated conversational turn.

    This mirrors the branch logic the Managed Agent prompt implements over the
    real phone, so the same end-to-end state changes are exercised in tests.
    """
    call = q1("SELECT * FROM calls WHERE id=?", (call_id,))
    if call is None:
        raise VoiceError(f"unknown call {call_id!r}")
    plan_id = call["plan_id"]
    session_id = call["session_id"]
    plan = plans.get_plan(plan_id)
    contact_id = call["contact_id"]

    events.emit(
        "call.turn", f"contact: {text}",
        plan_id=plan_id, call_id=call_id, session_id=session_id,
        payload={"speaker": "contact", "text": text}, simulated=True,
    )

    reply: str
    tool_calls: list[str] = []

    if _WHO.search(text):
        reply = (
            "Fair question - I'm an AI assistant called Still Here, not a person. "
            "Sam set me up to help him make plans, and nothing gets confirmed "
            "without his say-so. Want me to keep going?"
        )

    elif _BOOK_WITHOUT.search(text):
        reply = (
            "I can't do that one - I won't lock anything in without Sam approving it "
            "first. I can hold this time and check with him right now, though."
        )
        events.emit(
            "safety.blocked",
            "Refused request to confirm without patient approval",
            plan_id=plan_id, call_id=call_id, session_id=session_id,
            payload={"request": "book without asking Sam"}, simulated=True,
        )

    elif _HOWIS.search(text):
        reply = (
            "That's really his to share, so I'll let him tell you himself. "
            "What I can say is he's been thinking about you. Should I set this up?"
        )
        events.emit(
            "safety.blocked",
            "Declined to disclose patient health information to contact",
            plan_id=plan_id, call_id=call_id, session_id=session_id,
            payload={"question": "how is Sam doing"}, simulated=True,
        )

    elif _WHY.search(text):
        ctx = memory.shareable_context(plan["patient_id"], contact_id, [])
        why = ctx["shared_interests"][0] if ctx["shared_interests"] else "something you both enjoy"
        reply = f"Because of this, from an earlier conversation with Sam: {why}"
        tool_calls.append("get_support_context")

    elif _COFFEE.search(text):
        from .scheduling import lookup_showtimes

        lookup_showtimes("coffee")
        tool_calls.append("lookup_showtimes")
        plans.record_response(
            plan_id, contact_id, "counterproposed",
            activity="Coffee", simulated=True, session_id=session_id,
        )
        plans.set_state(
            plan_id, PlanState.FRIEND_COUNTERPROPOSED,
            reason="contact suggested coffee instead", session_id=session_id, simulated=True,
        )
        plans.counterpropose(plan_id, activity="Coffee", session_id=session_id)
        plans.set_state(
            plan_id, PlanState.CANDIDATE_PLAN_CREATED,
            reason="re-planned around coffee", session_id=session_id, simulated=True,
        )
        # The call is still live, so return to CONTACT_IN_PROGRESS - otherwise a
        # following "yes" would be an illegal transition.
        plans.set_state(
            plan_id, PlanState.CONTACT_IN_PROGRESS,
            reason="still on the call with the new terms",
            session_id=session_id, simulated=True,
        )
        reply = (
            "Coffee works just as well - I'll swap it. Same day still good? "
            "I'll run it past Sam before anything's final."
        )

    elif (_day := _requested_weekday(text, plan["starts_at"])) is not None:
        day_name, weekday = _day
        new_start = scheduling.find_slot_on_weekday(
            [plan["patient_id"], contact_id], weekday,
            exclude_start=plan["starts_at"],
        )
        if new_start is None:
            # Be honest rather than proposing a time that cannot work.
            reply = (
                f"{day_name.capitalize()} doesn't look like it works on Sam's side. "
                f"Would {_humanize(plan['starts_at'])} still be okay, or is there "
                "another day that's better for you?"
            )
            events.emit(
                "call.turn", f"agent: {reply}",
                plan_id=plan_id, call_id=call_id, session_id=session_id,
                payload={"speaker": "agent", "text": reply,
                         "note": f"no shared availability on {day_name}"},
                simulated=True,
            )
            return {
                "reply": reply, "tool_calls": ["find_free_slots"],
                "state": plans.get_plan(plan_id)["status"], "simulated": True,
            }

        plans.record_response(
            plan_id, contact_id, "counterproposed",
            starts_at=new_start, simulated=True, session_id=session_id,
        )
        plans.set_state(
            plan_id, PlanState.FRIEND_COUNTERPROPOSED,
            reason="contact proposed a different day", session_id=session_id, simulated=True,
        )
        plans.counterpropose(plan_id, starts_at=new_start, session_id=session_id)
        plans.set_state(
            plan_id, PlanState.CANDIDATE_PLAN_CREATED,
            reason="re-planned around the new day", session_id=session_id, simulated=True,
        )
        # Still mid-call: go back to CONTACT_IN_PROGRESS so a following "yes"
        # is a legal transition.
        plans.set_state(
            plan_id, PlanState.CONTACT_IN_PROGRESS,
            reason="still on the call with the new terms",
            session_id=session_id, simulated=True,
        )
        reply = (
            "Sunday's no problem, I'll move it. I'll confirm with Sam and text you "
            "once he's good with it."
        )

    elif _NO.search(text) and not _YES.search(text):
        plans.record_response(
            plan_id, contact_id, "declined", simulated=True, session_id=session_id
        )
        plans.set_state(
            plan_id, PlanState.FRIEND_DECLINED,
            reason="contact declined", session_id=session_id, simulated=True,
        )
        plans.set_state(
            plan_id, PlanState.COMPLETE,
            reason="attempt ended after decline", session_id=session_id, simulated=True,
        )
        reply = (
            "Totally fine, and thanks for being straight with me. No pressure at all - "
            "I won't keep bugging you. Take care."
        )

    elif _YES.search(text):
        plans.record_response(
            plan_id, contact_id, "accepted", simulated=True, session_id=session_id
        )
        plans.set_state(
            plan_id, PlanState.FRIEND_ACCEPTED,
            reason="contact accepted", session_id=session_id, simulated=True,
        )
        plans.set_state(
            plan_id, PlanState.PATIENT_APPROVAL_PENDING,
            reason="awaiting patient approval", session_id=session_id, simulated=True,
        )
        events.emit(
            "patient.approval_requested",
            "Asking Sam to approve the plan before anything is final",
            plan_id=plan_id, call_id=call_id, session_id=session_id,
            payload={"activity": plan["activity"], "starts_at": plan["starts_at"]},
        )
        tool_calls += ["record_friend_response", "request_patient_approval"]
        reply = (
            "That's great - I'll check with Sam and confirm. Nothing's locked in "
            "until he says yes, and then I'll text you both the details."
        )

    elif _INTERRUPT.search(text):
        reply = "Of course - go ahead, I'm listening."

    else:
        reply = (
            "I want to make sure I got that right - are you up for it, or would "
            "another day work better?"
        )

    events.emit(
        "call.turn", f"agent: {reply}",
        plan_id=plan_id, call_id=call_id, session_id=session_id,
        payload={"speaker": "agent", "text": reply, "tools": tool_calls}, simulated=True,
    )
    return {
        "reply": reply,
        "tool_calls": tool_calls,
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
