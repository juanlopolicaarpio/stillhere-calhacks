"""Consent and outreach authorization. Server-side only, never model-controlled.

Every outbound contact attempt must pass `verify_outreach()`. There is no code
path to a phone call that bypasses it - `voice.start_outbound_call()` calls it
unconditionally before touching a provider.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from ..config import get_settings
from ..db import ex, new_id, now_iso, q, q1
from . import clock

E164 = re.compile(r"^\+[1-9]\d{7,14}$")


class ConsentError(Exception):
    pass


def validate_e164(number: str) -> str:
    n = (number or "").strip().replace(" ", "").replace("-", "")
    if not E164.match(n):
        raise ConsentError(f"invalid E.164 phone number: {number!r}")
    return n


@dataclass
class ConsentDecision:
    allowed: bool
    reason: str
    scope: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {"allowed": self.allowed, "reason": self.reason, "scope": self.scope}


def grant_consent(
    patient_id: str,
    contact_id: str,
    *,
    purpose: str = "social_reconnection",
    allowed_actions: list[str] | None = None,
    allowed_fields: list[str] | None = None,
    quiet_hours_start: int | None = None,
    quiet_hours_end: int | None = None,
    max_per_day: int | None = None,
) -> str:
    s = get_settings()
    cid = new_id("consent")
    ex(
        "INSERT INTO consents (id, patient_id, contact_id, purpose, allowed_actions,"
        " allowed_fields, quiet_hours_start, quiet_hours_end, max_per_day, active, created_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,1,?)",
        (
            cid, patient_id, contact_id, purpose,
            json.dumps(allowed_actions or ["voice_call", "sms"]),
            json.dumps(allowed_fields or ["first_name", "shared_interests", "general_wellbeing"]),
            quiet_hours_start if quiet_hours_start is not None else s.quiet_hours_start,
            quiet_hours_end if quiet_hours_end is not None else s.quiet_hours_end,
            max_per_day if max_per_day is not None else s.max_calls_per_contact_per_day,
            now_iso(),
        ),
    )
    return cid


def revoke_consent(patient_id: str, contact_id: str) -> int:
    rows = q(
        "SELECT id FROM consents WHERE patient_id=? AND contact_id=? AND active=1",
        (patient_id, contact_id),
    )
    for r in rows:
        ex(
            "UPDATE consents SET active=0, revoked_at=? WHERE id=?",
            (now_iso(), r["id"]),
        )
    return len(rows)


def _in_quiet_hours(hour: int, start: int, end: int) -> bool:
    """Quiet hours may wrap midnight (e.g. 21 -> 9)."""
    if start == end:
        return False
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end


def verify_outreach(
    patient_id: str,
    contact_id: str,
    action: str,
    *,
    at: datetime | None = None,
    override_quiet_hours: bool = False,
) -> ConsentDecision:
    """The one gate. Checks, in order:

    1. patient activated outreach
    2. contact relationship accepted (friend affirmatively opted in)
    3. contact phone verified
    4. an active consent record permits this action
    5. not inside quiet hours
    6. under the per-day contact frequency cap
    """
    now = at or clock.now()

    patient = q1("SELECT * FROM users WHERE id=?", (patient_id,))
    if patient is None:
        return ConsentDecision(False, "unknown patient")
    if not patient["outreach_active"]:
        return ConsentDecision(False, "patient has not activated outreach")

    contact = q1("SELECT * FROM users WHERE id=?", (contact_id,))
    if contact is None:
        return ConsentDecision(False, "unknown contact")

    rel = q1(
        "SELECT * FROM relationships WHERE patient_id=? AND contact_id=?",
        (patient_id, contact_id),
    )
    if rel is None:
        return ConsentDecision(False, "no relationship on record")
    if rel["status"] != "accepted":
        return ConsentDecision(
            False, f"contact has not accepted the invitation (status={rel['status']})"
        )

    if action in {"voice_call", "sms"} and not contact["phone_verified"]:
        return ConsentDecision(False, "contact phone number is not verified")

    consent = q1(
        "SELECT * FROM consents WHERE patient_id=? AND contact_id=? AND active=1"
        " ORDER BY created_at DESC LIMIT 1",
        (patient_id, contact_id),
    )
    if consent is None:
        return ConsentDecision(False, "no active consent record")

    allowed_actions = json.loads(consent["allowed_actions"])
    if action not in allowed_actions:
        return ConsentDecision(
            False, f"action {action!r} not in consented actions {allowed_actions}"
        )

    if not override_quiet_hours:
        hour = now.astimezone(timezone.utc).hour
        if _in_quiet_hours(hour, consent["quiet_hours_start"], consent["quiet_hours_end"]):
            return ConsentDecision(
                False,
                f"inside quiet hours "
                f"({consent['quiet_hours_start']}:00-{consent['quiet_hours_end']}:00)",
            )

    since = (now - timedelta(days=1)).isoformat()
    used = q(
        "SELECT id FROM outreach_log WHERE patient_id=? AND contact_id=? AND occurred_at>=?",
        (patient_id, contact_id, since),
    )
    if len(used) >= consent["max_per_day"]:
        return ConsentDecision(
            False,
            f"contact frequency cap reached ({len(used)}/{consent['max_per_day']} in 24h)",
        )

    return ConsentDecision(
        True,
        "consent verified",
        {
            "allowed_actions": allowed_actions,
            "allowed_fields": json.loads(consent["allowed_fields"]),
            "contacts_used_24h": len(used),
            "max_per_day": consent["max_per_day"],
        },
    )


def record_outreach(patient_id: str, contact_id: str, channel: str) -> None:
    ex(
        "INSERT INTO outreach_log (id, patient_id, contact_id, channel, occurred_at)"
        " VALUES (?,?,?,?,?)",
        (new_id("out"), patient_id, contact_id, channel, now_iso()),
    )


def handle_stop(phone_e164: str) -> dict:
    """Inbound STOP keyword. Revokes every consent for that phone immediately."""
    n = validate_e164(phone_e164)
    user = q1("SELECT * FROM users WHERE phone_e164=?", (n,))
    if user is None:
        return {"ok": False, "reason": "no user with that number"}
    revoked = 0
    for r in q("SELECT * FROM relationships WHERE contact_id=?", (user["id"],)):
        revoked += revoke_consent(r["patient_id"], user["id"])
        ex("UPDATE relationships SET status='revoked' WHERE id=?", (r["id"],))
    return {"ok": True, "user_id": user["id"], "consents_revoked": revoked}
