"""Inbound: someone calls or texts Still Here.

The property under test: **calling in earns you nothing.** Whatever the agent
may say to a contact on an outbound call is exactly what it may say when that
contact dials in. Initiating contact is not a privilege escalation.
"""
from __future__ import annotations

import pytest

from app.db import ex, q1
from app.services import consent as consent_svc
from app.services.demo_reset import DANIEL, JOSH, MAYA, SAM

CLINICAL = ("hospital", "psychiatric", "depress", "diagnos", "discharge",
            "medication", "therapist", "inpatient")


def _phone(user_id: str) -> str:
    return q1("SELECT phone_e164 FROM users WHERE id=?", (user_id,))["phone_e164"]


def _identify(client, headers, number: str):
    return client.post("/api/inbound/identify", json={"from_number": number},
                       headers=headers)


class TestAuth:
    def test_identify_requires_the_secret(self, client):
        r = client.post("/api/inbound/identify", json={"from_number": "+14155551234"})
        assert r.status_code == 401

    def test_identify_rejects_a_wrong_secret(self, client):
        r = client.post("/api/inbound/identify", json={"from_number": "+14155551234"},
                        headers={"X-StillHere-Secret": "nope"})
        assert r.status_code == 401

    def test_sms_requires_the_secret(self, client):
        r = client.post("/api/inbound/sms",
                        json={"from_number": "+14155551234", "body": "hi"})
        assert r.status_code == 401


class TestIdentifyContact:
    def test_opted_in_contact_is_recognised_by_name(self, client, tool_headers):
        d = _identify(client, tool_headers, _phone(JOSH)).json()
        assert d["known_caller"] is True
        assert d["role"] == "contact"
        assert d["first_name"] == "Josh"
        assert "Josh" in d["greeting"]

    def test_contact_gets_shareable_context_only(self, client, tool_headers):
        d = _identify(client, tool_headers, _phone(JOSH)).json()
        assert d["context"], "an opted-in contact should get some safe context"
        assert d["withheld_private_items"] >= 2

    def test_health_is_never_discussable_on_an_inbound_call(self, client, tool_headers):
        """Dialling in must not widen what the agent may disclose."""
        d = _identify(client, tool_headers, _phone(JOSH)).json()
        assert d["may_discuss_patient_health"] is False

    def test_no_clinical_language_anywhere_in_the_payload(self, client, tool_headers):
        import json

        blob = json.dumps(_identify(client, tool_headers, _phone(JOSH)).json()).lower()
        for term in CLINICAL:
            assert term not in blob, f"inbound payload leaked {term!r}"

    def test_contact_without_an_accepted_relationship_gets_nothing(self, client, tool_headers):
        """Daniel was invited but never accepted."""
        d = _identify(client, tool_headers, _phone(DANIEL)).json()
        assert d["known_caller"] is True
        assert d["context"] == []
        assert d["may_discuss_patient_health"] is False

    def test_revoked_contact_still_leaks_nothing(self, client, tool_headers):
        import json

        consent_svc.revoke_consent(SAM, JOSH)
        ex("UPDATE relationships SET status='revoked' WHERE contact_id=?", (JOSH,))
        d = _identify(client, tool_headers, _phone(JOSH)).json()
        assert d["context"] == []
        blob = json.dumps(d).lower()
        for term in CLINICAL:
            assert term not in blob


class TestIdentifyPatient:
    def test_patient_is_recognised_and_may_hear_their_own_memories(self, client, tool_headers):
        d = _identify(client, tool_headers, _phone(SAM)).json()
        assert d["known_caller"] is True
        assert d["role"] == "patient"
        # It is the patient's own data; withholding it from them would be absurd.
        assert d["may_discuss_own_health"] is True


class TestIdentifyStrangers:
    def test_unknown_number_is_handled_politely(self, client, tool_headers):
        d = _identify(client, tool_headers, "+14155559999").json()
        assert d["known_caller"] is False
        assert d["greeting"]
        assert "context" not in d or not d.get("context")

    def test_unparseable_number_does_not_error(self, client, tool_headers):
        """A wrong number should not be met with a system fault."""
        r = _identify(client, tool_headers, "not-a-number")
        assert r.status_code == 200
        assert r.json()["known_caller"] is False

    def test_stranger_payload_has_no_names_or_context(self, client, tool_headers):
        import json

        blob = json.dumps(_identify(client, tool_headers, "+14155559999").json()).lower()
        for leak in ("sam", "josh", "maya", "marvel"):
            assert leak not in blob, f"stranger payload leaked {leak!r}"


class TestInboundSms:
    @pytest.mark.parametrize("text", [
        "STOP", "stop", "Stop", "please stop", "UNSUBSCRIBE", "cancel",
        "quit", "stop.", "STOP!",
    ])
    def test_stop_in_any_form_revokes_everything(self, client, tool_headers, text):
        r = client.post("/api/inbound/sms",
                        json={"from_number": _phone(JOSH), "body": text},
                        headers=tool_headers).json()
        assert r["action"] == "stopped"
        d = consent_svc.verify_outreach(SAM, JOSH, "voice_call",
                                        override_quiet_hours=True)
        assert not d.allowed, "outreach survived a STOP message"

    def test_stop_wins_even_mixed_with_other_words(self, client, tool_headers):
        r = client.post("/api/inbound/sms",
                        json={"from_number": _phone(JOSH),
                              "body": "hey thanks but please stop texting me"},
                        headers=tool_headers).json()
        assert r["action"] == "stopped"

    def test_ordinary_reply_is_recorded_not_stopped(self, client, tool_headers):
        r = client.post("/api/inbound/sms",
                        json={"from_number": _phone(JOSH), "body": "sounds good!"},
                        headers=tool_headers).json()
        assert r["action"] == "received"
        d = consent_svc.verify_outreach(SAM, JOSH, "voice_call",
                                        override_quiet_hours=True)
        assert d.allowed, "an ordinary reply should not revoke consent"

    def test_reply_mentions_how_to_opt_out(self, client, tool_headers):
        r = client.post("/api/inbound/sms",
                        json={"from_number": _phone(JOSH), "body": "ok"},
                        headers=tool_headers).json()
        assert "STOP" in r["reply"]

    def test_unknown_sender_is_ignored_safely(self, client, tool_headers):
        r = client.post("/api/inbound/sms",
                        json={"from_number": "+14155559999", "body": "hello?"},
                        headers=tool_headers).json()
        assert r["action"] == "ignored_unknown_sender"

    def test_sms_from_an_sms_only_contact_works(self, client, tool_headers):
        """Maya consented to SMS but not voice; texting in must still work."""
        r = client.post("/api/inbound/sms",
                        json={"from_number": _phone(MAYA), "body": "hi there"},
                        headers=tool_headers).json()
        assert r["action"] == "received"


class TestInboundNumber:
    def test_reports_honestly_without_credentials(self, client):
        """conftest blanks the Cartesia key, so nothing can answer."""
        d = client.get("/api/inbound/number").json()
        assert d["configured"] is False
        assert d["answers"] is False

    def test_never_claims_an_unanswered_number_answers(self, client):
        d = client.get("/api/inbound/number").json()
        # The UI keys off `answers`; it must never be true without an agent.
        assert d["answers"] is False
