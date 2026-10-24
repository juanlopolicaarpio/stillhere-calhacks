"""Demo orchestration: reset, start outreach, simulated Sam approval, plans."""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

from ..agents import orchestrator
from ..db import new_id, q
from ..domain.state_machine import PlanState
from ..services import demo_reset, events, plans, scheduling, voice

router = APIRouter(prefix="/api", tags=["demo"])


@router.post("/demo/reset")
def reset():
    info = demo_reset.reset()
    return {"ok": True, **info}


class StartBody(BaseModel):
    patient_id: str = demo_reset.SAM
    force_mode: str | None = Field(default=None, pattern="^(simulated|cartesia_pstn)$")


@router.post("/demo/start")
def start(body: StartBody | None = None):
    """Run the planner, then place the outbound call.

    Returns the plan and the call. If consent blocks the attempt, returns a
    403-style consent_denied via the exception handlers - no call is placed.
    """
    b = body or StartBody()
    session_id = new_id("sess")

    events.emit("demo.start", "Demo outreach started", session_id=session_id,
                payload={"patient_id": b.patient_id})

    opportunity = orchestrator.identify_opportunity(b.patient_id, session_id=session_id)
    if not opportunity["ok"]:
        return {
            "ok": False,
            "reason": opportunity["reason"],
            "detail": opportunity.get("detail"),
            "session_id": session_id,
        }

    plan = opportunity["plan"]
    call = voice.start_outbound_call(
        b.patient_id,
        plan["contact_id"],
        plan["id"],
        session_id=session_id,
        idempotency_key=f"demo:{plan['id']}",
        force_mode=b.force_mode,
        # Rehearsals happen at all hours; the seeded consent window is wide and
        # this flag is explicit rather than silently ignoring quiet hours.
        override_quiet_hours=True,
    )

    return {
        "ok": True,
        "session_id": session_id,
        "plan_id": plan["id"],
        "plan": plans.get_plan(plan["id"]),
        "call_id": call["call_id"],
        "mode": call["mode"],
        "status": call["status"],
        "provider_call_id": call.get("provider_call_id"),
        "opening": call.get("opening"),
        "error": call.get("error"),
        "why": {
            "contact": opportunity["contact"]["display_name"],
            "why_contact": opportunity["contact"]["why"],
            "activity": opportunity["activity"]["label"],
            "why_activity": opportunity["activity"]["rationale"],
            "memory_citations": plan["memory_citations"],
        },
    }


class SamResponseBody(BaseModel):
    plan_id: str
    response: str = Field(pattern="^(accepted|declined)$")
    activity: str | None = None
    starts_at: str | None = None


@router.post("/demo/sam-response")
def sam_response(body: SamResponseBody):
    """SIMULATED patient approval.

    In the solo demo there is no second human, so Sam's approval is simulated.
    It is recorded with simulated=True and every resulting event is flagged, so
    the dashboard labels it SIMULATED. This is never presented as a real person.
    """
    plan = plans.get_plan(body.plan_id)

    if plan["status"] != str(PlanState.PATIENT_APPROVAL_PENDING):
        raise ValueError(
            f"plan is not awaiting patient approval (status={plan['status']})"
        )

    plans.record_response(
        body.plan_id, plan["patient_id"], body.response,
        activity=body.activity, starts_at=body.starts_at,
        simulated=True, session_id=None,
    )

    if body.response == "declined":
        plans.set_state(body.plan_id, PlanState.PATIENT_DECLINED,
                        reason="Sam declined (simulated)", simulated=True)
        plans.set_state(body.plan_id, PlanState.COMPLETE,
                        reason="attempt ended after patient decline", simulated=True)
        return {"plan_id": body.plan_id, "state": PlanState.COMPLETE,
                "simulated": True, "confirmed": False}

    plans.set_state(body.plan_id, PlanState.PATIENT_ACCEPTED,
                    reason="Sam approved (simulated)", simulated=True)

    # Re-validate availability on the CURRENT terms before confirming.
    plan = plans.get_plan(body.plan_id)
    ok, why = scheduling.revalidate_availability(
        [plan["patient_id"], plan["contact_id"]], plan["starts_at"]
    )
    events.emit(
        "plan.state_changed",
        f"Availability revalidation: {why}",
        plan_id=body.plan_id,
        payload={"ok": ok, "reason": why, "starts_at": plan["starts_at"]},
    )
    if not ok:
        plans.set_state(body.plan_id, PlanState.FAILED, reason=why)
        return {"plan_id": body.plan_id, "state": PlanState.FAILED,
                "simulated": True, "confirmed": False, "reason": why}

    plans.set_state(body.plan_id, PlanState.AVAILABILITY_REVALIDATED, reason=why)

    result = plans.confirm_plan(body.plan_id, idempotency_key=f"confirm:{body.plan_id}")
    if not result["confirmed"]:
        return {"plan_id": body.plan_id, "state": result["plan"]["status"],
                "simulated": True, "confirmed": False, "reason": result["reason"]}

    plans.send_confirmation(body.plan_id)
    return {
        "plan_id": body.plan_id,
        "state": plans.get_plan(body.plan_id)["status"],
        "simulated": True,
        "confirmed": True,
    }


@router.get("/plans/{plan_id}")
def get_plan(plan_id: str):
    return plans.get_plan(plan_id)


@router.get("/plans")
def list_plans(patient_id: str | None = None):
    if patient_id:
        rows = q("SELECT id FROM plans WHERE patient_id=? ORDER BY created_at DESC",
                 (patient_id,))
    else:
        rows = q("SELECT id FROM plans ORDER BY created_at DESC LIMIT 50")
    return [plans.get_plan(r["id"]) for r in rows]
