"""Tests for longitudinal baselines and opportunity detection.

The graded behaviours (specs/CONTRACTS_V3.md §2) are:

* median/mean gap arithmetic against hand-computed fixtures;
* `deviation_ratio = current_gap / median_gap`;
* every `trend` value is reachable;
* fewer than 4 events ⇒ `confidence == "low"` and the basis says so;
* a decline or an unanswered call SUPPRESSES for a cooling-off window, never
  shortens it and never raises the score;
* no clinical term ever reaches a `reason` or a `basis`;
* every degenerate input (zero / one / same-day / out-of-order / future-dated
  events, no shared availability) is handled without crashing;
* detection is deterministic.

`fresh_db` (autouse, in conftest.py) has already wiped the database and applied
the fictional demo seed before each test here.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import pytest

from app.db import ex, q1
from app.services import baseline as bl
from app.services import memory, opportunity as opp
from app.services import plans

SAM = "user_sam"
JOSH = "user_josh"
MAYA = "user_maya"
DANIEL = "user_daniel"

NOW = datetime(2026, 10, 25, 12, 0, 0, tzinfo=timezone.utc)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _clear(patient_id: str = SAM, contact_id: str = JOSH) -> None:
    ex(
        "DELETE FROM contact_events WHERE patient_id=? AND contact_id=?",
        (patient_id, contact_id),
    )


def _clear_last_contact(contact_id: str = JOSH) -> None:
    """Remove the seeded `relationships.last_contact_at`.

    Several tests need the baseline to depend only on the events they wrote;
    the seeded last-contact date is a deliberate secondary source and would
    otherwise override a hand-built fixture.
    """
    ex(
        "UPDATE relationships SET last_contact_at=NULL WHERE patient_id=? AND contact_id=?",
        (SAM, contact_id),
    )


def _events_at(day_offsets: list[float], *, contact_id: str = JOSH, kind: str = "call") -> None:
    """Write one event per offset, expressed in days BEFORE `NOW`."""
    for off in day_offsets:
        bl.record_contact_event(
            SAM, contact_id, kind,
            occurred_at=NOW - timedelta(days=off),
            source="user_reported",
        )


def _fixture(contact_id: str = JOSH, *, gaps, current_gap) -> bl.Baseline:
    _clear(contact_id=contact_id)
    _clear_last_contact(contact_id)
    bl.seed_demo_history(
        SAM, contact_id, gaps_days=gaps, current_gap_days=current_gap,
        now=NOW, source="user_reported", clear=True,
    )
    return bl.relationship_baseline(SAM, contact_id, now=NOW)


# --------------------------------------------------------------------------
# schema / write path
# --------------------------------------------------------------------------


def test_contact_events_table_exists_after_init():
    row = q1("SELECT name FROM sqlite_master WHERE type='table' AND name='contact_events'")
    assert row is not None, "the additive migration in db.py did not create contact_events"


def test_record_contact_event_roundtrip():
    _clear()
    eid = bl.record_contact_event(SAM, JOSH, "met_in_person", note="coffee")
    assert eid.startswith("cev_")
    rows = bl.contact_events(SAM, JOSH)
    assert len(rows) == 1
    assert rows[0]["kind"] == "met_in_person"
    assert rows[0]["source"] == "observed"
    assert rows[0]["note"] == "coffee"


@pytest.mark.parametrize("kind", sorted(bl.EVENT_KINDS))
def test_every_frozen_kind_is_accepted(kind):
    _clear()
    assert bl.record_contact_event(SAM, JOSH, kind)


def test_unknown_kind_and_source_are_rejected():
    with pytest.raises(bl.BaselineError):
        bl.record_contact_event(SAM, JOSH, "telepathy")
    with pytest.raises(bl.BaselineError):
        bl.record_contact_event(SAM, JOSH, "call", source="guessed")


# --------------------------------------------------------------------------
# gap arithmetic against hand-computed fixtures
# --------------------------------------------------------------------------


def test_median_and_mean_gaps_hand_computed():
    # Events 40, 30, 22, 12, 5 days ago -> gaps 10, 8, 10, 7 days.
    # sorted gaps = [7, 8, 10, 10]; median = (8 + 10) / 2 = 9.0
    # mean = (10 + 8 + 10 + 7) / 4 = 8.75
    _clear()
    _clear_last_contact()
    _events_at([40, 30, 22, 12, 5])
    b = bl.relationship_baseline(SAM, JOSH, now=NOW)

    assert b.events_considered == 5
    assert b.gaps_days == [10.0, 8.0, 10.0, 7.0]
    assert b.median_gap_days == 9.0
    assert b.mean_gap_days == 8.75
    assert b.current_gap_days == 5.0


def test_median_is_robust_to_an_outlier_that_wrecks_the_mean():
    # gaps 7, 7, 7, 7, 200 -> median 7, mean 45.6. The whole reason the module
    # prefers the median: a single 200-day gap must not redefine "normal".
    _clear()
    _clear_last_contact()
    _events_at([231, 31, 24, 17, 10, 3])
    b = bl.relationship_baseline(SAM, JOSH, now=NOW)

    assert b.gaps_days == [200.0, 7.0, 7.0, 7.0, 7.0]
    assert b.median_gap_days == 7.0
    assert b.mean_gap_days == pytest.approx(45.6)
    # 3 days on a 7-day median is normal; the mean would have called it early.
    assert b.deviation_ratio == pytest.approx(3.0 / 7.0, abs=0.01)
    assert b.trend == "stable"


def test_deviation_ratio_is_current_gap_over_median():
    b = _fixture(gaps=(10, 10, 10, 10), current_gap=30)
    assert b.median_gap_days == 10.0
    assert b.current_gap_days == 30.0
    assert b.deviation_ratio == 3.0


def test_seeded_demo_numbers_are_exactly_what_the_docs_claim():
    """The worked example in docs/OPPORTUNITY_DETECTION.md, asserted."""
    bl.seed_demo_history_defaults(
        SAM, dormant_contact_id=JOSH, recent_contact_id=MAYA, now=NOW
    )
    josh = bl.relationship_baseline(SAM, JOSH, now=NOW)
    maya = bl.relationship_baseline(SAM, MAYA, now=NOW)

    assert (josh.events_considered, josh.median_gap_days, josh.mean_gap_days) == (8, 11.0, 11.0)
    assert josh.current_gap_days == 47.0
    assert josh.deviation_ratio == 4.27
    assert (josh.trend, josh.confidence) == ("dormant", "high")

    assert (maya.events_considered, maya.median_gap_days) == (7, 8.5)
    assert maya.current_gap_days == 9.0
    assert maya.deviation_ratio == 1.06
    assert maya.trend == "stable"


def test_seeded_history_contains_no_decline():
    """A seeded `plan_declined` would silently suppress the whole demo."""
    bl.seed_demo_history_defaults(
        SAM, dormant_contact_id=JOSH, recent_contact_id=MAYA, now=NOW
    )
    for cid in (JOSH, MAYA):
        kinds = {r["kind"] for r in bl.contact_events(SAM, cid)}
        assert "plan_declined" not in kinds


def test_seed_demo_history_is_idempotent():
    bl.seed_demo_history_defaults(SAM, dormant_contact_id=JOSH, now=NOW)
    first = bl.relationship_baseline(SAM, JOSH, now=NOW)
    bl.seed_demo_history_defaults(SAM, dormant_contact_id=JOSH, now=NOW)
    second = bl.relationship_baseline(SAM, JOSH, now=NOW)
    assert first.events_considered == second.events_considered == 8
    assert first.as_dict() == second.as_dict()


# --------------------------------------------------------------------------
# trend
# --------------------------------------------------------------------------


def test_trend_stable():
    b = _fixture(gaps=(10, 10, 10, 10, 10, 10), current_gap=11)
    assert b.deviation_ratio == 1.1
    assert b.trend == "stable"


def test_trend_drifting():
    b = _fixture(gaps=(10, 10, 10, 10, 10, 10), current_gap=18)
    assert b.deviation_ratio == 1.8
    assert bl.DRIFTING_RATIO <= b.deviation_ratio < bl.DORMANT_RATIO
    assert b.trend == "drifting"


def test_trend_dormant():
    b = _fixture(gaps=(10, 10, 10, 10, 10, 10), current_gap=40)
    assert b.deviation_ratio == 4.0
    assert b.trend == "dormant"


def test_trend_thresholds_are_inclusive_at_the_boundary():
    assert bl._trend(bl.DRIFTING_RATIO, 10.0) == "drifting"
    assert bl._trend(bl.DORMANT_RATIO, 10.0) == "dormant"
    assert bl._trend(bl.DRIFTING_RATIO - 0.01, 10.0) == "stable"


def test_trend_without_a_median_falls_back_to_absolute_days():
    # One event only: no gap to measure, so trend comes from elapsed time.
    _clear()
    _clear_last_contact()
    _events_at([40])
    b = bl.relationship_baseline(SAM, JOSH, now=NOW)
    assert b.median_gap_days is None
    assert b.deviation_ratio is None
    assert b.current_gap_days == 40.0
    assert b.trend == "dormant"
    assert "elapsed time" in b.basis


# --------------------------------------------------------------------------
# confidence
# --------------------------------------------------------------------------


@pytest.mark.parametrize("n_events", [0, 1, 2, 3])
def test_fewer_than_four_events_is_low_confidence_and_says_so(n_events):
    _clear()
    _clear_last_contact()
    _events_at([30, 20, 10, 2][:n_events])
    b = bl.relationship_baseline(SAM, JOSH, now=NOW)

    assert b.events_considered == n_events
    assert b.confidence == "low", "a baseline from <4 events must not claim precision"
    assert n_events < bl.MIN_EVENTS_FOR_BASELINE
    weak = ("weak", "no cadence can be measured", "no gap to measure")
    assert any(w in b.basis for w in weak), b.basis


def test_four_events_reaches_medium_and_six_reaches_high():
    assert _fixture(gaps=(10, 10, 10), current_gap=10).confidence == "medium"
    assert _fixture(gaps=(10, 10, 10, 10, 10), current_gap=10).confidence == "high"


def test_erratic_gaps_cost_a_level_of_confidence():
    # stdev of these gaps far exceeds the median, so 8 events still cannot
    # support a "high" reading.
    b = _fixture(gaps=(1, 1, 90, 1, 1, 120, 1), current_gap=5)
    assert b.events_considered == 8
    assert b.confidence == "medium"


def test_low_confidence_caps_the_opportunity_score():
    """A weak baseline cannot produce a strong score, even at a huge ratio."""
    strong = _fixture(gaps=(10,) * 7, current_gap=60)
    weak = _fixture(contact_id=MAYA, gaps=(10,), current_gap=60)
    assert strong.confidence == "high" and weak.confidence == "low"
    s_strong = opp.score_baseline(strong, has_shared_time=True)
    s_weak = opp.score_baseline(weak, has_shared_time=True)
    assert s_weak < s_strong


# --------------------------------------------------------------------------
# degenerate inputs
# --------------------------------------------------------------------------


def test_zero_events_with_no_last_contact_date():
    _clear()
    _clear_last_contact()
    b = bl.relationship_baseline(SAM, JOSH, now=NOW)
    assert b.events_considered == 0
    assert b.median_gap_days is None and b.mean_gap_days is None
    assert b.current_gap_days is None and b.deviation_ratio is None
    assert b.trend == "stable" and b.confidence == "low"
    assert "no cadence can be measured" in b.basis


def test_zero_events_falls_back_to_relationship_last_contact_at():
    _clear()
    ex(
        "UPDATE relationships SET last_contact_at=? WHERE patient_id=? AND contact_id=?",
        ((NOW - timedelta(days=50)).isoformat(), SAM, JOSH),
    )
    b = bl.relationship_baseline(SAM, JOSH, now=NOW)
    assert b.events_considered == 0
    assert b.current_gap_days == 50.0
    assert b.deviation_ratio is None       # one date is not a cadence
    assert b.confidence == "low"
    assert "50.0 days ago" in b.basis


def test_one_event_does_not_divide_by_zero():
    _clear()
    _clear_last_contact()
    _events_at([3])
    b = bl.relationship_baseline(SAM, JOSH, now=NOW)
    assert b.gaps_days == []
    assert b.median_gap_days is None
    assert b.deviation_ratio is None
    assert b.current_gap_days == 3.0


def test_all_events_on_the_same_day_uses_a_one_day_floor():
    _clear()
    _clear_last_contact()
    same = NOW - timedelta(days=4)
    for _ in range(5):
        bl.record_contact_event(SAM, JOSH, "message", occurred_at=same)
    b = bl.relationship_baseline(SAM, JOSH, now=NOW)

    assert b.events_considered == 5
    assert b.median_gap_days == 0.0 and b.mean_gap_days == 0.0
    # 4 days elapsed against a floored 1-day median.
    assert b.deviation_ratio == 4.0
    assert "same day" in b.basis and "floor" in b.basis


def test_events_inserted_out_of_order_are_sorted_before_measuring():
    _clear()
    _clear_last_contact()
    _events_at([10, 40, 2, 30, 20])         # deliberately shuffled
    b = bl.relationship_baseline(SAM, JOSH, now=NOW)
    assert b.gaps_days == [10.0, 10.0, 10.0, 8.0]
    assert b.median_gap_days == 10.0
    assert b.current_gap_days == 2.0


def test_future_dated_event_never_produces_a_negative_gap():
    _clear()
    _clear_last_contact()
    _events_at([20, 10, -5])                # one event five days in the future
    b = bl.relationship_baseline(SAM, JOSH, now=NOW)
    assert b.has_future_dated_events is True
    assert b.current_gap_days == 10.0       # measured from the last PAST event
    assert b.current_gap_days >= 0.0
    assert "future" in b.basis


def test_every_event_in_the_future_gives_a_zero_gap():
    _clear()
    _clear_last_contact()
    _events_at([-20, -10])
    b = bl.relationship_baseline(SAM, JOSH, now=NOW)
    assert b.current_gap_days == 0.0
    assert b.trend == "stable"


def test_unknown_contact_does_not_crash():
    b = bl.relationship_baseline(SAM, "user_nobody", now=NOW)
    assert b.events_considered == 0
    assert b.contact_id == "user_nobody"
    assert b.current_gap_days is None
    assert isinstance(b.basis, str) and b.basis


def test_detect_for_unknown_patient_returns_empty():
    assert opp.detect("user_nobody", now=NOW) == []


def test_contact_with_no_shared_availability_is_scored_down_but_still_reported():
    _clear_last_contact(MAYA)
    bl.seed_demo_history(
        SAM, MAYA, gaps_days=(10, 10, 10, 10, 10, 10), current_gap_days=60, now=NOW
    )
    found = {o.contact_id: o for o in opp.detect(SAM, now=NOW)}
    maya = found[MAYA]
    assert maya.shared_free_slots == []     # the seed gives Maya no overlap
    assert maya.actionable is False
    assert "No shared free window" in maya.reason
    # 6.0x saturated ratio, high confidence, dormant bonus -> 1.0, * 0.4
    assert maya.score == pytest.approx(1.0 * opp.NO_SHARED_TIME_FACTOR, abs=1e-4)


def test_all_baselines_covers_every_relationship_and_is_ordered():
    bl.seed_demo_history_defaults(
        SAM, dormant_contact_id=JOSH, recent_contact_id=MAYA, now=NOW
    )
    out = bl.all_baselines(SAM, now=NOW)
    assert [b.contact_id for b in out] == [JOSH, MAYA, DANIEL]


def test_all_baselines_can_filter_to_accepted_relationships():
    out = bl.all_baselines(SAM, now=NOW, statuses=["accepted"])
    assert {b.contact_id for b in out} == {JOSH, MAYA}


# --------------------------------------------------------------------------
# consent: a contact who never opted in is never an opportunity
# --------------------------------------------------------------------------


def test_never_accepted_contact_is_not_an_opportunity():
    bl.seed_demo_history(
        SAM, DANIEL, gaps_days=(5, 5, 5, 5, 5, 5), current_gap_days=200, now=NOW
    )
    ids = {o.contact_id for o in opp.detect(SAM, now=NOW)}
    assert DANIEL not in ids, "Daniel never accepted; he must never be raised"
    assert q1(
        "SELECT status FROM relationships WHERE patient_id=? AND contact_id=?",
        (SAM, DANIEL),
    )["status"] == "invited"


# --------------------------------------------------------------------------
# SUPPRESSION - the most important behaviour in the module
# --------------------------------------------------------------------------


def _dormant_josh() -> None:
    bl.seed_demo_history_defaults(SAM, dormant_contact_id=JOSH, now=NOW)


def _josh(now=NOW) -> opp.Opportunity:
    found = {o.contact_id: o for o in opp.detect(SAM, now=now)}
    return found[JOSH]


def test_decline_suppresses_and_zeroes_the_score():
    _dormant_josh()
    before = _josh()
    assert before.suppressed_reason is None
    assert before.score > 0.5

    bl.record_contact_event(
        SAM, JOSH, "plan_declined", occurred_at=NOW - timedelta(days=1)
    )
    after = _josh()

    assert after.suppressed_reason is not None
    assert after.score == 0.0
    assert after.score <= before.score, "a decline must never raise the score"


def test_decline_suppression_reason_is_plain_words_with_real_dates():
    _dormant_josh()
    declined_at = NOW - timedelta(days=3)
    bl.record_contact_event(SAM, JOSH, "plan_declined", occurred_at=declined_at)
    o = _josh()

    r = o.suppressed_reason
    assert r is not None
    assert "paused" in r.lower()
    assert declined_at.date().isoformat() in r
    assert str(opp.COOLING_OFF_DAYS) in r
    assert (declined_at + timedelta(days=opp.COOLING_OFF_DAYS)).date().isoformat() in r
    assert "11 more days" in r
    assert o.suppressed_until == (
        declined_at + timedelta(days=opp.COOLING_OFF_DAYS)
    ).isoformat()


def test_suppression_lasts_the_full_window_and_then_releases():
    _dormant_josh()
    declined_at = NOW - timedelta(days=opp.COOLING_OFF_DAYS)
    bl.record_contact_event(SAM, JOSH, "plan_declined", occurred_at=declined_at)

    # One second before the window closes: still held.
    just_inside = declined_at + timedelta(days=opp.COOLING_OFF_DAYS, seconds=-1)
    assert _josh(now=just_inside).suppressed_reason is not None

    # At the boundary: released.
    at_boundary = declined_at + timedelta(days=opp.COOLING_OFF_DAYS)
    released = _josh(now=at_boundary)
    assert released.suppressed_reason is None
    assert released.score > 0.0


def test_every_day_inside_the_window_is_suppressed():
    _dormant_josh()
    declined_at = NOW - timedelta(days=opp.COOLING_OFF_DAYS)
    bl.record_contact_event(SAM, JOSH, "plan_declined", occurred_at=declined_at)
    for day in range(opp.COOLING_OFF_DAYS):
        o = _josh(now=declined_at + timedelta(days=day))
        assert o.suppressed_reason is not None, f"day {day} should still be held"
        assert o.score == 0.0


def test_a_second_decline_extends_the_window_and_never_shortens_it():
    _dormant_josh()
    first = NOW - timedelta(days=10)
    bl.record_contact_event(SAM, JOSH, "plan_declined", occurred_at=first)
    after_first = _josh()
    assert after_first.suppressed_until is not None
    remaining_first = datetime.fromisoformat(after_first.suppressed_until) - NOW

    second = NOW - timedelta(days=2)
    bl.record_contact_event(SAM, JOSH, "plan_declined", occurred_at=second)
    after_second = _josh()
    remaining_second = datetime.fromisoformat(after_second.suppressed_until) - NOW

    assert remaining_second > remaining_first, "a second decline must extend the pause"
    assert after_second.score == 0.0


def test_more_silence_cannot_shorten_the_window_or_raise_the_score():
    """The exact anti-nag property: as more time passes with no contact, the
    pause must not shrink and the score must not lift off zero."""
    _dormant_josh()
    before = _josh()
    declined_at = NOW - timedelta(days=1)
    bl.record_contact_event(SAM, JOSH, "plan_declined", occurred_at=declined_at)
    expected_until = (declined_at + timedelta(days=opp.COOLING_OFF_DAYS)).isoformat()

    checked = []
    for extra in range(0, opp.COOLING_OFF_DAYS + 1):
        o = _josh(now=NOW + timedelta(days=extra))
        if o.suppressed_reason is None:
            break
        assert o.score == 0.0
        assert o.score <= before.score
        assert o.suppressed_until == expected_until, "the window must never move earlier"
        checked.append(extra)

    # Held every day from the decline until the window closes, then released.
    assert checked == list(range(opp.COOLING_OFF_DAYS - 1))


def test_a_decline_can_only_lower_the_deviation_ratio_never_raise_it():
    """A `plan_declined` row IS a contact: the friend was reached and answered.
    It therefore resets the gap and pulls the ratio DOWN. That direction is
    load-bearing - there is no arithmetic by which refusing makes the system
    consider the relationship more urgent."""
    _dormant_josh()
    before = _josh().baseline
    bl.record_contact_event(SAM, JOSH, "plan_declined", occurred_at=NOW - timedelta(days=1))
    after = _josh().baseline

    assert after.current_gap_days < before.current_gap_days
    assert after.deviation_ratio < before.deviation_ratio


def test_cooling_off_window_cannot_be_configured_shorter_than_the_floor():
    _dormant_josh()
    declined_at = NOW - timedelta(days=5)
    bl.record_contact_event(SAM, JOSH, "plan_declined", occurred_at=declined_at)
    # A caller asking for a 1-day window gets the 14-day floor anyway.
    reason, until, _signal = opp.cooling_off(SAM, JOSH, now=NOW, window_days=1)
    assert reason is not None
    assert until == (declined_at + timedelta(days=opp.COOLING_OFF_DAYS)).isoformat()


def test_cooling_off_window_is_configurable_upward():
    _dormant_josh()
    declined_at = NOW - timedelta(days=20)
    bl.record_contact_event(SAM, JOSH, "plan_declined", occurred_at=declined_at)
    assert opp.cooling_off(SAM, JOSH, now=NOW)[0] is None       # 14 days elapsed
    reason, until, _ = opp.cooling_off(SAM, JOSH, now=NOW, window_days=30)
    assert reason is not None
    assert until == (declined_at + timedelta(days=30)).isoformat()


@pytest.mark.parametrize("status", sorted(opp.UNANSWERED_CALL_STATUSES))
def test_unanswered_call_suppresses(status):
    _dormant_josh()
    ex(
        "INSERT INTO calls (id, contact_id, provider, status, mode, requested_at,"
        " ended_at) VALUES (?,?,?,?,?,?,?)",
        (
            f"call_test_{status}", JOSH, "cartesia", status, "simulated",
            (NOW - timedelta(days=2)).isoformat(),
            (NOW - timedelta(days=2)).isoformat(),
        ),
    )
    o = _josh()
    assert o.suppressed_reason is not None
    assert o.score == 0.0
    assert status.replace("_", " ") in o.suppressed_reason


def test_an_answered_call_does_not_suppress():
    _dormant_josh()
    ex(
        "INSERT INTO calls (id, contact_id, provider, status, mode, requested_at,"
        " answered_at, ended_at) VALUES (?,?,?,?,?,?,?,?)",
        (
            "call_answered", JOSH, "cartesia", "completed", "simulated",
            (NOW - timedelta(days=2)).isoformat(),
            (NOW - timedelta(days=2)).isoformat(),
            (NOW - timedelta(days=2)).isoformat(),
        ),
    )
    o = _josh()
    assert o.suppressed_reason is None
    assert o.score > 0.0


def test_plan_participant_decline_also_suppresses():
    """A decline recorded through the plan flow suppresses just like an event."""
    _dormant_josh()
    plan = plans.create_plan(SAM, JOSH, "Coffee", (NOW + timedelta(days=3)).isoformat())
    plans.record_response(plan["id"], JOSH, "declined")

    o = _josh()
    assert o.suppressed_reason is not None
    assert o.score == 0.0
    assert "declined" in o.suppressed_reason.lower()


def test_future_dated_decline_does_not_suppress_before_it_happens():
    _dormant_josh()
    bl.record_contact_event(
        SAM, JOSH, "plan_declined", occurred_at=NOW + timedelta(days=5)
    )
    assert _josh().suppressed_reason is None


def test_best_refuses_to_return_a_suppressed_contact():
    _dormant_josh()
    assert opp.best(SAM, now=NOW).contact_id == JOSH
    bl.record_contact_event(SAM, JOSH, "plan_declined", occurred_at=NOW)
    chosen = opp.best(SAM, now=NOW)
    assert chosen is None or chosen.contact_id != JOSH


def test_best_stays_silent_when_everyone_is_on_their_normal_cadence():
    bl.seed_demo_history(
        SAM, JOSH, gaps_days=(10, 10, 10, 10, 10, 10), current_gap_days=10, now=NOW
    )
    bl.seed_demo_history(
        SAM, MAYA, gaps_days=(10, 10, 10, 10, 10, 10), current_gap_days=9, now=NOW
    )
    assert opp.best(SAM, now=NOW) is None


def test_suppressed_opportunity_is_still_reported_not_hidden():
    _dormant_josh()
    bl.record_contact_event(SAM, JOSH, "plan_declined", occurred_at=NOW)
    ids = [o.contact_id for o in opp.detect(SAM, now=NOW)]
    assert JOSH in ids, "a visible pause is a feature; a hidden one looks like a bug"


def test_suppressed_contacts_sort_last():
    bl.seed_demo_history_defaults(
        SAM, dormant_contact_id=JOSH, recent_contact_id=MAYA, now=NOW
    )
    bl.record_contact_event(SAM, JOSH, "plan_declined", occurred_at=NOW)
    order = [o.contact_id for o in opp.detect(SAM, now=NOW)]
    assert order.index(JOSH) == len(order) - 1


# --------------------------------------------------------------------------
# reasons cite real numbers and never leak clinical content
# --------------------------------------------------------------------------


def test_reason_cites_the_actual_computed_numbers():
    _dormant_josh()
    o = _josh()
    for fragment in ("11.0 days", "47.0 days", "4.27x", "8 recorded contacts"):
        assert fragment in o.reason, f"{fragment!r} missing from: {o.reason}"


def test_reason_and_basis_are_not_canned_phrases():
    """Change the history, and both sentences change with it."""
    a = _fixture(gaps=(10, 10, 10, 10, 10, 10), current_gap=40)
    reason_a = _josh().reason
    b = _fixture(gaps=(3, 4, 3, 4, 3, 4), current_gap=5)
    reason_b = _josh().reason

    assert a.basis != b.basis
    assert reason_a != reason_b
    assert "10.0 days" in a.basis and "3.5 days" in b.basis


def test_no_clinical_term_leaks_into_any_detection_output():
    """The seed contains private_to_sam rows about hospitalisation and
    depression. Nothing they say may appear in any detector output field."""
    private = [
        m for m in memory.retrieve(SAM, audience="owner", limit=50)
        if m.visibility == "private_to_sam"
    ]
    assert private, "the seed must contain private clinical rows for this test to mean anything"

    bl.seed_demo_history_defaults(
        SAM, dormant_contact_id=JOSH, recent_contact_id=MAYA, now=NOW
    )
    bl.record_contact_event(SAM, MAYA, "plan_declined", occurred_at=NOW)

    surfaces: list[str] = []
    for o in opp.detect(SAM, now=NOW):
        surfaces += [o.reason, o.baseline.basis, o.suppressed_reason or ""]
        surfaces += o.supporting_memories
    for b in bl.all_baselines(SAM, now=NOW):
        surfaces.append(b.basis)

    for text in surfaces:
        # memory.py's own outbound guard, reused verbatim.
        memory.assert_no_sensitive_leak(text)
        assert not re.search(r"\bhospital|\bdiagnos|\bdepress|\bpsychiatr", text, re.I), text


def test_detection_never_reads_private_memory_rows():
    """Even a private row that mentions Josh by name stays out."""
    memory.store_memory(
        SAM, "fact",
        "Josh visited Sam on the inpatient ward during the hospitalization",
        subject_id=JOSH, visibility="private_to_sam", confirmed=True,
    )
    _dormant_josh()
    o = _josh()
    blob = " ".join([o.reason, o.baseline.basis] + o.supporting_memories)
    assert "ward" not in blob.lower()
    assert "hospitalization" not in blob.lower()
    memory.assert_no_sensitive_leak(blob)


def test_a_mislabelled_clinical_row_is_still_blocked_by_the_content_guard():
    """Defence in depth: an agent_internal row (planning-visible) that
    nonetheless reads clinically must not be surfaced."""
    memory.store_memory(
        SAM, "fact",
        "Josh knows about Sam's depression diagnosis",
        subject_id=JOSH, visibility="agent_internal", confirmed=True,
    )
    _dormant_josh()
    o = _josh()
    assert all("diagnosis" not in m.lower() for m in o.supporting_memories)
    memory.assert_no_sensitive_leak(" ".join(o.supporting_memories))


def test_supporting_memories_come_from_the_planning_audience():
    _dormant_josh()
    o = _josh()
    assert o.supporting_memory_count >= 1
    planning = {m.value for m in memory.retrieve(SAM, audience="planning", limit=50)}
    for value in o.supporting_memories:
        assert value in planning


# --------------------------------------------------------------------------
# purity / determinism
# --------------------------------------------------------------------------


def test_detection_is_deterministic():
    bl.seed_demo_history_defaults(
        SAM, dormant_contact_id=JOSH, recent_contact_id=MAYA, now=NOW
    )
    bl.record_contact_event(SAM, MAYA, "plan_declined", occurred_at=NOW - timedelta(days=1))

    runs = [[o.as_dict() for o in opp.detect(SAM, now=NOW)] for _ in range(5)]
    for later in runs[1:]:
        assert later == runs[0]


def test_detection_writes_nothing():
    """`detect()` must be side-effect free, or the dashboard would grow a row
    every time it refreshed."""
    _dormant_josh()
    counts_sql = (
        "SELECT (SELECT COUNT(*) FROM contact_events) AS ce,"
        " (SELECT COUNT(*) FROM agent_events) AS ae,"
        " (SELECT COUNT(*) FROM plans) AS p,"
        " (SELECT COUNT(*) FROM memories) AS m"
    )
    before = dict(q1(counts_sql))
    opp.detect(SAM, now=NOW)
    bl.all_baselines(SAM, now=NOW)
    assert dict(q1(counts_sql)) == before


#: Every way into the Anthropic adapter. Named explicitly rather than guessed:
#: a `hasattr` guard would silently turn this test into a no-op if the adapter
#: were renamed.
_LLM_ENTRY_POINTS = (
    "_client", "available", "tool_turn", "companion_reply", "extract_memories",
)


def test_detection_makes_no_llm_call(monkeypatch):
    from app.adapters import anthropic_adapter

    def boom(*a, **k):      # pragma: no cover - must never run
        raise AssertionError("detection must not call a model")

    for name in _LLM_ENTRY_POINTS:
        assert hasattr(anthropic_adapter, name), (
            f"anthropic_adapter.{name} no longer exists - update _LLM_ENTRY_POINTS, "
            "or this test proves nothing"
        )
        monkeypatch.setattr(anthropic_adapter, name, boom)

    _dormant_josh()
    assert opp.detect(SAM, now=NOW)
    assert bl.all_baselines(SAM, now=NOW)
    assert opp.best(SAM, now=NOW) is not None


def test_detection_modules_do_not_import_any_model_adapter():
    """Static backstop to the monkeypatch test: the source must not even
    reference an LLM adapter, so no future edit can sneak a call in."""
    import pathlib

    import app.services.baseline
    import app.services.opportunity

    for module in (app.services.baseline, app.services.opportunity):
        src = pathlib.Path(module.__file__).read_text(encoding="utf-8")
        code = "\n".join(
            line for line in src.splitlines()
            if line.startswith(("import ", "from "))
        )
        for banned in ("anthropic", "cartesia", "openai"):
            assert banned not in code.lower(), f"{module.__name__} imports {banned}"


def test_score_is_monotonic_in_deviation_ratio():
    ratios = [1.0, 1.5, 2.5, 4.0, 6.0]
    scores = []
    for r in ratios:
        b = _fixture(gaps=(10,) * 7, current_gap=10 * r)
        scores.append(opp.score_baseline(b, has_shared_time=True))
    assert scores == sorted(scores)
    assert all(0.0 <= s <= 1.0 for s in scores)


def test_score_saturates_so_one_contact_cannot_run_away_with_it():
    huge = _fixture(gaps=(10,) * 7, current_gap=10 * 50)
    big = _fixture(contact_id=MAYA, gaps=(10,) * 7, current_gap=10 * 6)
    assert opp.score_baseline(huge, has_shared_time=True) == opp.score_baseline(
        big, has_shared_time=True
    ) == 1.0
