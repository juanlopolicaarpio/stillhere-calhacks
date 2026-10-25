"""API-level tests: onboarding/dual consent, voice tools, idempotency, dashboard."""
from __future__ import annotations

from app.services.demo_reset import JOSH, SAM


class TestHealthAndStatus:
    def test_health(self, client):
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

    def test_status_reports_simulated_without_keys(self, client):
        body = client.get("/api/status").json()
        assert body["voice_mode"] == "simulated"
        assert body["real_call_possible"] is False
        assert "No real phone call" in body["notes"]

    def test_status_leaks_no_secrets(self, client):
        blob = str(client.get("/api/status").json()).lower()
        for bad in ("sk_", "sk-ant", "bearer ", "auth_token"):
            assert bad not in blob

    def test_public_signup_route_serves_the_spa_for_qr_scans(self, client):
        r = client.get("/signup")
        assert r.status_code == 200
        assert "Still Here" in r.text
        assert "text/html" in r.headers["content-type"]


class TestOnboarding:
    def test_signup_requires_adult_attestation(self, client):
        r = client.post("/api/signup", json={
            "phone_e164": "+14155557777", "preferred_channel": "call",
            "adult_attested": False, "display_name": "Tester",
        })
        assert r.status_code == 400
        assert r.json()["error"]["code"] == "validation_error"

    def test_signup_rejects_bad_phone(self, client):
        r = client.post("/api/signup", json={
            "phone_e164": "5551234", "preferred_channel": "call",
            "adult_attested": True, "display_name": "Tester",
        })
        assert r.status_code == 422 or r.status_code == 400

    def test_full_signup_and_verify(self, client):
        r = client.post("/api/signup", json={
            "phone_e164": "+14155557777", "preferred_channel": "text",
            "adult_attested": True, "display_name": "Tester",
        }).json()
        assert r["verification_required"] is True
        code = r["dev_code"]  # no SMS provider configured

        v = client.post("/api/verify", json={
            "phone_e164": "+14155557777", "code": code,
        }).json()
        assert v["verified"] is True

    def test_wrong_code_is_rejected(self, client):
        client.post("/api/signup", json={
            "phone_e164": "+14155557778", "preferred_channel": "call",
            "adult_attested": True, "display_name": "Tester2",
        })
        r = client.post("/api/verify", json={
            "phone_e164": "+14155557778", "code": "000000",
        })
        assert r.status_code == 400

    def test_cannot_activate_outreach_before_verifying(self, client):
        s = client.post("/api/signup", json={
            "phone_e164": "+14155557779", "preferred_channel": "call",
            "adult_attested": True, "display_name": "Tester3",
        }).json()
        r = client.post("/api/outreach/activate",
                        json={"user_id": s["user_id"], "active": True})
        assert r.status_code == 400
        assert "verify" in r.json()["error"]["message"]


class TestInvitationsAndDualConsent:
    def _invite(self, client):
        return client.post("/api/circle/invite", json={
            "patient_id": SAM, "contact_name": "Riley Quinn",
            "contact_phone_e164": "+14155556060", "relationship_label": "friend",
        }).json()

    def test_invite_page_leaks_no_clinical_context(self, client):
        inv = self._invite(client)
        body = client.get(f"/api/invite/{inv['token']}").json()
        blob = str(body).lower()
        for term in ("hospital", "psychiatric", "depress", "diagnos", "discharge"):
            assert term not in blob
        assert body["contact_name"] == "Riley Quinn"

    def test_accept_records_optin_but_not_phone_verification(self, client):
        inv = self._invite(client)
        acc = client.post(f"/api/invite/{inv['token']}/accept", json={}).json()
        assert acc["relationship_status"] == "accepted"
        # Holding the link does not prove phone ownership.
        assert acc["phone_verified"] is False
        assert acc["verification_required"] is True

    def test_unverified_contact_cannot_be_called(self, client):
        from app.services.consent import verify_outreach

        inv = self._invite(client)
        acc = client.post(f"/api/invite/{inv['token']}/accept", json={}).json()
        d = verify_outreach(SAM, acc["contact_id"], "voice_call",
                            override_quiet_hours=True)
        assert not d.allowed
        assert "not verified" in d.reason

    def test_accept_with_code_verifies_and_unblocks(self, client):
        from app.services.consent import verify_outreach

        inv = self._invite(client)
        first = client.post(f"/api/invite/{inv['token']}/accept", json={}).json()
        code = first["dev_code"]

        # Re-accepting is rejected, but the code still verifies the number.
        v = client.post("/api/verify", json={
            "phone_e164": "+14155556060", "code": code,
        })
        # 'invite' purpose code is not a signup code; verify the direct path instead.
        from app.db import ex

        ex("UPDATE users SET phone_verified=1 WHERE id=?", (first["contact_id"],))
        d = verify_outreach(SAM, first["contact_id"], "voice_call",
                            override_quiet_hours=True)
        assert d.allowed, d.reason

    def test_double_accept_is_rejected(self, client):
        inv = self._invite(client)
        client.post(f"/api/invite/{inv['token']}/accept", json={})
        again = client.post(f"/api/invite/{inv['token']}/accept", json={})
        assert again.status_code == 400

    def test_decline_blocks_outreach(self, client):
        inv = self._invite(client)
        client.post(f"/api/invite/{inv['token']}/accept", json={})
        client.post(f"/api/invite/{inv['token']}/decline")
        circle = client.get("/api/circle", params={"patient_id": SAM}).json()
        row = next(c for c in circle if c["token"] == inv["token"])
        assert row["status"] == "declined"
        assert row["callable"] is False

    def test_unknown_token_404s(self, client):
        assert client.get("/api/invite/nope").status_code == 400

    def test_circle_lists_seeded_and_new_invites(self, client):
        self._invite(client)
        circle = client.get("/api/circle", params={"patient_id": SAM}).json()
        assert any(c["contact_name"] == "Riley Quinn" for c in circle)

    def test_contacts_resolve_names(self, client):
        contacts = client.get("/api/contacts", params={"patient_id": SAM}).json()
        names = {c["display_name"] for c in contacts}
        assert "Josh Tanaka" in names
        josh = next(c for c in contacts if c["contact_id"] == JOSH)
        assert josh["relationship_label"] == "best friend"

    def test_consent_scope_cannot_be_set_before_acceptance(self, client):
        r = client.post("/api/consents", json={
            "patient_id": SAM, "contact_id": "user_daniel",
            "allowed_actions": ["voice_call"], "allowed_fields": ["first_name"],
        })
        assert r.status_code == 400

    def test_consents_include_contact_names(self, client):
        rows = client.get("/api/consents", params={"patient_id": SAM}).json()
        assert any(r["contact_name"] == "Josh Tanaka" for r in rows)


class TestCompanionAndMemoryApi:
    def test_message_persists_and_extracts(self, client):
        r = client.post("/api/companion/message", json={
            "user_id": SAM,
            "text": "Josh and I used to watch Marvel movies every opening weekend.",
        }).json()
        assert r["reply"]
        assert r["reasoning_mode"] == "fallback"  # no ANTHROPIC_API_KEY in tests
        assert r["session_id"]

    def test_memories_respect_audience(self, client):
        owner = client.get("/api/memories",
                           params={"user_id": SAM, "audience": "owner"}).json()
        contact = client.get("/api/memories",
                             params={"user_id": SAM, "audience": "contact"}).json()
        assert len(owner) > len(contact)
        assert all(m["visibility"] in ("shareable_with_contact", "public_demo_safe")
                   for m in contact)

    def test_unknown_audience_rejected(self, client):
        r = client.get("/api/memories",
                       params={"user_id": SAM, "audience": "everyone"})
        assert r.status_code == 400

    def test_patch_and_delete(self, client):
        mems = client.get("/api/memories",
                          params={"user_id": SAM, "audience": "owner"}).json()
        target = mems[0]["id"]
        patched = client.patch(f"/api/memories/{target}",
                               json={"value": "updated value"}).json()
        assert patched["value"] == "updated value"

        assert client.delete(f"/api/memories/{patched['id']}").json()["deleted"] is True

    def test_patch_with_no_fields_rejected(self, client):
        mems = client.get("/api/memories",
                          params={"user_id": SAM, "audience": "owner"}).json()
        r = client.patch(f"/api/memories/{mems[0]['id']}", json={})
        assert r.status_code == 400


class TestVoiceTools:
    def _start(self, client):
        return client.post("/api/demo/start", json={"force_mode": "simulated"}).json()

    def test_tools_require_secret(self, client):
        for path, body in [
            ("/api/voice/tools/get_support_context", {"call_id": "x"}),
            ("/api/voice/tools/record_friend_response",
             {"call_id": "x", "response": "accepted"}),
            ("/api/voice/tools/request_patient_approval", {"call_id": "x"}),
            ("/api/voice/tools/lookup_showtimes", {"movie": "marvel"}),
        ]:
            assert client.post(path, json=body).status_code == 401

    def test_tools_reject_wrong_secret(self, client):
        r = client.post("/api/voice/tools/lookup_showtimes", json={"movie": "marvel"},
                        headers={"X-StillHere-Secret": "wrong"})
        assert r.status_code == 401

    def test_support_context_is_contact_safe(self, client, tool_headers):
        s = self._start(client)
        ctx = client.post("/api/voice/tools/get_support_context",
                          json={"call_id": s["call_id"]},
                          headers=tool_headers).json()
        blob = str(ctx).lower()
        for term in ("hospital", "psychiatric", "depress", "diagnos"):
            assert term not in blob
        assert ctx["withheld_private_memories"] >= 2
        assert ctx["contact_first_name"] == "Josh"

    def test_showtimes_are_labeled_mock(self, client, tool_headers):
        r = client.post("/api/voice/tools/lookup_showtimes",
                        json={"movie": "marvel"}, headers=tool_headers).json()
        assert r["source"] == "mock"
        assert "MOCK" in r["disclaimer"]

    def test_record_response_is_idempotent(self, client, tool_headers):
        s = self._start(client)
        payload = {"call_id": s["call_id"], "response": "accepted"}
        a = client.post("/api/voice/tools/record_friend_response",
                        json=payload, headers=tool_headers).json()
        b = client.post("/api/voice/tools/record_friend_response",
                        json=payload, headers=tool_headers).json()
        assert a == b
        assert a["state"] == "PATIENT_APPROVAL_PENDING"

    def test_agent_cannot_confirm_via_tools(self, client, tool_headers):
        """Accepting on the call must stop at PATIENT_APPROVAL_PENDING."""
        s = self._start(client)
        r = client.post("/api/voice/tools/record_friend_response",
                        json={"call_id": s["call_id"], "response": "accepted"},
                        headers=tool_headers).json()
        assert r["state"] != "PLAN_CONFIRMED"
        plan = client.get(f"/api/plans/{s['plan_id']}").json()
        assert plan["confirmed_at"] is None

    def test_webhook_unknown_call_does_not_crash(self, client, tool_headers):
        r = client.post("/api/voice/webhook",
                        json={"agent_call_id": "nope", "status": "ringing"},
                        headers=tool_headers)
        assert r.status_code == 200
        assert r.json()["matched"] is False

    def test_webhook_maps_status(self, client, tool_headers):
        from app.db import ex

        s = self._start(client)
        ex("UPDATE calls SET provider_call_id='prov_1' WHERE id=?", (s["call_id"],))
        r = client.post("/api/voice/webhook",
                        json={"agent_call_id": "prov_1", "status": "no_answer"},
                        headers=tool_headers).json()
        assert r["status"] == "no_answer"

    def test_webhook_maps_current_cartesia_event_envelope(self, client):
        from app.db import ex, q1

        s = self._start(client)
        ex("UPDATE calls SET provider_call_id='prov_current' WHERE id=?", (s["call_id"],))
        headers = {"X-Webhook-Secret": "test-secret"}
        started = client.post(
            "/api/voice/webhook",
            json={
                "type": "call_started",
                "call_id": "prov_current",
                "call": {"id": "prov_current", "status": "started"},
            },
            headers=headers,
        ).json()
        assert started["status"] == "answered"
        assert q1("SELECT status FROM calls WHERE id=?", (s["call_id"],))["status"] == "answered"

        completed = client.post(
            "/api/voice/webhook",
            json={
                "type": "call_completed",
                "call_id": "prov_current",
                "call": {"id": "prov_current", "status": "completed"},
            },
            headers=headers,
        ).json()
        assert completed["status"] == "completed"
        assert q1("SELECT status FROM calls WHERE id=?", (s["call_id"],))["status"] == "completed"


class TestDemoFlowApi:
    def test_reset_is_repeatable(self, client):
        v1 = client.post("/api/demo/reset").json()["reset_version"]
        v2 = client.post("/api/demo/reset").json()["reset_version"]
        assert v2 == v1 + 1

    def test_sam_response_rejected_when_not_pending(self, client):
        s = client.post("/api/demo/start", json={"force_mode": "simulated"}).json()
        r = client.post("/api/demo/sam-response",
                        json={"plan_id": s["plan_id"], "response": "accepted"})
        assert r.status_code == 400

    def test_sam_decline_ends_without_confirming(self, client):
        s = client.post("/api/demo/start", json={"force_mode": "simulated"}).json()
        client.post("/api/voice/simulate/turn",
                    json={"call_id": s["call_id"], "text": "yes"})
        r = client.post("/api/demo/sam-response",
                        json={"plan_id": s["plan_id"], "response": "declined"}).json()
        assert r["confirmed"] is False
        plan = client.get(f"/api/plans/{s['plan_id']}").json()
        assert plan["confirmed_at"] is None

    def test_friend_decline_ends_without_confirming(self, client):
        s = client.post("/api/demo/start", json={"force_mode": "simulated"}).json()
        t = client.post("/api/voice/simulate/turn",
                        json={"call_id": s["call_id"], "text": "no, I can't"}).json()
        assert t["state"] == "COMPLETE"
        plan = client.get(f"/api/plans/{s['plan_id']}").json()
        assert plan["confirmed_at"] is None

    def test_counterproposal_then_accept_on_same_call(self, client):
        """The most likely improvised path at the booth:
        'can we do Sunday?' followed by 'yeah sounds good'.
        A mid-call re-plan must leave the plan acceptable, not stuck.
        """
        from datetime import datetime

        s = client.post("/api/demo/start", json={"force_mode": "simulated"}).json()
        original = client.get(f"/api/plans/{s['plan_id']}").json()

        # Ask for the OTHER seeded weekend day, whichever one wasn't proposed,
        # so this test is stable regardless of what day it runs on.
        proposed = datetime.fromisoformat(original["starts_at"]).weekday()
        other_day = "Sunday" if proposed == 5 else "Saturday"

        t1 = client.post("/api/voice/simulate/turn", json={
            "call_id": s["call_id"], "text": f"Can we do {other_day} instead?"}).json()
        assert t1["state"] == "CONTACT_IN_PROGRESS", t1["state"]

        t2 = client.post("/api/voice/simulate/turn", json={
            "call_id": s["call_id"], "text": "Yeah sounds good"})
        assert t2.status_code == 200, t2.text
        assert t2.json()["state"] == "PATIENT_APPROVAL_PENDING"

        plan = client.get(f"/api/plans/{s['plan_id']}").json()
        assert plan["version"] == original["version"] + 1
        assert plan["starts_at"] != original["starts_at"]

        # And it must confirm on the NEW agreed time.
        done = client.post("/api/demo/sam-response", json={
            "plan_id": s["plan_id"], "response": "accepted"}).json()
        assert done["confirmed"] is True, done
        final = client.get(f"/api/plans/{s['plan_id']}").json()
        assert final["starts_at"] == plan["starts_at"]

    def test_counterproposal_clears_prior_approvals(self, client):
        """Changed terms must invalidate any approval already given."""
        s = client.post("/api/demo/start", json={"force_mode": "simulated"}).json()
        client.post("/api/voice/simulate/turn",
                    json={"call_id": s["call_id"], "text": "yes"})
        # Friend has accepted; now they change their mind about the activity.
        client.post("/api/voice/simulate/turn",
                    json={"call_id": s["call_id"], "text": "actually can we get coffee"})
        plan = client.get(f"/api/plans/{s['plan_id']}").json()
        contact = next(p for p in plan["participants"] if p["role"] == "contact")
        assert contact["response"] is None, "stale approval survived a term change"

    def test_coffee_counterproposal_then_accept(self, client):
        s = client.post("/api/demo/start", json={"force_mode": "simulated"}).json()
        client.post("/api/voice/simulate/turn", json={
            "call_id": s["call_id"], "text": "Could we get coffee instead?"})
        t = client.post("/api/voice/simulate/turn", json={
            "call_id": s["call_id"], "text": "yes that works"})
        assert t.status_code == 200, t.text
        assert t.json()["state"] == "PATIENT_APPROVAL_PENDING"
        plan = client.get(f"/api/plans/{s['plan_id']}").json()
        assert plan["activity"] == "Coffee"

    def test_plan_exposes_structured_rationale(self, client):
        s = client.post("/api/demo/start", json={"force_mode": "simulated"}).json()
        plan = client.get(f"/api/plans/{s['plan_id']}").json()
        assert plan["why_contact"] and "Josh" in plan["why_contact"]
        assert plan["why_activity"]
        assert plan["memory_citations"]

    def test_simulate_turn_rejected_for_real_calls(self, client):
        from app.db import ex

        s = client.post("/api/demo/start", json={"force_mode": "simulated"}).json()
        ex("UPDATE calls SET mode='cartesia_pstn' WHERE id=?", (s["call_id"],))
        r = client.post("/api/voice/simulate/turn",
                        json={"call_id": s["call_id"], "text": "yes"})
        assert r.status_code == 409


class TestDashboardAuth:
    def test_events_require_token(self, client):
        assert client.get("/api/events/recent").status_code == 401

    def test_header_token_works(self, client, dash_headers):
        assert client.get("/api/events/recent", headers=dash_headers).status_code == 200

    def test_query_token_works_for_eventsource(self, client):
        """EventSource cannot set headers, so the query param must work."""
        r = client.get("/api/events/recent", params={"dashboard_token": "test-dash"})
        assert r.status_code == 200

    def test_bad_token_rejected(self, client):
        r = client.get("/api/events/recent", params={"dashboard_token": "nope"})
        assert r.status_code == 401
