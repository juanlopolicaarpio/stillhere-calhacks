"""Coordination bridge: SpacetimeDB is the authoritative transactional store
for the shared live workflow; SQLite mirrors it for local/private reads.

Per specs/CONTRACTS_V2.md §1 and §3:

* Every shared coordination fact (session, call state, proposal version,
  participant response, consent snapshot, confirmation) is written through a
  SpacetimeDB **reducer**.
* `confirm_plan()` is authoritative **in the reducer**. If
  `confirm_if_matching` rejects, SQLite must not record a confirmation. That is
  what makes the invariant un-bypassable rather than duplicated.
* Private data (transcripts, clinical memory, phone numbers, tokens) never
  crosses this boundary. Only sanitized projections do.

Degradation is explicit and honest. When the module is unreachable, these
functions return a `CoordResult` with `applied=False` and a reason, the caller
continues on the SQLite path, and `/api/status` reports
`coordination_mode: "sqlite_fallback"`. Nothing claims SpacetimeDB was used
when it was not.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from ..config import get_settings
from . import events

log = logging.getLogger("stillhere.coordination")


@dataclass
class CoordResult:
    """Outcome of a coordination write."""

    applied: bool          # did a reducer actually run?
    ok: bool               # did it succeed (meaningful only when applied)?
    reason: str = ""       # machine-readable rejection reason, or why skipped

    @property
    def rejected(self) -> bool:
        """A reducer ran and refused. Distinct from 'never ran'."""
        return self.applied and not self.ok


_SKIPPED = CoordResult(applied=False, ok=True, reason="spacetimedb_unavailable")


def proposal_ref(plan_id: str, version: int) -> str:
    """Stable SpacetimeDB proposal id for one version of a SQLite plan.

    The two stores model revisions differently and this function is the bridge:

    * SQLite keeps ONE mutable `plans` row whose `version` increments.
    * SpacetimeDB keeps one IMMUTABLE `plan_proposals` row per version, keyed by
      `proposal_id`.

    Deriving the key as `<plan_id>_v<version>` keeps versions addressable
    without a lookup table, and means a response or confirmation can only ever
    be recorded against the exact version it was given for. Reusing the bare
    plan id (the original bug) left SpacetimeDB stuck on v1 while SQLite moved
    to v2, so the reducer compared the wrong terms and refused to confirm.
    """
    return f"{plan_id}_v{int(version)}"


def enabled() -> bool:
    """True when coordination writes should be attempted."""
    try:
        from ..adapters import spacetimedb_adapter as stdb
    except Exception:
        return False
    try:
        return stdb.configured() and bool(stdb.health().get("reachable"))
    except Exception:
        return False


def _call(reducer: str, args: list[Any], *, plan_id: str | None = None,
          session_id: str | None = None) -> CoordResult:
    """Invoke a reducer, emitting a real event either way.

    Never raises: a coordination outage must not break the user-facing flow.
    The caller decides whether a rejection is fatal (it is, for confirmation).
    """
    try:
        from ..adapters import spacetimedb_adapter as stdb
    except Exception as e:  # pragma: no cover
        return CoordResult(False, True, f"adapter_import_failed: {e}")

    try:
        stdb.call_reducer(reducer, args)
    except stdb.SpacetimeNotConfigured:
        return _SKIPPED
    except stdb.SpacetimeError as e:
        reason = getattr(e, "reason", None) or str(e)[:200]
        # A transport/404 failure means the module never ran the logic; a 400
        # with a known reason means it ran and refused. Only the latter is a
        # real rejection.
        if getattr(e, "status", None) in (None, 404, 502, 503, 504):
            events.emit(
                "stdb.unavailable",
                f"SpacetimeDB unreachable for {reducer}; continuing on SQLite fallback",
                plan_id=plan_id, session_id=session_id,
                payload={"reducer": reducer, "detail": reason},
            )
            return CoordResult(False, True, f"unreachable: {reason}")
        events.emit(
            "stdb.reducer_rejected",
            f"{reducer} rejected: {reason}",
            plan_id=plan_id, session_id=session_id,
            payload={"reducer": reducer, "reason": reason},
        )
        return CoordResult(True, False, reason)
    except Exception as e:  # pragma: no cover
        log.warning("coordination write failed reducer=%s err=%s", reducer, e)
        return CoordResult(False, True, f"error: {type(e).__name__}")

    events.emit(
        "stdb.reducer_called",
        f"{reducer} committed",
        plan_id=plan_id, session_id=session_id,
        payload={"reducer": reducer},
    )
    return CoordResult(True, True)


# ---------------------------------------------------------------------------
# Session / consent
# ---------------------------------------------------------------------------
def start_session(session_id: str, patient_ref: str, contact_ref: str,
                  mode: str) -> CoordResult:
    return _call("start_session", [session_id, patient_ref, contact_ref, mode],
                 session_id=session_id)


def set_consent_state(session_id: str, *, outreach_active: bool, opted_in: bool,
                      phone_verified: bool, allowed_actions: list[str],
                      reason: str = "") -> CoordResult:
    """Sanitized consent snapshot: booleans and a reason, never field values."""
    return _call(
        "set_consent_state",
        [session_id, bool(outreach_active), bool(opted_in), bool(phone_verified),
         list(allowed_actions), reason[:200]],
        session_id=session_id,
    )


def revoke(session_id: str, reason: str) -> CoordResult:
    return _call("revoke_or_cancel", [session_id, reason[:200]], session_id=session_id)


# ---------------------------------------------------------------------------
# Calls
# ---------------------------------------------------------------------------
def set_call_state(call_id: str, session_id: str, *, provider_call_id: str | None,
                   to_number_redacted: str, status: str, mode: str,
                   simulated: bool, error: str | None = None) -> CoordResult:
    """Record call lifecycle. `to_number_redacted` MUST already be masked."""
    if to_number_redacted and not to_number_redacted.startswith("***"):
        # Fail closed rather than write a real number into a public table.
        raise ValueError("refusing to store an unredacted number in SpacetimeDB")
    return _call(
        "set_call_state",
        [call_id, session_id, provider_call_id or "", to_number_redacted,
         status, mode, bool(simulated), error or ""],
        session_id=session_id,
    )


# ---------------------------------------------------------------------------
# Proposals / responses / confirmation
# ---------------------------------------------------------------------------
def propose(proposal_id: str, session_id: str, version: int, *, activity: str,
            location: str | None, starts_at: str, why_contact: str,
            why_activity: str, memory_citations: list[str],
            supersedes_version: int = 0) -> CoordResult:
    return _call(
        "propose_or_revise_plan",
        [proposal_id, session_id, int(version), activity, location or "",
         starts_at, why_contact[:500], why_activity[:500],
         list(memory_citations or []), int(supersedes_version or 0)],
        plan_id=proposal_id, session_id=session_id,
    )


def respond(response_id: str, proposal_id: str, version: int, *, role: str,
            response: str, agreed_activity: str, agreed_starts_at: str,
            simulated: bool) -> CoordResult:
    """Record one party's answer against a SPECIFIC proposal version."""
    reducer = "respond_as_friend" if role == "contact" else "record_patient_approval"
    return _call(
        reducer,
        [response_id, proposal_id, int(version), response, agreed_activity,
         agreed_starts_at, bool(simulated)],
        plan_id=proposal_id,
    )


def confirm(proposal_id: str, version: int, idempotency_key: str) -> CoordResult:
    """THE authoritative confirmation gate.

    A rejection here means the plan is NOT confirmed, and the caller must not
    write a confirmation to SQLite. When the module is unreachable the caller
    falls back to the local deterministic gate, which enforces the same rule.
    """
    return _call(
        "confirm_if_matching",
        [proposal_id, int(version), idempotency_key],
        plan_id=proposal_id,
    )


def append_event(event_id: str, seq: int, *, session_id: str | None,
                 proposal_id: str | None, call_id: str | None, event_type: str,
                 safe_summary: str, payload_json: str,
                 simulated: bool) -> CoordResult:
    """Mirror one already-redacted event into the public feed."""
    return _call(
        "append_sanitized_event",
        [event_id, int(seq), session_id or "", proposal_id or "", call_id or "",
         event_type, safe_summary[:300], payload_json[:4000], bool(simulated)],
    )
