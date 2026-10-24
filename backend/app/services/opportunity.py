"""Opportunity detection: noticing that a relationship has gone quiet.

This is the "backend independently detects meaningful changes in connection
patterns" step of the product directive. It runs *before* Claude is involved:
`detect()` decides whether there is an opportunity worth raising and why,
citing measured numbers from `services/baseline.py`. Claude is then asked - in
`agents/orchestrator.py` - what *activity* to propose. The distinction matters
for auditability: a judge can ask "why did it decide to reach out?" and get an
arithmetic answer, not a model's opinion.

**There is no LLM call anywhere in this file.** `detect()` is pure: it reads
the database and writes nothing, so the same rows always produce the same list
in the same order.

Harm reduction, not politeness
------------------------------
The target population is adults in the first 30 days after discharge from
inpatient psychiatric care. For that person, a system that reads "no reply" as
"try harder" is not merely annoying - it manufactures the exact feeling of
being a burden that makes someone stop answering their phone at all. So:

* A `plan_declined` event, or a call that went unanswered, puts that contact
  into a **cooling-off window** (`COOLING_OFF_DAYS`, 14). While it holds, the
  opportunity is returned with `score = 0.0` and a `suppressed_reason` written
  in plain words.
* The window is measured from the **most recent** suppressing signal, so
  further silence can only ever *extend* it. There is no code path that
  shortens it and no code path that raises a score because of a decline.
* A `plan_declined` event also counts as a *contact* in the cadence maths -
  the friend was reached and they answered. That pulls `deviation_ratio` DOWN,
  never up, so a decline cannot make the relationship look more urgent. The
  direction is deliberate and tested.
* The opportunity is still *returned* rather than hidden, because the dashboard
  should be able to show that the system noticed and chose to wait. Suppression
  you can see is a feature; suppression you cannot see is indistinguishable
  from a bug.

Privacy
-------
The only memory access is `memory.retrieve(..., audience="planning")`, whose
allow-list (`memory.AUDIENCE_VISIBILITY`) excludes `private_to_sam`. Every
value that survives that filter is additionally run through
`memory.assert_no_sensitive_leak` before it can appear in any output field, so
a *mislabelled* clinical row still cannot reach a `reason` or a `basis`.

See `docs/OPPORTUNITY_DETECTION.md`.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from ..db import q, q1
from . import clock
from . import memory, scheduling
from .baseline import Baseline, relationship_baseline

# --------------------------------------------------------------------------
# Tunables
# --------------------------------------------------------------------------

#: Cooling-off window after a decline or an unanswered call. Configurable, but
#: only ever upward: nothing in this module reads a smaller value.
COOLING_OFF_DAYS = 14

#: Call statuses that mean "we rang and nobody picked up". `failed` is included
#: deliberately: from the contact's side a failed call is still an attempt, and
#: the safe reading of a technical failure is to back off rather than retry in
#: a loop.
UNANSWERED_CALL_STATUSES: frozenset[str] = frozenset(
    {"no_answer", "busy", "voicemail", "failed"}
)

#: Only contacts who affirmatively opted in are ever considered.
ELIGIBLE_RELATIONSHIP_STATUSES: frozenset[str] = frozenset({"accepted"})

#: Ratio at which the deviation component of the score saturates. A gap 6x the
#: usual is already as notable as it needs to be; 20x should not score higher
#: and crowd out everyone else.
DEVIATION_SATURATION = 6.0

#: Confidence multiplier. A weak baseline cannot produce a strong score.
CONFIDENCE_WEIGHT: dict[str, float] = {"low": 0.5, "medium": 0.8, "high": 1.0}

#: Small additive nudge so a clearly dormant relationship outranks a merely
#: drifting one at the same ratio bucket boundary.
TREND_BONUS: dict[str, float] = {"stable": 0.0, "drifting": 0.10, "dormant": 0.20}

#: A relationship with no shared free time is still a real opportunity (the
#: patient could ask for a new window), but it is not actionable today.
NO_SHARED_TIME_FACTOR = 0.4

#: Scoring fallback when there is no computable median: elapsed days are
#: divided by this and the result is halved, because absolute time is much
#: weaker evidence than a deviation from a known habit.
NO_BASELINE_REFERENCE_DAYS = 35.0
NO_BASELINE_PENALTY = 0.5

#: How far ahead to look for shared free windows.
SLOT_WINDOW_DAYS = 14
SLOT_MIN_MINUTES = 60

#: `best()` will not raise anything below this. A relationship sitting on its
#: own normal cadence is not an opportunity, and proposing one anyway is how a
#: reconnection system turns into a nag. 0.25 is reached at roughly 1.5x the
#: usual gap on a high-confidence baseline with shared free time - i.e. the
#: `drifting` threshold - so `best()` stays silent while things are fine.
#: A relationship must be meaningfully OFF its own cadence before Still Here
#: says anything. Observed 2026-10-25: a contact at a 1.06x deviation - i.e.
#: exactly on schedule - scored 0.07 and that was enough to trigger an
#: outreach. Phoning someone because it has been 9 days when they normally
#: speak every 8.5 is the behaviour that makes a product like this
#: insufferable, so the floor is set above incidental noise.
MIN_SCORE_TO_RAISE = 0.25

#: Hard gate in front of the score: below this ratio the relationship is
#: simply current, whatever the score arithmetic says. Two independent guards,
#: because being too eager is the failure mode that matters here.
MIN_DEVIATION_TO_RAISE = 1.6


@dataclass
class Opportunity:
    """One detected (or deliberately suppressed) reconnection opportunity."""

    contact_id: str
    score: float
    reason: str
    baseline: Baseline
    shared_free_slots: list[dict[str, str]]
    suppressed_reason: str | None
    #: Set when the relationship is simply running at its normal cadence.
    #: Distinct from `suppressed_reason`, which means "we are deliberately
    #: backing off after a decline or an unanswered call". One is restraint,
    #: the other is nothing to say; the dashboard shows them differently and
    #: only cooling-off has a release date.
    below_floor_reason: str | None = None

    # --- supporting detail (beyond the §2 minimum) -----------------------
    display_name: str = ""
    relationship_label: str = ""
    suppressed_until: str | None = None
    suppressing_signal: dict[str, Any] | None = None
    supporting_memory_count: int = 0
    supporting_memories: list[str] = field(default_factory=list)
    detected_at: str = ""

    @property
    def should_raise(self) -> bool:
        """Worth saying something about: not backing off, and genuinely off
        its own cadence.

        Distinct from the pre-existing `actionable`, which additionally
        requires shared free time. Kept separate because "nothing has drifted"
        and "they have no overlapping free time this week" are different
        answers and the dashboard should not conflate them.
        """
        return self.suppressed_reason is None and self.below_floor_reason is None

    @property
    def hold_reason(self) -> str | None:
        """Why nothing is being raised, whichever reason applies."""
        if self.suppressed_reason:
            return self.suppressed_reason
        if self.below_floor_reason:
            return self.below_floor_reason
        if not self.shared_free_slots:
            return "no overlapping free time in the scheduling window"
        return None

    @property
    def suppressed(self) -> bool:
        return self.suppressed_reason is not None

    @property
    def actionable(self) -> bool:
        return not self.suppressed and bool(self.shared_free_slots)

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["baseline"] = self.baseline.as_dict()
        d["suppressed"] = self.suppressed
        d["actionable"] = self.actionable
        return d


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _parse(ts: str) -> datetime:
    d = datetime.fromisoformat(ts)
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _utcnow(now: datetime | None = None) -> datetime:
    if now is None:
        return clock.now()
    return now if now.tzinfo else now.replace(tzinfo=timezone.utc)


def _days(delta: timedelta) -> float:
    return delta.total_seconds() / 86400.0


def _ago(days: float) -> str:
    if days < 1.0:
        return "today"
    if days < 2.0:
        return "yesterday"
    return f"{days:.0f} days ago"


# --------------------------------------------------------------------------
# Suppression
# --------------------------------------------------------------------------


def suppressing_signals(
    patient_id: str,
    contact_id: str,
    *,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Every "do not escalate" signal for this pair, most recent first.

    Three independent sources, because a decline can be recorded in three
    places depending on how it arrived:

    1. a `contact_events` row of kind `plan_declined`;
    2. a `plan_participants.response = 'declined'` row on one of this
       patient's plans with this contact;
    3. a `calls` row for this contact that was never answered.

    Future-dated signals are ignored - a signal cannot suppress before it
    happens.
    """
    ref_now = _utcnow(now)
    out: list[dict[str, Any]] = []

    for r in q(
        "SELECT occurred_at, note FROM contact_events"
        " WHERE patient_id=? AND contact_id=? AND kind='plan_declined'",
        (patient_id, contact_id),
    ):
        out.append(
            {
                "source": "contact_event",
                "kind": "plan_declined",
                "at": r["occurred_at"],
                "detail": "declined a proposed plan",
            }
        )

    for r in q(
        "SELECT pp.response_at, p.activity FROM plan_participants pp"
        " JOIN plans p ON p.id = pp.plan_id"
        " WHERE pp.user_id=? AND p.patient_id=? AND p.contact_id=?"
        " AND pp.response='declined' AND pp.response_at IS NOT NULL",
        (contact_id, patient_id, contact_id),
    ):
        out.append(
            {
                "source": "plan_participant",
                "kind": "plan_declined",
                "at": r["response_at"],
                "detail": f"declined the proposed {r['activity']}",
            }
        )

    # Scoped to this patient AND contact. `calls.patient_id` was added for
    # exactly this query (db.py migration): matching on contact_id alone
    # over-suppressed, because an unanswered call placed on someone else's
    # behalf would also pause outreach here. Rows predating the column have
    # patient_id NULL, so they are still honoured for this contact rather than
    # silently ignored - keeping the conservative behaviour for old data while
    # new data is scoped correctly.
    placeholders = ",".join("?" for _ in UNANSWERED_CALL_STATUSES)
    statuses = sorted(UNANSWERED_CALL_STATUSES)
    for r in q(
        "SELECT id, status, requested_at, ended_at FROM calls"
        f" WHERE contact_id=? AND (patient_id=? OR patient_id IS NULL)"
        f" AND answered_at IS NULL AND status IN ({placeholders})",
        (contact_id, patient_id, *statuses),
    ):
        at = r["ended_at"] or r["requested_at"]
        if not at:
            continue
        out.append(
            {
                "source": "call",
                "kind": f"call_{r['status']}",
                "at": at,
                "detail": f"an outbound call ended {r['status'].replace('_', ' ')}",
            }
        )

    # Drop anything dated in the future, then sort newest first. The sort key
    # includes `kind` so equal timestamps order deterministically.
    out = [s for s in out if _parse(s["at"]) <= ref_now]
    out.sort(key=lambda s: (_parse(s["at"]), s["kind"]), reverse=True)
    return out


def cooling_off(
    patient_id: str,
    contact_id: str,
    *,
    now: datetime | None = None,
    window_days: int = COOLING_OFF_DAYS,
) -> tuple[str | None, str | None, dict[str, Any] | None]:
    """Is this contact inside a cooling-off window?

    Returns `(suppressed_reason, suppressed_until_iso, signal)`, all `None`
    when outreach is permitted.

    The window is anchored to the MOST RECENT signal, which is what makes it
    impossible to shorten: adding another decline moves the anchor forward and
    the window with it.
    """
    ref_now = _utcnow(now)
    # Guard against a caller passing a shorter window than the configured
    # floor. The contract is "never shorten the window after a decline", so the
    # floor wins.
    effective_days = max(int(window_days), COOLING_OFF_DAYS)

    signals = suppressing_signals(patient_id, contact_id, now=ref_now)
    if not signals:
        return None, None, None

    newest = signals[0]
    at = _parse(newest["at"])
    until = at + timedelta(days=effective_days)
    if until <= ref_now:
        return None, None, None

    elapsed = max(0.0, _days(ref_now - at))
    remaining = max(0.0, _days(until - ref_now))
    plural = "s" if round(remaining) != 1 else ""
    reason = (
        f"Outreach is paused: {newest['detail']} on {at.date().isoformat()} "
        f"({_ago(elapsed)}). Still Here waits {effective_days} days after a decline or "
        f"an unanswered call before raising this contact again, so nothing will be "
        f"proposed until {until.date().isoformat()} - {remaining:.0f} more day{plural}. "
        f"A decline or a silence never shortens that wait and never increases the "
        f"score; it only ever pushes the next check further out."
    )
    return reason, until.isoformat(), newest


# --------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------


def score_baseline(baseline: Baseline, *, has_shared_time: bool) -> float:
    """Deterministic 0.0-1.0 urgency score. No model, no randomness.

        deviation    = min(deviation_ratio, 6) / 6        (or a weak absolute
                       fallback when there is no median)
        raw          = deviation * confidence_weight + trend_bonus
        score        = min(raw, 1.0) * (1.0 if shared time else 0.4)
    """
    if baseline.deviation_ratio is not None:
        deviation = min(baseline.deviation_ratio, DEVIATION_SATURATION) / DEVIATION_SATURATION
    elif baseline.current_gap_days is not None:
        deviation = (
            min(baseline.current_gap_days / NO_BASELINE_REFERENCE_DAYS, 1.0)
            * NO_BASELINE_PENALTY
        )
    else:
        deviation = 0.0

    weight = CONFIDENCE_WEIGHT.get(baseline.confidence, 0.5)
    raw = deviation * weight + TREND_BONUS.get(baseline.trend, 0.0)
    score = min(max(raw, 0.0), 1.0)
    if not has_shared_time:
        score *= NO_SHARED_TIME_FACTOR
    return round(score, 4)


def _planning_memories(patient_id: str, contact_id: str, display_name: str) -> list[str]:
    """Planning-visible memories mentioning this contact.

    Two guards, matching `memory.shareable_context`'s approach: the audience
    allow-list excludes `private_to_sam`, and `assert_no_sensitive_leak`
    independently rejects any surviving row that still reads clinically.
    """
    first = (display_name or "").split()[0].lower() if display_name else ""
    out: list[str] = []
    for m in memory.retrieve(
        patient_id, query=first, subject_id=contact_id, audience="planning", limit=20
    ):
        if m.subject_id != contact_id and (not first or first not in m.value.lower()):
            continue
        try:
            memory.assert_no_sensitive_leak(m.value)
        except memory.MemoryError_:
            continue   # mislabelled clinical row - never surface it
        out.append(m.value)
    return out


def _reason_sentence(
    *,
    who: str,
    label: str,
    baseline: Baseline,
    slots: list[dict[str, str]],
    memory_count: int,
    suppressed: bool,
) -> str:
    """The judge-facing explanation. Assembled from measured values only."""
    parts: list[str] = []

    if baseline.deviation_ratio is not None and baseline.median_gap_days is not None:
        parts.append(
            f"{who} ({label or 'contact'}): normally in touch about every "
            f"{baseline.median_gap_days:.1f} days across "
            f"{baseline.events_considered} recorded contacts; it has been "
            f"{baseline.current_gap_days:.1f} days, which is "
            f"{baseline.deviation_ratio:.2f}x the usual gap."
        )
    elif baseline.current_gap_days is not None:
        parts.append(
            f"{who} ({label or 'contact'}): {baseline.events_considered} recorded "
            f"contact{'s' if baseline.events_considered != 1 else ''}, which is too "
            f"little to establish a normal cadence, so there is no ratio to quote; "
            f"the only measurable fact is that {baseline.current_gap_days:.1f} days "
            f"have passed since the last one."
        )
    else:
        parts.append(
            f"{who} ({label or 'contact'}): no contact history and no last-contact "
            f"date on record, so nothing can be measured yet."
        )

    parts.append(
        f"That reads as {baseline.trend} at {baseline.confidence} confidence"
        + (
            f" (fewer than 4 events, so the baseline is weak and this is a hint "
            f"rather than a measured habit)."
            if baseline.confidence == "low"
            else "."
        )
    )

    if slots:
        first = _parse(slots[0]["start_at"])
        parts.append(
            f"{len(slots)} shared free window{'s' if len(slots) != 1 else ''} in the next "
            f"{SLOT_WINDOW_DAYS} days, the earliest at "
            f"{first.strftime('%a %d %b %H:%M')} UTC."
        )
    else:
        parts.append(
            f"No shared free window in the next {SLOT_WINDOW_DAYS} days, so the score is "
            f"reduced to {NO_SHARED_TIME_FACTOR:g}x - the opportunity is real but not "
            f"actionable without new availability."
        )

    parts.append(
        f"{memory_count} planning-visible memor{'ies' if memory_count != 1 else 'y'} "
        f"reference {who}."
    )

    if suppressed:
        parts.append(
            "Scored 0.0 and held: see suppressed_reason. Detection still reports the "
            "observation so the pause is visible rather than silent."
        )
    return " ".join(parts)


# --------------------------------------------------------------------------
# The entry point
# --------------------------------------------------------------------------


def _below_deviation_floor(baseline) -> str | None:
    """Reason this relationship is simply current, or None if it has drifted."""
    ratio = getattr(baseline, "deviation_ratio", None)
    if ratio is None:
        return None
    if ratio < MIN_DEVIATION_TO_RAISE:
        median = getattr(baseline, "median_gap_days", None)
        current = getattr(baseline, "current_gap_days", None)
        detail = (
            f" (about {current:.0f} days against a usual {median:.0f})"
            if current is not None and median is not None else ""
        )
        return (
            f"this friendship is running at its normal cadence{detail}; "
            f"{ratio:.2f}x is below the {MIN_DEVIATION_TO_RAISE:g}x needed to say "
            "anything"
        )
    return None


def detect(patient_id: str, *, now: datetime | None = None) -> list[Opportunity]:
    """Detect reconnection opportunities for one patient.

    Pure and deterministic: reads the DB, writes nothing, calls no model.
    Ordered by score descending, then by `contact_id`, so the ordering is
    stable for a given database state. Suppressed contacts score 0.0 and
    therefore sort last.

    Only contacts in `ELIGIBLE_RELATIONSHIP_STATUSES` ("accepted") appear: a
    friend who was invited but never opted in is not a candidate for anything,
    and their absence here is a consent guarantee, not an oversight.
    """
    ref_now = _utcnow(now)

    if q1("SELECT id FROM users WHERE id=?", (patient_id,)) is None:
        return []

    rows = q(
        "SELECT r.contact_id, r.relationship_label, r.status, u.display_name"
        " FROM relationships r LEFT JOIN users u ON u.id = r.contact_id"
        " WHERE r.patient_id=? ORDER BY r.contact_id",
        (patient_id,),
    )

    out: list[Opportunity] = []
    seen: set[str] = set()
    for r in rows:
        contact_id = r["contact_id"]
        if r["status"] not in ELIGIBLE_RELATIONSHIP_STATUSES:
            continue
        if contact_id in seen:
            continue
        seen.add(contact_id)

        display_name = r["display_name"] or contact_id
        who = display_name.split()[0] if display_name else contact_id
        label = r["relationship_label"] or ""

        bl = relationship_baseline(patient_id, contact_id, now=ref_now)

        slots = [
            s.as_dict()
            for s in scheduling.find_free_slots(
                [patient_id, contact_id],
                window_start=ref_now.isoformat(),
                window_end=(ref_now + timedelta(days=SLOT_WINDOW_DAYS)).isoformat(),
                min_minutes=SLOT_MIN_MINUTES,
            )
        ]

        suppressed_reason, until, signal = cooling_off(patient_id, contact_id, now=ref_now)

        # The score is computed from the baseline, then ZEROED if suppressed.
        # Written in this order on purpose: there is no arithmetic path by
        # which a decline can contribute positively to a score.
        score = score_baseline(bl, has_shared_time=bool(slots))
        # Computed independently of cooling-off: a relationship can be both
        # inside a cooling-off window AND on-cadence, and the two release on
        # completely different conditions.
        below_floor = _below_deviation_floor(bl)
        # Only cooling-off zeroes the score. The floor is a GATE, not a score
        # mutation: a 1.2x deviation really is 1.2x, and reporting it as 0.0
        # would both discard honest information and break the ordering that
        # puts genuinely-paused contacts last.
        if suppressed_reason is not None:
            score = 0.0

        mems = _planning_memories(patient_id, contact_id, display_name)
        reason = _reason_sentence(
            who=who,
            label=label,
            baseline=bl,
            slots=slots,
            memory_count=len(mems),
            suppressed=suppressed_reason is not None,
        )

        out.append(
            Opportunity(
                contact_id=contact_id,
                score=score,
                reason=reason,
                baseline=bl,
                shared_free_slots=slots,
                suppressed_reason=suppressed_reason,
                below_floor_reason=below_floor,
                display_name=display_name,
                relationship_label=label,
                suppressed_until=until,
                suppressing_signal=signal,
                supporting_memory_count=len(mems),
                supporting_memories=mems[:3],
                detected_at=ref_now.isoformat(),
            )
        )

    out.sort(key=lambda o: (-o.score, o.contact_id))
    return out


def best(
    patient_id: str,
    *,
    now: datetime | None = None,
    min_score: float = MIN_SCORE_TO_RAISE,
) -> Opportunity | None:
    """The highest-scoring *non-suppressed* opportunity worth raising, or None.

    This is the convenience the planner wants. It will never hand back a
    contact inside a cooling-off window, and it returns `None` rather than
    falling back to a suppressed or merely-stable contact - "nothing to raise
    right now" is a correct, and frequently the kindest, answer.
    """
    for o in detect(patient_id, now=now):
        # should_raise covers cooling-off AND the on-cadence floor; the
        # original `actionable` additionally requires shared free time, which
        # a plan obviously needs.
        if not o.should_raise or not o.actionable or o.score < min_score:
            continue
        return o
    return None
