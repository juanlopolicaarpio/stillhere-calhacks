"""The deterministic core: illegal transitions and the confirmation gate."""
from __future__ import annotations

import pytest

from app.domain.state_machine import (
    Approval,
    PlanState,
    TransitionError,
    assert_transition,
    can_transition,
    evaluate_confirmation,
)

ACT = "a Marvel movie"
WHEN = "2026-10-25T19:10:00+00:00"


def _appr(uid, response="accepted", activity=ACT, starts_at=WHEN, simulated=False):
    return Approval(uid, response, activity, starts_at, simulated)


class TestTransitions:
    def test_happy_path_is_legal(self):
        path = [
            PlanState.OPPORTUNITY_IDENTIFIED, PlanState.CONSENT_VERIFIED,
            PlanState.MEMORY_RETRIEVED, PlanState.CANDIDATE_PLAN_CREATED,
            PlanState.CONTACT_IN_PROGRESS, PlanState.FRIEND_ACCEPTED,
            PlanState.PATIENT_APPROVAL_PENDING, PlanState.PATIENT_ACCEPTED,
            PlanState.AVAILABILITY_REVALIDATED, PlanState.PLAN_CONFIRMED,
            PlanState.NOTIFICATIONS_SENT, PlanState.COMPLETE,
        ]
        for a, b in zip(path, path[1:]):
            assert_transition(a, b)

    def test_cannot_jump_straight_to_confirmed(self):
        with pytest.raises(TransitionError):
            assert_transition(PlanState.CONTACT_IN_PROGRESS, PlanState.PLAN_CONFIRMED)

    def test_cannot_confirm_from_friend_accepted(self):
        """The LLM's most likely shortcut must be impossible."""
        with pytest.raises(TransitionError):
            assert_transition(PlanState.FRIEND_ACCEPTED, PlanState.PLAN_CONFIRMED)

    def test_counterproposal_loops_and_never_confirms(self):
        assert can_transition(
            PlanState.FRIEND_COUNTERPROPOSED, PlanState.CANDIDATE_PLAN_CREATED
        )
        assert not can_transition(
            PlanState.FRIEND_COUNTERPROPOSED, PlanState.PLAN_CONFIRMED
        )

    def test_revocation_reachable_from_every_live_state(self):
        live = [
            PlanState.OPPORTUNITY_IDENTIFIED, PlanState.CONTACT_IN_PROGRESS,
            PlanState.FRIEND_ACCEPTED, PlanState.PATIENT_APPROVAL_PENDING,
            PlanState.AVAILABILITY_REVALIDATED, PlanState.PLAN_CONFIRMED,
        ]
        for s in live:
            assert can_transition(s, PlanState.HALTED_CONSENT_REVOKED)

    def test_terminal_states_are_terminal(self):
        for s in (PlanState.COMPLETE, PlanState.FAILED, PlanState.HALTED_CONSENT_REVOKED):
            assert not can_transition(s, PlanState.PLAN_CONFIRMED)


class TestConfirmationGate:
    def test_confirms_when_both_accepted_identical_terms(self):
        d = evaluate_confirmation(
            PlanState.AVAILABILITY_REVALIDATED,
            _appr("sam"), _appr("josh"), ACT, WHEN,
        )
        assert d.ok
        assert d.target_state is PlanState.PLAN_CONFIRMED

    def test_refuses_without_patient(self):
        d = evaluate_confirmation(
            PlanState.AVAILABILITY_REVALIDATED, None, _appr("josh"), ACT, WHEN
        )
        assert not d.ok

    def test_refuses_without_contact(self):
        d = evaluate_confirmation(
            PlanState.AVAILABILITY_REVALIDATED, _appr("sam"), None, ACT, WHEN
        )
        assert not d.ok

    def test_refuses_on_time_mismatch(self):
        """Josh says Sunday, Sam approves Saturday -> must NOT confirm."""
        d = evaluate_confirmation(
            PlanState.AVAILABILITY_REVALIDATED,
            _appr("sam", starts_at="2026-10-24T19:10:00+00:00"),
            _appr("josh", starts_at="2026-10-25T19:10:00+00:00"),
            ACT, "2026-10-25T19:10:00+00:00",
        )
        assert not d.ok
        assert "different time" in d.reason

    def test_refuses_on_activity_mismatch(self):
        d = evaluate_confirmation(
            PlanState.AVAILABILITY_REVALIDATED,
            _appr("sam", activity="Coffee"), _appr("josh", activity=ACT), ACT, WHEN,
        )
        assert not d.ok
        assert "different activity" in d.reason

    def test_refuses_when_either_declined(self):
        for who in ("sam", "josh"):
            sam = _appr("sam", response="declined" if who == "sam" else "accepted")
            josh = _appr("josh", response="declined" if who == "josh" else "accepted")
            d = evaluate_confirmation(
                PlanState.AVAILABILITY_REVALIDATED, sam, josh, ACT, WHEN
            )
            assert not d.ok

    def test_refuses_when_counterproposed(self):
        d = evaluate_confirmation(
            PlanState.AVAILABILITY_REVALIDATED,
            _appr("sam"), _appr("josh", response="counterproposed"), ACT, WHEN,
        )
        assert not d.ok

    def test_refuses_before_availability_revalidated(self):
        d = evaluate_confirmation(
            PlanState.PATIENT_ACCEPTED, _appr("sam"), _appr("josh"), ACT, WHEN
        )
        assert not d.ok
        assert "revalidated" in d.reason

    def test_refuses_after_revocation(self):
        d = evaluate_confirmation(
            PlanState.HALTED_CONSENT_REVOKED, _appr("sam"), _appr("josh"), ACT, WHEN
        )
        assert not d.ok
        assert "revoked" in d.reason
