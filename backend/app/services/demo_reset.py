"""Deterministic fictional seed + one-click demo reset.

EVERY person and record here is FICTIONAL. No real patient data is used.

Josh's phone number is deliberately set to MY_PHONE_E164 (the developer's own
verified phone) so that the live demo calls a phone the developer controls and
the judge can physically answer it.

The seed intentionally includes:
  * two PRIVATE clinical memories that must NEVER reach Josh, so the privacy
    guard is provable rather than asserted;
  * Daniel, who was invited but never accepted, so the consent gate is provable;
  * overlapping availability for Sam and Josh only.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from ..config import get_settings
from ..db import ex, new_id, now_iso, q1, wipe_all
from . import consent as consent_svc
from . import events, memory, scheduling

SAM = "user_sam"
JOSH = "user_josh"
MAYA = "user_maya"
DANIEL = "user_daniel"

# Fallback demo numbers (clearly fake, 555 range) used when MY_PHONE_E164 is unset.
FAKE_JOSH = "+14155550101"
FAKE_MAYA = "+14155550102"
FAKE_DANIEL = "+14155550103"
FAKE_SAM = "+14155550100"


def _mk_user(uid: str, name: str, role: str, phone: str, *, verified: bool, active: bool = False) -> None:
    ex(
        "INSERT INTO users (id, display_name, role, phone_e164, phone_verified,"
        " timezone, preferred_channel, is_adult_attested, outreach_active, created_at)"
        " VALUES (?,?,?,?,?,?,?,1,?,?)",
        (uid, name, role, phone, int(verified), "America/Los_Angeles", "call",
         int(active), now_iso()),
    )


def _next_weekday(target_weekday: int, hour: int) -> datetime:
    """Next occurrence of a weekday (0=Mon) at `hour`, at least 1 day out."""
    now = datetime.now(timezone.utc)
    base = (now + timedelta(days=1)).replace(
        hour=hour, minute=0, second=0, microsecond=0
    )
    delta = (target_weekday - base.weekday()) % 7
    return base + timedelta(days=delta)


def seed(*, reset_version: int = 1) -> dict[str, Any]:
    s = get_settings()
    josh_phone = s.my_phone_e164 or FAKE_JOSH

    # --- people ---
    _mk_user(SAM, "Sam Rivera", "patient", FAKE_SAM, verified=True, active=True)
    _mk_user(JOSH, "Josh Tanaka", "contact", josh_phone, verified=True)
    _mk_user(MAYA, "Maya Chen", "contact", FAKE_MAYA, verified=True)
    _mk_user(DANIEL, "Daniel Osei", "contact", FAKE_DANIEL, verified=False)

    # --- relationships: Josh & Maya accepted, Daniel never did ---
    for cid, label, status in (
        (JOSH, "best friend", "accepted"),
        (MAYA, "friend", "accepted"),
        (DANIEL, "friend", "invited"),
    ):
        ex(
            "INSERT INTO relationships (id, patient_id, contact_id, relationship_label,"
            " status, created_at) VALUES (?,?,?,?,?,?)",
            (new_id("rel"), SAM, cid, label, status, now_iso()),
        )

    # --- consent: only the two who accepted ---
    consent_svc.grant_consent(
        SAM, JOSH,
        allowed_actions=["voice_call", "sms"],
        allowed_fields=["first_name", "shared_interests", "general_availability"],
        quiet_hours_start=23, quiet_hours_end=7,   # wide window so demos aren't blocked
        max_per_day=25,                            # rehearsal-friendly
    )
    consent_svc.grant_consent(
        SAM, MAYA,
        allowed_actions=["sms"],                    # Maya consented to texts only
        allowed_fields=["first_name"],
        quiet_hours_start=23, quiet_hours_end=7,
        max_per_day=25,
    )

    # --- fictional conversation history + memories with real provenance ---
    sid = "seed_session"
    seeded: list[tuple[str, str, str, str, str, float]] = [
        # (user message, kind, memory value, visibility, subject, confidence)
        (
            "Josh has been my best friend since high school. He's the one person "
            "I actually want to see.",
            "relationship", "Josh is Sam's best friend since high school",
            "shareable_with_contact", JOSH, 0.95,
        ),
        (
            "Josh and I used to watch every Marvel movie together on opening weekend.",
            "preference", "Sam and Josh watch Marvel movies together on opening weekend",
            "shareable_with_contact", JOSH, 0.92,
        ),
        (
            "I miss Josh a lot but I keep not texting him back.",
            "preference", "Sam misses Josh but struggles to initiate contact",
            "agent_internal", JOSH, 0.9,
        ),
        (
            "Before all this, Josh and I used to grab coffee at Four Barrel on "
            "Sunday mornings.",
            "preference", "Sam and Josh used to get coffee on Sunday mornings",
            "shareable_with_contact", JOSH, 0.85,
        ),
        (
            "If something's planned a few days ahead I can usually handle it. "
            "Last minute stuff makes me bail.",
            "boundary", "Sam prefers low-pressure plans arranged a few days in advance",
            "shareable_with_contact", None, 0.88,
        ),
        (
            "I got out of the hospital three weeks ago.",
            "fact", "Sam was discharged from psychiatric hospitalization three weeks ago",
            "private_to_sam", None, 0.99,
        ),
        (
            "The depression is the part that makes reaching out feel impossible.",
            "fact", "Sam's depression diagnosis makes initiating social contact difficult",
            "private_to_sam", None, 0.97,
        ),
    ]

    for text, kind, value, vis, subject, conf in seeded:
        conv_id = memory.save_message(
            SAM, sid, "user", text,
            sensitivity="sensitive" if vis == "private_to_sam" else "normal",
        )
        memory.store_memory(
            SAM, kind, value,
            subject_id=subject,
            source_conversation_id=conv_id,
            source_quote=text,
            confidence=conf,
            visibility=vis,
            confirmed=True,
        )

    # --- availability: Sam & Josh overlap Saturday evening; Maya does not ---
    sat = _next_weekday(5, 17)      # Saturday 17:00
    sun = _next_weekday(6, 11)      # Sunday 11:00 (supports the "Sunday instead" branch)
    for uid in (SAM, JOSH):
        scheduling.add_availability(uid, sat.isoformat(), (sat + timedelta(hours=6)).isoformat())
        scheduling.add_availability(uid, sun.isoformat(), (sun + timedelta(hours=8)).isoformat())
    scheduling.add_availability(
        MAYA,
        (sat + timedelta(days=3)).isoformat(),
        (sat + timedelta(days=3, hours=2)).isoformat(),
    )

    demo_id = new_id("demo")
    ex(
        "INSERT INTO demo_sessions (id, state, reset_version, started_at) VALUES (?,?,?,?)",
        (demo_id, "ready", reset_version, now_iso()),
    )

    return {
        "demo_session_id": demo_id,
        "reset_version": reset_version,
        "patient_id": SAM,
        "contacts": {"josh": JOSH, "maya": MAYA, "daniel": DANIEL},
        "josh_phone_is_real": bool(s.my_phone_e164),
        "memories_seeded": len(seeded),
    }


def reset() -> dict[str, Any]:
    """Wipe and reseed. Safe to run repeatedly between demo rehearsals."""
    prev = q1("SELECT MAX(reset_version) AS v FROM demo_sessions")
    version = ((prev["v"] if prev and prev["v"] else 0) or 0) + 1

    wipe_all()
    events.reset_sequence()
    info = seed(reset_version=version)

    events.emit(
        "demo.reset",
        f"Demo reset to fictional seed (version {version})",
        payload={
            "reset_version": version,
            "patient_id": info["patient_id"],
            "memories_seeded": info["memories_seeded"],
            "josh_number_is_developer_phone": info["josh_phone_is_real"],
            "all_data_fictional": True,
        },
    )
    return info
