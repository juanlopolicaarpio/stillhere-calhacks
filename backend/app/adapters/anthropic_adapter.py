"""Claude adapter for companion replies, memory extraction, and tool-based planning.

Verified against anthropic 2.3.0 on 2026-10-24.

If ANTHROPIC_API_KEY is absent every function degrades to a deterministic
rule-based path and reports `mode="fallback"`. The fallback is honest, not a
fake: it does real keyword extraction and real template replies, and the UI
labels it. Nothing here ever claims to be Claude when it is not.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from ..config import get_settings


@dataclass
class LLMResult:
    text: str
    mode: str  # 'claude' | 'fallback'
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    raw_stop_reason: str | None = None
    error: str | None = None


def _client():
    from anthropic import Anthropic

    return Anthropic(api_key=get_settings().anthropic_api_key)


def available() -> bool:
    return get_settings().anthropic_configured


# --------------------------------------------------------------------------
# Tool-use turn (used by agents/conversation.py)
# --------------------------------------------------------------------------

class LLMUnavailable(Exception):
    """No API key, so there is no Claude to call. Callers must degrade."""


def tool_turn(
    *,
    system: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    max_tokens: int = 700,
    tool_choice: dict[str, Any] | None = None,
) -> Any:
    """One raw `messages.create` call with tools, returning the SDK response.

    Deliberately thin: the agentic loop (reading `tool_use` blocks, dispatching
    them, feeding `tool_result` back) lives in `agents/conversation.py` so the
    loop and its safety gates are testable without a network.

    Raises `LLMUnavailable` when no key is configured. Provider/transport errors
    propagate so the caller can degrade honestly rather than silently.
    """
    if not available():
        raise LLMUnavailable("ANTHROPIC_API_KEY is not set")
    s = get_settings()
    kwargs: dict[str, Any] = {
        "model": s.anthropic_model,
        "max_tokens": max_tokens,
        "system": system,
        "messages": messages,
        "tools": tools,
    }
    if tool_choice is not None:
        kwargs["tool_choice"] = tool_choice
    return _client().messages.create(**kwargs)


def blocks_of(resp: Any) -> list[Any]:
    """Content blocks of a response, tolerant of a missing/odd shape."""
    return list(getattr(resp, "content", None) or [])


def text_of(resp: Any) -> str:
    """Concatenated text blocks of a response."""
    return "".join(
        getattr(b, "text", "") or ""
        for b in blocks_of(resp)
        if getattr(b, "type", "") == "text"
    ).strip()


def tool_uses_of(resp: Any) -> list[dict[str, Any]]:
    """`tool_use` blocks as plain dicts: {id, name, input}."""
    out: list[dict[str, Any]] = []
    for b in blocks_of(resp):
        if getattr(b, "type", "") != "tool_use":
            continue
        out.append({
            "id": getattr(b, "id", "") or "",
            "name": getattr(b, "name", "") or "",
            "input": dict(getattr(b, "input", None) or {}),
        })
    return out


def assistant_echo(resp: Any) -> dict[str, Any]:
    """Re-serialize an assistant turn so it can be appended to `messages`.

    Only text and tool_use blocks are echoed, which is all the Messages API
    needs to continue a tool-use loop.
    """
    content: list[dict[str, Any]] = []
    for b in blocks_of(resp):
        t = getattr(b, "type", "")
        if t == "text" and (getattr(b, "text", "") or "").strip():
            content.append({"type": "text", "text": b.text})
        elif t == "tool_use":
            content.append({
                "type": "tool_use",
                "id": getattr(b, "id", "") or "",
                "name": getattr(b, "name", "") or "",
                "input": dict(getattr(b, "input", None) or {}),
            })
    return {"role": "assistant", "content": content}


# --------------------------------------------------------------------------
# Companion chat
# --------------------------------------------------------------------------

COMPANION_SYSTEM = """You are Still Here, a warm, steady companion for an adult \
who is rebuilding their social life. You are NOT a therapist, doctor, or crisis \
service, and you never diagnose, assess risk, or infer a clinical state.

How you talk:
- Brief, human, specific. Usually 1-3 sentences. Never a lecture.
- Curious about the people in their life: names, shared history, what they enjoyed.
- Never pressuring. "No" and "not today" are complete answers you accept warmly.
- You never imply you have already contacted anyone, or that a friend has agreed
  to anything, unless the context explicitly says so.

Safety:
- If the person describes immediate danger to themselves or someone else, do not
  play clinician. Say plainly that you are not a crisis service, and surface local
  emergency help: in the US, call or text 988 (Suicide & Crisis Lifeline), or 911
  for immediate danger. Then stay warm and present. Do not promise to alert anyone.

Memory:
- You may be given MEMORIES retrieved from earlier conversations. Use them
  naturally. Do not recite them as a list. If a memory conflicts with what the
  person says now, believe the person and treat the memory as outdated."""


def companion_reply(
    user_text: str,
    *,
    memories: list[str] | None = None,
    history: list[dict[str, str]] | None = None,
) -> LLMResult:
    mem_block = ""
    if memories:
        mem_block = "\n\nMEMORIES retrieved for this turn:\n" + "\n".join(
            f"- {m}" for m in memories
        )

    if not available():
        return LLMResult(text=_fallback_reply(user_text, memories or []), mode="fallback")

    msgs: list[dict[str, Any]] = []
    for h in (history or [])[-12:]:
        role = "user" if h.get("message_role") == "user" else "assistant"
        msgs.append({"role": role, "content": h.get("text", "")})
    msgs.append({"role": "user", "content": user_text})

    try:
        s = get_settings()
        resp = _client().messages.create(
            model=s.anthropic_model,
            max_tokens=400,
            system=COMPANION_SYSTEM + mem_block,
            messages=msgs,
        )
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        return LLMResult(
            text=text.strip() or _fallback_reply(user_text, memories or []),
            mode="claude",
            raw_stop_reason=getattr(resp, "stop_reason", None),
        )
    except Exception as e:
        # Never break the demo on a provider hiccup; degrade and say so.
        return LLMResult(
            text=_fallback_reply(user_text, memories or []),
            mode="fallback",
            error=f"{type(e).__name__}: {e}"[:300],
        )


_CRISIS_RE = re.compile(
    r"\b(kill myself|end my life|suicid\w*|hurt myself|want to die|self-harm)\b",
    re.IGNORECASE,
)


def _fallback_reply(user_text: str, memories: list[str]) -> str:
    """Deterministic companion reply. Real logic, no LLM."""
    t = (user_text or "").strip()
    low = t.lower()

    if _CRISIS_RE.search(low):
        return (
            "I want to be straight with you: I'm not a crisis service, and I'm not "
            "able to keep you safe on my own. If you're in immediate danger, please "
            "call 911. You can also call or text 988, the Suicide & Crisis Lifeline, "
            "any time. I'm still here with you."
        )

    names = re.findall(r"\b([A-Z][a-z]{2,})\b", t)
    name = next((n for n in names if n.lower() not in {"i", "im", "the", "and"}), None)

    if "?" in t:
        if name and any(m for m in memories if name.lower() in m.lower()):
            hit = next(m for m in memories if name.lower() in m.lower())
            return f"From what you've told me before: {hit}"
        if name:
            return f"I don't think you've told me much about {name} yet. Who are they to you?"
        return "I don't want to guess at that one. Can you tell me a bit more?"

    if any(w in low for w in ("miss", "lonely", "alone", "isolated")):
        who = f" {name}" if name else ""
        return (
            f"That sounds heavy, and it makes sense. Missing{who} is a sign that "
            "the connection matters. Would it help to think about one small way to reach out?"
        )

    if any(w in low for w in ("used to", "we would", "we always", "love", "enjoy", "favorite")):
        return "I'll remember that. What was the best part of doing that together?"

    if any(w in low for w in ("tired", "exhausted", "hard", "rough", "bad day")):
        return "Thanks for telling me. You don't have to do anything with that right now."

    return "I'm listening. Tell me more about that."


# --------------------------------------------------------------------------
# Memory extraction
# --------------------------------------------------------------------------

EXTRACTION_SYSTEM = """Extract durable, user-stated facts from ONE message.

Return ONLY a JSON array. Each element:
  {"kind": "fact"|"preference"|"relationship"|"boundary",
   "value": "<short third-person statement>",
   "subject_name": "<person it is about, or null>",
   "confidence": 0.0-1.0,
   "source_quote": "<the exact substring of the message it came from>"}

Rules:
- Only what the user actually said. Never infer a diagnosis, a mental state, a
  risk level, or anything clinical. Never invent.
- "boundary" is for limits and dislikes: "doesn't want crowded theaters".
- "relationship" is for who someone is: "Josh is Sam's best friend".
- If the message contains nothing durable, return [].
- Output the raw JSON array only. No prose, no markdown fence."""


def extract_memories(message: str) -> tuple[list[dict[str, Any]], str]:
    """Returns (candidates, mode)."""
    if not available():
        return _fallback_extract(message), "fallback"
    try:
        s = get_settings()
        resp = _client().messages.create(
            model=s.anthropic_model,
            max_tokens=800,
            system=EXTRACTION_SYSTEM,
            messages=[{"role": "user", "content": message}],
        )
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        text = text.strip()
        if text.startswith("```"):
            text = re.sub(r"^```[a-z]*\n?|\n?```$", "", text).strip()
        data = json.loads(text)
        if not isinstance(data, list):
            raise ValueError("expected a JSON array")
        out = []
        for d in data:
            if not isinstance(d, dict) or not d.get("value"):
                continue
            kind = d.get("kind")
            if kind not in {"fact", "preference", "relationship", "boundary"}:
                kind = "fact"
            out.append({
                "kind": kind,
                "value": str(d["value"])[:300],
                "subject_name": d.get("subject_name"),
                "confidence": float(d.get("confidence", 0.7)),
                "source_quote": str(d.get("source_quote") or message)[:300],
            })
        return out, "claude"
    except Exception:
        return _fallback_extract(message), "fallback"


_NEGATIVE_RE = re.compile(
    r"\b(don'?t|do not|hate|avoid|can'?t stand|rather not|no longer|not into)\b",
    re.IGNORECASE,
)
_RELATION_WORDS = (
    "best friend", "friend", "brother", "sister", "roommate", "cousin",
    "partner", "wife", "husband", "mom", "dad", "mother", "father",
)


def _fallback_extract(message: str) -> list[dict[str, Any]]:
    """Deterministic extraction. Real string work, clearly labeled as fallback."""
    text = (message or "").strip()
    if len(text) < 8:
        return []
    out: list[dict[str, Any]] = []

    for sent in re.split(r"(?<=[.!?])\s+", text):
        s_low = sent.lower()
        if not sent.strip():
            continue

        names = [
            n for n in re.findall(r"\b([A-Z][a-z]{2,})\b", sent)
            if n.lower() not in {"i", "im", "the", "and", "but", "we", "my", "marvel"}
        ]
        subject = names[0] if names else None

        if any(w in s_low for w in _RELATION_WORDS) and subject:
            label = next(w for w in _RELATION_WORDS if w in s_low)
            out.append({
                "kind": "relationship",
                "value": f"{subject} is described as: {label}",
                "subject_name": subject,
                "confidence": 0.8,
                "source_quote": sent.strip()[:300],
            })
            continue

        if _NEGATIVE_RE.search(s_low):
            out.append({
                "kind": "boundary",
                "value": sent.strip()[:300],
                "subject_name": subject,
                "confidence": 0.75,
                "source_quote": sent.strip()[:300],
            })
            continue

        if any(w in s_low for w in (
            "used to", "we would", "we always", "love", "loved", "enjoy",
            "favorite", "like ", "miss",
        )):
            out.append({
                "kind": "preference",
                "value": sent.strip()[:300],
                "subject_name": subject,
                "confidence": 0.7,
                "source_quote": sent.strip()[:300],
            })

    return out[:5]
