"""Adversarial security / privacy tests.

Owned exclusively by Agent E (QA/security) per specs/CONTRACTS_V2.md section 7.
This file does NOT re-test what backend/tests/test_consent.py, test_memory.py
and test_api_flow.py already cover (dual opt-in basics, quiet-hour wrap math,
mislabelled-memory content guard, tool-secret auth, invite leak checks). It
goes one layer deeper: the full call path, the dashboard SSE auth surface, and
a sweep of every event payload actually emitted during a representative flow.

Everything here must pass or skip cleanly with no provider keys and no
SpacetimeDB.
"""
from __future__ import annotations

import json
import re

import pytest

from app.db import ex, q1
from app.services import coordination, events, memory, plans, voice
from app.services.consent import ConsentError
from app.services.demo_reset import DANIEL, JOSH, MAYA, SAM

CLINICAL_TERMS = (
    "hospital", "psychiatric", "depress", "diagnos", "discharge",
    "medication", "therapist", "inpatient", "crisis", "meds",
)

# A real E.164 number, standalone. Deliberately excludes short opaque ids.
_PHONE_RE = re.compile(r"(?<![\w])\+?1?\d{10,14}(?![\w])")


def _blob_has_clinical_term(obj) -> str | None:
    blob = json.dumps(obj, default=str).lower()
    for term in CLINICAL_TERMS:
        if term in blob:
            return term
    return None


# ---------------------------------------------------------------------------
# Dashboard + webhook auth: header AND query-param forms, missing AND wrong
# ---------------------------------------------------------------------------
class TestDashboardTokenBothTransports:
    def test_recent_rejects_wrong_header_token(self, client):
        r = client.get("/api/events/recent", headers={"X-Dashboard-Token": "nope"})
        assert r.status_code == 401

    def test_recent_rejects_missing_everything(self, client):
        assert client.get("/api/events/recent").status_code == 401

    def test_stream_rejects_missing_token(self, client):
        r = client.get("/api/events/stream")
        assert r.status_code == 401

    def test_stream_rejects_wrong_header_token(self, client):
        r = client.get("/api/events/stream", headers={"X-Dashboard-Token": "nope"})
        assert r.status_code == 401

    def test_stream_rejects_wrong_query_token(self, client):
        r = client.get("/api/events/stream", params={"dashboard_token": "nope"})
        assert r.status_code == 401

    def test_stream_accepts_header_token(self, client, dash_headers):
        # Streamed so the test never blocks on the infinite keep-alive loop:
        # only the headers (and the 200) are observed, then the connection
        # is closed without reading the body.
        with client.stream(
            "GET", "/api/events/stream", headers=dash_headers, params={"replay": 1}
        ) as r:
            assert r.status_code == 200
            assert "text/event-stream" in r.headers.get("content-type", "")

    def test_stream_accepts_query_token(self, client):
        with client.stream(
            "GET", "/api/events/stream", params={"dashboard_token": "test-dash", "replay": 1}
        ) as r:
            assert r.status_code == 200


class TestVoiceToolAuthBothFailureModes:
    ENDPOINTS = [
        ("/api/voice/tools/get_support_context", {"call_id": "x"}),
        ("/api/voice/tools/lookup_showtimes", {"movie": "marvel"}),
        ("/api/voice/tools/record_friend_response", {"call_id": "x", "response": "accepted"}),
        ("/api/voice/tools/request_patient_approval", {"call_id": "x"}),
    ]

    @pytest.mark.parametrize("path,body", ENDPOINTS)
    def test_missing_secret_401(self, client, path, body):
        assert client.post(path, json=body).status_code == 401

    @pytest.mark.parametrize("path,body", ENDPOINTS)
    def test_wrong_secret_401(self, client, path, body):
        r = client.post(path, json=body, headers={"X-StillHere-Secret": "wrong"})
        assert r.status_code == 401

    def test_webhook_requires_secret_too(self, client):
        r = client.post("/api/voice/webhook", json={"agent_call_id": "x", "status": "ringing"})
        assert r.status_code == 401


# ---------------------------------------------------------------------------
# Outreach gating, exercised through the FULL call path (not just the
# consent service in isolation) and the exact boundary of the rate cap.
# ---------------------------------------------------------------------------
class TestOutreachGatingThroughTheCallPath:
    def _plan(self, contact_id=JOSH):
        return plans.create_plan(
            SAM, contact_id, "Coffee", "2026-11-01T18:00:00+00:00",
            location="Four Barrel Coffee",
        )

    def _plan_ready_to_call(self, contact_id=JOSH):
        """A plan walked through the real opening transitions, so a call that
        is actually ALLOWED to proceed (unlike the blocked-path tests above)
        does not trip over an illegal-transition error unrelated to consent."""
        plan = self._plan(contact_id)
        plans.set_state(plan["id"], "CONSENT_VERIFIED")
        plans.set_state(plan["id"], "MEMORY_RETRIEVED")
        plans.set_state(plan["id"], "CANDIDATE_PLAN_CREATED")
        return plan

    def test_patient_not_activated_blocks_the_call(self):
        ex("UPDATE users SET outreach_active=0 WHERE id=?", (SAM,))
        plan = self._plan()
        with pytest.raises(voice.VoiceError, match="consent denied"):
            voice.start_outbound_call(
                SAM, JOSH, plan["id"], session_id="s1", idempotency_key="k1",
                force_mode="simulated", override_quiet_hours=True,
            )

    def test_contact_never_accepted_blocks_the_call(self):
        plan = self._plan(DANIEL)
        with pytest.raises(voice.VoiceError, match="consent denied"):
            voice.start_outbound_call(
                SAM, DANIEL, plan["id"], session_id="s2", idempotency_key="k2",
                force_mode="simulated", override_quiet_hours=True,
            )

    def test_unverified_phone_blocks_the_call_itself(self):
        """Not just verify_outreach() in isolation - the real dial path."""
        ex("UPDATE users SET phone_verified=0 WHERE id=?", (JOSH,))
        plan = self._plan()
        with pytest.raises(voice.VoiceError, match="consent denied"):
            voice.start_outbound_call(
                SAM, JOSH, plan["id"], session_id="s3", idempotency_key="k3",
                force_mode="simulated", override_quiet_hours=True,
            )
        # No call row should have been created for a blocked attempt.
        assert q1("SELECT id FROM calls WHERE idempotency_key='k3'") is None

    def test_sms_only_consent_blocks_a_voice_call_through_the_call_path(self):
        """Maya consented to SMS only; dialing her must fail end to end."""
        plan = self._plan(MAYA)
        with pytest.raises(voice.VoiceError, match="consent denied"):
            voice.start_outbound_call(
                SAM, MAYA, plan["id"], session_id="s4", idempotency_key="k4",
                force_mode="simulated", override_quiet_hours=True,
            )

    def test_quiet_hours_block_the_call_when_not_overridden(self):
        from datetime import datetime, timezone

        now_h = datetime.now(timezone.utc).hour
        ex(
            "UPDATE consents SET quiet_hours_start=?, quiet_hours_end=?"
            " WHERE patient_id=? AND contact_id=?",
            (now_h, (now_h + 2) % 24, SAM, JOSH),
        )
        plan = self._plan()
        with pytest.raises(voice.VoiceError, match="consent denied"):
            voice.start_outbound_call(
                SAM, JOSH, plan["id"], session_id="s5", idempotency_key="k5",
                force_mode="simulated",
                # deliberately NOT overriding quiet hours
            )

    def test_rate_cap_exact_boundary(self):
        """max_per_day=2: the 2nd call is allowed, the 3rd is not."""
        from app.services import consent as cs

        ex("UPDATE consents SET max_per_day=2 WHERE patient_id=? AND contact_id=?",
           (SAM, JOSH))

        for i in range(2):
            plan = self._plan_ready_to_call()
            voice.start_outbound_call(
                SAM, JOSH, plan["id"], session_id=f"rate{i}", idempotency_key=f"ratekey{i}",
                force_mode="simulated", override_quiet_hours=True,
            )
        # Exactly at the cap now (2 used). The next attempt must be refused.
        d = cs.verify_outreach(SAM, JOSH, "voice_call", override_quiet_hours=True)
        assert not d.allowed
        assert "frequency cap" in d.reason

        plan3 = self._plan()
        with pytest.raises(voice.VoiceError, match="consent denied"):
            voice.start_outbound_call(
                SAM, JOSH, plan3["id"], session_id="rate2", idempotency_key="ratekey2",
                force_mode="simulated", override_quiet_hours=True,
            )

    def test_phone_validation_is_checked_before_any_provider_contact(self):
        ex("UPDATE users SET phone_e164='not-a-number' WHERE id=?", (JOSH,))
        plan = self._plan()
        with pytest.raises(voice.VoiceError):
            voice.start_outbound_call(
                SAM, JOSH, plan["id"], session_id="s6", idempotency_key="k6",
                force_mode="simulated", override_quiet_hours=True,
            )


# ---------------------------------------------------------------------------
# Revocation mid-flight
# ---------------------------------------------------------------------------
class TestRevocationMidFlight:
    def _plan_in_progress(self):
        plan = plans.create_plan(SAM, JOSH, "Coffee", "2026-11-02T18:00:00+00:00")
        plans.set_state(plan["id"], "CONSENT_VERIFIED")
        plans.set_state(plan["id"], "MEMORY_RETRIEVED")
        plans.set_state(plan["id"], "CANDIDATE_PLAN_CREATED")
        plans.set_state(plan["id"], "CONTACT_IN_PROGRESS")
        return plan

    def test_consents_revoke_endpoint_halts_an_in_progress_plan(self, client):
        plan = self._plan_in_progress()
        r = client.post("/api/consents/revoke", json={"patient_id": SAM, "contact_id": JOSH})
        assert r.status_code == 200
        body = r.json()
        assert plan["id"] in body["plans_halted"]

        reloaded = plans.get_plan(plan["id"])
        assert reloaded["status"] == "HALTED_CONSENT_REVOKED"

    def test_revocation_blocks_new_outreach_immediately(self, client):
        self._plan_in_progress()
        client.post("/api/consents/revoke", json={"patient_id": SAM, "contact_id": JOSH})

        new_plan = plans.create_plan(SAM, JOSH, "Dinner", "2026-11-03T18:00:00+00:00")
        with pytest.raises(voice.VoiceError, match="consent denied"):
            voice.start_outbound_call(
                SAM, JOSH, new_plan["id"], session_id="post-revoke",
                idempotency_key="post-revoke-key",
                force_mode="simulated", override_quiet_hours=True,
            )

    def test_halted_plan_can_never_be_confirmed(self, client):
        plan = self._plan_in_progress()
        client.post("/api/consents/revoke", json={"patient_id": SAM, "contact_id": JOSH})
        result = plans.confirm_plan(plan["id"], idempotency_key="try-after-halt")
        assert result["confirmed"] is False
        assert "revoked" in result["reason"]

    def test_stop_keyword_revokes_consent_and_blocks_new_outreach(self):
        """specs/CONTRACTS.md: STOP is an immediate, total opt-out."""
        from app.services import consent as cs

        phone = q1("SELECT phone_e164 FROM users WHERE id=?", (JOSH,))["phone_e164"]
        cs.handle_stop(phone)

        plan = plans.create_plan(SAM, JOSH, "Coffee", "2026-11-04T18:00:00+00:00")
        with pytest.raises(voice.VoiceError, match="consent denied"):
            voice.start_outbound_call(
                SAM, JOSH, plan["id"], session_id="post-stop",
                idempotency_key="post-stop-key",
                force_mode="simulated", override_quiet_hours=True,
            )

    def test_stop_for_an_in_progress_plan_leaves_the_plan_state_as_is(self):
        """Documents actual behaviour (not a desired-but-missing feature):
        `consent.handle_stop()` (the STOP keyword path, used by `/api/sms/stop`)
        revokes consent and marks the relationship 'revoked', which blocks any
        FUTURE outreach attempt (see test above). It does NOT call
        `plans.halt_for_revoked_consent()` the way `/api/consents/revoke` does,
        so a plan that is already `CONTACT_IN_PROGRESS` for that contact is not
        itself transitioned to HALTED_CONSENT_REVOKED by STOP alone. Any further
        attempt to *advance* that plan still goes through code that re-checks
        consent (e.g. a fresh `start_outbound_call`), so no call can ever be
        re-dialed - but the stale in-flight row's status is unchanged. See the
        QA report for this as a recommended follow-up (use
        `plans.halt_for_revoked_consent` from the STOP path too, for a fully
        consistent dashboard state).
        """
        from app.services import consent as cs

        plan = self._plan_in_progress()
        phone = q1("SELECT phone_e164 FROM users WHERE id=?", (JOSH,))["phone_e164"]
        cs.handle_stop(phone)

        reloaded = plans.get_plan(plan["id"])
        assert reloaded["status"] == "CONTACT_IN_PROGRESS"  # documents the gap


# ---------------------------------------------------------------------------
# Consent scope cannot precede acceptance (service-layer complement to the
# API-layer test already in test_api_flow.py)
# ---------------------------------------------------------------------------
class TestConsentScopeOrdering:
    def test_cannot_grant_scoped_consent_before_relationship_exists(self, client):
        r = client.post("/api/consents", json={
            "patient_id": SAM, "contact_id": "user_no_such_contact",
            "allowed_actions": ["voice_call"], "allowed_fields": ["first_name"],
        })
        assert r.status_code == 400

    def test_cannot_widen_consent_for_a_declined_contact(self, client):
        inv = client.post("/api/circle/invite", json={
            "patient_id": SAM, "contact_name": "Pat Declined",
            "contact_phone_e164": "+14155556161", "relationship_label": "friend",
        }).json()
        client.post(f"/api/invite/{inv['token']}/accept", json={})
        client.post(f"/api/invite/{inv['token']}/decline")

        circle = client.get("/api/circle", params={"patient_id": SAM}).json()
        row = next(c for c in circle if c["token"] == inv["token"])
        r = client.post("/api/consents", json={
            "patient_id": SAM, "contact_id": row["contact_id"],
            "allowed_actions": ["voice_call"], "allowed_fields": ["first_name"],
        })
        assert r.status_code == 400


# ---------------------------------------------------------------------------
# Private-data leakage: defence in depth, and a full-flow event sweep
# ---------------------------------------------------------------------------
class TestPrivateDataLeakage:
    def test_call_path_refuses_to_dial_if_shareable_context_is_contaminated(self, monkeypatch):
        """Defence in depth: even if `memory.shareable_context()` itself were
        ever to return clinical language (e.g. a future bug in its content
        guard), `voice.start_outbound_call()` re-checks every dynamic variable
        with `memory.assert_no_sensitive_leak()` before anything reaches a
        provider, and refuses to dial rather than send it.
        """
        contaminated = {
            "shared_interests": ["Sam was hospitalized for a psychiatric crisis"],
            "relationship": "friend",
            "boundaries": [],
            "citations": [],
            "blocked_count": 0,
            "withheld_by_visibility": 0,
            "blocked_by_content_guard": 0,
        }
        monkeypatch.setattr(voice.memory, "shareable_context", lambda *a, **k: contaminated)

        plan = plans.create_plan(SAM, JOSH, "Coffee", "2026-11-05T18:00:00+00:00")
        with pytest.raises(memory.MemoryError_):
            voice.start_outbound_call(
                SAM, JOSH, plan["id"], session_id="contaminated",
                idempotency_key="contaminated-key",
                force_mode="simulated", override_quiet_hours=True,
            )
        # And no call row was ever written for the blocked attempt.
        assert q1("SELECT id FROM calls WHERE idempotency_key='contaminated-key'") is None

    def test_get_support_context_tool_dispatch_is_clinically_clean(self):
        from app.agents import tools as agent_tools

        out = agent_tools.dispatch(
            "get_support_context", {"patient_id": SAM, "contact_id": JOSH}
        )
        assert _blob_has_clinical_term(out) is None

    def test_full_demo_flow_event_sweep_has_no_clinical_language(self, client):
        """Run a representative end-to-end flow, then scan EVERY event payload
        and summary ever emitted for clinical vocabulary."""
        s = client.post("/api/demo/start", json={"force_mode": "simulated"}).json()
        client.post("/api/voice/simulate/turn",
                    json={"call_id": s["call_id"], "text": "how is Sam really doing?"})
        client.post("/api/voice/simulate/turn",
                    json={"call_id": s["call_id"], "text": "yes, that works for me"})
        client.post("/api/demo/sam-response",
                    json={"plan_id": s["plan_id"], "response": "accepted"})

        all_events = events.recent(2000)
        assert len(all_events) > 5
        for evt in all_events:
            hit = _blob_has_clinical_term(evt)
            assert hit is None, f"event {evt['event_type']!r} leaked {hit!r}: {evt}"

    def test_full_demo_flow_event_sweep_has_no_secrets_or_raw_phone_numbers(self, client):
        signup = client.post("/api/signup", json={
            "phone_e164": "+14155559090", "preferred_channel": "call",
            "adult_attested": True, "display_name": "Leak Probe",
        }).json()
        real_code = signup["dev_code"]

        inv = client.post("/api/circle/invite", json={
            "patient_id": SAM, "contact_name": "Leak Probe Contact",
            "contact_phone_e164": "+14155559191", "relationship_label": "friend",
        }).json()
        real_token = inv["token"]

        s = client.post("/api/demo/start", json={"force_mode": "simulated"}).json()
        client.post("/api/voice/simulate/turn", json={"call_id": s["call_id"], "text": "yes"})

        blob = json.dumps(events.recent(2000), default=str)
        assert real_code not in blob
        assert real_token not in blob
        assert "sk-ant" not in blob.lower()
        assert "bearer " not in blob.lower()

        # No bare E.164-shaped phone number anywhere in any event.
        for evt in events.recent(2000):
            payload_blob = json.dumps(evt["payload"], default=str)
            m = _PHONE_RE.search(payload_blob)
            assert m is None, f"event {evt['event_type']!r} leaked phone-like value {m.group(0)!r}"

    def test_redaction_masks_sensitive_keys_rather_than_dropping_them(self):
        """events.redact() must mask, not silently omit - an omission would be
        indistinguishable from a bug that forgot to pass the field at all."""
        evt = events.emit(
            "error", "probe", payload={"phone_e164": "+14155551234", "secret": "topsecret123"}
        )
        assert evt["payload"]["phone_e164"].startswith("***")
        assert evt["payload"]["phone_e164"] != "+14155551234"
        assert evt["payload"]["secret"].startswith("***")

    def test_api_status_never_echoes_webhook_or_dashboard_secrets(self, client):
        blob = json.dumps(client.get("/api/status").json()).lower()
        assert "dev-only-change-me" not in blob
        assert "test-secret" not in blob
        assert "test-dash" not in blob


# ---------------------------------------------------------------------------
# dispatch() forbids backend-only tools (complement to test_negative_paths.py,
# which covers this from the confirmation-invariant angle)
# ---------------------------------------------------------------------------
class TestModelForbiddenToolsAreUnreachable:
    def test_confirm_plan_is_not_in_the_handler_table_at_all(self):
        from app.agents import tools as agent_tools

        assert "confirm_plan" not in agent_tools.HANDLERS
        assert "send_confirmation" not in agent_tools.HANDLERS
        assert "start_outbound_call" not in agent_tools.HANDLERS

    def test_tool_specs_refuses_to_expose_a_forbidden_tool_by_name(self):
        from app.agents import tools as agent_tools

        with pytest.raises(ValueError):
            agent_tools.tool_specs(["confirm_plan"])
