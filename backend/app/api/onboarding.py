"""QR onboarding, phone verification, circle invitations, dual consent.

The onboarding contract: scanning a QR code is NOT consent to anything.
A call can only happen after (a) the user verifies their own phone and
activates outreach, and (b) each friend separately accepts an invitation.
"""
from __future__ import annotations

import io
import json
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Response
from pydantic import BaseModel, Field, field_validator

from ..config import get_settings
from ..db import ex, new_id, now_iso, q, q1
from ..services import consent as consent_svc
from ..services import events

router = APIRouter(prefix="/api", tags=["onboarding"])

INVITE_TTL_DAYS = 7
CODE_TTL_MINUTES = 10
MAX_CODE_ATTEMPTS = 5


# --------------------------------------------------------------------------
# QR code
# --------------------------------------------------------------------------
@router.get("/qr.png")
def qr_png(target: str | None = None):
    """QR pointing at the public signup page."""
    import qrcode

    s = get_settings()
    url = target or f"{s.public_base_url.rstrip('/')}/signup"
    img = qrcode.make(url)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return Response(
        content=buf.getvalue(),
        media_type="image/png",
        headers={"Cache-Control": "no-store", "X-Signup-Url": url},
    )


# --------------------------------------------------------------------------
# Signup + verification
# --------------------------------------------------------------------------
class SignupBody(BaseModel):
    phone_e164: str
    preferred_channel: str = Field(default="call", pattern="^(call|text)$")
    adult_attested: bool
    display_name: str = Field(min_length=1, max_length=80)

    @field_validator("phone_e164")
    @classmethod
    def _phone(cls, v: str) -> str:
        return consent_svc.validate_e164(v)


def _issue_code(phone: str, purpose: str) -> str:
    code = f"{secrets.randbelow(1000000):06d}"
    ex(
        "INSERT INTO verification_codes (id, phone_e164, code, purpose, expires_at, created_at)"
        " VALUES (?,?,?,?,?,?)",
        (
            new_id("vc"), phone, code, purpose,
            (datetime.now(timezone.utc) + timedelta(minutes=CODE_TTL_MINUTES)).isoformat(),
            now_iso(),
        ),
    )
    return code


@router.post("/signup")
def signup(body: SignupBody):
    """Create an adult account. Returns a dev code only when no SMS provider exists."""
    s = get_settings()
    if not body.adult_attested:
        raise ValueError("you must confirm you are 18 or older to sign up")

    existing = q1("SELECT * FROM users WHERE phone_e164=?", (body.phone_e164,))
    if existing is not None:
        uid = existing["id"]
        ex(
            "UPDATE users SET display_name=?, preferred_channel=? WHERE id=?",
            (body.display_name, body.preferred_channel, uid),
        )
    else:
        uid = new_id("user")
        ex(
            "INSERT INTO users (id, display_name, role, phone_e164, phone_verified,"
            " preferred_channel, is_adult_attested, outreach_active, created_at)"
            " VALUES (?,?,'patient',?,0,?,1,0,?)",
            (uid, body.display_name, body.phone_e164, body.preferred_channel, now_iso()),
        )

    code = _issue_code(body.phone_e164, "signup")
    events.emit(
        "consent.verified",
        f"Signup started for {body.display_name} (adult attested); verification sent",
        payload={
            "user_id": uid,
            "phone": body.phone_e164,
            "preferred_channel": body.preferred_channel,
            "note": "signup is NOT authorization to contact anyone",
        },
    )

    out = {
        "user_id": uid,
        "verification_required": True,
        "channel": body.preferred_channel,
    }
    if not s.twilio_configured:
        # No SMS provider: expose the code so local onboarding is testable.
        out["dev_code"] = code
        out["dev_note"] = "No SMS provider configured; code shown for local testing only."
    return out


class VerifyBody(BaseModel):
    phone_e164: str
    code: str

    @field_validator("phone_e164")
    @classmethod
    def _phone(cls, v: str) -> str:
        return consent_svc.validate_e164(v)


def _consume_code(phone: str, code: str, purpose: str) -> bool:
    row = q1(
        "SELECT * FROM verification_codes WHERE phone_e164=? AND purpose=?"
        " AND consumed_at IS NULL ORDER BY created_at DESC LIMIT 1",
        (phone, purpose),
    )
    if row is None:
        return False
    if row["attempts"] >= MAX_CODE_ATTEMPTS:
        return False
    if datetime.fromisoformat(row["expires_at"]) < datetime.now(timezone.utc):
        return False
    ex("UPDATE verification_codes SET attempts=attempts+1 WHERE id=?", (row["id"],))
    if not secrets.compare_digest(str(row["code"]), str(code)):
        return False
    ex("UPDATE verification_codes SET consumed_at=? WHERE id=?", (now_iso(), row["id"]))
    return True


@router.post("/verify")
def verify(body: VerifyBody):
    user = q1("SELECT * FROM users WHERE phone_e164=?", (body.phone_e164,))
    if user is None:
        raise ValueError("no account for that phone number")
    if not _consume_code(body.phone_e164, body.code, "signup"):
        raise ValueError("invalid or expired verification code")
    ex("UPDATE users SET phone_verified=1 WHERE id=?", (user["id"],))
    events.emit(
        "consent.verified",
        f"Phone verified for {user['display_name']}",
        payload={"user_id": user["id"]},
    )
    return {"user_id": user["id"], "verified": True}


class PreferencesBody(BaseModel):
    user_id: str
    timezone: str | None = None
    activity_preferences: list[str] = Field(default_factory=list)
    quiet_hours_start: int | None = Field(default=None, ge=0, le=23)
    quiet_hours_end: int | None = Field(default=None, ge=0, le=23)


@router.post("/onboarding/preferences")
def preferences(body: PreferencesBody):
    from ..services import memory

    user = q1("SELECT * FROM users WHERE id=?", (body.user_id,))
    if user is None:
        raise ValueError("unknown user")
    if body.timezone:
        ex("UPDATE users SET timezone=? WHERE id=?", (body.timezone, body.user_id))

    stored = []
    for pref in body.activity_preferences:
        m = memory.store_memory(
            body.user_id, "preference", pref,
            source_quote="stated during onboarding",
            confidence=0.9, confirmed=True,
            visibility="shareable_with_contact",
        )
        stored.append(m.id)

    if stored:
        events.emit(
            "memory.extracted",
            f"{len(stored)} onboarding preferences saved",
            payload={"memory_ids": stored},
        )
    return {"ok": True, "memories_created": stored}


class ActivateBody(BaseModel):
    user_id: str
    active: bool


@router.post("/outreach/activate")
def activate(body: ActivateBody):
    """Activate or pause outreach. Even when active, each contact's own opt-in
    is still required before any call.
    """
    user = q1("SELECT * FROM users WHERE id=?", (body.user_id,))
    if user is None:
        raise ValueError("unknown user")
    if body.active and not user["phone_verified"]:
        raise ValueError("verify your phone number before activating outreach")

    ex("UPDATE users SET outreach_active=? WHERE id=?", (int(body.active), body.user_id))
    events.emit(
        "consent.verified" if body.active else "consent.revoked",
        f"Outreach {'ACTIVATED' if body.active else 'PAUSED'} by {user['display_name']}",
        payload={"user_id": body.user_id, "outreach_active": body.active},
    )
    return {"user_id": body.user_id, "outreach_active": body.active}


# --------------------------------------------------------------------------
# Circle invitations + dual consent
# --------------------------------------------------------------------------
class InviteBody(BaseModel):
    patient_id: str
    contact_name: str = Field(min_length=1, max_length=80)
    contact_phone_e164: str
    relationship_label: str = Field(min_length=1, max_length=60)

    @field_validator("contact_phone_e164")
    @classmethod
    def _phone(cls, v: str) -> str:
        return consent_svc.validate_e164(v)


@router.post("/circle/invite")
def invite(body: InviteBody):
    s = get_settings()
    patient = q1("SELECT * FROM users WHERE id=?", (body.patient_id,))
    if patient is None:
        raise ValueError("unknown user")

    token = secrets.token_urlsafe(24)
    expires = (datetime.now(timezone.utc) + timedelta(days=INVITE_TTL_DAYS)).isoformat()
    ex(
        "INSERT INTO invitations (token, patient_id, contact_name, contact_phone_e164,"
        " relationship_label, expires_at, created_at) VALUES (?,?,?,?,?,?,?)",
        (
            token, body.patient_id, body.contact_name, body.contact_phone_e164,
            body.relationship_label, expires, now_iso(),
        ),
    )
    events.emit(
        "consent.verified",
        f"Invitation created for {body.contact_name} ({body.relationship_label})",
        payload={
            "contact_name": body.contact_name,
            "phone": body.contact_phone_e164,
            "note": "contact must affirmatively accept before any outreach",
        },
    )
    return {
        "token": token,
        "invite_url": f"{s.public_base_url.rstrip('/')}/invite/{token}",
        "expires_at": expires,
    }


@router.get("/circle")
def list_circle(patient_id: str):
    """The patient's trusted circle: every invite and its real opt-in status.

    Added after the frontend correctly reported that invites were otherwise
    only recoverable from browser localStorage.
    """
    s = get_settings()
    rows = q(
        "SELECT i.token, i.contact_name, i.relationship_label, i.expires_at,"
        " i.accepted_at, i.declined_at, i.contact_id,"
        " u.phone_verified, r.status AS relationship_status"
        " FROM invitations i"
        " LEFT JOIN users u ON u.id = i.contact_id"
        " LEFT JOIN relationships r ON r.contact_id = i.contact_id"
        "   AND r.patient_id = i.patient_id"
        " WHERE i.patient_id=? ORDER BY i.created_at DESC",
        (patient_id,),
    )
    out = []
    for r in rows:
        # Decline takes precedence over an earlier accept: the most restrictive
        # answer wins, so an accepted-then-declined invite is never callable.
        status = "pending"
        if r["declined_at"]:
            status = "declined"
        elif r["accepted_at"]:
            status = "accepted"
        out.append({
            "token": r["token"],
            "invite_url": f"{s.public_base_url.rstrip('/')}/invite/{r['token']}",
            "contact_name": r["contact_name"],
            "contact_id": r["contact_id"],
            "relationship_label": r["relationship_label"],
            "status": status,
            "relationship_status": r["relationship_status"],
            "phone_verified": bool(r["phone_verified"]) if r["phone_verified"] is not None else False,
            "callable": status == "accepted" and bool(r["phone_verified"]),
            "expires_at": r["expires_at"],
        })
    return out


@router.get("/contacts")
def list_contacts(patient_id: str):
    """Resolve contact ids to names, so consent screens can show 'Josh' not a uuid."""
    rows = q(
        "SELECT u.id, u.display_name, u.phone_verified, r.relationship_label, r.status"
        " FROM relationships r JOIN users u ON u.id = r.contact_id"
        " WHERE r.patient_id=? ORDER BY u.display_name",
        (patient_id,),
    )
    return [
        {
            "contact_id": r["id"],
            "display_name": r["display_name"],
            "relationship_label": r["relationship_label"],
            "relationship_status": r["status"],
            "phone_verified": bool(r["phone_verified"]),
        }
        for r in rows
    ]


def _load_invite(token: str):
    inv = q1("SELECT * FROM invitations WHERE token=?", (token,))
    if inv is None:
        raise ValueError("invitation not found")
    if datetime.fromisoformat(inv["expires_at"]) < datetime.now(timezone.utc):
        raise ValueError("this invitation has expired")
    return inv


@router.get("/invite/{token}")
def get_invite(token: str):
    """Contact-facing. Deliberately exposes NO clinical context whatsoever."""
    inv = _load_invite(token)
    patient = q1("SELECT display_name FROM users WHERE id=?", (inv["patient_id"],))
    # Decline wins over an earlier accept (most restrictive answer).
    status = "pending"
    if inv["declined_at"]:
        status = "declined"
    elif inv["accepted_at"]:
        status = "accepted"
    return {
        "patient_display_name": patient["display_name"] if patient else "A friend",
        "contact_name": inv["contact_name"],
        "relationship_label": inv["relationship_label"],
        "expires_at": inv["expires_at"],
        "status": status,
    }


class AcceptBody(BaseModel):
    code: str | None = None


@router.post("/invite/{token}/accept")
def accept_invite(token: str, body: AcceptBody | None = None):
    """The friend's affirmative opt-in. Creates the contact, the accepted
    relationship, and a scoped consent record in one atomic step.
    """
    inv = _load_invite(token)
    if inv["accepted_at"]:
        raise ValueError("this invitation was already accepted")
    if inv["declined_at"]:
        raise ValueError("this invitation was declined")

    # Phone ownership is proven by an SMS code, NOT by holding the invite link.
    # The link is shown in the UI, so possessing it proves nothing about the
    # phone. Until a valid code is supplied, phone_verified stays 0 and
    # `consent.verify_outreach` refuses to dial this contact.
    code_ok = bool(body and body.code and _consume_code(
        inv["contact_phone_e164"], body.code, "invite"
    ))

    existing = q1("SELECT * FROM users WHERE phone_e164=?", (inv["contact_phone_e164"],))
    if existing is not None:
        contact_id = existing["id"]
        if code_ok:
            ex("UPDATE users SET phone_verified=1 WHERE id=?", (contact_id,))
    else:
        contact_id = new_id("user")
        ex(
            "INSERT INTO users (id, display_name, role, phone_e164, phone_verified,"
            " preferred_channel, is_adult_attested, outreach_active, created_at)"
            " VALUES (?,?,'contact',?,?,'call',1,0,?)",
            (
                contact_id, inv["contact_name"], inv["contact_phone_e164"],
                int(code_ok), now_iso(),
            ),
        )

    rel = q1(
        "SELECT * FROM relationships WHERE patient_id=? AND contact_id=?",
        (inv["patient_id"], contact_id),
    )
    if rel is None:
        ex(
            "INSERT INTO relationships (id, patient_id, contact_id, relationship_label,"
            " status, created_at) VALUES (?,?,?,?,'accepted',?)",
            (new_id("rel"), inv["patient_id"], contact_id, inv["relationship_label"], now_iso()),
        )
    else:
        ex("UPDATE relationships SET status='accepted' WHERE id=?", (rel["id"],))

    consent_id = consent_svc.grant_consent(
        inv["patient_id"], contact_id,
        allowed_actions=["voice_call", "sms"],
        allowed_fields=["first_name", "shared_interests", "general_availability"],
    )
    ex(
        "UPDATE invitations SET accepted_at=?, contact_id=? WHERE token=?",
        (now_iso(), contact_id, token),
    )

    verified = bool(q1("SELECT phone_verified FROM users WHERE id=?", (contact_id,))["phone_verified"])
    events.emit(
        "consent.verified",
        f"{inv['contact_name']} ACCEPTED the invitation and opted in to AI calls"
        + ("" if verified else " (phone NOT yet verified - calls still blocked)"),
        payload={
            "contact_id": contact_id,
            "relationship_label": inv["relationship_label"],
            "consent_id": consent_id,
            "phone_verified": verified,
            "dual_consent_complete": verified,
        },
    )

    out = {
        "contact_id": contact_id,
        "relationship_status": "accepted",
        "consent_id": consent_id,
        "phone_verified": verified,
    }
    if not verified:
        # Issue a code so the contact can complete phone verification.
        code = _issue_code(inv["contact_phone_e164"], "invite")
        out["verification_required"] = True
        out["note"] = (
            "Opt-in recorded, but no call can be placed until this phone number "
            "is verified. Re-submit with the code."
        )
        if not get_settings().twilio_configured:
            out["dev_code"] = code
    return out


@router.post("/invite/{token}/decline")
def decline_invite(token: str):
    inv = _load_invite(token)
    ex("UPDATE invitations SET declined_at=? WHERE token=?", (now_iso(), token))
    if inv["contact_id"]:
        consent_svc.revoke_consent(inv["patient_id"], inv["contact_id"])
        ex(
            "UPDATE relationships SET status='declined' WHERE patient_id=? AND contact_id=?",
            (inv["patient_id"], inv["contact_id"]),
        )
    events.emit(
        "consent.revoked",
        f"{inv['contact_name']} DECLINED the invitation; no outreach permitted",
        payload={"contact_name": inv["contact_name"]},
    )
    return {"relationship_status": "declined"}


# --------------------------------------------------------------------------
# Consent management
# --------------------------------------------------------------------------
@router.get("/consents")
def list_consents(patient_id: str):
    rows = q(
        "SELECT c.*, u.display_name AS contact_name, r.status AS relationship_status"
        " FROM consents c JOIN users u ON u.id = c.contact_id"
        " LEFT JOIN relationships r ON r.contact_id = c.contact_id AND r.patient_id = c.patient_id"
        " WHERE c.patient_id=? ORDER BY c.created_at DESC",
        (patient_id,),
    )
    return [
        {
            "id": r["id"],
            "contact_id": r["contact_id"],
            "contact_name": r["contact_name"],
            "relationship_status": r["relationship_status"],
            "purpose": r["purpose"],
            "allowed_actions": json.loads(r["allowed_actions"]),
            "allowed_fields": json.loads(r["allowed_fields"]),
            "quiet_hours_start": r["quiet_hours_start"],
            "quiet_hours_end": r["quiet_hours_end"],
            "max_per_day": r["max_per_day"],
            "active": bool(r["active"]),
            "revoked_at": r["revoked_at"],
        }
        for r in rows
    ]


class ConsentBody(BaseModel):
    patient_id: str
    contact_id: str
    allowed_actions: list[str]
    allowed_fields: list[str]
    quiet_hours_start: int | None = Field(default=None, ge=0, le=23)
    quiet_hours_end: int | None = Field(default=None, ge=0, le=23)
    max_per_day: int | None = Field(default=None, ge=0, le=50)


@router.post("/consents")
def upsert_consent(body: ConsentBody):
    rel = q1(
        "SELECT * FROM relationships WHERE patient_id=? AND contact_id=?",
        (body.patient_id, body.contact_id),
    )
    if rel is None or rel["status"] != "accepted":
        raise ValueError(
            "cannot set consent scope before the contact accepts their invitation"
        )
    for a in body.allowed_actions:
        if a not in {"voice_call", "sms"}:
            raise ValueError(f"unknown action {a!r}")

    cid = consent_svc.grant_consent(
        body.patient_id, body.contact_id,
        allowed_actions=body.allowed_actions,
        allowed_fields=body.allowed_fields,
        quiet_hours_start=body.quiet_hours_start,
        quiet_hours_end=body.quiet_hours_end,
        max_per_day=body.max_per_day,
    )
    events.emit(
        "consent.verified",
        f"Consent scope updated for contact {body.contact_id}",
        payload={
            "allowed_actions": body.allowed_actions,
            "allowed_fields": body.allowed_fields,
        },
    )
    return {"consent_id": cid}


class RevokeBody(BaseModel):
    patient_id: str
    contact_id: str


@router.post("/consents/revoke")
def revoke(body: RevokeBody):
    """Revoke consent. Any in-flight plan for this pair is halted immediately."""
    from ..services import plans as plans_svc

    n = consent_svc.revoke_consent(body.patient_id, body.contact_id)
    events.emit(
        "consent.revoked",
        f"Consent revoked for contact {body.contact_id} ({n} record(s))",
        payload={"contact_id": body.contact_id, "revoked": n},
    )

    halted = []
    live = q(
        "SELECT id FROM plans WHERE patient_id=? AND contact_id=?"
        " AND status NOT IN ('COMPLETE','HALTED_CONSENT_REVOKED','FAILED')",
        (body.patient_id, body.contact_id),
    )
    for p in live:
        plans_svc.halt_for_revoked_consent(p["id"], "consent revoked by user")
        halted.append(p["id"])

    return {"revoked": n, "plans_halted": halted}


class StopBody(BaseModel):
    phone_e164: str

    @field_validator("phone_e164")
    @classmethod
    def _phone(cls, v: str) -> str:
        return consent_svc.validate_e164(v)


@router.post("/sms/stop")
def sms_stop(body: StopBody):
    """Inbound STOP keyword handling. Immediate, total opt-out."""
    result = consent_svc.handle_stop(body.phone_e164)
    events.emit(
        "consent.revoked",
        "STOP received; all outreach to that number revoked",
        payload={"phone": body.phone_e164, **result},
    )
    return result
