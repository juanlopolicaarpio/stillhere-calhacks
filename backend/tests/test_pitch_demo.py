from __future__ import annotations

import json


def test_pitch_rehearsal_runs_three_friends_then_josh_gate(client):
    rehearsed = client.post("/api/pitch/rehearse")
    assert rehearsed.status_code == 200, rehearsed.text
    body = rehearsed.json()
    assert body["state"] == "PATIENT_APPROVAL_PENDING"
    assert body["simulated"] is True

    status = client.get("/api/pitch/status").json()
    plan = status["plan"]
    contacts = [p for p in plan["participants"] if p["role"] == "contact"]
    patient = next(p for p in plan["participants"] if p["role"] == "patient")
    assert {p["display_name"] for p in contacts} == {
        "Maya Chen", "Daniel Osei", "Alex Morgan"
    }
    assert all(p["response"] == "accepted" for p in contacts)
    assert patient["display_name"] == "Josh Rivera"
    assert patient["response"] is None
    assert len(status["calls"]) == 3
    assert all(c["mode"] == "simulated" for c in status["calls"])

    approved = client.post("/api/pitch/josh-response", json={
        "plan_id": body["plan_id"], "response": "accepted",
    })
    assert approved.status_code == 200, approved.text
    assert approved.json()["confirmed"] is True
    assert client.get("/api/pitch/status").json()["plan"]["status"] == "COMPLETE"


def test_pitch_calls_persist_backend_verified_purpose_and_context(client):
    from app.db import q1

    client.post("/api/pitch/reset")
    client.post("/api/pitch/simulate-three-weeks")
    started = client.post("/api/pitch/start", json={"force_mode": "simulated"}).json()
    call = q1("SELECT purpose,authorized_context FROM calls WHERE id=?", (started["call_id"],))
    assert call["purpose"] == "friend_reunion_coordination"
    context = json.loads(call["authorized_context"])
    assert context["purpose"] == "friend_reunion_coordination"
    assert context["patient_id"] == "pitch_josh"
    assert context["contact_id"] == "pitch_maya"
    assert "allowed_actions" in context


def test_josh_invitation_call_has_fixed_verified_script(client):
    from app.db import q1

    client.post("/api/pitch/reset")
    response = client.post(
        "/api/pitch/josh-invitation-call", json={"force_mode": "simulated"}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["mode"] == "simulated"
    assert "Saturday at 5 PM" in body["script"]
    call = q1("SELECT purpose,authorized_context FROM calls WHERE id=?", (body["call_id"],))
    assert call["purpose"] == "patient_reunion_invitation"
    context = json.loads(call["authorized_context"])
    assert context["patient_id"] == "pitch_josh"
    assert context["friends"] == ["Maya", "Daniel", "Alex"]
    assert context["allowed_actions"] == ["request_patient_response"]
    assert "confirm_plan" in context["forbidden_actions"]


def test_first_group_acceptance_does_not_skip_remaining_contacts(client, tool_headers):
    client.post("/api/pitch/reset")
    client.post("/api/pitch/simulate-three-weeks")
    started = client.post("/api/pitch/start", json={"force_mode": "simulated"}).json()
    response = client.post(
        "/api/voice/tools/record_friend_response",
        headers=tool_headers,
        json={"call_id": started["call_id"], "response": "accepted"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["state"] == "FRIEND_ACCEPTED"

    approval = client.post(
        "/api/voice/tools/request_patient_approval",
        headers=tool_headers,
        json={"call_id": started["call_id"]},
    )
    assert approval.status_code == 200
    assert approval.json()["approval_requested"] is False
    assert approval.json()["remaining_contacts"] == 2
