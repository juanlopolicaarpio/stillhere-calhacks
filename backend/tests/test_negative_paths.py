"""Adversarial negative-path tests: the confirmation invariant exercised from
the Python layer, idempotency under retries/replays, and invalid-input paths.

Owned exclusively by Agent E. Does not duplicate backend/tests/test_state_machine.py
(pure state-machine unit tests) or backend/tests/test_spacetimedb.py (the raw
reducer invariant) - this file exercises `services/plans.py` and the HTTP API
built on top of it, which is a materially different path: it is what proves
the reducer and the SQLite mirror cannot be talked into disagreeing.
"""
from __future__ import annotations

import threading

import pytest

from app.db import q, q1
from app.domain.state_machine import TransitionError
from app.services import coordination, events, plans
from app.services.demo_reset import JOSH, SAM

ACTIVITY = "Coffee"
WHEN = "2026-11-10T18:00:00+00:00"


def _fresh_plan() -> dict:
    return plans.create_plan(SAM, JOSH, ACTIVITY, WHEN, location="Four Barrel Coffee")


def _advance_to_contact_in_progress(plan_id: str) -> None:
    plans.set_state(plan_id, "CONSENT_VERIFIED")
    plans.set_state(plan_id, "MEMORY_RETRIEVED")
    plans.set_state(plan_id, "CANDIDATE_PLAN_CREATED")
    plans.set_state(plan_id, "CONTACT_IN_PROGRESS")


def _advance_to_revalidated(
    plan_id: str, *, contact_activity=None, contact_when=None,
    patient_activity=None, patient_when=None,
) -> None:
    """Walk a plan all the way to AVAILABILITY_REVALIDATED, with each party's
    approval independently overridable so mismatch scenarios are reachable."""
    _advance_to_contact_in_progress(plan_id)
    plans.record_response(
        plan_id, JOSH, "accepted", activity=contact_activity, starts_at=contact_when,
    )
    plans.set_state(plan_id, "FRIEND_ACCEPTED")
    plans.set_state(plan_id, "PATIENT_APPROVAL_PENDING")
    plans.record_response(
        plan_id, SAM, "accepted", activity=patient_activity, starts_at=patient_when,
        simulated=True,
    )
    plans.set_state(plan_id, "PATIENT_ACCEPTED")
    plans.set_state(plan_id, "AVAILABILITY_REVALIDATED")


# ---------------------------------------------------------------------------
# The confirmation invariant, at the Python (services.plans) layer
# ---------------------------------------------------------------------------
class TestConfirmationInvariant:
    def test_missing_both_approvals_refuses(self):
        plan = _fresh_plan()
        _advance_to_contact_in_progress(plan["id"])
        result = plans.confirm_plan(plan["id"], idempotency_key="no-approvals")
        assert result["confirmed"] is False
        assert "has not accepted" in result["reason"]

    def test_missing_patient_approval_refuses(self):
        plan = _fresh_plan()
        _advance_to_contact_in_progress(plan["id"])
        plans.record_response(plan["id"], JOSH, "accepted")
        plans.set_state(plan["id"], "FRIEND_ACCEPTED")
        plans.set_state(plan["id"], "PATIENT_APPROVAL_PENDING")
        result = plans.confirm_plan(plan["id"], idempotency_key="no-patient")
        assert result["confirmed"] is False
        assert "has not accepted" in result["reason"]

    def test_activity_mismatch_refuses(self):
        plan = _fresh_plan()
        _advance_to_revalidated(plan["id"], patient_activity="Dinner")
        result = plans.confirm_plan(plan["id"], idempotency_key="activity-mismatch")
        assert result["confirmed"] is False
        assert "different activity" in result["reason"]
        # And the plan must NOT have silently advanced past revalidation.
        assert plans.get_plan(plan["id"])["status"] == "AVAILABILITY_REVALIDATED"

    def test_time_mismatch_refuses(self):
        plan = _fresh_plan()
        _advance_to_revalidated(
            plan["id"], patient_when="2026-11-11T11:00:00+00:00",
        )
        result = plans.confirm_plan(plan["id"], idempotency_key="time-mismatch")
        assert result["confirmed"] is False
        assert "different time" in result["reason"]
        assert plans.get_plan(plan["id"])["status"] == "AVAILABILITY_REVALIDATED"

    def test_stale_approval_invalidated_by_a_revision(self):
        """Approve both on v1 terms, then the terms change (a revision lands).
        `plans.confirm_plan` always reads the CURRENT version off the plan row,
        so there is no SQLite code path that can act on a stale version - the
        revision's `counterpropose()` cleared both approvals, so confirmation
        now fails for missing approval, not because anyone checked the version
        number. This is what makes "confirm the old version" structurally
        impossible from this layer (the raw-reducer version of this same
        property is proven directly in test_spacetimedb.py).
        """
        plan = _fresh_plan()
        _advance_to_contact_in_progress(plan["id"])
        plans.record_response(plan["id"], JOSH, "accepted")
        plans.set_state(plan["id"], "FRIEND_ACCEPTED")
        plans.set_state(plan["id"], "PATIENT_APPROVAL_PENDING")
        plans.record_response(plan["id"], SAM, "accepted", simulated=True)
        plans.set_state(plan["id"], "PATIENT_ACCEPTED")
        plans.set_state(plan["id"], "AVAILABILITY_REVALIDATED")

        v1 = plans.get_plan(plan["id"])["version"]
        plans.counterpropose(plan["id"], starts_at="2026-11-12T19:00:00+00:00")
        v2 = plans.get_plan(plan["id"])["version"]
        assert v2 == v1 + 1

        result = plans.confirm_plan(plan["id"], idempotency_key="stale-approval")
        assert result["confirmed"] is False
        assert "has not accepted" in result["reason"]

        contact = next(p for p in plans.get_plan(plan["id"])["participants"]
                       if p["role"] == "contact")
        assert contact["response"] is None, "a stale approval survived the revision"

    def test_stale_version_explicitly_refused_by_the_reducer(self):
        """The coordination-layer equivalent of 'confirm the old version':
        call the reducer directly with the OLD version number after a revision.
        Skips cleanly when SpacetimeDB is unreachable."""
        if not coordination.enabled():
            pytest.skip("SpacetimeDB not reachable in this environment")

        plan = _fresh_plan()
        _advance_to_revalidated(plan["id"])
        v1 = plans.get_plan(plan["id"])["version"]
        plans.counterpropose(plan["id"], starts_at="2026-11-13T19:00:00+00:00")

        coord = coordination.confirm(
            coordination.proposal_ref(plan["id"], v1), v1, "stale-version-key",
        )
        assert coord.rejected, coord

    def test_consent_revoked_state_refuses_confirmation(self):
        plan = _fresh_plan()
        _advance_to_revalidated(plan["id"])
        plans.halt_for_revoked_consent(plan["id"], "test revocation")
        result = plans.confirm_plan(plan["id"], idempotency_key="revoked-confirm")
        assert result["confirmed"] is False
        assert "revoked" in result["reason"]


# ---------------------------------------------------------------------------
# dispatch() refuses backend-only tools
# ---------------------------------------------------------------------------
class TestDispatchRefusesBackendOnlyTools:
    @pytest.mark.parametrize("name", ["confirm_plan", "send_confirmation", "start_outbound_call"])
    def test_refuses_and_emits_safety_blocked(self, name):
        from app.agents import tools as agent_tools

        before = len(events.recent(500))
        with pytest.raises(agent_tools.ToolRefused, match="backend-authorized only"):
            agent_tools.dispatch(name, {})
        after = events.recent(500)
        assert len(after) == before + 1
        assert after[-1]["event_type"] == "safety.blocked"
        assert after[-1]["payload"]["tool"] == name


# ---------------------------------------------------------------------------
# Idempotency: confirmation, notification, and the webhook tool endpoints
# ---------------------------------------------------------------------------
class TestIdempotency:
    def test_duplicate_confirm_plan_call_is_idempotent(self):
        plan = _fresh_plan()
        _advance_to_revalidated(plan["id"])

        first = plans.confirm_plan(plan["id"], idempotency_key="dup-confirm")
        second = plans.confirm_plan(plan["id"], idempotency_key="dup-confirm")
        assert first == second
        assert first["confirmed"] is True

        confirmed_events = [
            e for e in events.recent(500)
            if e["event_type"] == "plan.confirmed" and e["plan_id"] == plan["id"]
        ]
        assert len(confirmed_events) == 1

    def test_duplicate_send_confirmation_does_not_double_send(self):
        plan = _fresh_plan()
        _advance_to_revalidated(plan["id"])
        plans.confirm_plan(plan["id"], idempotency_key="notify-confirm")

        first = plans.send_confirmation(plan["id"])
        second = plans.send_confirmation(plan["id"])
        assert first == second

        sent_events = [
            e for e in events.recent(500)
            if e["event_type"] == "notification.sent" and e["plan_id"] == plan["id"]
        ]
        assert len(sent_events) == 1

    def test_replayed_record_friend_response_webhook_does_not_double_advance(self, client, tool_headers):
        start = client.post("/api/demo/start", json={"force_mode": "simulated"}).json()
        payload = {"call_id": start["call_id"], "response": "accepted"}

        before_count = len(q(
            "SELECT id FROM agent_events WHERE plan_id=? AND event_type='plan.state_changed'",
            (start["plan_id"],),
        ))

        a = client.post("/api/voice/tools/record_friend_response",
                        json=payload, headers=tool_headers).json()
        mid_count = len(q(
            "SELECT id FROM agent_events WHERE plan_id=? AND event_type='plan.state_changed'",
            (start["plan_id"],),
        ))
        b = client.post("/api/voice/tools/record_friend_response",
                        json=payload, headers=tool_headers).json()
        after_count = len(q(
            "SELECT id FROM agent_events WHERE plan_id=? AND event_type='plan.state_changed'",
            (start["plan_id"],),
        ))

        assert a == b
        assert mid_count > before_count
        assert after_count == mid_count, "a replayed tool call advanced state a second time"

        part = q1(
            "SELECT * FROM plan_participants WHERE plan_id=? AND role='contact'",
            (start["plan_id"],),
        )
        assert part["response"] == "accepted"

    def test_duplicate_outbound_call_request_via_api_is_suppressed(self, client):
        """`/api/demo/start` uses a deterministic idempotency key per plan, so
        calling it with the SAME plan id twice (by reusing the same session's
        plan through a direct service call) must not place a second call."""
        from app.services import voice as voice_svc

        plan = _fresh_plan()
        plans.set_state(plan["id"], "CONSENT_VERIFIED")
        plans.set_state(plan["id"], "MEMORY_RETRIEVED")
        plans.set_state(plan["id"], "CANDIDATE_PLAN_CREATED")
        # start_outbound_call() itself transitions CANDIDATE_PLAN_CREATED ->
        # CONTACT_IN_PROGRESS, so the SECOND call must short-circuit on the
        # idempotency key before ever touching the state machine again -
        # otherwise it would hit an illegal CONTACT_IN_PROGRESS -> itself
        # transition, which is a different failure mode than what this test
        # is checking for.
        first = voice_svc.start_outbound_call(
            SAM, JOSH, plan["id"], session_id="dup-call-sess",
            idempotency_key="dup-call-key", force_mode="simulated",
            override_quiet_hours=True,
        )
        second = voice_svc.start_outbound_call(
            SAM, JOSH, plan["id"], session_id="dup-call-sess",
            idempotency_key="dup-call-key", force_mode="simulated",
            override_quiet_hours=True,
        )
        assert second["duplicate"] is True
        assert second["call_id"] == first["call_id"]
        rows = q("SELECT id FROM calls WHERE idempotency_key='dup-call-key'")
        assert len(rows) == 1


# ---------------------------------------------------------------------------
# Provider webhook replay (distinct from the tool-endpoint idempotency above:
# the CONTRACTS.md idempotency guarantee is scoped to /api/voice/tools/*, not
# to the provider call-status webhook - this documents what actually happens)
# ---------------------------------------------------------------------------
class TestProviderWebhookReplay:
    def test_replayed_status_webhook_does_not_crash_and_settles_on_the_same_status(
        self, client, tool_headers
    ):
        from app.db import ex

        start = client.post("/api/demo/start", json={"force_mode": "simulated"}).json()
        ex("UPDATE calls SET provider_call_id='prov_replay' WHERE id=?", (start["call_id"],))

        for _ in range(3):
            r = client.post(
                "/api/voice/webhook",
                json={"agent_call_id": "prov_replay", "status": "completed"},
                headers=tool_headers,
            )
            assert r.status_code == 200
            assert r.json()["status"] == "completed"

        call = q1("SELECT * FROM calls WHERE id=?", (start["call_id"],))
        assert call["status"] == "completed"

        # Documented gap for the QA report: unlike the /tools/* endpoints, this
        # webhook has no idempotency key, so each replay DOES append its own
        # call.ended event (harmless to plan state, but noisy on the dashboard).
        ended = [
            e for e in events.recent(500)
            if e["event_type"] == "call.ended" and e["call_id"] == start["call_id"]
        ]
        assert len(ended) == 3


# ---------------------------------------------------------------------------
# Illegal transitions and assorted invalid-input negative paths
# ---------------------------------------------------------------------------
class TestIllegalTransitionsAndBadInput:
    def test_illegal_transition_raises_and_is_logged(self):
        plan = _fresh_plan()
        before = len(events.recent(500))
        with pytest.raises(TransitionError):
            plans.set_state(plan["id"], "PLAN_CONFIRMED")  # straight from the start
        after = events.recent(500)
        assert len(after) == before + 1
        assert after[-1]["event_type"] == "error"

    def test_verification_code_lockout_blocks_even_the_correct_code(self, client):
        """5 wrong attempts must lock the code out entirely - including a
        subsequent correct attempt. This is intended lockout behaviour, not a
        usability nicety, so it is pinned here as a security property."""
        signup = client.post("/api/signup", json={
            "phone_e164": "+14155550707", "preferred_channel": "call",
            "adult_attested": True, "display_name": "Lockout Test",
        }).json()
        real_code = signup["dev_code"]

        for _ in range(5):
            r = client.post("/api/verify", json={
                "phone_e164": "+14155550707", "code": "000000",
            })
            assert r.status_code == 400

        locked = client.post("/api/verify", json={
            "phone_e164": "+14155550707", "code": real_code,
        })
        assert locked.status_code == 400

    def test_record_friend_response_rejects_invalid_enum_value(self, client, tool_headers):
        start = client.post("/api/demo/start", json={"force_mode": "simulated"}).json()
        r = client.post(
            "/api/voice/tools/record_friend_response",
            json={"call_id": start["call_id"], "response": "maybe_lol"},
            headers=tool_headers,
        )
        assert r.status_code == 422

    def test_unknown_plan_id_404s(self, client):
        r = client.get("/api/plans/does-not-exist")
        assert r.status_code == 404
        assert r.json()["error"]["code"] == "not_found"

    def test_unknown_call_id_404s_on_voice_tools(self, client, tool_headers):
        r = client.post(
            "/api/voice/tools/get_support_context",
            json={"call_id": "does-not-exist"},
            headers=tool_headers,
        )
        assert r.status_code == 404

    def test_revoking_consent_for_an_unrelated_pair_is_a_safe_no_op(self, client):
        r = client.post("/api/consents/revoke",
                        json={"patient_id": SAM, "contact_id": "user_nobody"})
        assert r.status_code == 200
        assert r.json()["revoked"] == 0
        assert r.json()["plans_halted"] == []

    def test_double_decline_is_not_a_crash(self, client):
        inv = client.post("/api/circle/invite", json={
            "patient_id": SAM, "contact_name": "Decline Twice",
            "contact_phone_e164": "+14155556262", "relationship_label": "friend",
        }).json()
        client.post(f"/api/invite/{inv['token']}/decline")
        r = client.post(f"/api/invite/{inv['token']}/decline")
        assert r.status_code == 200  # declining an already-declined invite is a no-op, not an error
