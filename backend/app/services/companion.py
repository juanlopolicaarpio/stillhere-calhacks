"""Companion chat pipeline.

message -> persist -> retrieve permitted memories -> reply -> extract candidates
-> classify sensitivity -> attach provenance -> persist

Extraction never invents and never infers clinical state; see
`adapters/anthropic_adapter.EXTRACTION_SYSTEM` and `_fallback_extract`.
"""
from __future__ import annotations

from typing import Any

from ..adapters import anthropic_adapter as llm
from ..db import new_id, q1
from . import events, memory


def _resolve_subject(owner_id: str, subject_name: str | None) -> str | None:
    """Map an extracted person name onto a real user id when we know them."""
    if not subject_name:
        return None
    row = q1(
        "SELECT u.id FROM users u JOIN relationships r ON r.contact_id = u.id"
        " WHERE r.patient_id = ? AND lower(u.display_name) LIKE ?",
        (owner_id, f"{subject_name.lower()}%"),
    )
    return row["id"] if row else None


def handle_message(
    user_id: str, text: str, session_id: str | None = None
) -> dict[str, Any]:
    if not (text or "").strip():
        raise ValueError("message text is required")

    sid = session_id or new_id("sess")

    # 1. persist the user's message
    conv_id = memory.save_message(user_id, sid, "user", text)

    # 2. retrieve what the companion is allowed to use (never private clinical rows
    #    belonging to someone else; owner audience = Sam's own full memory)
    retrieved = memory.retrieve(user_id, text, audience="owner", limit=8)
    events.emit(
        "memory.retrieved",
        f"{len(retrieved)} memories retrieved for this turn",
        session_id=sid,
        payload={
            "count": len(retrieved),
            "memory_ids": [m.id for m in retrieved],
            "values": [m.value for m in retrieved][:5],
        },
    )

    # 3. reply
    result = llm.companion_reply(
        text,
        memories=[m.value for m in retrieved],
        history=memory.history(user_id, limit=12),
    )
    memory.save_message(user_id, sid, "assistant", result.text)

    # 4. extract candidate memories with provenance
    candidates, extract_mode = llm.extract_memories(text)
    stored: list[dict[str, Any]] = []
    for c in candidates:
        vis = memory.classify_sensitivity(c["value"])
        m = memory.store_memory(
            user_id,
            c["kind"],
            c["value"],
            subject_id=_resolve_subject(user_id, c.get("subject_name")),
            source_conversation_id=conv_id,
            source_quote=c.get("source_quote"),
            confidence=c.get("confidence", 0.7),
            visibility=vis,
        )
        stored.append(m.as_dict())

    if stored:
        events.emit(
            "memory.extracted",
            f"{len(stored)} candidate memories extracted ({extract_mode})",
            session_id=sid,
            payload={
                "mode": extract_mode,
                "memories": [
                    {
                        "id": s["id"],
                        "kind": s["kind"],
                        "value": s["value"],
                        "visibility": s["visibility"],
                        "source_conversation_id": s["source_conversation_id"],
                    }
                    for s in stored
                ],
            },
        )

    return {
        "reply": result.text,
        "session_id": sid,
        "reasoning_mode": result.mode,
        "extraction_mode": extract_mode,
        "provider_error": result.error,
        "memories_considered": [
            {
                "id": m.id,
                "value": m.value,
                "kind": m.kind,
                "visibility": m.visibility,
                "confidence": m.confidence,
                "source_quote": m.source_quote,
            }
            for m in retrieved
        ],
        "memories_extracted": stored,
    }
