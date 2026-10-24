"""Typed tool specs + server-side handlers.

Two consumers:
  * Claude (Anthropic tool-use format) via `TOOL_SPECS`
  * the Cartesia Managed Agent, via the webhook routes in `api/voice.py`

Both funnel into the same handlers here, so there is exactly one implementation
of each capability and one place where validation and consent are enforced.

The model can propose any tool call it likes. These handlers decide what is
actually permitted. Note what is absent: there is no `confirm_plan` tool
exposed to the model. Confirmation is backend-only.
"""
from __future__ import annotations

import time
from typing import Any, Callable

from ..services import consent as consent_svc
from ..services import events, memory, plans, scheduling

TOOL_SPECS: list[dict[str, Any]] = [
    {
        "name": "get_support_context",
        "description": (
            "Retrieve the consent-filtered shared context for a patient/contact "
            "pair, with memory ids as citations. Returns ONLY information the "
            "contact is permitted to hear. Never returns clinical information."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "patient_id": {"type": "string"},
                "contact_id": {"type": "string"},
            },
            "required": ["patient_id", "contact_id"],
        },
    },
    {
        "name": "verify_consent",
        "description": (
            "Check whether a specific outreach action is currently permitted. "
            "Must be called before proposing any contact. Returns allowed=false "
            "with a reason when opt-in is missing, revoked, outside quiet hours, "
            "or rate limited."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "patient_id": {"type": "string"},
                "contact_id": {"type": "string"},
                "action": {"type": "string", "enum": ["voice_call", "sms"]},
            },
            "required": ["patient_id", "contact_id", "action"],
        },
    },
    {
        "name": "find_free_slots",
        "description": "Find time windows where all participants are free.",
        "input_schema": {
            "type": "object",
            "properties": {
                "participant_ids": {"type": "array", "items": {"type": "string"}},
                "min_minutes": {"type": "integer", "default": 60},
            },
            "required": ["participant_ids"],
        },
    },
    {
        "name": "suggest_activity",
        "description": (
            "Propose an activity grounded in the patient's stored preferences, "
            "honouring stated boundaries. Returns the proposal with memory "
            "citations, or null when no stored preference supports one."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "patient_id": {"type": "string"},
                "contact_id": {"type": "string"},
            },
            "required": ["patient_id", "contact_id"],
        },
    },
    {
        "name": "lookup_showtimes",
        "description": (
            "Look up illustrative showtimes. Returns source='mock'. Nothing is "
            "booked, reserved or paid for. Always disclose that options are "
            "illustrative."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "movie": {"type": "string"},
                "date": {"type": "string"},
            },
            "required": ["movie"],
        },
    },
    {
        "name": "record_friend_response",
        "description": (
            "Record the contact's answer. 'accepted' advances only as far as "
            "PATIENT_APPROVAL_PENDING - it never confirms a plan. "
            "'counterproposed' creates a new plan version and clears approvals."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "plan_id": {"type": "string"},
                "response": {
                    "type": "string",
                    "enum": ["accepted", "declined", "counterproposed"],
                },
                "activity": {"type": "string"},
                "starts_at": {"type": "string"},
            },
            "required": ["plan_id", "response"],
        },
    },
    {
        "name": "request_patient_approval",
        "description": (
            "Ask the patient to approve the plan. The patient's decision is "
            "recorded by the backend, not by you."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"plan_id": {"type": "string"}},
            "required": ["plan_id"],
        },
    },
    {
        "name": "store_preference_feedback",
        "description": (
            "Persist a preference or boundary the user just stated, with "
            "provenance. Use for corrections such as 'I don't want crowded "
            "theaters'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "user_id": {"type": "string"},
                "preference": {"type": "string"},
                "kind": {
                    "type": "string",
                    "enum": ["preference", "boundary"],
                    "default": "preference",
                },
                "source": {"type": "string"},
            },
            "required": ["user_id", "preference"],
        },
    },
]

# Deliberately NOT exposed to any model:
#   confirm_plan, send_confirmation, start_outbound_call
# These are backend-authorized only. See specs/CONTRACTS.md section 1.
MODEL_FORBIDDEN_TOOLS = {"confirm_plan", "send_confirmation", "start_outbound_call"}

# The subset handed to Claude during a LIVE conversational turn
# (`agents/conversation.py`). Deliberately narrower than TOOL_SPECS: every tool
# here is read-only except `store_preference_feedback`, which only ever creates a
# memory row. `record_friend_response` and `request_patient_approval` are
# withheld on purpose - during a live turn the model's job is interpretation and
# phrasing, and the deterministic backend records responses from the returned
# intent. That keeps "who recorded what" single-sourced and means a hallucinated
# tool call cannot advance plan state at all.
CONVERSATION_TOOL_NAMES: tuple[str, ...] = (
    "get_support_context",
    "verify_consent",
    "find_free_slots",
    "suggest_activity",
    "lookup_showtimes",
    "store_preference_feedback",
)


def tool_specs(names: tuple[str, ...] | list[str] | None = None) -> list[dict[str, Any]]:
    """Anthropic-format tool specs, optionally filtered to `names`.

    Raises ValueError if a caller asks for a model-forbidden tool, so a wiring
    mistake fails loudly at startup instead of quietly widening the model's
    authority.
    """
    if names is None:
        return list(TOOL_SPECS)
    wanted = list(names)
    bad = [n for n in wanted if n in MODEL_FORBIDDEN_TOOLS]
    if bad:
        raise ValueError(f"refusing to expose model-forbidden tools: {bad}")
    by_name = {spec["name"]: spec for spec in TOOL_SPECS}
    missing = [n for n in wanted if n not in by_name]
    if missing:
        raise ValueError(f"unknown tool(s): {missing}")
    return [by_name[n] for n in wanted]


# --------------------------------------------------------------------------
# Handlers
# --------------------------------------------------------------------------
def _h_get_support_context(patient_id: str, contact_id: str) -> dict[str, Any]:
    decision = consent_svc.verify_outreach(
        patient_id, contact_id, "voice_call", override_quiet_hours=True
    )
    ctx = memory.shareable_context(
        patient_id, contact_id, decision.scope.get("allowed_fields", [])
    )
    return {
        "shared_interests": ctx["shared_interests"],
        "relationship": ctx["relationship"],
        "boundaries": ctx["boundaries"],
        "citations": ctx["citations"],
        "withheld_private_memories": ctx["withheld_by_visibility"],
    }


def _h_verify_consent(patient_id: str, contact_id: str, action: str) -> dict[str, Any]:
    return consent_svc.verify_outreach(patient_id, contact_id, action).as_dict()


def _h_find_free_slots(participant_ids: list[str], min_minutes: int = 60) -> dict[str, Any]:
    slots = scheduling.find_free_slots(participant_ids, min_minutes=min_minutes)
    return {"slots": [s.as_dict() for s in slots], "count": len(slots)}


def _h_suggest_activity(patient_id: str, contact_id: str) -> dict[str, Any]:
    from .orchestrator import choose_activity

    opt = choose_activity(patient_id, contact_id)
    if opt is None:
        return {
            "proposal": None,
            "reason": "no stored preference supports an activity for this pair",
        }
    return {
        "proposal": {"activity": opt.label, "location": opt.location},
        "rationale": opt.rationale,
        "citations": opt.citations,
        "score": opt.score,
    }


def _h_lookup_showtimes(movie: str, date: str | None = None) -> dict[str, Any]:
    return scheduling.lookup_showtimes(movie, date)


def _h_record_friend_response(
    plan_id: str,
    response: str,
    activity: str | None = None,
    starts_at: str | None = None,
) -> dict[str, Any]:
    from ..domain.state_machine import PlanState

    plan = plans.get_plan(plan_id)
    plans.record_response(
        plan_id, plan["contact_id"], response, activity=activity, starts_at=starts_at
    )
    if response == "accepted":
        plans.set_state(plan_id, PlanState.FRIEND_ACCEPTED, reason="contact accepted")
        plans.set_state(plan_id, PlanState.PATIENT_APPROVAL_PENDING,
                        reason="awaiting patient approval")
    elif response == "declined":
        plans.set_state(plan_id, PlanState.FRIEND_DECLINED, reason="contact declined")
        plans.set_state(plan_id, PlanState.COMPLETE, reason="ended after decline")
    else:
        plans.set_state(plan_id, PlanState.FRIEND_COUNTERPROPOSED,
                        reason="contact proposed different terms")
        plans.counterpropose(plan_id, activity=activity, starts_at=starts_at)
        plans.set_state(plan_id, PlanState.CANDIDATE_PLAN_CREATED,
                        reason="re-planned on counterproposal")
        # Still mid-conversation, so a following acceptance stays legal.
        plans.set_state(plan_id, PlanState.CONTACT_IN_PROGRESS,
                        reason="still on the call with the new terms")
    return {"plan_id": plan_id, "state": plans.get_plan(plan_id)["status"]}


def _h_request_patient_approval(plan_id: str) -> dict[str, Any]:
    plan = plans.get_plan(plan_id)
    events.emit(
        "patient.approval_requested",
        "Patient approval requested",
        plan_id=plan_id,
        payload={"activity": plan["activity"], "starts_at": plan["starts_at"]},
    )
    return {"plan_id": plan_id, "state": plan["status"]}


def _h_store_preference_feedback(
    user_id: str, preference: str, kind: str = "preference", source: str = "conversation"
) -> dict[str, Any]:
    m = memory.store_memory(
        user_id, kind, preference,
        source_quote=f"stated via {source}", confidence=0.9, confirmed=True,
    )
    return {"memory_id": m.id, "value": m.value, "visibility": m.visibility}


HANDLERS: dict[str, Callable[..., dict[str, Any]]] = {
    "get_support_context": _h_get_support_context,
    "verify_consent": _h_verify_consent,
    "find_free_slots": _h_find_free_slots,
    "suggest_activity": _h_suggest_activity,
    "lookup_showtimes": _h_lookup_showtimes,
    "record_friend_response": _h_record_friend_response,
    "request_patient_approval": _h_request_patient_approval,
    "store_preference_feedback": _h_store_preference_feedback,
}


class ToolRefused(Exception):
    """Raised when a model asks for a capability it may never have."""


def dispatch(name: str, payload: dict[str, Any], *, session_id: str | None = None) -> dict[str, Any]:
    """Execute a tool call, logging a real timed event for the dashboard."""
    if name in MODEL_FORBIDDEN_TOOLS:
        events.emit(
            "safety.blocked",
            f"Refused model-forbidden tool {name!r}",
            session_id=session_id,
            payload={"tool": name},
        )
        raise ToolRefused(
            f"{name!r} is backend-authorized only and cannot be invoked by a model"
        )

    handler = HANDLERS.get(name)
    if handler is None:
        raise ToolRefused(f"unknown tool {name!r}")

    t0 = time.monotonic()
    try:
        result = handler(**payload)
        status = "ok"
    except Exception as e:
        elapsed = int((time.monotonic() - t0) * 1000)
        events.emit(
            "call.tool_invoked",
            f"{name}: FAILED ({type(e).__name__})",
            session_id=session_id,
            payload={"tool": name, "status": "error", "elapsed_ms": elapsed,
                     "error": str(e)[:200]},
        )
        raise

    elapsed = int((time.monotonic() - t0) * 1000)
    events.emit(
        "call.tool_invoked",
        f"{name}: {status}",
        session_id=session_id,
        payload={"tool": name, "status": status, "elapsed_ms": elapsed},
    )
    return result
