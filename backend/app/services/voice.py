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
