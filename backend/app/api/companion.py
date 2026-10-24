"""Companion chat and user-controlled memory."""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

from ..services import companion, memory

router = APIRouter(prefix="/api", tags=["companion"])


class MessageBody(BaseModel):
    user_id: str
    text: str = Field(min_length=1, max_length=4000)
    session_id: str | None = None


@router.post("/companion/message")
def message(body: MessageBody):
    return companion.handle_message(body.user_id, body.text, body.session_id)


@router.get("/memories")
def list_memories(user_id: str, audience: str = "owner"):
    if audience not in memory.AUDIENCE_VISIBILITY:
        raise ValueError(
            f"unknown audience {audience!r}; expected one of "
            f"{sorted(memory.AUDIENCE_VISIBILITY)}"
        )
    allowed = memory.AUDIENCE_VISIBILITY[audience]
    return [
        m.as_dict() for m in memory.list_memories(user_id) if m.visibility in allowed
    ]


class PatchBody(BaseModel):
    value: str | None = None
    visibility: str | None = None


@router.patch("/memories/{memory_id}")
def patch_memory(memory_id: str, body: PatchBody):
    """Correct a memory and/or change its visibility.

    A value change creates a NEW row and supersedes the old one, so provenance
    survives and later retrieval uses the correction.
    """
    from ..services import events

    if body.value is None and body.visibility is None:
        raise ValueError("provide at least one of value or visibility")

    if body.value is not None:
        m = memory.correct_memory(memory_id, body.value, visibility=body.visibility)
        events.emit(
            "memory.corrected",
            f"Memory corrected to: {m.value}",
            payload={
                "old_memory_id": memory_id,
                "new_memory_id": m.id,
                "value": m.value,
                "visibility": m.visibility,
            },
        )
        return m.as_dict()

    m = memory.set_visibility(memory_id, body.visibility)  # type: ignore[arg-type]
    events.emit(
        "memory.corrected",
        f"Memory visibility changed to {m.visibility}",
        payload={"memory_id": m.id, "visibility": m.visibility},
    )
    return m.as_dict()


@router.delete("/memories/{memory_id}")
def delete_memory(memory_id: str):
    from ..services import events

    ok = memory.delete_memory(memory_id)
    if not ok:
        raise ValueError("memory not found")
    events.emit(
        "memory.deleted",
        "Memory deleted; it will no longer be retrieved",
        payload={"memory_id": memory_id},
    )
    return {"deleted": True}
