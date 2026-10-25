"""The judge-facing Josh/Maya/Daniel/Alex coordination scenario.

All people and history are fictional.  Provider actions remain real when
``force_mode=cartesia_pstn``; the rehearsal path is explicitly marked
simulated on every generated response/event.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from ..config import get_settings
from ..db import ex, new_id, now_iso, q, q1, wipe_all
from ..domain.state_machine import PlanState
from ..adapters import cartesia_adapter as ca
from . import (
    baseline,
    clock,
    consent,
    events,
    group_coordination,
    memory,
    opportunity,
    plans,
    scheduling,
    voice,
)

JOSH = "pitch_josh"
MAYA = "pitch_maya"
DANIEL = "pitch_daniel"
ALEX = "pitch_alex"
CONTACTS = (MAYA, DANIEL, ALEX)


def _user(uid: str, name: str, role: str, phone: str, *, active: bool = False) -> None:
    ex(
        "INSERT INTO users (id,display_name,role,phone_e164,phone_verified,timezone,"
        "preferred_channel,is_adult_attested,outreach_active,created_at) "
        "VALUES (?,?,?,?,1,'America/Los_Angeles','call',1,?,?)",
        (uid, name, role, phone, int(active), now_iso()),
    )


def _next_weekday(day: int, hour: int) -> datetime:
    start = (clock.now() + timedelta(days=1)).replace(
        hour=hour, minute=0, second=0, microsecond=0
    )
    return start + timedelta(days=(day - start.weekday()) % 7)


def _remember(subject: str | None, value: str, visibility: str = "shareable_with_contact") -> str:
    quote = value
    conv = memory.save_message(JOSH, "pitch_seed", "user", quote)
    return memory.store_memory(
        JOSH, "preference", value, subject_id=subject,
        source_conversation_id=conv, source_quote=quote, confidence=0.95,
        visibility=visibility, confirmed=True,
    ).id


def reset() -> dict[str, Any]:
    s = get_settings()
    controlled_phone = s.my_phone_e164 or "+14155550199"
    wipe_all()
    events.reset_sequence()
    clock.invalidate_cache()
    clock.reset()

    # In a solo demo all roles intentionally ring the presenter-controlled
    # verified test phone.  The dashboard discloses this; production contacts
    # would each have their own independently verified number.
    _user(JOSH, "Josh Rivera", "patient", controlled_phone, active=True)
    _user(MAYA, "Maya Chen", "contact", controlled_phone)
    _user(DANIEL, "Daniel Osei", "contact", controlled_phone)
    _user(ALEX, "Alex Morgan", "contact", controlled_phone)

    now = clock.now()
    for cid, label in ((MAYA, "close friend"), (DANIEL, "friend"), (ALEX, "friend")):
        ex(
            "INSERT INTO relationships (id,patient_id,contact_id,relationship_label,"
            "status,last_contact_at,created_at) VALUES (?,?,?,?,?,?,?)",
            (new_id("rel"), JOSH, cid, label, "accepted",
             (now - timedelta(days=47 if cid == MAYA else 18)).isoformat(), now_iso()),
        )
        consent.grant_consent(
            JOSH, cid,
            allowed_actions=["voice_call", "sms"],
            allowed_fields=["first_name", "shared_interests", "general_availability"],
            quiet_hours_start=23, quiet_hours_end=7, max_per_day=25,
        )
        ex(
            "INSERT INTO invitations (token,patient_id,contact_name,contact_phone_e164,"
            "relationship_label,expires_at,accepted_at,contact_id,created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (f"pitch-invite-{cid}", JOSH,
             {MAYA: "Maya Chen", DANIEL: "Daniel Osei", ALEX: "Alex Morgan"}[cid],
             controlled_phone, label, (now + timedelta(days=30)).isoformat(),
             now_iso(), cid, now_iso()),
        )

    memory_ids = [
        _remember(MAYA, "Josh, Maya, Daniel, and Alex enjoy playing billiards together"),
        _remember(DANIEL, "Josh, Maya, Daniel, and Alex enjoy playing billiards together"),
        _remember(ALEX, "Josh, Maya, Daniel, and Alex enjoy playing billiards together"),
        _remember(DANIEL, "Daniel needs plans to end before 7 PM"),
        _remember(ALEX, "Alex likes getting dinner with friends after billiards"),
        _remember(None, "Josh prefers low-pressure plans arranged a few days ahead"),
        _remember(None, "Josh usually prefers Sunday afternoon plans"),
        _remember(MAYA, "Maya likes quieter places where the group can talk"),
        _remember(MAYA, "Maya is usually free on Sunday afternoons"),
        _remember(DANIEL, "Daniel works early on Monday mornings"),
        _remember(DANIEL, "Daniel prefers plans confirmed at least a day ahead"),
        _remember(ALEX, "Alex often suggests casual food after group activities"),
        _remember(ALEX, "Alex shares availability manually instead of connecting a calendar"),
        _remember(None, "The group prefers casual activities over formal events"),
        _remember(None, "The group usually meets near Mission District"),
        _remember(None, "Josh recently left psychiatric care", "private_to_sam"),
    ]
    events.emit(
        "memory.stored",
        "Seeded consented group-call memory: the group enjoys billiards",
        payload={
            "kind": "shared_interest", "value": "Billiards",
            "participants": [JOSH, MAYA, DANIEL, ALEX],
            "source": "seeded_consented_group_call", "confirmed": True,
            "memory_ids": memory_ids[:3],
        },
        simulated=True,
    )

    saturday = _next_weekday(5, 17)
    sunday = _next_weekday(6, 16)
    for week in range(12):
        sat = saturday + timedelta(weeks=week)
        sun = sunday + timedelta(weeks=week)
        scheduling.add_availability(JOSH, sat.isoformat(), (sat + timedelta(hours=5)).isoformat(), "seeded_demo")
        scheduling.add_availability(JOSH, sun.isoformat(), (sun + timedelta(hours=5)).isoformat(), "seeded_demo")
        scheduling.add_availability(MAYA, sun.isoformat(), (sun + timedelta(hours=4)).isoformat(), "seeded_google_demo")
        scheduling.add_availability(DANIEL, sun.isoformat(), (sun + timedelta(hours=3)).isoformat(), "seeded_outlook_demo")
        scheduling.add_availability(ALEX, sun.isoformat(), (sun + timedelta(hours=5)).isoformat(), "manual_demo")

    # Begin with Maya inside Josh's normal cadence. The judge can then advance
    # the single simulated clock by three weeks and watch the *real* detector
    # cross its own threshold instead of loading a pre-decided alert.
    maya_history = baseline.seed_demo_history(
        JOSH, MAYA, gaps_days=(8, 12, 9, 14, 11, 10, 13, 8, 9, 12, 10, 11, 9, 13, 8, 10, 12),
        current_gap_days=2, source="seed", note="fictional demo history",
    )
    daniel_history = baseline.seed_demo_history(
        JOSH, DANIEL, gaps_days=(7, 10, 8, 12, 9, 11, 8, 10, 13, 9, 8, 12, 10, 9),
        current_gap_days=9, source="seed", note="fictional demo history",
    )
    baseline.seed_demo_history(
        JOSH, ALEX, gaps_days=(9, 12, 10, 14, 11, 8, 13, 10, 9, 12, 11, 10, 14, 9), current_gap_days=18,
        source="seed", note="fictional demo history",
    )

    demo_id = new_id("pitch")
    ex(
        "INSERT INTO demo_sessions (id,state,reset_version,started_at) VALUES (?,?,1,?)",
        (demo_id, "pitch_ready", now_iso()),
    )
    events.emit(
        "demo.reset", "Pitch demo reset: fictional Josh circle ready",
        payload={
            "patient_id": JOSH, "contacts": list(CONTACTS),
            "all_data_fictional": True, "shared_test_phone": True,
            "memories_seeded": len(memory_ids),
            "contact_events_seeded": len(maya_history) + len(daniel_history) + 6,
        },
    )
    return {
        "ok": True, "demo_session_id": demo_id, "patient_id": JOSH,
        "contacts": list(CONTACTS), "shared_test_phone": True,
        "saturday": saturday.isoformat(), "sunday": sunday.isoformat(),
    }


def start_onboarding_call() -> dict[str, Any]:
    """One-scan demo trigger: place one real PSTN call to the configured phone.

    The fictional demo identity is already verified and permissioned by reset().
    This endpoint deliberately has no form flow and is idempotent per reset.
    """
    if q1("SELECT id FROM users WHERE id=?", (JOSH,)) is None:
        reset()
    s = get_settings()
    if not s.cartesia_call_ready:
        raise voice.VoiceError("Cartesia PSTN calling is not configured")
    patient = q1("SELECT * FROM users WHERE id=?", (JOSH,))
    demo = q1("SELECT id FROM demo_sessions ORDER BY started_at DESC LIMIT 1")
    key = f"pitch-onboarding:{demo['id'] if demo else 'default'}"
    existing = q1("SELECT * FROM calls WHERE idempotency_key=?", (key,))
    if existing:
        return {"ok": True, "call_id": existing["id"], "status": existing["status"],
                "mode": existing["mode"], "duplicate": True}

    call_id = new_id("call")
    session_id = new_id("onboarding")
    context = {
        "purpose": "patient_onboarding", "patient_id": JOSH,
        "participant_role": "patient", "preauthorized_demo": True,
        "allowed_actions": ["collect_profile", "select_circle", "approve_invitations"],
    }
    ex(
        "INSERT INTO calls (id,plan_id,patient_id,contact_id,session_id,provider,"
        "to_number_redacted,status,mode,idempotency_key,requested_at,purpose,authorized_context) "
        "VALUES (?,NULL,?,?,?,?,?,'requested','cartesia_pstn',?,?,?,?)",
        (call_id, JOSH, JOSH, session_id, "cartesia", "***" + patient["phone_e164"][-2:],
         key, now_iso(), "patient_onboarding", __import__("json").dumps(context)),
    )
    events.emit(
        "call.requested", "QR scanned — real onboarding call requested for Josh",
        call_id=call_id, session_id=session_id,
        payload={"mode": "cartesia_pstn", "verified_call_purpose": "patient_onboarding"},
    )
    result = ca.place_outbound_call(
        patient["phone_e164"],
        dynamic_variables={
            "verified_call_purpose": "patient_onboarding",
            "patient_first_name": "Josh", "contact_first_name": "Josh",
            "authorized_opening": "Hey Josh! I'm Still Here. I'm here to make staying connected a little easier.",
            "onboarding_instruction": "Ask who Josh wants to keep in touch with, then ask whether he wants invitations sent.",
            "call_id": call_id, "session_id": session_id,
        },
    )
    if not result.ok:
        ex("UPDATE calls SET status='failed',error=?,ended_at=? WHERE id=?",
           (result.error, now_iso(), call_id))
        raise voice.VoiceError(result.error or "Cartesia rejected the onboarding call")
    ex("UPDATE calls SET status='ringing',provider_call_id=?,raw_provider_response=? WHERE id=?",
       (result.provider_call_id, __import__("json").dumps(result.raw), call_id))
    events.emit("call.ringing", "Josh's phone is ringing for onboarding",
                call_id=call_id, session_id=session_id,
                payload={"provider_call_id": result.provider_call_id})
    return {"ok": True, "call_id": call_id, "provider_call_id": result.provider_call_id,
            "status": "ringing", "mode": "cartesia_pstn"}


def start_josh_invitation_call(
    *, force_mode: str | None = None, take_id: str | None = None
) -> dict[str, Any]:
    """Place the demo's final, backend-scripted invitation call to Josh.

    This is deliberately separate from friend scheduling.  The provider gets
    only the reviewed invitation and may ask for Josh's yes/no response; it may
    not change the plan, confirm it, or invent a reason for the call.
    """
    if q1("SELECT id FROM users WHERE id=?", (JOSH,)) is None:
        reset()
    patient = q1("SELECT * FROM users WHERE id=?", (JOSH,))
    if not patient or not patient["phone_verified"] or not patient["outreach_active"]:
        raise voice.VoiceError("Josh's verified demo calling permission is not active")

    s = get_settings()
    mode = force_mode or s.voice_mode()
    if mode == "cartesia_pstn" and not s.cartesia_call_ready:
        raise voice.VoiceError("Cartesia PSTN calling is not configured")

    demo = q1("SELECT id FROM demo_sessions ORDER BY started_at DESC LIMIT 1")
    take = take_id or "v1"
    key = f"pitch-josh-invitation:{demo['id'] if demo else 'default'}:{take}"
    existing = q1("SELECT * FROM calls WHERE idempotency_key=?", (key,))
    if existing:
        return {"ok": True, "call_id": existing["id"], "status": existing["status"],
                "mode": existing["mode"], "provider_call_id": existing["provider_call_id"],
                "duplicate": True}

    call_id = new_id("call")
    session_id = new_id("josh_invite")
    purpose = "patient_reunion_invitation"
    activity = "Billiards with Maya, Daniel, and Alex"
    time_human = "Saturday at 5 PM"
    location = "Mission Billiards Hall"
    opening = (
        "Hi Josh! It's Still Here. Maya, Daniel, and Alex would love to meet up "
        "with you for billiards this Saturday at 5 PM at Mission Billiards Hall. "
        "Would you like to join them?"
    )
    context = {
        "purpose": purpose,
        "patient_id": JOSH,
        "participant_role": "patient",
        "preauthorized_demo": True,
        "friends": ["Maya", "Daniel", "Alex"],
        "activity": activity,
        "time": time_human,
        "location": location,
        "authorized_opening": opening,
        "allowed_actions": ["request_patient_response"],
        "forbidden_actions": ["change_plan", "confirm_plan", "invent_context"],
    }
    ex(
        "INSERT INTO calls (id,plan_id,patient_id,contact_id,session_id,provider,"
        "to_number_redacted,status,mode,idempotency_key,requested_at,purpose,authorized_context) "
        "VALUES (?,NULL,?,?,?,?,?,?,?,?,?,?,?)",
        (call_id, JOSH, JOSH, session_id,
         "cartesia" if mode == "cartesia_pstn" else "simulated",
         "***" + patient["phone_e164"][-2:], "requested", mode, key, now_iso(),
         purpose, __import__("json").dumps(context)),
    )
    events.emit(
        "patient.invitation_requested",
        "Josh invitation requested: Saturday 5 PM billiards with Maya, Daniel, and Alex",
        call_id=call_id, session_id=session_id,
        payload={"mode": mode, "verified_call_purpose": purpose},
        simulated=(mode != "cartesia_pstn"),
    )
    dynamic_variables = {
        "verified_call_purpose": purpose,
        "patient_first_name": "Josh",
        "contact_first_name": "Josh",
        "authorized_opening": opening,
        "invitation_instruction": (
            "Use the authorized opening exactly. Wait for Josh's answer. If he says yes, "
            "say you will let everyone know. If he says no, thank him. Do not change or "
            "confirm the plan and do not invent any detail."
        ),
        "friends_names": "Maya, Daniel, and Alex",
        "proposed_activity": activity,
        "proposed_time_human": time_human,
        "proposed_location": location,
        "call_id": call_id,
        "session_id": session_id,
    }
    for value in dynamic_variables.values():
        if isinstance(value, str) and value:
            memory.assert_no_sensitive_leak(value)

    if mode != "cartesia_pstn":
        ex("UPDATE calls SET status='ringing' WHERE id=?", (call_id,))
        return {"ok": True, "call_id": call_id, "provider_call_id": None,
                "status": "ringing", "mode": "simulated", "script": opening}

    result = ca.place_outbound_call(
        patient["phone_e164"], dynamic_variables=dynamic_variables
    )
    if not result.ok:
        ex("UPDATE calls SET status='failed',error=?,ended_at=? WHERE id=?",
           (result.error, now_iso(), call_id))
        raise voice.VoiceError(result.error or "Cartesia rejected Josh's invitation call")
    ex("UPDATE calls SET status='ringing',provider_call_id=?,raw_provider_response=? WHERE id=?",
       (result.provider_call_id, __import__("json").dumps(result.raw), call_id))
    events.emit(
        "call.ringing", "Josh's phone is ringing with the final invitation",
        call_id=call_id, session_id=session_id,
        payload={"provider_call_id": result.provider_call_id, "verified_call_purpose": purpose},
    )
    return {"ok": True, "call_id": call_id, "provider_call_id": result.provider_call_id,
            "status": "ringing", "mode": "cartesia_pstn"}


def simulate_three_weeks() -> dict[str, Any]:
    if q1("SELECT id FROM users WHERE id=?", (JOSH,)) is None:
        reset()
    before = baseline.relationship_baseline(JOSH, MAYA).as_dict()
    events.emit(
        "learning.baseline_established",
        f"Learned Josh and Maya's usual cadence: about {before['median_gap_days']:.0f} days",
        payload={"baseline": before, "source": "seeded_labeled_history"},
        simulated=True,
    )
    for summary, subject in (
        ("Learned from a consented group call: the group enjoys billiards", MAYA),
        ("Learned: Daniel needs plans to end before 7 PM", DANIEL),
        ("Learned: Alex enjoys dinner after billiards", ALEX),
    ):
        events.emit(
            "learning.memory_updated", summary,
            payload={"subject_id": subject, "source": "seeded_labeled_history"},
            simulated=True,
        )
    clock.advance(days=21, reason="judge demo: simulate three weeks")
    after = baseline.relationship_baseline(JOSH, MAYA).as_dict()
    found = opportunity.detect(JOSH)
    maya = next(o for o in found if o.contact_id == MAYA)
    events.emit(
        "sim.clock_advanced",
        "Three weeks simulated; reran the real connection-baseline detector",
        payload={"days": 21, "before": before, "after": after},
        simulated=True,
    )
    if maya.should_raise and maya.actionable:
        events.emit(
            "scheduler.opportunity",
            "Connection below baseline — reconnection opportunity",
            payload={"contact_id": MAYA, "score": maya.score, "reason": maya.reason},
            simulated=True,
        )
    return {
        "ok": True, "simulated_days": 21, "before": before, "after": after,
        "opportunity": maya.as_dict(),
    }


def start(*, force_mode: str | None = None) -> dict[str, Any]:
    if q1("SELECT id FROM users WHERE id=?", (JOSH,)) is None:
        reset()
    sid = new_id("pitchsess")
    found = opportunity.detect(JOSH)
    maya_opp = next((o for o in found if o.contact_id == MAYA), None)
    if maya_opp is None or not maya_opp.should_raise:
        raise ValueError("the seeded Maya baseline did not produce an opportunity")

    events.emit(
        "scheduler.opportunity",
        "Connection below baseline — reconnection opportunity",
        session_id=sid,
        payload={
            "contact_id": MAYA, "score": maya_opp.score,
            "baseline": maya_opp.baseline.as_dict(), "reason": maya_opp.reason,
        },
    )

    saturday = _next_weekday(5, 17).isoformat()
    plan = plans.create_plan(
        JOSH, MAYA, "Billiards with Josh, Maya, Daniel, and Alex", saturday,
        location="Mission Billiards Hall",
        rationale="Maya is below Josh's established connection baseline",
        why_contact=maya_opp.reason,
        why_activity="A confirmed, consented group-call billiards memory is on record",
        memory_citations=maya_opp.supporting_memories,
        session_id=sid,
    )
    plans.add_participant(plan["id"], DANIEL, session_id=sid)
    plans.add_participant(plan["id"], ALEX, session_id=sid)
    plans.set_state(plan["id"], PlanState.CONSENT_VERIFIED,
                    reason="all three friends independently opted in", session_id=sid)
    plans.set_state(plan["id"], PlanState.MEMORY_RETRIEVED,
                    reason="only approved relationship memories retrieved", session_id=sid)
    plans.set_state(plan["id"], PlanState.CANDIDATE_PLAN_CREATED,
                    reason="tentative Saturday proposal ready for negotiation", session_id=sid)
    call = voice.start_outbound_call(
        JOSH, MAYA, plan["id"], session_id=sid,
        idempotency_key=f"pitch:{plan['id']}:maya:v1",
        force_mode=force_mode, override_quiet_hours=True,
        purpose="friend_reunion_coordination",
    )
    return {"ok": True, "session_id": sid, "plan_id": plan["id"], **call}


def _simulated_tool(call_id: str, summary: str) -> None:
    call = q1("SELECT * FROM calls WHERE id=?", (call_id,))
    events.emit(
        "call.tool_invoked", summary, plan_id=call["plan_id"], call_id=call_id,
        session_id=call["session_id"], payload={"tool": "record_friend_response"},
        simulated=True,
    )


def rehearse() -> dict[str, Any]:
    """Deterministic, visibly simulated version of the complete group flow."""
    reset()
    simulate_three_weeks()
    started = start(force_mode="simulated")
    pid, maya_call = started["plan_id"], started["call_id"]
    sunday = _next_weekday(6, 16).isoformat()

    plans.record_response(pid, MAYA, "counterproposed", starts_at=sunday,
                          simulated=True, session_id=started["session_id"])
    plans.set_state(pid, PlanState.FRIEND_COUNTERPROPOSED,
                    reason="Maya cannot do Saturday and offered Sunday", simulated=True)
    plans.counterpropose(pid, starts_at=sunday, session_id=started["session_id"])
    plans.set_state(pid, PlanState.CANDIDATE_PLAN_CREATED,
                    reason="Sunday proposal grounded in shared availability", simulated=True)
    plans.set_state(pid, PlanState.CONTACT_IN_PROGRESS,
                    reason="Maya is still on the call", simulated=True)
    plans.record_response(pid, MAYA, "accepted", simulated=True)
    plans.set_state(pid, PlanState.FRIEND_ACCEPTED,
                    reason="Maya accepted Sunday billiards", simulated=True)
    _simulated_tool(maya_call, "record_friend_response: Maya countered to Sunday and accepted")
    voice.end_call(maya_call, "completed")
    daniel_call = group_coordination.start_next_after_completed_call(maya_call)

    plans.record_response(pid, DANIEL, "accepted", simulated=True)
    plans.set_state(pid, PlanState.FRIEND_ACCEPTED,
                    reason="Daniel accepted; must leave by 7 PM", simulated=True)
    events.emit("friend.constraint_recorded", "Daniel must leave by 7 PM",
                plan_id=pid, call_id=daniel_call["call_id"], simulated=True)
    _simulated_tool(daniel_call["call_id"], "record_friend_response: Daniel accepted with 7 PM boundary")
    voice.end_call(daniel_call["call_id"], "completed")
    alex_call = group_coordination.start_next_after_completed_call(daniel_call["call_id"])

    plans.record_response(pid, ALEX, "accepted", simulated=True)
    plans.set_state(pid, PlanState.FRIEND_ACCEPTED,
                    reason="Alex accepted and suggested dinner afterward", simulated=True)
    events.emit("memory.stored", "Alex suggested dinner after billiards",
                plan_id=pid, call_id=alex_call["call_id"], simulated=True,
                payload={"visibility": "shareable_with_contact", "source": "simulated_rehearsal"})
    _simulated_tool(alex_call["call_id"], "record_friend_response: Alex accepted and suggested dinner")
    plans.set_state(pid, PlanState.PATIENT_APPROVAL_PENDING,
                    reason="all three friends accepted identical terms", simulated=True)
    voice.end_call(alex_call["call_id"], "completed")
    events.emit("patient.approval_requested", "Awaiting Josh's final approval",
                plan_id=pid, simulated=True)
    return {"ok": True, "plan_id": pid, "state": plans.get_plan(pid)["status"], "simulated": True}


def approve_josh(plan_id: str, *, accepted: bool, simulated: bool = True) -> dict[str, Any]:
    plan = plans.get_plan(plan_id)
    if plan["patient_id"] != JOSH:
        raise ValueError("this endpoint only handles the fictional pitch demo")
    if plan["status"] != str(PlanState.PATIENT_APPROVAL_PENDING):
        raise ValueError(f"plan is not awaiting Josh (status={plan['status']})")
    response = "accepted" if accepted else "declined"
    plans.record_response(plan_id, JOSH, response, simulated=simulated)
    if not accepted:
        plans.set_state(plan_id, PlanState.PATIENT_DECLINED,
                        reason="Josh declined", simulated=simulated)
        plans.set_state(plan_id, PlanState.COMPLETE,
                        reason="attempt ended after patient decline", simulated=simulated)
        return {"ok": True, "confirmed": False, "state": "COMPLETE"}

    plans.set_state(plan_id, PlanState.PATIENT_ACCEPTED,
                    reason="Josh approved the agreed group plan", simulated=simulated)
    current = plans.get_plan(plan_id)
    participant_ids = [p["user_id"] for p in current["participants"]]
    ok, reason = scheduling.revalidate_availability(participant_ids, current["starts_at"])
    events.emit("plan.availability_revalidated", reason, plan_id=plan_id,
                payload={"ok": ok, "participants": len(participant_ids)}, simulated=simulated)
    if not ok:
        plans.set_state(plan_id, PlanState.FAILED, reason=reason, simulated=simulated)
        return {"ok": False, "confirmed": False, "state": "FAILED", "reason": reason}
    plans.set_state(plan_id, PlanState.AVAILABILITY_REVALIDATED,
                    reason=reason, simulated=simulated)
    result = plans.confirm_plan(plan_id, f"pitch-confirm:{plan_id}")
    if result["confirmed"]:
        plans.send_confirmation(plan_id)
    return {"ok": True, "confirmed": result["confirmed"],
            "state": plans.get_plan(plan_id)["status"],
            "notification_delivery": "simulated" if get_settings().twilio_configured is False else "provider"}


def status() -> dict[str, Any]:
    people = [dict(r) for r in q(
        "SELECT id,display_name,role FROM users WHERE id IN (?,?,?,?) ORDER BY role DESC,display_name",
        (JOSH, MAYA, DANIEL, ALEX),
    )]
    latest = q1("SELECT id FROM plans WHERE patient_id=? ORDER BY created_at DESC LIMIT 1", (JOSH,))
    plan = plans.get_plan(latest["id"]) if latest else None
    calls = [dict(r) for r in q(
        "SELECT c.id,c.contact_id,u.display_name,c.status,c.mode,c.purpose,c.requested_at,c.ended_at "
        "FROM calls c LEFT JOIN users u ON u.id=c.contact_id WHERE c.patient_id=? ORDER BY c.requested_at",
        (JOSH,),
    )]
    availability = [dict(r) for r in q(
        "SELECT a.user_id,u.display_name,a.start_at,a.end_at,a.source FROM availability a "
        "JOIN users u ON u.id=a.user_id WHERE a.user_id IN (?,?,?,?) ORDER BY a.start_at LIMIT 24",
        (JOSH, MAYA, DANIEL, ALEX),
    )]
    baseline_row = baseline.relationship_baseline(JOSH, MAYA).as_dict() if people else None
    approved_memories = [dict(r) for r in q(
        "SELECT value,source_quote,confidence,confirmed,visibility,created_at FROM memories "
        "WHERE owner_id=? AND visibility='shareable_with_contact' AND deleted_at IS NULL "
        "ORDER BY created_at DESC LIMIT 20",
        (JOSH,),
    )]
    # Eight real seven-day buckets from the stored interaction rows. The rows
    # are fictional demo seed, but this aggregation is the same live query the
    # trigger uses and moves with the single simulated clock.
    now = clock.now()
    weekly = []
    for weeks_ago in range(7, -1, -1):
        start_at = now - timedelta(days=(weeks_ago + 1) * 7)
        end_at = now - timedelta(days=weeks_ago * 7)
        count = q1(
            "SELECT COUNT(*) AS n FROM contact_events WHERE patient_id=? "
            "AND occurred_at>=? AND occurred_at<?",
            (JOSH, start_at.isoformat(), end_at.isoformat()),
        )["n"]
        weekly.append({"label": f"W{8 - weeks_ago}", "count": int(count)})
    stats = {
        "recorded_interactions": q1("SELECT COUNT(*) AS n FROM contact_events WHERE patient_id=?", (JOSH,))["n"],
        "approved_memories": q1(
            "SELECT COUNT(*) AS n FROM memories WHERE owner_id=? "
            "AND visibility='shareable_with_contact' AND deleted_at IS NULL",
            (JOSH,),
        )["n"],
        "availability_windows": q1("SELECT COUNT(*) AS n FROM availability WHERE user_id IN (?,?,?,?)", (JOSH, MAYA, DANIEL, ALEX))["n"],
        "real_calls": sum(1 for c in calls if c["mode"] == "cartesia_pstn"),
    }
    return {"people": people, "plan": plan, "calls": calls,
            "availability": availability, "baseline": baseline_row,
            "approved_memories": approved_memories,
            "interaction_history": weekly,
            "stats": stats,
            "positioning": "Connection-support tool; not clinical monitoring, diagnosis, or treatment."}
