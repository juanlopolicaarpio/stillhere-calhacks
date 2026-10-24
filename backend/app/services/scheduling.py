"""Availability overlap and mock showtimes.

All showtime data is MOCK and labelled `source: "mock"` in every response, event
and UI surface. Nothing here books, reserves, or pays for anything.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from ..db import ex, new_id, q
from . import clock


@dataclass
class Slot:
    start_at: str
    end_at: str

    def as_dict(self) -> dict[str, str]:
        return {"start_at": self.start_at, "end_at": self.end_at}


def _parse(ts: str) -> datetime:
    d = datetime.fromisoformat(ts)
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def add_availability(user_id: str, start_at: str, end_at: str, source: str = "seed") -> str:
    aid = new_id("avail")
    ex(
        "INSERT INTO availability (id, user_id, start_at, end_at, source, visibility)"
        " VALUES (?,?,?,?,?,'agent_internal')",
        (aid, user_id, start_at, end_at, source),
    )
    return aid


def find_free_slots(
    participant_ids: list[str],
    *,
    window_start: str | None = None,
    window_end: str | None = None,
    min_minutes: int = 60,
) -> list[Slot]:
    """Intersect every participant's availability windows.

    Returns slots where ALL participants are free for at least `min_minutes`.
    """
    if not participant_ids:
        return []

    ws = _parse(window_start) if window_start else clock.now()
    we = _parse(window_end) if window_end else ws + timedelta(days=14)

    per_user: list[list[tuple[datetime, datetime]]] = []
    for uid in participant_ids:
        rows = q(
            "SELECT start_at, end_at FROM availability WHERE user_id=? ORDER BY start_at",
            (uid,),
        )
        windows = []
        for r in rows:
            a, b = _parse(r["start_at"]), _parse(r["end_at"])
            a, b = max(a, ws), min(b, we)
            if b > a:
                windows.append((a, b))
        if not windows:
            return []  # someone has no availability at all
        per_user.append(windows)

    # Fold pairwise intersections across all users.
    current = per_user[0]
    for nxt in per_user[1:]:
        merged: list[tuple[datetime, datetime]] = []
        for a1, b1 in current:
            for a2, b2 in nxt:
                a, b = max(a1, a2), min(b1, b2)
                if (b - a).total_seconds() >= min_minutes * 60:
                    merged.append((a, b))
        current = merged
        if not current:
            return []

    current.sort(key=lambda t: t[0])
    return [Slot(a.isoformat(), b.isoformat()) for a, b in current]


def slot_covers(slot_start: str, slot_end: str, candidate_start: str, duration_min: int = 120) -> bool:
    a, b = _parse(slot_start), _parse(slot_end)
    cs = _parse(candidate_start)
    ce = cs + timedelta(minutes=duration_min)
    return a <= cs and ce <= b


WEEKDAYS = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}


def find_slot_on_weekday(
    participant_ids: list[str],
    weekday: int,
    *,
    duration_min: int = 120,
    exclude_start: str | None = None,
) -> str | None:
    """Find a real free slot on a given weekday.

    Used when a contact counterproposes a day by name. Returns an actual
    available start time, or None if that day genuinely has no shared window -
    so the agent can say so instead of inventing a time that fails validation.
    """
    for s in find_free_slots(participant_ids, min_minutes=min(duration_min, 60)):
        start = _parse(s.start_at)
        if start.weekday() != weekday:
            continue
        if exclude_start and start.isoformat() == _parse(exclude_start).isoformat():
            continue
        if slot_covers(s.start_at, s.end_at, start.isoformat(), duration_min):
            return start.isoformat()
    return None


def revalidate_availability(
    participant_ids: list[str], starts_at: str, duration_min: int = 120
) -> tuple[bool, str]:
    """Re-check right before confirming. Guards against availability changing
    between the friend's yes and the patient's approval.
    """
    slots = find_free_slots(participant_ids, min_minutes=min(duration_min, 60))
    for s in slots:
        if slot_covers(s.start_at, s.end_at, starts_at, duration_min):
            return True, "all participants still free at the agreed time"
    return False, "the agreed time is no longer inside a shared free window"


# --------------------------------------------------------------------------
# MOCK showtimes - never a real booking
# --------------------------------------------------------------------------

MOCK_SHOWTIMES: dict[str, list[dict[str, Any]]] = {
    "marvel": [
        {"title": "Marvel: Secret Wars", "theater": "AMC Metreon 16",
         "time": "19:10", "format": "Standard"},
        {"title": "Marvel: Secret Wars", "theater": "AMC Metreon 16",
         "time": "21:45", "format": "IMAX"},
        {"title": "Marvel: Secret Wars", "theater": "Alamo Drafthouse Mission",
         "time": "18:30", "format": "Standard"},
    ],
    "coffee": [
        {"title": "Coffee at Four Barrel", "theater": "Four Barrel Coffee",
         "time": "10:30", "format": "Cafe"},
        {"title": "Coffee at Ritual", "theater": "Ritual Coffee Roasters",
         "time": "15:00", "format": "Cafe"},
    ],
}


def lookup_showtimes(movie: str, date: str | None = None) -> dict[str, Any]:
    """Returns MOCK options. `source` is always 'mock' - callers must surface it."""
    key = "coffee" if "coffee" in (movie or "").lower() else "marvel"
    return {
        "source": "mock",
        "disclaimer": "MOCK DATA - not real showtimes, nothing is booked or paid for",
        "query": {"movie": movie, "date": date},
        "options": MOCK_SHOWTIMES[key],
    }
