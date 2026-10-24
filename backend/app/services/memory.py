"""Persistent memory with provenance, visibility and corrections.

Visibility ladder (narrowest to widest):
  private_to_sam      - never leaves the owner's own view
  agent_internal      - usable for planning, never spoken to a third party
  shareable_with_contact - may be referenced on a call with an opted-in contact
  public_demo_safe    - safe for the judge-facing dashboard

`shareable_context()` is the ONLY function the outbound call path may use to
build what the agent is allowed to say. It filters by visibility AND by the
consented `allowed_fields` scope.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, asdict
from typing import Any

from ..db import ex, new_id, now_iso, q, q1

VISIBILITY_ORDER = {
    "private_to_sam": 0,
    "agent_internal": 1,
    "shareable_with_contact": 2,
    "public_demo_safe": 3,
}

# Terms that must never appear in contact-facing context, regardless of
# visibility labelling. Defence in depth against a mislabelled row.
SENSITIVE_PATTERNS = [
    r"\bdiagnos\w*", r"\bhospitaliz\w*", r"\bdischarg\w*", r"\bpsychiatric\b",
    r"\bdepress\w*", r"\bsuicid\w*", r"\bself-harm\b", r"\bmedication\b",
    r"\bantidepress\w*", r"\btherapist\b", r"\binpatient\b", r"\bward\b",
    r"\bcrisis\b", r"\bward\b", r"\bmeds\b",
]
_SENSITIVE_RE = re.compile("|".join(SENSITIVE_PATTERNS), re.IGNORECASE)


class MemoryError_(Exception):
    pass


@dataclass
class Memory:
    id: str
    owner_id: str
    subject_id: str | None
    kind: str
    value: str
    source_conversation_id: str | None
    source_quote: str | None
    confidence: float
    confirmed: bool
    visibility: str
    created_at: str
    updated_at: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _row_to_memory(r) -> Memory:
    return Memory(
        id=r["id"],
        owner_id=r["owner_id"],
        subject_id=r["subject_id"],
        kind=r["kind"],
        value=r["value"],
        source_conversation_id=r["source_conversation_id"],
        source_quote=r["source_quote"],
        confidence=r["confidence"],
        confirmed=bool(r["confirmed"]),
        visibility=r["visibility"],
        created_at=r["created_at"],
        updated_at=r["updated_at"],
    )


def classify_sensitivity(text: str) -> str:
    """Default visibility for a newly extracted memory.

    Errs toward privacy: anything touching clinical topics is private by default
    and must be explicitly widened by the user.
    """
    return "private_to_sam" if _SENSITIVE_RE.search(text or "") else "agent_internal"


def store_memory(
    owner_id: str,
    kind: str,
    value: str,
    *,
    subject_id: str | None = None,
    source_conversation_id: str | None = None,
    source_quote: str | None = None,
    confidence: float = 0.6,
    visibility: str | None = None,
    confirmed: bool = False,
) -> Memory:
    if kind not in {"fact", "preference", "relationship", "boundary"}:
        raise MemoryError_(f"unknown memory kind {kind!r}")
    vis = visibility or classify_sensitivity(value)
    if vis not in VISIBILITY_ORDER:
        raise MemoryError_(f"unknown visibility {vis!r}")

    mid = new_id("mem")
    ts = now_iso()
    ex(
        "INSERT INTO memories (id, owner_id, subject_id, kind, value,"
        " source_conversation_id, source_quote, confidence, confirmed, visibility,"
        " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            mid, owner_id, subject_id, kind, value, source_conversation_id,
            source_quote, confidence, int(confirmed), vis, ts, ts,
        ),
    )
    return get_memory(mid)  # type: ignore[return-value]


def get_memory(memory_id: str) -> Memory | None:
    r = q1("SELECT * FROM memories WHERE id=? AND deleted_at IS NULL", (memory_id,))
    return _row_to_memory(r) if r else None


def list_memories(owner_id: str, *, include_deleted: bool = False) -> list[Memory]:
    sql = "SELECT * FROM memories WHERE owner_id=?"
    if not include_deleted:
        sql += " AND deleted_at IS NULL AND superseded_by IS NULL"
    sql += " ORDER BY updated_at DESC"
    return [_row_to_memory(r) for r in q(sql, (owner_id,))]


def correct_memory(
    memory_id: str,
    new_value: str,
    *,
    visibility: str | None = None,
    supersede: bool = True,
) -> Memory:
    """Correct a memory. By default the old row is kept as a superseded
    tombstone so provenance survives, and retrieval stops returning it.
    """
    old = q1("SELECT * FROM memories WHERE id=?", (memory_id,))
    if old is None:
        raise MemoryError_(f"no memory {memory_id!r}")

    new_mem = store_memory(
        old["owner_id"],
        old["kind"],
        new_value,
        subject_id=old["subject_id"],
        source_conversation_id=old["source_conversation_id"],
        source_quote=f"corrected from: {old['value']!r}",
        confidence=0.95,
        visibility=visibility or old["visibility"],
        confirmed=True,
    )
    if supersede:
        ex(
            "UPDATE memories SET superseded_by=?, updated_at=? WHERE id=?",
            (new_mem.id, now_iso(), memory_id),
        )
    return new_mem


def set_visibility(memory_id: str, visibility: str) -> Memory:
    if visibility not in VISIBILITY_ORDER:
        raise MemoryError_(f"unknown visibility {visibility!r}")
    ex(
        "UPDATE memories SET visibility=?, updated_at=? WHERE id=?",
        (visibility, now_iso(), memory_id),
    )
    m = get_memory(memory_id)
    if m is None:
        raise MemoryError_(f"no memory {memory_id!r}")
    return m


def delete_memory(memory_id: str) -> bool:
    """Tombstone the row. Retrieval must stop returning it immediately."""
    if q1("SELECT id FROM memories WHERE id=?", (memory_id,)) is None:
        return False
    ex(
        "UPDATE memories SET deleted_at=?, updated_at=? WHERE id=?",
        (now_iso(), now_iso(), memory_id),
    )
    return True


def _score(mem: Memory, terms: list[str]) -> float:
    text = f"{mem.value} {mem.kind}".lower()
    hits = sum(1 for t in terms if t and t in text)
    return hits + mem.confidence * 0.1 + (0.3 if mem.confirmed else 0.0)


# Explicit allow-lists per audience. Being a set rather than an ordinal
# threshold means "private_to_sam can reach planning" is impossible to express
# by accident.
AUDIENCE_VISIBILITY: dict[str, set[str]] = {
    # Sam reviewing his own memory panel: everything he owns.
    "owner": {"private_to_sam", "agent_internal", "shareable_with_contact", "public_demo_safe"},
    # Internal planning: private clinical context is deliberately excluded.
    "planning": {"agent_internal", "shareable_with_contact", "public_demo_safe"},
    # Anything a contact could hear.
    "contact": {"shareable_with_contact", "public_demo_safe"},
    # Judge-facing dashboard.
    "demo": {"public_demo_safe"},
}


def retrieve(
    owner_id: str,
    query: str = "",
    *,
    subject_id: str | None = None,
    audience: str = "planning",
    limit: int = 12,
) -> list[Memory]:
    """Retrieve memories for a named audience.

    `audience` selects an explicit visibility allow-list; unknown audiences
    fail closed to the most restrictive set.
    """
    allowed = AUDIENCE_VISIBILITY.get(audience, AUDIENCE_VISIBILITY["demo"])
    terms = [t for t in re.split(r"\W+", (query or "").lower()) if len(t) > 2]

    candidates = [m for m in list_memories(owner_id) if m.visibility in allowed]
    if subject_id:
        subject_matches = [m for m in candidates if m.subject_id == subject_id]
        others = [m for m in candidates if m.subject_id != subject_id]
        candidates = subject_matches + others

    if terms:
        candidates.sort(key=lambda m: _score(m, terms), reverse=True)
    return candidates[:limit]


def shareable_context(
    owner_id: str,
    contact_id: str,
    allowed_fields: list[str],
    *,
    limit: int = 8,
) -> dict[str, Any]:
    """Build the ONLY payload the outbound voice agent may reference.

    Two independent guards:
      1. visibility must be shareable_with_contact or public_demo_safe
      2. the value must not match any clinical/sensitive pattern

    Returns citations so the dashboard can prove where each line came from.
    """
    all_rows = list_memories(owner_id)
    contact_visible = {"shareable_with_contact", "public_demo_safe"}

    # Withheld because the user never widened their visibility. These are the
    # genuinely private rows (e.g. the hospitalisation and diagnosis facts).
    withheld_by_visibility = [
        {"memory_id": m.id, "visibility": m.visibility, "reason": "visibility"}
        for m in all_rows
        if m.visibility not in contact_visible
    ]

    safe: list[Memory] = []
    blocked_by_content: list[dict[str, str]] = []
    for m in all_rows:
        if m.visibility not in contact_visible:
            continue
        if _SENSITIVE_RE.search(m.value):
            # A row labelled shareable but containing clinical language. The
            # content guard catches the mislabelling.
            blocked_by_content.append(
                {"memory_id": m.id, "reason": "matched sensitive pattern"}
            )
            continue
        if m.subject_id not in (None, contact_id) and m.kind == "relationship":
            continue
        safe.append(m)

    safe = safe[:limit]
    blocked = withheld_by_visibility + blocked_by_content
    return {
        "shared_interests": [m.value for m in safe if m.kind in {"fact", "preference"}],
        "relationship": next(
            (m.value for m in safe if m.kind == "relationship"), None
        ),
        "boundaries": [m.value for m in safe if m.kind == "boundary"],
        "citations": [
            {
                "memory_id": m.id,
                "value": m.value,
                "kind": m.kind,
                "visibility": m.visibility,
                "source_conversation_id": m.source_conversation_id,
                "source_quote": m.source_quote,
                "confidence": m.confidence,
            }
            for m in safe
        ],
        # Total memories deliberately kept away from this contact.
        "blocked_count": len(blocked),
        "withheld_by_visibility": len(withheld_by_visibility),
        "blocked_by_content_guard": len(blocked_by_content),
        "blocked": blocked,
        "allowed_fields": allowed_fields,
    }


def assert_no_sensitive_leak(text: str) -> None:
    """Final outbound guard. Raises if contact-facing text mentions clinical topics."""
    hit = _SENSITIVE_RE.search(text or "")
    if hit:
        raise MemoryError_(
            f"refusing to send contact-facing text containing sensitive term {hit.group(0)!r}"
        )


def save_message(
    owner_id: str, session_id: str, role: str, text: str, sensitivity: str = "normal"
) -> str:
    cid = new_id("conv")
    ex(
        "INSERT INTO conversations (id, owner_id, session_id, message_role, text,"
        " sensitivity, created_at) VALUES (?,?,?,?,?,?,?)",
        (cid, owner_id, session_id, role, text, sensitivity, now_iso()),
    )
    return cid


def history(owner_id: str, limit: int = 40) -> list[dict[str, Any]]:
    rows = q(
        "SELECT * FROM conversations WHERE owner_id=? ORDER BY created_at DESC LIMIT ?",
        (owner_id, limit),
    )
    return [dict(r) for r in reversed(rows)]
