"""The single source of "now".

Still Here's whole premise is longitudinal: it notices that a friendship has
gone quiet *relative to its own history*. That behaviour is untestable in a
hackathon if the only way to observe three weeks is to wait three weeks.

So every time-dependent decision reads `clock.now()` instead of
`datetime.now()`, and `clock` can be offset. Setting a +21 day offset makes
baselines, cooling-off windows, quiet hours, availability and opportunity
detection *all* agree that three weeks have passed. Nothing is faked: the same
code runs on the same data, it is just asked a question about a different
instant.

The offset is persisted, so it survives a backend restart - otherwise a demo
would silently snap back to the present mid-rehearsal.

Honesty rule: whenever the offset is non-zero the system is in a simulated
present. `offset_state()` says so, `/api/status` reports it, and the UI must
label it. A warped clock must never be mistaken for real elapsed time.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from ..db import ex, now_iso, q1

#: Rows are keyed so there is exactly one offset for the whole system. A
#: per-user offset would let two parts of one decision disagree about what day
#: it is, which is worse than no time travel at all.
_KEY = "global"

_lock = threading.Lock()
_cached: float | None = None


def _ensure_table() -> None:
    ex(
        "CREATE TABLE IF NOT EXISTS clock_offset ("
        " key TEXT PRIMARY KEY, offset_seconds REAL NOT NULL,"
        " reason TEXT, set_at TEXT NOT NULL)"
    )


def offset_seconds() -> float:
    """Current offset in seconds. 0.0 means the real present."""
    global _cached
    if _cached is not None:
        return _cached
    with _lock:
        if _cached is not None:
            return _cached
        try:
            _ensure_table()
            row = q1("SELECT offset_seconds FROM clock_offset WHERE key=?", (_KEY,))
            _cached = float(row["offset_seconds"]) if row else 0.0
        except Exception:
            # A clock that cannot read its offset must behave like a normal
            # clock, never crash the request.
            _cached = 0.0
        return _cached


def now() -> datetime:
    """Timezone-aware current time, including any simulation offset."""
    return datetime.now(timezone.utc) + timedelta(seconds=offset_seconds())


def real_now() -> datetime:
    """Actual wall clock, ignoring the offset.

    Used for things that describe *the system* rather than the scenario: when a
    row was physically written, how long a provider request took, token
    expiry. Backdating those would corrupt the audit trail.
    """
    return datetime.now(timezone.utc)


def advance(*, days: float = 0, hours: float = 0, reason: str = "") -> float:
    """Move the simulated present forward (or back, with negatives).

    Returns the new total offset in seconds.
    """
    global _cached
    delta = days * 86400 + hours * 3600
    with _lock:
        _ensure_table()
        current = offset_seconds()
        new = current + delta
        ex(
            "INSERT INTO clock_offset (key, offset_seconds, reason, set_at)"
            " VALUES (?,?,?,?)"
            " ON CONFLICT(key) DO UPDATE SET offset_seconds=excluded.offset_seconds,"
            " reason=excluded.reason, set_at=excluded.set_at",
            (_KEY, new, reason or f"advanced {days}d {hours}h", now_iso()),
        )
        _cached = new
    return new


def set_offset(seconds: float, *, reason: str = "") -> float:
    """Set the offset absolutely. `set_offset(0)` returns to the real present."""
    global _cached
    with _lock:
        _ensure_table()
        ex(
            "INSERT INTO clock_offset (key, offset_seconds, reason, set_at)"
            " VALUES (?,?,?,?)"
            " ON CONFLICT(key) DO UPDATE SET offset_seconds=excluded.offset_seconds,"
            " reason=excluded.reason, set_at=excluded.set_at",
            (_KEY, float(seconds), reason or "set directly", now_iso()),
        )
        _cached = float(seconds)
    return float(seconds)


def reset() -> None:
    """Return to the real present."""
    set_offset(0.0, reason="reset to real time")


def invalidate_cache() -> None:
    """Drop the in-process cache.

    Needed after a `wipe_all()` or a DB swap, where the table contents changed
    underneath us.
    """
    global _cached
    with _lock:
        _cached = None


@dataclass(frozen=True)
class ClockState:
    simulated: bool
    offset_seconds: float
    offset_days: float
    simulated_now: str
    real_now: str
    reason: str
    note: str


def offset_state() -> ClockState:
    """Everything a UI or an API response needs to be honest about the clock."""
    off = offset_seconds()
    reason = ""
    try:
        _ensure_table()
        row = q1("SELECT reason FROM clock_offset WHERE key=?", (_KEY,))
        reason = (row["reason"] or "") if row else ""
    except Exception:
        pass
    simulated = abs(off) > 1.0
    return ClockState(
        simulated=simulated,
        offset_seconds=off,
        offset_days=round(off / 86400, 2),
        simulated_now=now().isoformat(),
        real_now=real_now().isoformat(),
        reason=reason,
        note=(
            f"SIMULATED TIME: the system believes it is "
            f"{round(off / 86400, 1)} days from the real present. Longitudinal "
            f"behaviour is being exercised against shifted time, not real "
            f"elapsed time."
            if simulated else
            "Real time. No clock offset is in effect."
        ),
    )
