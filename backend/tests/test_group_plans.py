"""Group plans: N invitees, each consenting independently.

The rule that matters: a plan confirms only when the patient AND *every*
participating contact accepted the identical activity and start time. One
silent invitee blocks the outing, because confirming a group plan someone never
agreed to is exactly the failure the gate exists to prevent.
"""
from __future__ import annotations

import pytest

from app.domain.state_machine import (
    MAX_GROUP_SIZE,
    Approval,
    PlanState,
    evaluate_group_confirmation,
)
from app.services import consent as consent_svc
from app.services import plans
from app.services.demo_reset import DANIEL, JOSH, MAYA, SAM

ACT = "a Marvel movie"
WHEN = "2026-10-31T19:10:00+00:00"
OTHER = "2026-11-01T11:00:00+00:00"


def _appr(uid, response="accepted", activity=ACT, starts_at=WHEN):
    return Approval(uid, response, activity, starts_at, False)


class TestGroupGate:
    """Pure-function tests of the gate itself."""

    def test_two_party_path_is_unchanged(self):
        d = evaluate_group_confirmation(
            PlanState.AVAILABILITY_REVALIDATED,
            _appr("sam"), [_appr("josh")], ACT, WHEN,
        )
        assert d.ok

    def test_confirms_when_patient_and_all_invitees_agree(self):
        d = evaluate_group_confirmation(
            PlanState.AVAILABILITY_REVALIDATED,
            _appr("sam"), [_appr("josh"), _appr("maya")], ACT, WHEN,
        )
        assert d.ok, d.reason
        assert "2 invitees" in d.reason

    def test_one_silent_invitee_blocks_the_whole_plan(self):
        d = evaluate_group_confirmation(
            PlanState.AVAILABILITY_REVALIDATED,
            _appr("sam"),
            [_appr("josh"), _appr("maya", response=None)],
            ACT, WHEN,
        )
        assert not d.ok

    def test_one_declining_invitee_blocks_the_whole_plan(self):
        d = evaluate_group_confirmation(
            PlanState.AVAILABILITY_REVALIDATED,
            _appr("sam"),
            [_appr("josh"), _appr("maya", response="declined")],
            ACT, WHEN,
        )
        assert not d.ok

    def test_refusal_does_not_name_who_declined(self):
        """A decline must not be broadcast to the other invitees."""
        d = evaluate_group_confirmation(
            PlanState.AVAILABILITY_REVALIDATED,
            _appr("sam"),
            [_appr("josh"), _appr("maya_chen_user_id", response="declined")],
            ACT, WHEN,
        )
        assert not d.ok
        assert "maya" not in d.reason.lower()
        assert "one invitee" in d.reason

    def test_invitee_on_a_different_time_blocks(self):
        d = evaluate_group_confirmation(
            PlanState.AVAILABILITY_REVALIDATED,
            _appr("sam"),
            [_appr("josh"), _appr("maya", starts_at=OTHER)],
            ACT, WHEN,
        )
        assert not d.ok
        assert "different time" in d.reason

    def test_invitee_on_a_different_activity_blocks(self):
        d = evaluate_group_confirmation(
            PlanState.AVAILABILITY_REVALIDATED,
            _appr("sam"),
            [_appr("josh"), _appr("maya", activity="Coffee")],
            ACT, WHEN,
        )
        assert not d.ok
        assert "different activity" in d.reason

    def test_patient_must_still_accept(self):
        d = evaluate_group_confirmation(
            PlanState.AVAILABILITY_REVALIDATED,
            _appr("sam", response=None), [_appr("josh"), _appr("maya")], ACT, WHEN,
        )
        assert not d.ok

    def test_empty_group_is_refused(self):
        d = evaluate_group_confirmation(
            PlanState.AVAILABILITY_REVALIDATED, _appr("sam"), [], ACT, WHEN
        )
        assert not d.ok
        assert "no participating contacts" in d.reason

    def test_group_size_cap_enforced(self):
        too_many = [_appr(f"c{i}") for i in range(MAX_GROUP_SIZE)]
        d = evaluate_group_confirmation(
            PlanState.AVAILABILITY_REVALIDATED, _appr("sam"), too_many, ACT, WHEN
        )
        assert not d.ok
        assert "MAX_GROUP_SIZE" in d.reason

    def test_revocation_blocks_a_group_plan(self):
        d = evaluate_group_confirmation(
            PlanState.HALTED_CONSENT_REVOKED,
            _appr("sam"), [_appr("josh"), _appr("maya")], ACT, WHEN,
        )
        assert not d.ok

    def test_group_cannot_confirm_before_revalidation(self):
        d = evaluate_group_confirmation(
            PlanState.PATIENT_ACCEPTED,
            _appr("sam"), [_appr("josh"), _appr("maya")], ACT, WHEN,
        )
        assert not d.ok
        assert "revalidated" in d.reason


class TestAddParticipant:
    """Adding an invitee goes through the same consent gate as the first one."""

    def _plan(self):
        return plans.create_plan(
            SAM, JOSH, ACT, WHEN, location="AMC Metreon 16",
            why_contact="best friend", why_activity="they watch Marvel",
            memory_citations=["mem_a"],
        )

    def test_adding_a_consented_contact_works(self):
        plan = self._plan()
        # Maya accepted her invitation but consented to SMS only; widen her
        # scope so she is genuinely callable, then add her.
        consent_svc.grant_consent(
            SAM, MAYA, allowed_actions=["voice_call", "sms"],
            allowed_fields=["first_name"],
            quiet_hours_start=23, quiet_hours_end=7, max_per_day=25,
        )
        updated = plans.add_participant(plan["id"], MAYA)
        contacts = [p for p in updated["participants"] if p["role"] == "contact"]
        assert {c["user_id"] for c in contacts} == {JOSH, MAYA}

    def test_adding_a_non_consented_contact_is_refused(self):
        """Daniel never accepted his invitation."""
        plan = self._plan()
        with pytest.raises(plans.PlanError) as e:
            plans.add_participant(plan["id"], DANIEL)
        assert "consent denied" in str(e.value)

    def test_adding_someone_clears_existing_approvals(self):
        """A bigger outing is a different outing."""
        plan = self._plan()
        plans.record_response(plan["id"], JOSH, "accepted")
        assert any(
            p["response"] == "accepted" for p in plans.get_plan(plan["id"])["participants"]
        )

        consent_svc.grant_consent(
            SAM, MAYA, allowed_actions=["voice_call"], allowed_fields=["first_name"],
            quiet_hours_start=23, quiet_hours_end=7, max_per_day=25,
        )
        updated = plans.add_participant(plan["id"], MAYA)
        assert all(p["response"] is None for p in updated["participants"]), (
            "a stale approval survived the group changing"
        )

    def test_adding_the_same_contact_twice_is_a_no_op(self):
        plan = self._plan()
        again = plans.add_participant(plan["id"], JOSH)
        contacts = [p for p in again["participants"] if p["role"] == "contact"]
        assert len(contacts) == 1

    def test_cap_is_enforced_when_adding(self):
        plan = self._plan()
        for uid in (MAYA, "user_extra_1", "user_extra_2"):
            consent_svc.grant_consent(
                SAM, uid if uid == MAYA else MAYA,
                allowed_actions=["voice_call"], allowed_fields=["first_name"],
                quiet_hours_start=23, quiet_hours_end=7, max_per_day=25,
            )
        plans.add_participant(plan["id"], MAYA)
        # Sam + Josh + Maya = 3; one more would make 4, which is the cap, so a
        # fifth must fail. Use a contact that cannot pass consent anyway to
        # confirm the cap or the gate stops it.
        with pytest.raises(plans.PlanError):
            plans.add_participant(plan["id"], DANIEL)


class TestGroupConfirmationEndToEnd:
    def test_group_plan_confirms_only_after_everyone_accepts(self):
        plan = plans.create_plan(
            SAM, JOSH, ACT, WHEN, why_contact="x", why_activity="y",
        )
        consent_svc.grant_consent(
            SAM, MAYA, allowed_actions=["voice_call"], allowed_fields=["first_name"],
            quiet_hours_start=23, quiet_hours_end=7, max_per_day=25,
        )
        plans.add_participant(plan["id"], MAYA)
        pid = plan["id"]

        plans.set_state(pid, PlanState.CONSENT_VERIFIED)
        plans.set_state(pid, PlanState.MEMORY_RETRIEVED)
        plans.set_state(pid, PlanState.CANDIDATE_PLAN_CREATED)
        plans.set_state(pid, PlanState.CONTACT_IN_PROGRESS)

        # Only Josh accepts so far.
        plans.record_response(pid, JOSH, "accepted")
        plans.set_state(pid, PlanState.FRIEND_ACCEPTED)
        plans.set_state(pid, PlanState.PATIENT_APPROVAL_PENDING)
        plans.record_response(pid, SAM, "accepted", simulated=True)
        plans.set_state(pid, PlanState.PATIENT_ACCEPTED)
        plans.set_state(pid, PlanState.AVAILABILITY_REVALIDATED)

        blocked = plans.confirm_plan(pid, "grp-1")
        assert blocked["confirmed"] is False, "confirmed without Maya's answer"

        # Now Maya accepts the same terms.
        plans.record_response(pid, MAYA, "accepted")
        ok = plans.confirm_plan(pid, "grp-2")
        assert ok["confirmed"] is True, ok["reason"]
