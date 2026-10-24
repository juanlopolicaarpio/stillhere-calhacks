"""The thing that makes Still Here proactive instead of a button.

A reconnection product that only acts when someone presses "go" has not solved
the problem it claims to: the whole difficulty is that the person cannot
initiate. So something has to notice, on its own, that a friendship has gone
quiet and that both calendars are free.

`tick()` is that something. It is deliberately a plain function rather than a
background thread, because:

* it must be callable from a test with a fixed clock,
* it must be callable from a cron job, a Lambda, or a loop, without the design
  assuming which,
* and an autonomous system that reaches out to vulnerable people must have ONE
  auditable entry point, not a timer buried in application startup.

Everything `tick()` can do is gated by the same checks as a manual run. It
cannot widen consent, skip quiet hours, or shorten a cooling-off window - it
only decides *when to ask*, never *whether it is allowed*.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from ..db import new_id, q
from . import clock, events, opportunity

#: Minimum simulated time between two autonomous outreach attempts for ONE
#: patient, regardless of how often tick() is called. A scheduler that fires
#: every minute must not produce an outreach every minute.
MIN_HOURS_BETWEEN_AUTONOMOUS_OUTREACH = 20

#: tick() will never start more than this many outreaches in a single call,
#: however many opportunities it finds. A detector bug should degrade into
#: "one unnecessary call", not a phone tree.
MAX_OUTREACH_PER_TICK = 1


@dataclass
class TickResult:
    """What a single tick considered and did. Returned, logged, and displayed."""

    at: str
    patients_considered: int = 0
    opportunities_found: int = 0
    suppressed: int = 0
    outreach_started: int = 0
    acted_on: list[dict[str, Any]] = field(default_factory=list)
    skipped: list[dict[str, Any]] = field(default_factory=list)
    simulated_clock: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _active_patients() -> list[str]:
    """Only patients who have actually switched outreach on."""
    return [
        r["id"] for r in q(
            "SELECT id FROM users WHERE role='patient' AND outreach_active=1"
        )
    ]


def _hours_since_last_autonomous(patient_id: str) -> float | None:
    """Simulated hours since this patient's last outreach of any kind."""
    rows = q(
        "SELECT occurred_at FROM outreach_log WHERE patient_id=?"
        " ORDER BY occurred_at DESC LIMIT 1",
        (patient_id,),
    )
    if not rows:
        return None
    from datetime import datetime, timezone

    try:
        then = datetime.fromisoformat(rows[0]["occurred_at"])
    except (TypeError, ValueError):
        return None
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    return (clock.now() - then).total_seconds() / 3600


def tick(*, dry_run: bool = False, force_mode: str | None = None) -> TickResult:
    """Look for reconnection opportunities and, at most, act on one.

    `dry_run=True` reports what it would have done without calling anyone -
    which is how you inspect an autonomous system's judgement before trusting
    it with a phone line.
    """
    state = clock.offset_state()
    result = TickResult(at=clock.now().isoformat(), simulated_clock=state.simulated)

    patients = _active_patients()
    result.patients_considered = len(patients)

    events.emit(
        "scheduler.tick",
        f"Scheduler tick: {len(patients)} active patient(s)"
        + (" [DRY RUN]" if dry_run else "")
        + (f" [SIMULATED CLOCK {state.offset_days:+.1f}d]" if state.simulated else ""),
        payload={
            "patients": len(patients),
            "dry_run": dry_run,
            "simulated_clock": state.simulated,
            "offset_days": state.offset_days,
        },
        simulated=state.simulated,
    )

    for patient_id in patients:
        # Rate limit the autonomy itself, not just the provider calls.
        hours = _hours_since_last_autonomous(patient_id)
        if hours is not None and hours < MIN_HOURS_BETWEEN_AUTONOMOUS_OUTREACH:
            result.skipped.append({
                "patient_id": patient_id,
                "reason": f"last outreach was {hours:.1f}h ago; minimum is "
                          f"{MIN_HOURS_BETWEEN_AUTONOMOUS_OUTREACH}h",
            })
            continue

        # detect() for the full picture (including what we are holding back
        # on, which the dashboard shows), but best() for the DECISION. best()
        # is the one place that applies the score threshold, so routing the
        # decision through it stops the scheduler from acting on a marginal
        # opportunity the planner would have declined.
        found = opportunity.detect(patient_id)
        live = [o for o in found if o.should_raise and o.actionable]
        result.opportunities_found += len(live)
        result.suppressed += len(found) - len(live)
        worth_raising = opportunity.best(patient_id)

        for o in found:
            if not (o.should_raise and o.actionable):
                # Record the restraint. "We deliberately did nothing, and here
                # is why" is the most important thing this system can log.
                events.emit(
                    "scheduler.suppressed",
                    f"Held back on {o.display_name or o.contact_id}: "
                    f"{o.hold_reason}",
                    payload={
                        "contact_id": o.contact_id,
                        "reason": o.hold_reason,
                        "until": o.suppressed_until,
                    },
                    simulated=state.simulated,
                )

        if worth_raising is None:
            detail = (
                f"{len(live)} drifting but none above the "
                f"{opportunity.MIN_SCORE_TO_RAISE} score threshold"
                if live else "no opportunity worth raising right now"
            )
            result.skipped.append({"patient_id": patient_id, "reason": detail})
            continue

        if result.outreach_started >= MAX_OUTREACH_PER_TICK:
            result.skipped.append({
                "patient_id": patient_id,
                "reason": f"per-tick cap of {MAX_OUTREACH_PER_TICK} already reached",
            })
            continue

        top = worth_raising
        events.emit(
            "scheduler.opportunity",
            f"Opportunity: {top.display_name or top.contact_id} - {top.reason}",
            payload={
                "contact_id": top.contact_id,
                "score": round(top.score, 2),
                "reason": top.reason,
                "dry_run": dry_run,
            },
            simulated=state.simulated,
        )

        if dry_run:
            result.acted_on.append({
                "patient_id": patient_id,
                "contact_id": top.contact_id,
                "display_name": top.display_name,
                "reason": top.reason,
                "would_call": True,
            })
            continue

        # Act. Every consent check still applies inside these two calls - this
        # only decides the moment, never the permission.
        from ..agents import orchestrator
        from . import voice

        sid = new_id("sess")
        opp = orchestrator.identify_opportunity(patient_id, session_id=sid)
        if not opp.get("ok"):
            result.skipped.append({
                "patient_id": patient_id,
                "reason": f"planner declined: {opp.get('reason')}",
            })
            continue

        plan = opp["plan"]
        try:
            call = voice.start_outbound_call(
                patient_id, plan["contact_id"], plan["id"],
                session_id=sid,
                # Keyed to the plan, so a scheduler that fires twice for the
                # same opportunity cannot dial twice.
                idempotency_key=f"auto:{plan['id']}",
                force_mode=force_mode,
            )
        except Exception as e:
            result.skipped.append({
                "patient_id": patient_id,
                "reason": f"outreach refused: {type(e).__name__}: {e}"[:200],
            })
            continue

        result.outreach_started += 1
        result.acted_on.append({
            "patient_id": patient_id,
            "contact_id": plan["contact_id"],
            "plan_id": plan["id"],
            "call_id": call.get("call_id"),
            "mode": call.get("mode"),
            "provider_call_id": call.get("provider_call_id"),
            "reason": top.reason,
        })

    events.emit(
        "scheduler.tick",
        f"Tick complete: {result.opportunities_found} live, "
        f"{result.suppressed} held back, {result.outreach_started} outreach started",
        payload=result.as_dict(),
        simulated=state.simulated,
    )
    return result
