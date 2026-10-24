"""Real event bus. The dashboard renders only what passes through here.

Nothing in this file fabricates events. If the dashboard shows a row, a backend
function actually ran. Each event carries `simulated` so the UI can label
anything that is not a real provider action.
"""
from __future__ import annotations

import asyncio
import json
import threading
from typing import Any

from ..db import ex, new_id, now_iso, q

_subscribers: list[asyncio.Queue] = []
_lock = threading.Lock()
_seq = 0
_loop: asyncio.AbstractEventLoop | None = None


def bind_loop(loop: asyncio.AbstractEventLoop) -> None:
    """Called at app startup so sync code can push into async subscriber queues."""
    global _loop
    _loop = loop


def subscribe() -> asyncio.Queue:
    qq: asyncio.Queue = asyncio.Queue(maxsize=1000)
    with _lock:
        _subscribers.append(qq)
    return qq


def unsubscribe(qq: asyncio.Queue) -> None:
    with _lock:
        if qq in _subscribers:
            _subscribers.remove(qq)


_SENSITIVE_KEYS = {
    "phone", "phone_e164", "to_number", "from_number", "api_key",
    "authorization", "x-api-key", "code", "token", "secret",
}


def redact(payload: dict[str, Any] | None) -> dict[str, Any]:
    """Strip secrets and PII before an event is persisted or streamed."""
    if not payload:
        return {}
    out: dict[str, Any] = {}
    for k, v in payload.items():
        lk = k.lower()
        if lk in _SENSITIVE_KEYS:
            if isinstance(v, str) and len(v) >= 4:
                out[k] = f"***{v[-4:]}"
            else:
                out[k] = "***"
        elif isinstance(v, dict):
            out[k] = redact(v)
        else:
            out[k] = v
    return out


def emit(
    event_type: str,
    safe_summary: str,
    *,
    session_id: str | None = None,
    plan_id: str | None = None,
    call_id: str | None = None,
    payload: dict[str, Any] | None = None,
    simulated: bool = False,
) -> dict[str, Any]:
    """Persist an event and fan it out to live dashboard subscribers."""
    global _seq
    with _lock:
        _seq += 1
        seq = _seq

    safe_payload = redact(payload)
    evt = {
        "id": new_id("evt"),
        "seq": seq,
        "event_type": event_type,
        "safe_summary": safe_summary,
        "session_id": session_id,
        "plan_id": plan_id,
        "call_id": call_id,
        "payload": safe_payload,
        "simulated": simulated,
        "timestamp": now_iso(),
    }

    ex(
        "INSERT INTO agent_events (id, session_id, plan_id, call_id, event_type,"
        " safe_summary, payload, simulated, seq, timestamp)"
        " VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            evt["id"], session_id, plan_id, call_id, event_type, safe_summary,
            json.dumps(safe_payload), int(simulated), seq, evt["timestamp"],
        ),
    )

    for qq in list(_subscribers):
        try:
            if _loop is not None and _loop.is_running():
                _loop.call_soon_threadsafe(qq.put_nowait, evt)
            else:
                qq.put_nowait(evt)
        except Exception:
            # A slow or dead subscriber must never break the workflow.
            pass
    return evt


def recent(limit: int = 200) -> list[dict[str, Any]]:
    rows = q(
        "SELECT * FROM agent_events ORDER BY seq DESC LIMIT ?", (limit,)
    )
    out = []
    for r in reversed(rows):
        d = dict(r)
        d["payload"] = json.loads(d["payload"] or "{}")
        d["simulated"] = bool(d["simulated"])
        out.append(d)
    return out


def reset_sequence() -> None:
    global _seq
    with _lock:
        _seq = 0
