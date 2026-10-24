"""Longitudinal relationship baselines.

What this module is for
-----------------------
"It has been a while since you talked to Josh" is only a trustworthy statement
if the backend can say *how long* and *how long is normal for this particular
friendship*. A three-week-post-discharge story hardcoded into a demo cannot do
that. This module computes it from recorded history instead.

Every number here is derived from the `contact_events` table (plus
`relationships.last_contact_at` as a fallback when no events exist yet). There
is **no LLM call anywhere in this file** - these are pure, deterministic
functions over the database, so the same rows always produce the same
`Baseline`. Claude is asked to propose an *activity* afterwards
(`agents/orchestrator.py`); it is never asked whether an opportunity exists.

Design notes
------------
* **Median, not mean, for the typical gap.** Real friendships have outliers: a
  holiday, a work trip, one burst of five messages in a day. A single 90-day
  gap drags a mean badly and would make a genuinely dormant relationship look
  normal (or vice versa). The median is robust to those. The mean is still
  reported, because the difference between the two is itself informative, and
  `confidence` is downgraded when the gaps are erratic.
* **Low confidence is stated, not hidden.** Fewer than
  `MIN_EVENTS_FOR_BASELINE` (4) events cannot support a cadence claim, so
  `confidence` is `"low"` and `basis` says the baseline is weak rather than
  quoting a median computed from two data points as if it were fact.
* **Clinical data never enters.** This module reads `users`, `relationships`
  and `contact_events` only. It never reads `memories`; the one place memory is
  consulted at all is `opportunity.py`, and only through
  `memory.retrieve(..., audience="planning")`, which excludes `private_to_sam`.

See `docs/OPPORTUNITY_DETECTION.md` for the formulas and a worked example.
"""
from __future__ import annotations

import statistics
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Sequence

from ..db import ex, new_id, q, q1

# --------------------------------------------------------------------------
# Tunables. All thresholds live here so a reviewer can see every magic number
# in one place and a test can reference them instead of duplicating literals.
# --------------------------------------------------------------------------

#: Vocabulary frozen by specs/CONTRACTS_V3.md §2.
EVENT_KINDS: frozenset[str] = frozenset(
    {"call", "message", "met_in_person", "plan_completed", "plan_declined"}
)
EVENT_SOURCES: frozenset[str] = frozenset({"seed", "observed", "user_reported"})

#: Below this many events there is no credible cadence, only anecdote.
MIN_EVENTS_FOR_BASELINE = 4
#: At or above this many events the baseline may be called "high" confidence.
STRONG_EVIDENCE_EVENTS = 6

#: current_gap / median_gap thresholds for `trend`.
DRIFTING_RATIO = 1.5
DORMANT_RATIO = 2.5

#: Absolute day thresholds used ONLY when there is no computable median
#: (0 or 1 recorded events). Deliberately conservative.
NO_BASELINE_DRIFTING_DAYS = 21.0
NO_BASELINE_DORMANT_DAYS = 35.0

#: A median gap of 0 days (every recorded contact on the same calendar day)
#: would make `deviation_ratio` a division by zero. One day is the smallest
#: honest unit of "a gap" for this purpose; the basis sentence says so.
MIN_GAP_FLOOR_DAYS = 1.0

#: Gaps whose stdev exceeds (this x median) are "erratic" and cost one level
#: of confidence, however many events there are.
ERRATIC_STDEV_RATIO = 1.0

#: `relationships.last_contact_at` only overrides the event log when it is
#: fresher by more than this margin. Without the margin, a seed that writes the
#: relationship row and the matching event microseconds apart would look like a
#: disagreement between two sources and say so in the basis sentence.
REL_LAST_CONTACT_PREFERENCE_HOURS = 1.0


class BaselineError(Exception):
    pass


@dataclass
class Baseline:
    """One relationship's contact cadence, measured.

    `median_gap_days`, `mean_gap_days` and `deviation_ratio` are `None` when
    they genuinely cannot be computed (fewer than two recorded events). They
    are never filled in with a guess - a caller that needs a number must
    decide for itself what to do with the absence, and `basis` explains it.
    """

    contact_id: str
    events_considered: int
    median_gap_days: float | None
    mean_gap_days: float | None
    current_gap_days: float | None
    deviation_ratio: float | None
    trend: str                      # "stable" | "drifting" | "dormant"
    confidence: str                 # "low" | "medium" | "high"
    basis: str                      # human-readable, cites the real numbers

    # --- supporting detail (beyond the §2 minimum) -----------------------
    display_name: str = ""
    relationship_label: str = ""
    first_contact_at: str | None = None
    last_contact_at: str | None = None
    gaps_days: list[float] = field(default_factory=list)
    kinds_counted: dict[str, int] = field(default_factory=dict)
    has_future_dated_events: bool = False
    computed_at: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


# --------------------------------------------------------------------------
# Time helpers
# --------------------------------------------------------------------------


def _parse(ts: str) -> datetime:
    """Parse a stored ISO timestamp, treating a naive value as UTC."""
    d = datetime.fromisoformat(ts)
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _utcnow(now: datetime | None = None) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    return now if now.tzinfo else now.replace(tzinfo=timezone.utc)


def _days(delta: timedelta) -> float:
    return delta.total_seconds() / 86400.0


def _r(value: float | None, places: int = 2) -> float | None:
    """Round for display and for determinism across equal inputs."""
    return None if value is None else round(float(value), places)


def _fmt_day(ts: str | None) -> str:
    if not ts:
        return "unknown"
    return _parse(ts).date().isoformat()


# --------------------------------------------------------------------------
# Writes
# --------------------------------------------------------------------------


def record_contact_event(
    patient_id: str,
    contact_id: str,
    kind: str,
    *,
    occurred_at: str | datetime | None = None,
    source: str = "observed",
    note: str = "",
) -> str:
    """Append one contact event. Returns its id.

    Append-only on purpose: the history that a baseline is computed from must
    not be quietly rewritten, and a `plan_declined` row in particular is a
    safety signal that must survive.
    """
    if kind not in EVENT_KINDS:
        raise BaselineError(
            f"unknown contact event kind {kind!r}; expected one of {sorted(EVENT_KINDS)}"
        )
    if source not in EVENT_SOURCES:
        raise BaselineError(
            f"unknown contact event source {source!r}; expected one of {sorted(EVENT_SOURCES)}"
        )

    if occurred_at is None:
        ts = _utcnow().isoformat()
    elif isinstance(occurred_at, datetime):
        ts = _utcnow(occurred_at).isoformat()
    else:
        ts = _parse(occurred_at).isoformat()

    eid = new_id("cev")
    ex(
        "INSERT INTO contact_events (id, patient_id, contact_id, kind, occurred_at,"
        " source, note) VALUES (?,?,?,?,?,?,?)",
        (eid, patient_id, contact_id, kind, ts, source, note or ""),
    )
    return eid


def contact_events(
    patient_id: str,
    contact_id: str,
    *,
    kinds: Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    """Recorded events for one pair, oldest first.

    The ORDER BY is a safety net only - `relationship_baseline` re-sorts by
    parsed timestamp, because events can be inserted out of order (a
    `user_reported` backfill of last month's coffee) and string ordering of
    mixed-offset ISO timestamps is not chronological ordering.
    """
    rows = q(
        "SELECT * FROM contact_events WHERE patient_id=? AND contact_id=?"
        " ORDER BY occurred_at",
        (patient_id, contact_id),
    )
    out = [dict(r) for r in rows]
    if kinds is not None:
        wanted = set(kinds)
        out = [r for r in out if r["kind"] in wanted]
    return out


# --------------------------------------------------------------------------
# Baseline computation
# --------------------------------------------------------------------------


def _relationship_row(patient_id: str, contact_id: str):
    return q1(
        "SELECT r.relationship_label, r.status, r.last_contact_at, u.display_name"
        " FROM relationships r LEFT JOIN users u ON u.id = r.contact_id"
        " WHERE r.patient_id=? AND r.contact_id=?",
        (patient_id, contact_id),
    )


def _confidence(events_considered: int, gaps: Sequence[float], median: float | None) -> str:
    if events_considered < MIN_EVENTS_FOR_BASELINE or median is None:
        return "low"
    level = "high" if events_considered >= STRONG_EVIDENCE_EVENTS else "medium"
    if len(gaps) >= 2 and median > 0:
        if statistics.stdev(gaps) > ERRATIC_STDEV_RATIO * median:
            # The cadence exists but is too noisy to quote confidently.
            level = "medium" if level == "high" else "low"
    return level


def _trend(deviation_ratio: float | None, current_gap_days: float | None) -> str:
    if deviation_ratio is not None:
        if deviation_ratio >= DORMANT_RATIO:
            return "dormant"
        if deviation_ratio >= DRIFTING_RATIO:
            return "drifting"
        return "stable"
    # No computable median. Fall back to absolute elapsed time, conservatively.
    if current_gap_days is None:
        return "stable"
    if current_gap_days >= NO_BASELINE_DORMANT_DAYS:
        return "dormant"
    if current_gap_days >= NO_BASELINE_DRIFTING_DAYS:
        return "drifting"
    return "stable"


def _basis_sentence(
    *,
    who: str,
    events_considered: int,
    gaps: list[float],
    median: float | None,
    mean: float | None,
    current_gap_days: float | None,
    deviation_ratio: float | None,
    trend: str,
    confidence: str,
    first_at: str | None,
    last_at: str | None,
    same_day_only: bool,
    has_future: bool,
    fell_back_to_last_contact_at: bool,
) -> str:
    """Build the sentence shown on the judge-facing dashboard.

    It must quote the real computed numbers. Nothing here is a canned phrase
    selected from a list; every clause is assembled from measured values.
    """
    parts: list[str] = []

    if events_considered == 0:
        if current_gap_days is None:
            return (
                f"No contact history is recorded for {who} yet, and the relationship "
                "row carries no last-contact date either, so no cadence can be "
                "measured. Treating this as unknown rather than quiet."
            )
        return (
            f"No individual contact events are recorded for {who}; the only evidence "
            f"is the relationship's last-contact date ({_fmt_day(last_at)}), "
            f"{current_gap_days:.1f} days ago. One date cannot establish a normal "
            f"cadence, so this baseline is weak (confidence {confidence}) and the "
            f"trend reading of \"{trend}\" rests on elapsed time alone."
        )

    if events_considered == 1:
        elapsed = 0.0 if current_gap_days is None else current_gap_days
        return (
            f"Only 1 contact with {who} is on record ({_fmt_day(last_at)}), "
            f"{elapsed:.1f} days ago. A single event gives no gap to measure, "
            f"so there is no median to compare against; confidence is {confidence} and "
            f"the \"{trend}\" reading comes from elapsed time only, not from "
            f"{who}'s own history."
        )

    span = f"between {_fmt_day(first_at)} and {_fmt_day(last_at)}"
    gap_list = ", ".join(f"{g:.0f}" if float(g).is_integer() else f"{g:.1f}" for g in gaps[-8:])
    parts.append(
        f"{events_considered} contacts with {who} are on record {span}, "
        f"with {len(gaps)} gap{'s' if len(gaps) != 1 else ''} of {gap_list} days"
        + (" (most recent 8 shown)" if len(gaps) > 8 else "")
        + "."
    )

    if same_day_only:
        parts.append(
            f"Every one of those {events_considered} contacts fell on the same day, so "
            f"the median gap is 0 days; a {MIN_GAP_FLOOR_DAYS:.0f}-day floor is used for "
            "the ratio below because dividing by zero would be meaningless."
        )
    else:
        parts.append(
            f"The median gap is {median:.1f} days and the mean is {mean:.1f} days"
            + (
                f" - they differ by {abs((mean or 0) - (median or 0)):.1f} days, so the "
                "gaps are skewed and the median is the fairer summary."
                if median is not None and mean is not None and abs(mean - median) >= 1.0
                else ", so the cadence is fairly even."
            )
        )

    if current_gap_days is not None and deviation_ratio is not None:
        parts.append(
            f"It has now been {current_gap_days:.1f} days since the last contact, "
            f"{deviation_ratio:.2f}x the usual gap, which reads as {trend}."
        )
    elif current_gap_days is not None:
        parts.append(
            f"It has now been {current_gap_days:.1f} days since the last contact, "
            f"which reads as {trend}."
        )

    if events_considered < MIN_EVENTS_FOR_BASELINE:
        parts.append(
            f"With only {events_considered} events (fewer than the {MIN_EVENTS_FOR_BASELINE} "
            "needed) this baseline is weak: confidence is low and these figures should be "
            "read as a hint, not a measured habit."
        )
    else:
        parts.append(
            f"Confidence is {confidence} on {events_considered} events."
        )

    if has_future:
        parts.append(
            "At least one recorded event is dated in the future, so the current gap is "
            "reported as 0 days rather than a negative number."
        )
    if fell_back_to_last_contact_at:
        parts.append(
            "The current gap is measured from the relationship's last-contact date, "
            "which is more recent than any recorded event."
        )

    return " ".join(parts)


def relationship_baseline(
    patient_id: str,
    contact_id: str,
    *,
    now: datetime | None = None,
) -> Baseline:
    """Measure one relationship's cadence. Pure: reads the DB, writes nothing."""
    ref_now = _utcnow(now)
    rel = _relationship_row(patient_id, contact_id)

    if rel is not None and rel["display_name"]:
        display_name = rel["display_name"]
    else:
        urow = q1("SELECT display_name FROM users WHERE id=?", (contact_id,))
        display_name = urow["display_name"] if urow else contact_id
    who = display_name.split()[0] if display_name else contact_id
    label = rel["relationship_label"] if rel is not None else ""

    rows = contact_events(patient_id, contact_id)
    times = sorted(_parse(r["occurred_at"]) for r in rows)   # out-of-order safe
    kinds_counted: dict[str, int] = {}
    for r in rows:
        kinds_counted[r["kind"]] = kinds_counted.get(r["kind"], 0) + 1

    has_future = any(t > ref_now for t in times)

    # --- gaps between consecutive contacts ---
    gaps = [max(0.0, _days(b - a)) for a, b in zip(times, times[1:])]
    gaps = [round(g, 4) for g in gaps]
    median = statistics.median(gaps) if gaps else None
    mean = statistics.fmean(gaps) if gaps else None
    same_day_only = bool(gaps) and all(g == 0.0 for g in gaps)

    # --- when were we last actually in touch? ---
    past_times = [t for t in times if t <= ref_now]
    last_dt: datetime | None = max(past_times) if past_times else None
    fell_back = False
    rel_last = rel["last_contact_at"] if rel is not None else None
    if rel_last:
        rel_dt = _parse(rel_last)
        margin = timedelta(hours=REL_LAST_CONTACT_PREFERENCE_HOURS)
        if last_dt is None:
            last_dt = rel_dt
        elif rel_dt > last_dt + margin:
            # `relationships.last_contact_at` is maintained by the rest of the
            # app; if it is meaningfully fresher than our event log, believe it
            # and say so rather than reporting a stale gap.
            fell_back = True
            last_dt = rel_dt

    if times and not past_times:
        # Every recorded event is in the future. Nothing has elapsed.
        current_gap = 0.0 if last_dt is None else max(0.0, _days(ref_now - last_dt))
    elif last_dt is None:
        current_gap = None
    else:
        current_gap = max(0.0, _days(ref_now - last_dt))

    # --- deviation ratio ---
    if median is None or current_gap is None:
        deviation = None
    else:
        deviation = current_gap / max(median, MIN_GAP_FLOOR_DAYS)

    median_r, mean_r = _r(median), _r(mean)
    current_r, deviation_r = _r(current_gap), _r(deviation)
    trend = _trend(deviation_r, current_r)
    confidence = _confidence(len(times), gaps, median)

    first_at = times[0].isoformat() if times else None
    last_at = last_dt.isoformat() if last_dt is not None else None

    basis = _basis_sentence(
        who=who,
        events_considered=len(times),
        gaps=gaps,
        median=median_r,
        mean=mean_r,
        current_gap_days=current_r,
        deviation_ratio=deviation_r,
        trend=trend,
        confidence=confidence,
        first_at=first_at,
        last_at=last_at,
        same_day_only=same_day_only,
        has_future=has_future,
        fell_back_to_last_contact_at=fell_back,
    )

    return Baseline(
        contact_id=contact_id,
        events_considered=len(times),
        median_gap_days=median_r,
        mean_gap_days=mean_r,
        current_gap_days=current_r,
        deviation_ratio=deviation_r,
        trend=trend,
        confidence=confidence,
        basis=basis,
        display_name=display_name,
        relationship_label=label,
        first_contact_at=first_at,
        last_contact_at=last_at,
        gaps_days=[_r(g) for g in gaps],      # type: ignore[list-item]
        kinds_counted=kinds_counted,
        has_future_dated_events=has_future,
        computed_at=ref_now.isoformat(),
    )


def all_baselines(
    patient_id: str,
    *,
    now: datetime | None = None,
    statuses: Iterable[str] | None = None,
) -> list[Baseline]:
    """Baselines for every contact this patient has a relationship or history with.

    Sorted most-deviant first (then by contact_id) so the ordering is stable
    for a given database state. `statuses` filters on `relationships.status`;
    `None` means every relationship, including ones never accepted - callers
    that must not contact anyone unconsented (i.e. `opportunity.detect`) filter
    for themselves.
    """
    ids: list[str] = []
    seen: set[str] = set()
    for r in q(
        "SELECT contact_id, status FROM relationships WHERE patient_id=?"
        " ORDER BY contact_id",
        (patient_id,),
    ):
        if statuses is not None and r["status"] not in set(statuses):
            continue
        if r["contact_id"] not in seen:
            seen.add(r["contact_id"])
            ids.append(r["contact_id"])
    if statuses is None:
        # A contact can have history without a relationship row (e.g. imported
        # events). Include them so nothing silently disappears.
        for r in q(
            "SELECT DISTINCT contact_id FROM contact_events WHERE patient_id=?"
            " ORDER BY contact_id",
            (patient_id,),
        ):
            if r["contact_id"] not in seen:
                seen.add(r["contact_id"])
                ids.append(r["contact_id"])

    out = [relationship_baseline(patient_id, cid, now=now) for cid in ids]
    out.sort(key=lambda b: (-(b.deviation_ratio or -1.0), b.contact_id))
    return out


# --------------------------------------------------------------------------
# Demo history seeding
# --------------------------------------------------------------------------
#
# `demo_reset.py` is lead-owned, so the seed lives here and the lead calls it.
# These cadences are FICTIONAL, like everything else in the demo seed. They are
# chosen so that detection has to do real work: Josh's dormancy falls out of a
# short historical median against a long current gap, and Maya's normality
# falls out of a current gap that sits right on her own median. Change the
# numbers and the detector's output changes with them - nothing downstream
# hardcodes "47 days" or "Josh".

#: Gaps (in days) between consecutive contacts for a contact who has gone
#: quiet. Median 11.0, mean 11.0 - a steady, frequent friendship.
DEMO_DORMANT_GAPS: tuple[float, ...] = (8, 12, 9, 14, 11, 10, 13)
#: Days since the last contact for that same contact: far outside the habit.
DEMO_DORMANT_CURRENT_GAP = 47.0

#: A contact who is still in regular touch. Median 8.5, current gap 9.
DEMO_RECENT_GAPS: tuple[float, ...] = (6, 9, 7, 11, 8, 10)
DEMO_RECENT_CURRENT_GAP = 9.0

#: Cycled over the seeded events. `plan_declined` is deliberately absent: a
#: seeded decline would suppress outreach for 14 days and silence the demo.
DEMO_KIND_CYCLE: tuple[str, ...] = (
    "call", "message", "met_in_person", "plan_completed", "message", "call",
    "met_in_person", "message",
)


def seed_demo_history(
    patient_id: str,
    contact_id: str,
    *,
    gaps_days: Sequence[float],
    current_gap_days: float,
    now: datetime | None = None,
    kind_cycle: Sequence[str] = DEMO_KIND_CYCLE,
    source: str = "seed",
    note: str = "fictional demo history",
    clear: bool = True,
) -> list[str]:
    """Generate a plausible contact history ending `current_gap_days` ago.

    `len(gaps_days) + 1` events are written, walking backwards from
    `now - current_gap_days`. `clear=True` first removes this pair's existing
    events so repeated demo resets stay idempotent.
    """
    ref_now = _utcnow(now)
    if clear:
        ex(
            "DELETE FROM contact_events WHERE patient_id=? AND contact_id=?",
            (patient_id, contact_id),
        )

    stamps: list[datetime] = [ref_now - timedelta(days=float(current_gap_days))]
    for gap in reversed(list(gaps_days)):
        stamps.append(stamps[-1] - timedelta(days=float(gap)))
    stamps.reverse()   # oldest first

    ids: list[str] = []
    cycle = list(kind_cycle) or ["call"]
    for i, when in enumerate(stamps):
        ids.append(
            record_contact_event(
                patient_id,
                contact_id,
                cycle[i % len(cycle)],
                occurred_at=when,
                source=source,
                note=note,
            )
        )
    return ids


def seed_demo_history_defaults(
    patient_id: str,
    *,
    dormant_contact_id: str,
    recent_contact_id: str | None = None,
    now: datetime | None = None,
) -> dict[str, list[str]]:
    """One call for `demo_reset.seed()`: a dormant friend and a recent one.

    Returns `{contact_id: [event_id, ...]}`.
    """
    out: dict[str, list[str]] = {
        dormant_contact_id: seed_demo_history(
            patient_id,
            dormant_contact_id,
            gaps_days=DEMO_DORMANT_GAPS,
            current_gap_days=DEMO_DORMANT_CURRENT_GAP,
            now=now,
        )
    }
    if recent_contact_id:
        out[recent_contact_id] = seed_demo_history(
            patient_id,
            recent_contact_id,
            gaps_days=DEMO_RECENT_GAPS,
            current_gap_days=DEMO_RECENT_CURRENT_GAP,
            now=now,
        )
    return out
