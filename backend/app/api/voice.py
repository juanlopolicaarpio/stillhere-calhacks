"""Voice endpoints.

Two distinct groups:

1. `/api/voice/tools/*` - WEBHOOK TOOLS called BY Cartesia during a live call.
   These are how the Managed Agent reaches our memory, consent and planning
   logic mid-conversation. Authenticated with a shared secret header.
   Idempotent: a repeated (call_id, tool, payload) returns the first result.

2. `/api/voice/simulate/turn` - the local simulator used when no real telephony
   is configured. Everything it produces is flagged simulated.
"""
from __future__ import annotations

import hashlib
import json

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field

from ..config import get_settings
from ..db import ex, now_iso, q1
from ..domain.state_machine import PlanState
from ..services import events, memory, plans, scheduling, voice

router = APIRouter(prefix="/api/voice", tags=["voice"])


def _auth(secret: str | None) -> None:
    import secrets as _s

    expected = get_settings().webhook_secret
    if not secret or not _s.compare_digest(str(secret), str(expected)):
        raise HTTPException(
            status_code=401,
            detail={"error": {"code": "unauthorized", "message": "bad or missing tool secret"}},
        )


def _idem(call_id: str, tool: str, payload: dict) -> tuple[str, dict | None]:
    """Stable key over (call, tool, payload) so provider retries are safe."""
    blob = json.dumps(payload, sort_keys=True, default=str)
    key = f"tool:{tool}:{call_id}:{hashlib.sha256(blob.encode()).hexdigest()[:16]}"
    row = q1("SELECT * FROM idempotency WHERE key=? AND scope='voice_tool'", (key,))
    return key, (json.loads(row["result"]) if row else None)


def _remember(key: str, result: dict) -> dict:
    ex(
        "INSERT OR REPLACE INTO idempotency (key, scope, result, created_at)"
        " VALUES (?,?,?,?)",
        (key, "voice_tool", json.dumps(result, default=str), now_iso()),
    )
    return result


def _call_or_404(call_id: str):
    call = q1("SELECT * FROM calls WHERE id=?", (call_id,))
    if call is None:
        # Cartesia may address the call by its own provider id.
        call = q1("SELECT * FROM calls WHERE provider_call_id=?", (call_id,))
    if call is None:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": "not_found", "message": f"unknown call {call_id!r}"}},
        )
    return call


def _log_tool(call, tool: str, status: str, elapsed_ms: int, summary: str, payload: dict):
    events.emit(
        "call.tool_invoked",
        f"{tool}: {summary}",
        plan_id=call["plan_id"],
        call_id=call["id"],
        session_id=call["session_id"],
        payload={"tool": tool, "status": status, "elapsed_ms": elapsed_ms, **payload},
        simulated=(call["mode"] != "cartesia_pstn"),
    )


# --------------------------------------------------------------------------
# Webhook tools (Cartesia -> us)
# --------------------------------------------------------------------------
class CallRef(BaseModel):
    call_id: str


@router.post("/tools/get_support_context")
def tool_get_support_context(
    body: CallRef, x_stillhere_secret: str | None = Header(default=None)
):
    """Contact-safe context only. Private clinical memory cannot pass this."""
    import time

    _auth(x_stillhere_secret)
    t0 = time.monotonic()
    call = _call_or_404(body.call_id)
    plan = plans.get_plan(call["plan_id"])
    contact = q1("SELECT * FROM users WHERE id=?", (call["contact_id"],))

    ctx = memory.shareable_context(plan["patient_id"], call["contact_id"], [])
    out = {
        "contact_first_name": contact["display_name"].split()[0] if contact else "there",
        "shared_interests": ctx["shared_interests"],
        "relationship": ctx["relationship"],
        "boundaries": ctx["boundaries"],
        "citations": ctx["citations"],
        "withheld_private_memories": ctx["blocked_count"],
    }
    _log_tool(
        call, "get_support_context", "ok", int((time.monotonic() - t0) * 1000),
        f"{len(ctx['citations'])} shareable memories, {ctx['blocked_count']} withheld",
        {"memory_ids": [c["memory_id"] for c in ctx["citations"]]},
    )
    return out


class ShowtimesBody(BaseModel):
    movie: str
    date: str | None = None


@router.post("/tools/lookup_showtimes")
def tool_lookup_showtimes(
    body: ShowtimesBody, x_stillhere_secret: str | None = Header(default=None)
):
    """MOCK showtimes. Nothing is booked, reserved, or paid for."""
    import time

    _auth(x_stillhere_secret)
    t0 = time.monotonic()
    result = scheduling.lookup_showtimes(body.movie, body.date)
    events.emit(
        "call.tool_invoked",
        f"lookup_showtimes({body.movie}): {len(result['options'])} MOCK options",
        payload={
            "tool": "lookup_showtimes",
            "status": "ok",
            "elapsed_ms": int((time.monotonic() - t0) * 1000),
            "source": result["source"],
            "disclaimer": result["disclaimer"],
        },
    )
    return result


class FriendResponseBody(BaseModel):
    call_id: str
    response: str = Field(pattern="^(accepted|declined|counterproposed)$")
    activity: str | None = None
    starts_at: str | None = None
    note: str | None = None


@router.post("/tools/record_friend_response")
def tool_record_friend_response(
    body: FriendResponseBody, x_stillhere_secret: str | None = Header(default=None)
):
    """Record the friend's answer and advance the deterministic state machine.

    The agent CANNOT confirm a plan here. Acceptance only moves the plan to
    PATIENT_APPROVAL_PENDING.
    """
    import time

    _auth(x_stillhere_secret)
    t0 = time.monotonic()
    call = _call_or_404(body.call_id)
    payload = body.model_dump(exclude={"call_id"})
    key, cached = _idem(call["id"], "record_friend_response", payload)
    if cached is not None:
        return cached

    plan_id = call["plan_id"]
    plans.record_response(
        plan_id, call["contact_id"], body.response,
        activity=body.activity, starts_at=body.starts_at,
        session_id=call["session_id"],
    )

    if body.response == "accepted":
        plans.set_state(plan_id, PlanState.FRIEND_ACCEPTED,
                        reason="contact accepted on the call", session_id=call["session_id"])
        plans.set_state(plan_id, PlanState.PATIENT_APPROVAL_PENDING,
                        reason="awaiting patient approval", session_id=call["session_id"])
    elif body.response == "declined":
        plans.set_state(plan_id, PlanState.FRIEND_DECLINED,
                        reason="contact declined", session_id=call["session_id"])
        plans.set_state(plan_id, PlanState.COMPLETE,
                        reason="attempt ended after decline", session_id=call["session_id"])
    else:
        plans.set_state(plan_id, PlanState.FRIEND_COUNTERPROPOSED,
                        reason=body.note or "contact proposed different terms",
                        session_id=call["session_id"])
        plans.counterpropose(plan_id, activity=body.activity, starts_at=body.starts_at,
                             session_id=call["session_id"])
        plans.set_state(plan_id, PlanState.CANDIDATE_PLAN_CREATED,
                        reason="re-planned on counterproposal", session_id=call["session_id"])
        # The friend is still on the line, so the plan returns to in-progress.
        # Without this a subsequent "yes" on the same call is illegal.
        plans.set_state(plan_id, PlanState.CONTACT_IN_PROGRESS,
                        reason="still on the call with the new terms",
                        session_id=call["session_id"])

    state = plans.get_plan(plan_id)["status"]
    out = {"plan_id": plan_id, "state": state}
    _log_tool(call, "record_friend_response", "ok", int((time.monotonic() - t0) * 1000),
              f"{body.response} -> {state}", payload)
    return _remember(key, out)


@router.post("/tools/request_patient_approval")
def tool_request_patient_approval(
    body: CallRef, x_stillhere_secret: str | None = Header(default=None)
):
    import time

    _auth(x_stillhere_secret)
    t0 = time.monotonic()
    call = _call_or_404(body.call_id)
    plan = plans.get_plan(call["plan_id"])

    events.emit(
        "patient.approval_requested",
        "Patient approval requested; nothing is confirmed until Sam agrees",
        plan_id=plan["id"], call_id=call["id"], session_id=call["session_id"],
        payload={"activity": plan["activity"], "starts_at": plan["starts_at"]},
    )
    _log_tool(call, "request_patient_approval", "ok",
              int((time.monotonic() - t0) * 1000), "approval pending", {})
    return {"plan_id": plan["id"], "state": plan["status"]}


# --------------------------------------------------------------------------
# Provider call-status webhook
# --------------------------------------------------------------------------
@router.post("/webhook")
async def provider_webhook(
    request: Request, x_stillhere_secret: str | None = Header(default=None)
):
    """Cartesia call lifecycle events.

    Maps provider status onto our call row and emits real, timestamped events.
    Unknown or duplicate statuses are recorded but never crash the call.
    """
    _auth(x_stillhere_secret)
    body = await request.json()

    provider_call_id = (
        body.get("agent_call_id") or body.get("call_id") or body.get("id") or ""
    )
    status = str(body.get("status") or body.get("event") or "unknown").lower()

    call = q1("SELECT * FROM calls WHERE provider_call_id=?", (provider_call_id,))
    if call is None:
        events.emit(
            "error",
            f"Webhook for unknown provider call id (status={status})",
            payload={"provider_call_id": provider_call_id, "status": status},
        )
        return {"ok": True, "matched": False}

    mapping = {
        "ringing": ("call.ringing", "ringing"),
        "in_progress": ("call.answered", "answered"),
        "answered": ("call.answered", "answered"),
        "completed": ("call.ended", "completed"),
        "ended": ("call.ended", "completed"),
        "failed": ("call.failed", "failed"),
        "no_answer": ("call.ended", "no_answer"),
        "busy": ("call.ended", "busy"),
        "voicemail": ("call.ended", "voicemail"),
    }
    event_type, new_status = mapping.get(status, ("call.turn", call["status"]))

    if new_status == "answered" and not call["answered_at"]:
        ex("UPDATE calls SET status=?, answered_at=? WHERE id=?",
           (new_status, now_iso(), call["id"]))
    elif new_status in {"completed", "failed", "no_answer", "busy", "voicemail"}:
        ex("UPDATE calls SET status=?, ended_at=? WHERE id=?",
           (new_status, now_iso(), call["id"]))
    else:
        ex("UPDATE calls SET status=? WHERE id=?", (new_status, call["id"]))

    events.emit(
        event_type,
        f"Provider status: {status}",
        plan_id=call["plan_id"], call_id=call["id"], session_id=call["session_id"],
        payload={"provider_status": status, "provider_call_id": provider_call_id},
    )

    # A friend who never picked up has not consented to anything.
    if new_status in {"no_answer", "busy", "voicemail"}:
        events.emit(
            "safety.blocked",
            f"No conversation took place ({new_status}); no plan advanced and no "
            "voicemail left without opt-in",
            plan_id=call["plan_id"], call_id=call["id"],
            payload={"status": new_status},
        )

    return {"ok": True, "matched": True, "status": new_status}


# --------------------------------------------------------------------------
# Local simulator
# --------------------------------------------------------------------------
class SimTurnBody(BaseModel):
    call_id: str
    text: str = Field(min_length=1, max_length=2000)


@router.post("/simulate/turn")
def simulate_turn(body: SimTurnBody):
    """One turn of the clearly-labeled local voice simulator."""
    call = _call_or_404(body.call_id)
    if call["mode"] == "cartesia_pstn":
        raise HTTPException(
            status_code=409,
            detail={"error": {"code": "conflict", "message":
                    "this call is a real PSTN call; speak to it on the phone"}},
        )
    return voice.simulate_turn(call["id"], body.text)


@router.post("/simulate/end")
def simulate_end(body: CallRef):
    call = _call_or_404(body.call_id)
    voice.end_call(call["id"], "completed")
    return {"ok": True}
