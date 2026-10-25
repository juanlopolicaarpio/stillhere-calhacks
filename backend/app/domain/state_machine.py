"""Deterministic plan state machine.

Design rule from the build plan: *Claude proposes, code authorizes.*
The LLM can never write PLAN_CONFIRMED. Only `attempt_confirm()` can, and only
when both parties have accepted the *identical* activity and start time.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class PlanState(StrEnum):
    OPPORTUNITY_IDENTIFIED = "OPPORTUNITY_IDENTIFIED"
    CONSENT_VERIFIED = "CONSENT_VERIFIED"
    MEMORY_RETRIEVED = "MEMORY_RETRIEVED"
    CANDIDATE_PLAN_CREATED = "CANDIDATE_PLAN_CREATED"
    CONTACT_IN_PROGRESS = "CONTACT_IN_PROGRESS"
    FRIEND_ACCEPTED = "FRIEND_ACCEPTED"
    FRIEND_DECLINED = "FRIEND_DECLINED"
    FRIEND_COUNTERPROPOSED = "FRIEND_COUNTERPROPOSED"
    PATIENT_APPROVAL_PENDING = "PATIENT_APPROVAL_PENDING"
    PATIENT_ACCEPTED = "PATIENT_ACCEPTED"
    PATIENT_DECLINED = "PATIENT_DECLINED"
    AVAILABILITY_REVALIDATED = "AVAILABILITY_REVALIDATED"
    PLAN_CONFIRMED = "PLAN_CONFIRMED"
    NOTIFICATIONS_SENT = "NOTIFICATIONS_SENT"
    COMPLETE = "COMPLETE"
    HALTED_CONSENT_REVOKED = "HALTED_CONSENT_REVOKED"
    FAILED = "FAILED"


# Allowed transitions. Anything not listed here is rejected.
TRANSITIONS: dict[PlanState, set[PlanState]] = {
    PlanState.OPPORTUNITY_IDENTIFIED: {PlanState.CONSENT_VERIFIED, PlanState.FAILED},
    PlanState.CONSENT_VERIFIED: {PlanState.MEMORY_RETRIEVED, PlanState.FAILED},
    PlanState.MEMORY_RETRIEVED: {PlanState.CANDIDATE_PLAN_CREATED, PlanState.FAILED},
    PlanState.CANDIDATE_PLAN_CREATED: {PlanState.CONTACT_IN_PROGRESS, PlanState.FAILED},
    PlanState.CONTACT_IN_PROGRESS: {
        PlanState.FRIEND_ACCEPTED,
        PlanState.FRIEND_DECLINED,
        PlanState.FRIEND_COUNTERPROPOSED,
        PlanState.FAILED,
    },
    # A counterproposal loops back to a fresh candidate plan - it never confirms directly.
    PlanState.FRIEND_COUNTERPROPOSED: {PlanState.CANDIDATE_PLAN_CREATED, PlanState.FAILED},
    # A friend may change their mind after saying yes, including while we are
    # waiting on the patient. That is a normal human event, not an error.
    PlanState.FRIEND_ACCEPTED: {
        # Group plans return here between independently consented calls.  The
        # next contact can be called only after the previous contact's response
        # is durably recorded.
        PlanState.CONTACT_IN_PROGRESS,
        PlanState.PATIENT_APPROVAL_PENDING,
        PlanState.FRIEND_COUNTERPROPOSED,
        PlanState.FRIEND_DECLINED,
        PlanState.FAILED,
    },
    PlanState.FRIEND_DECLINED: {PlanState.COMPLETE},
    PlanState.PATIENT_APPROVAL_PENDING: {
        PlanState.PATIENT_ACCEPTED,
        PlanState.PATIENT_DECLINED,
        # The friend can still retract or change terms before the patient answers.
        PlanState.FRIEND_COUNTERPROPOSED,
        PlanState.FRIEND_DECLINED,
        PlanState.FAILED,
    },
    PlanState.PATIENT_DECLINED: {PlanState.COMPLETE},
    PlanState.PATIENT_ACCEPTED: {PlanState.AVAILABILITY_REVALIDATED, PlanState.FAILED},
    PlanState.AVAILABILITY_REVALIDATED: {PlanState.PLAN_CONFIRMED, PlanState.FAILED},
    PlanState.PLAN_CONFIRMED: {PlanState.NOTIFICATIONS_SENT, PlanState.FAILED},
    PlanState.NOTIFICATIONS_SENT: {PlanState.COMPLETE},
    PlanState.COMPLETE: set(),
    PlanState.HALTED_CONSENT_REVOKED: set(),
    PlanState.FAILED: set(),
}

# Consent revocation is an interrupt: legal from any live state.
_LIVE_STATES = {
    s for s in PlanState
    if s not in {PlanState.COMPLETE, PlanState.HALTED_CONSENT_REVOKED, PlanState.FAILED}
}
for _s in _LIVE_STATES:
    TRANSITIONS[_s].add(PlanState.HALTED_CONSENT_REVOKED)


class TransitionError(Exception):
    """Raised when an illegal state transition is attempted."""


def assert_transition(current: PlanState | str, target: PlanState | str) -> None:
    cur = PlanState(current)
    tgt = PlanState(target)
    if tgt not in TRANSITIONS.get(cur, set()):
        raise TransitionError(f"illegal transition {cur} -> {tgt}")


def can_transition(current: PlanState | str, target: PlanState | str) -> bool:
    try:
        assert_transition(current, target)
        return True
    except (TransitionError, ValueError):
        return False


@dataclass(frozen=True)
class Approval:
    """One party's response, recorded with the exact terms they agreed to."""
    user_id: str
    response: str | None
    activity: str | None
    starts_at: str | None
    simulated: bool = False

    @property
    def accepted(self) -> bool:
        return self.response == "accepted"


@dataclass(frozen=True)
class ConfirmDecision:
    ok: bool
    reason: str
    target_state: PlanState | None = None


def evaluate_confirmation(
    current: PlanState | str,
    patient: Approval | None,
    contact: Approval | None,
    plan_activity: str,
    plan_starts_at: str,
) -> ConfirmDecision:
    """The single gate that authorizes PLAN_CONFIRMED.

    Requires: both parties accepted, and both accepted the SAME activity and
    start time as the plan of record. Mismatched terms are a hard failure -
    this is what stops "Josh said Sunday, Sam approved Saturday" from confirming.
    """
    cur = PlanState(current)

    if cur is PlanState.HALTED_CONSENT_REVOKED:
        return ConfirmDecision(False, "consent was revoked; workflow halted")

    if patient is None or contact is None:
        return ConfirmDecision(False, "missing approval from one or both parties")

    if not contact.accepted:
        return ConfirmDecision(False, f"contact has not accepted (response={contact.response})")
    if not patient.accepted:
        return ConfirmDecision(False, f"patient has not accepted (response={patient.response})")

    for label, appr in (("contact", contact), ("patient", patient)):
        if appr.activity != plan_activity:
            return ConfirmDecision(
                False,
                f"{label} approved a different activity "
                f"({appr.activity!r} != plan {plan_activity!r})",
            )
        if appr.starts_at != plan_starts_at:
            return ConfirmDecision(
                False,
                f"{label} approved a different time "
                f"({appr.starts_at!r} != plan {plan_starts_at!r})",
            )

    if cur is not PlanState.AVAILABILITY_REVALIDATED:
        return ConfirmDecision(
            False, f"availability must be revalidated first (state={cur})"
        )

    return ConfirmDecision(True, "both parties approved identical terms", PlanState.PLAN_CONFIRMED)


#: Upper bound on participants in one plan (patient + contacts). A group outing
#: that needs coordinating beyond this stops being low-pressure, which is the
#: whole point of the product.
MAX_GROUP_SIZE = 4


def evaluate_group_confirmation(
    current: PlanState | str,
    patient: Approval | None,
    contacts: list[Approval],
    plan_activity: str,
    plan_starts_at: str,
) -> ConfirmDecision:
    """Confirmation gate for a plan with any number of contacts.

    Generalises `evaluate_confirmation`: the patient AND **every** participating
    contact must have accepted the identical activity and start time on the
    plan of record. One silent or mismatched contact blocks the whole plan,
    because confirming an outing that one invitee never agreed to is exactly
    the failure this gate exists to prevent.

    The 1:1 function remains the single source of truth for the two-party case
    and is delegated to, so there is one implementation of the core rule.
    """
    cur = PlanState(current)

    if cur is PlanState.HALTED_CONSENT_REVOKED:
        return ConfirmDecision(False, "consent was revoked; workflow halted")

    if not contacts:
        return ConfirmDecision(False, "plan has no participating contacts")

    if len(contacts) + 1 > MAX_GROUP_SIZE:
        return ConfirmDecision(
            False,
            f"group of {len(contacts) + 1} exceeds MAX_GROUP_SIZE={MAX_GROUP_SIZE}",
        )

    # Two-party plans take the original path verbatim.
    if len(contacts) == 1:
        return evaluate_confirmation(
            current, patient, contacts[0], plan_activity, plan_starts_at
        )

    if patient is None:
        return ConfirmDecision(False, "missing approval from the patient")

    for appr in contacts:
        if not appr.accepted:
            # Identify by position, not by name: a decline must not be
            # broadcast to the other invitees.
            return ConfirmDecision(
                False,
                f"one invitee has not accepted (response={appr.response})",
            )

    if not patient.accepted:
        return ConfirmDecision(
            False, f"patient has not accepted (response={patient.response})"
        )

    for label, appr in [("patient", patient)] + [
        ("an invitee", a) for a in contacts
    ]:
        if appr.activity != plan_activity:
            return ConfirmDecision(
                False,
                f"{label} approved a different activity "
                f"({appr.activity!r} != plan {plan_activity!r})",
            )
        if appr.starts_at != plan_starts_at:
            return ConfirmDecision(
                False,
                f"{label} approved a different time "
                f"({appr.starts_at!r} != plan {plan_starts_at!r})",
            )

    if cur is not PlanState.AVAILABILITY_REVALIDATED:
        return ConfirmDecision(
            False, f"availability must be revalidated first (state={cur})"
        )

    return ConfirmDecision(
        True,
        f"patient and all {len(contacts)} invitees approved identical terms",
        PlanState.PLAN_CONFIRMED,
    )
