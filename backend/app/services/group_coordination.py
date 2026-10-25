"""Safe progression for a consented multi-person reunion plan.

Calls are serialized: the next participant is dialed only after the current
provider call has ended *and* that participant has accepted the current plan
version.  A voicemail, decline, missing tool call, or revoked consent stops the
sequence.  ``voice.start_outbound_call`` still performs every identity,
consent, quiet-hour, frequency, and memory-visibility check.
"""
from __future__ import annotations

from typing import Any

from ..db import q, q1
from ..domain.state_machine import PlanState
from . import events, plans, voice


def start_next_after_completed_call(call_id: str) -> dict[str, Any] | None:
    call = q1("SELECT * FROM calls WHERE id=?", (call_id,))
    if call is None or call["status"] != "completed" or not call["plan_id"]:
        return None

    current = q1(
        "SELECT response FROM plan_participants WHERE plan_id=? AND user_id=?",
        (call["plan_id"], call["contact_id"]),
    )
    # Never interpret silence, voicemail, or a missing webhook tool as consent.
    if current is None or current["response"] != "accepted":
        return None

    plan = plans.get_plan(call["plan_id"])
    if plan["status"] == str(PlanState.PATIENT_APPROVAL_PENDING):
        return None
    if plan["status"] != str(PlanState.FRIEND_ACCEPTED):
        return None

    waiting = q(
        "SELECT pp.user_id, u.display_name FROM plan_participants pp "
        "JOIN users u ON u.id=pp.user_id "
        "WHERE pp.plan_id=? AND pp.role='contact' "
        "AND (pp.response IS NULL OR pp.response!='accepted') ORDER BY pp.rowid",
        (plan["id"],),
    )
    if not waiting:
        return None

    nxt = waiting[0]
    events.emit(
        "coordination.next_contact_started",
        f"Calling the next consented participant: {nxt['display_name']}",
        plan_id=plan["id"], session_id=call["session_id"],
        payload={"contact_id": nxt["user_id"], "remaining": len(waiting)},
        simulated=(call["mode"] != "cartesia_pstn"),
    )
    return voice.start_outbound_call(
        plan["patient_id"], nxt["user_id"], plan["id"],
        session_id=call["session_id"],
        idempotency_key=(
            f"group:{plan['id']}:v{plan['version']}:{nxt['user_id']}"
        ),
        force_mode=call["mode"],
        override_quiet_hours=True,
        purpose="friend_reunion_coordination",
    )

