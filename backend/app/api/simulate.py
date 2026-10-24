"""Time travel and the autonomous scheduler, as API routes.

Still Here's claim is longitudinal: it notices a friendship going quiet
*relative to its own history*. You cannot evaluate that by waiting three weeks
during a hackathon, so these routes let you move the simulated present and run
the scheduler against it.

Nothing here fabricates behaviour. The same detector, the same consent gates
and the same state machine run against a different instant. Whenever an offset
is in effect, every response and every emitted event carries
`simulated_clock: true`, and `/api/status` says so - a warped clock must never
be mistaken for real elapsed time.
"""
from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from ..services import baseline, clock, events, opportunity, scheduler

router = APIRouter(prefix="/api/sim", tags=["simulate"])


def _auth(token: str | None) -> None:
    """Time travel is a demo-operator power, so it shares the dashboard token."""
    import secrets as _s

    from ..config import get_settings

    expected = get_settings().dashboard_token
    if not token or not _s.compare_digest(str(token), str(expected)):
        raise HTTPException(
            status_code=401,
            detail={"error": {"code": "unauthorized",
                              "message": "dashboard token required"}},
        )


@router.get("/clock")
def get_clock(
    dashboard_token: str | None = None,
    x_dashboard_token: str | None = Header(default=None),
):
    _auth(x_dashboard_token or dashboard_token)
    return clock.offset_state().__dict__


class AdvanceBody(BaseModel):
    days: float = Field(default=0, ge=-3650, le=3650)
    hours: float = Field(default=0, ge=-87600, le=87600)
    reason: str = ""


@router.post("/clock/advance")
def advance_clock(
    body: AdvanceBody,
    dashboard_token: str | None = None,
    x_dashboard_token: str | None = Header(default=None),
):
    """Move the simulated present. Negative values move back."""
    _auth(x_dashboard_token or dashboard_token)
    if body.days == 0 and body.hours == 0:
        raise ValueError("provide a non-zero days or hours value")
    clock.advance(days=body.days, hours=body.hours, reason=body.reason)
    state = clock.offset_state()
    events.emit(
        "sim.clock_advanced",
        f"Simulated clock moved {body.days:+g}d {body.hours:+g}h "
        f"(now {state.offset_days:+.1f}d from real time)",
        payload={"days": body.days, "hours": body.hours,
                 "offset_days": state.offset_days, "reason": body.reason},
        simulated=True,
    )
    return state.__dict__


@router.post("/clock/reset")
def reset_clock(
    dashboard_token: str | None = None,
    x_dashboard_token: str | None = Header(default=None),
):
    """Return to the real present."""
    _auth(x_dashboard_token or dashboard_token)
    clock.reset()
    events.emit("sim.clock_reset", "Simulated clock reset to real time")
    return clock.offset_state().__dict__


@router.get("/opportunities")
def list_opportunities(
    patient_id: str,
    dashboard_token: str | None = None,
    x_dashboard_token: str | None = Header(default=None),
):
    """What the detector currently sees, including what it is holding back on.

    The suppressed entries matter as much as the live ones: they are the
    evidence that a decline or an unanswered call quiets the system down
    instead of making it try harder.
    """
    _auth(x_dashboard_token or dashboard_token)
    found = opportunity.detect(patient_id)
    state = clock.offset_state()
    return {
        "simulated_clock": state.simulated,
        "offset_days": state.offset_days,
        "now": state.simulated_now,
        "count": len(found),
        "opportunities": [
            {
                "contact_id": o.contact_id,
                "display_name": o.display_name,
                "relationship_label": o.relationship_label,
                "score": round(o.score, 3),
                "reason": o.reason,
                "suppressed": not (o.should_raise and o.actionable),
                "suppressed_reason": o.hold_reason,
                "cooling_off_reason": o.suppressed_reason,
                "below_floor_reason": o.below_floor_reason,
                "suppressed_until": o.suppressed_until,
                "median_gap_days": getattr(o.baseline, "median_gap_days", None),
                "current_gap_days": getattr(o.baseline, "current_gap_days", None),
                "deviation_ratio": getattr(o.baseline, "deviation_ratio", None),
                "confidence": getattr(o.baseline, "confidence", None),
                "events_considered": getattr(o.baseline, "events_considered", None),
                "basis": getattr(o.baseline, "basis", None),
                "shared_free_slots": o.shared_free_slots[:3],
                "supporting_memories": o.supporting_memories[:3],
            }
            for o in found
        ],
    }


class TickBody(BaseModel):
    dry_run: bool = True
    force_mode: str | None = Field(default=None, pattern="^(simulated|cartesia_pstn)$")


@router.post("/tick")
def run_tick(
    body: TickBody | None = None,
    dashboard_token: str | None = None,
    x_dashboard_token: str | None = Header(default=None),
):
    """Run one autonomous scheduler pass.

    Defaults to `dry_run: true` deliberately. An endpoint that can phone a
    real person should not do so because someone forgot a parameter.
    """
    _auth(x_dashboard_token or dashboard_token)
    b = body or TickBody()
    return scheduler.tick(dry_run=b.dry_run, force_mode=b.force_mode).as_dict()


class SeedRecentBody(BaseModel):
    patient_id: str = "user_sam"
    contact_id: str = "user_josh"
    #: Days since the last contact. Small, so nothing has drifted yet.
    current_gap_days: float = Field(default=2, ge=0, le=365)
    #: The relationship's historical cadence, oldest gap first.
    gaps_days: list[float] = Field(
        default_factory=lambda: [9, 12, 10, 11, 13, 10, 12]
    )


@router.post("/seed-recent")
def seed_recent(
    body: SeedRecentBody | None = None,
    dashboard_token: str | None = None,
    x_dashboard_token: str | None = Header(default=None),
):
    """Rewrite one relationship's history so it is CURRENT, not dormant.

    The default demo seed is already 47 days quiet, so the detector fires
    immediately - correct, but it shows you the conclusion rather than the
    reasoning. Seeding a recently-in-touch relationship instead lets you
    advance the clock a day at a time and watch the gap grow past the
    relationship's own median until the system decides, on its own, that it is
    time to call.
    """
    b = body or SeedRecentBody()
    _auth(x_dashboard_token or dashboard_token)
    ids = baseline.seed_demo_history(
        b.patient_id, b.contact_id,
        gaps_days=b.gaps_days,
        current_gap_days=b.current_gap_days,
    )
    bl = baseline.relationship_baseline(b.patient_id, b.contact_id)
    events.emit(
        "sim.seeded_recent",
        f"Reseeded {b.contact_id} as recently-in-touch "
        f"({b.current_gap_days:g}d ago, median {getattr(bl, 'median_gap_days', '?')}d)",
        payload={"contact_id": b.contact_id, "events": len(ids)},
        simulated=True,
    )
    return {
        "events_written": len(ids),
        "median_gap_days": getattr(bl, "median_gap_days", None),
        "current_gap_days": getattr(bl, "current_gap_days", None),
        "deviation_ratio": getattr(bl, "deviation_ratio", None),
        "basis": getattr(bl, "basis", None),
    }
