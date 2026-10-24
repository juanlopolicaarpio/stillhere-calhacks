"""Inbound: someone calls Still Here, or texts it.

Outbound is the product's core, but a companion you cannot reach is not a
companion. Two surfaces:

* `POST /api/inbound/identify` - a webhook tool the Managed Agent calls when it
  answers an inbound call, to find out who is on the line and what it is
  allowed to say to them. Recognising the caller is what separates "a voice
  menu" from "the thing that already knows you".
* `POST /api/inbound/sms` - inbound SMS, including STOP handling.

Both apply the SAME consent and visibility rules as an outbound call. Being the
one who dialled does not widen what the agent may disclose: if Josh phones in
and asks how Sam is doing, the answer is exactly as bounded as it would be if
Still Here had called him.
"""
from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field, field_validator

from ..db import new_id, now_iso, q1
from ..services import consent as consent_svc
from ..services import events, memory

router = APIRouter(prefix="/api/inbound", tags=["inbound"])


def _auth(secret: str | None) -> None:
    import secrets as _s

    from ..config import get_settings

    expected = get_settings().webhook_secret
    if not secret or not _s.compare_digest(str(secret), str(expected)):
        raise HTTPException(
            status_code=401,
            detail={"error": {"code": "unauthorized",
                              "message": "bad or missing tool secret"}},
        )


class IdentifyBody(BaseModel):
    """Cartesia sends the caller's number; everything else is optional."""

    from_number: str = Field(default="", description="caller's number, E.164")
    call_id: str = ""

    @field_validator("from_number")
    @classmethod
    def _norm(cls, v: str) -> str:
        return (v or "").strip().replace(" ", "").replace("-", "")


@router.post("/identify")
def identify(body: IdentifyBody, x_stillhere_secret: str | None = Header(default=None)):
    """Who is calling, and what may the agent say to them?

    Returns a deliberately small payload. An unrecognised caller gets a polite
    stranger response rather than an error, because a wrong number should not
    be met with a system fault - and must certainly not be told anything.
    """
    _auth(x_stillhere_secret)

    try:
        number = consent_svc.validate_e164(body.from_number)
    except consent_svc.ConsentError:
        events.emit(
            "call.answered",
            "Inbound call from an unparseable number; treated as a stranger",
            payload={"from_number": body.from_number},
        )
        return {
            "known_caller": False,
            "greeting": "Hi, this is Still Here. I don't recognise this number, "
                        "so I'm not able to help with anything specific - but if "
                        "you were trying to reach someone, I'm sorry for the mix-up.",
        }

    user = q1("SELECT * FROM users WHERE phone_e164=?", (number,))
    if user is None:
        events.emit(
            "call.answered",
            "Inbound call from an unknown number",
            payload={"from_number": number},
        )
        return {
            "known_caller": False,
            "greeting": "Hi, this is Still Here. I don't have this number on "
                        "file, so there's not much I can help with - but thanks "
                        "for calling.",
        }

    first = user["display_name"].split()[0]

    # --- the patient calling their own companion ---
    if user["role"] == "patient":
        recent = memory.retrieve(user["id"], audience="owner", limit=3)
        events.emit(
            "call.answered",
            f"Inbound call from {user['display_name']} (patient)",
            payload={"user_id": user["id"], "known": True},
        )
        return {
            "known_caller": True,
            "role": "patient",
            "first_name": first,
            "greeting": f"Hey {first}, it's Still Here. Good to hear from you - "
                        "what's going on?",
            # The patient may hear their own memories; this is their data.
            "context": [m.value for m in recent],
            "may_discuss_own_health": True,
        }

    # --- a contact calling in ---
    rel = q1(
        "SELECT * FROM relationships WHERE contact_id=? ORDER BY created_at DESC",
        (user["id"],),
    )
    if rel is None or rel["status"] != "accepted":
        events.emit(
            "call.answered",
            f"Inbound call from {user['display_name']} (no accepted relationship)",
            payload={"user_id": user["id"]},
        )
        return {
            "known_caller": True,
            "role": "contact",
            "first_name": first,
            "greeting": f"Hi {first}, this is Still Here. I don't have an active "
                        "invitation for this number, so I can't set anything up - "
                        "but I can pass on that you called.",
            "context": [],
            "may_discuss_patient_health": False,
        }

    patient = q1("SELECT * FROM users WHERE id=?", (rel["patient_id"],))
    patient_first = (patient["display_name"] if patient else "your friend").split()[0]

    decision = consent_svc.verify_outreach(
        rel["patient_id"], user["id"], "voice_call", override_quiet_hours=True
    )
    ctx = memory.shareable_context(
        rel["patient_id"], user["id"], decision.scope.get("allowed_fields", [])
    )

    # Same guard as the outbound path: nothing clinical may be handed over.
    safe: list[str] = []
    for line in ctx["shared_interests"]:
        try:
            memory.assert_no_sensitive_leak(line)
            safe.append(line)
        except memory.MemoryError_:
            continue

    events.emit(
        "call.answered",
        f"Inbound call from {user['display_name']} (opted-in contact of "
        f"{patient_first})",
        payload={
            "user_id": user["id"],
            "patient_id": rel["patient_id"],
            "shareable_items": len(safe),
            "withheld": ctx["withheld_by_visibility"],
        },
    )
    return {
        "known_caller": True,
        "role": "contact",
        "first_name": first,
        "patient_first_name": patient_first,
        "greeting": f"Hi {first}, it's Still Here. Good to hear from you - "
                    f"is this about {patient_first}?",
        "context": safe,
        # The whole point of restating this on the inbound path: calling in does
        # not earn you information you could not have been told on an outbound
        # call.
        "may_discuss_patient_health": False,
        "withheld_private_items": ctx["withheld_by_visibility"],
    }


class InboundSmsBody(BaseModel):
    from_number: str
    body: str = ""

    @field_validator("from_number")
    @classmethod
    def _norm(cls, v: str) -> str:
        return (v or "").strip().replace(" ", "").replace("-", "")


#: Keywords that must stop all outreach. Recognised case-insensitively and
#: whether or not they arrive with other words around them.
STOP_WORDS = {"stop", "unsubscribe", "cancel", "end", "quit", "stopall", "optout"}


@router.post("/sms")
def inbound_sms(body: InboundSmsBody, x_stillhere_secret: str | None = Header(default=None)):
    """Inbound SMS. STOP is honoured before anything else is considered."""
    _auth(x_stillhere_secret)

    try:
        number = consent_svc.validate_e164(body.from_number)
    except consent_svc.ConsentError as e:
        raise ValueError(str(e)) from e

    words = {w.strip(".,!?:;").lower() for w in (body.body or "").split()}
    if words & STOP_WORDS:
        result = consent_svc.handle_stop(number)
        events.emit(
            "consent.revoked",
            "STOP received by SMS; all outreach to that number revoked",
            payload={"from_number": number, **result},
        )
        return {
            "action": "stopped",
            "reply": "You won't hear from Still Here again. Take care.",
            **result,
        }

    user = q1("SELECT * FROM users WHERE phone_e164=?", (number,))
    if user is None:
        return {
            "action": "ignored_unknown_sender",
            "reply": "This number isn't set up with Still Here.",
        }

    first = user["display_name"].split()[0]

    # A contact replying by text is a signal about the relationship, so record
    # it - that is what keeps `baseline`/`opportunity` honest about who is
    # actually in touch.
    if user["role"] == "contact":
        rel = q1(
            "SELECT * FROM relationships WHERE contact_id=? ORDER BY created_at DESC",
            (user["id"],),
        )
        if rel is not None:
            try:
                from ..services import baseline

                baseline.record_contact_event(
                    rel["patient_id"], user["id"], "message",
                    source="observed", note="inbound SMS",
                )
            except Exception:
                # Recording history is useful, not load-bearing.
                pass

    events.emit(
        "call.turn",
        f"Inbound SMS from {user['display_name']}",
        payload={"user_id": user["id"], "length": len(body.body or "")},
    )
    return {
        "action": "received",
        "user_id": user["id"],
        "reply": f"Thanks {first} - I've noted that. Reply STOP any time to "
                 "stop hearing from me.",
    }


@router.get("/number")
def inbound_number():
    """The number people can reach, and whether anything will answer it.

    Reports `answers: false` unless a Cartesia agent is actually attached, so
    the UI can never advertise a number that rings into nothing.
    """
    from ..config import get_settings

    s = get_settings()
    if not s.cartesia_configured:
        return {"configured": False, "answers": False,
                "detail": "no CARTESIA_API_KEY"}

    try:
        from ..adapters import cartesia_adapter as ca

        data = ca.list_phone_numbers()
        nums = data if isinstance(data, list) else (
            data.get("data") or data.get("phone_numbers") or []
        )
    except Exception as e:
        return {"configured": True, "answers": False, "detail": str(e)[:200]}

    for n in nums:
        if not isinstance(n, dict):
            continue
        attached = n.get("agent") or None
        if n.get("id") == s.cartesia_from_number_id:
            return {
                "configured": True,
                "number": n.get("number"),
                "answers": bool(attached),
                "agent": (attached or {}).get("name") if isinstance(attached, dict) else None,
                "detail": ("an agent is attached, inbound calls are answered"
                           if attached else
                           "no agent attached; inbound calls will not be answered"),
            }
    return {"configured": True, "answers": False,
            "detail": "configured number not found on the account"}
