"""Judge-video orchestration for the fictional Josh group scenario."""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

from ..services import pitch_demo

router = APIRouter(prefix="/api/pitch", tags=["pitch-demo"])


class StartBody(BaseModel):
    force_mode: str | None = Field(default=None, pattern="^(simulated|cartesia_pstn)$")


class ApprovalBody(BaseModel):
    plan_id: str
    response: str = Field(pattern="^(accepted|declined)$")


class InvitationCallBody(StartBody):
    take_id: str | None = Field(default=None, max_length=80, pattern="^[A-Za-z0-9_.:-]+$")


@router.post("/reset")
def reset():
    return pitch_demo.reset()


@router.post("/start")
def start(body: StartBody | None = None):
    return pitch_demo.start(force_mode=(body.force_mode if body else None))


@router.post("/onboarding-call")
def onboarding_call():
    return pitch_demo.start_onboarding_call()


@router.post("/josh-invitation-call")
def josh_invitation_call(body: InvitationCallBody | None = None):
    return pitch_demo.start_josh_invitation_call(
        force_mode=(body.force_mode if body else None),
        take_id=(body.take_id if body else None),
    )


@router.post("/rehearse")
def rehearse():
    return pitch_demo.rehearse()


@router.post("/simulate-three-weeks")
def simulate_three_weeks():
    return pitch_demo.simulate_three_weeks()


@router.post("/josh-response")
def josh_response(body: ApprovalBody):
    return pitch_demo.approve_josh(
        body.plan_id, accepted=body.response == "accepted", simulated=True
    )


@router.get("/status")
def status():
    return pitch_demo.status()
