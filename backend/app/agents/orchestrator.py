"""Connection planner.

Chooses WHO to reach out to and WHAT to propose by scoring stored memories.
Nothing about Josh or Marvel is hardcoded here: both fall out of the memory
rows and the consent table. Deleting the Marvel memory changes the proposal,
and adding a "no crowded theaters" boundary suppresses the theater option.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from ..db import new_id, q, q1
from ..domain.state_machine import PlanState
from ..services import consent as consent_svc
from ..services import events, memory, opportunity, plans, scheduling


@dataclass
class ActivityOption:
    label: str
    location: str | None
    score: float
    citations: list[str] = field(default_factory=list)
    rationale: str = ""


# Activity catalogue. Each entry is matched against the user's own memory text,
# so the catalogue proposes nothing the user never mentioned.
ACTIVITY_RULES: list[dict[str, Any]] = [
    {
        "label": "a Marvel movie",
        "keywords": ["marvel", "superhero", "avengers", "movie", "movies", "cinema"],
        "location": "AMC Metreon 16",
        "crowded": True,
    },
    {
        "label": "Coffee",
        "keywords": ["coffee", "cafe", "espresso", "latte", "tea"],
        "location": "Four Barrel Coffee",
        "crowded": False,
    },
    {
        "label": "a walk",
        "keywords": ["walk", "walking", "hike", "hiking", "park", "outside"],
        "location": "Golden Gate Park",
        "crowded": False,
    },
    {
        "label": "dinner",
        "keywords": ["dinner", "food", "restaurant", "ramen", "tacos", "eat"],
        "location": "Mission district",
        "crowded": True,
    },
]

_AVOID_CROWDS = re.compile(
    r"\b(crowded|crowds|busy place|too many people|packed)\b", re.IGNORECASE
)
_NEGATION = re.compile(r"\b(don'?t|do not|hate|avoid|rather not|no longer|not into)\b", re.I)


def eligible_contacts(patient_id: str) -> list[dict[str, Any]]:
    """Contacts who have affirmatively opted in AND currently pass the consent gate."""
    rows = q(
        "SELECT r.contact_id, r.relationship_label, u.display_name, u.phone_verified"
        " FROM relationships r JOIN users u ON u.id = r.contact_id"
        " WHERE r.patient_id=? AND r.status='accepted'",
        (patient_id,),
    )
    out = []
    for r in rows:
        decision = consent_svc.verify_outreach(
            patient_id, r["contact_id"], "voice_call", override_quiet_hours=True
        )
        out.append(
            {
                "contact_id": r["contact_id"],
                "display_name": r["display_name"],
                "relationship_label": r["relationship_label"],
                "consent_allowed": decision.allowed,
                "consent_reason": decision.reason,
            }
        )
    return out


def score_contact(patient_id: str, contact_id: str) -> tuple[float, list[str], str]:
    """Score a relationship by memory evidence. Returns (score, citations, why)."""
    mems = memory.retrieve(patient_id, audience="planning", limit=50)
    contact = q1("SELECT * FROM users WHERE id=?", (contact_id,))
    if contact is None:
        return 0.0, [], "unknown contact"
    first = contact["display_name"].split()[0].lower()

    score = 0.0
    cites: list[str] = []
    reasons: list[str] = []

    for m in mems:
        text = m.value.lower()
        mentions = m.subject_id == contact_id or first in text
        if not mentions:
            continue
        weight = {"relationship": 2.0, "preference": 1.5, "fact": 1.0, "boundary": 0.5}[m.kind]
        if any(w in text for w in ("miss", "best friend", "closest")):
            weight += 1.5
        score += weight * (0.5 + m.confidence / 2)
        cites.append(m.id)
        reasons.append(m.value)

    why = "; ".join(reasons[:3]) or "no supporting memories found"
    return score, cites, why


def choose_activity(patient_id: str, contact_id: str) -> ActivityOption | None:
    """Pick an activity from memory evidence, honouring stated boundaries."""
    mems = memory.retrieve(patient_id, audience="planning", limit=60)

    # Boundaries first - these can veto an option outright.
    avoid_crowds = False
    boundary_cites: list[str] = []
    for m in mems:
        if m.kind == "boundary" and _AVOID_CROWDS.search(m.value):
            avoid_crowds = True
            boundary_cites.append(m.id)

    best: ActivityOption | None = None
    for rule in ACTIVITY_RULES:
        score = 0.0
        cites: list[str] = []
        reasons: list[str] = []
        for m in mems:
            text = m.value.lower()
            if not any(k in text for k in rule["keywords"]):
                continue
            # A negated mention is evidence AGAINST, not for.
            if _NEGATION.search(text) or m.kind == "boundary":
                score -= 2.0
                continue
            weight = 2.0 if m.kind == "preference" else 1.0
            if contact_id and m.subject_id == contact_id:
                weight += 1.0
            score += weight * (0.5 + m.confidence / 2)
            cites.append(m.id)
            reasons.append(m.value)

        if score <= 0:
            continue
        if avoid_crowds and rule["crowded"]:
            # Respect the stated boundary rather than silently overriding it.
            cites = cites + boundary_cites
            reasons.append("suppressed: user said they avoid crowded places")
            continue

        opt = ActivityOption(
            label=rule["label"],
            location=rule["location"],
            score=score,
            citations=cites,
            rationale="; ".join(reasons[:3]),
        )
        if best is None or opt.score > best.score:
            best = opt

    return best


def next_slot(
    patient_id: str, contact_id: str, *, prefer_evening: bool = True
) -> str | None:
    """Pick a shared free slot.

    Prefers an evening start for outings like a movie, because an 11am
    "Marvel movie" reads wrong and a weekend evening is what people actually
    do. Falls back to the earliest shared window.
    """
    slots = scheduling.find_free_slots([patient_id, contact_id], min_minutes=90)
    if not slots:
        return None

    if prefer_evening:
        for s in slots:
            if datetime.fromisoformat(s.start_at).hour >= 17:
                return datetime.fromisoformat(s.start_at).isoformat()

    return datetime.fromisoformat(slots[0].start_at).isoformat()


def identify_opportunity(
    patient_id: str, *, session_id: str | None = None
) -> dict[str, Any]:
    """The full planning pass. Returns a candidate plan or an explained refusal."""
    sid = session_id or new_id("sess")

    patient = q1("SELECT * FROM users WHERE id=?", (patient_id,))
    if patient is None:
        return {"ok": False, "reason": "unknown patient"}
    if not patient["outreach_active"]:
        events.emit(
            "consent.denied",
            "Planning stopped: patient has not activated outreach",
            session_id=sid,
            payload={"patient_id": patient_id},
        )
        return {"ok": False, "reason": "patient has not activated outreach"}

    contacts = eligible_contacts(patient_id)
    allowed = [c for c in contacts if c["consent_allowed"]]
    if not allowed:
        reasons = {c["display_name"]: c["consent_reason"] for c in contacts}
        events.emit(
            "consent.denied",
            "Planning stopped: no contact currently passes the consent gate",
            session_id=sid,
            payload={"evaluated": reasons},
        )
        return {"ok": False, "reason": "no consented contact available", "detail": reasons}

    # --- who ---
    # Prefer the longitudinal detector: it scores against each relationship's
    # OWN history (median gap, deviation ratio) and enforces the cooling-off
    # window after a decline or an unanswered call. The memory-overlap scoring
    # below is the fallback for a patient with too little history to have a
    # baseline yet.
    detected = opportunity.best(patient_id)
    if detected is not None:
        events.emit(
            "memory.retrieved",
            f"Opportunity detected: {detected.display_name} - {detected.reason}",
            session_id=sid,
            payload={
                "source": "longitudinal_baseline",
                "contact_id": detected.contact_id,
                "score": round(detected.score, 2),
                "reason": detected.reason,
                "median_gap_days": getattr(detected.baseline, "median_gap_days", None),
                "current_gap_days": getattr(detected.baseline, "current_gap_days", None),
                "deviation_ratio": getattr(detected.baseline, "deviation_ratio", None),
                "confidence": getattr(detected.baseline, "confidence", None),
            },
        )

    scored = []
    for c in allowed:
        s, cites, why = score_contact(patient_id, c["contact_id"])
        scored.append({**c, "score": s, "citations": cites, "why": why})
    scored.sort(key=lambda x: x["score"], reverse=True)

    if detected is not None:
        match = next(
            (c for c in scored if c["contact_id"] == detected.contact_id), None
        )
        if match is not None:
            # The detector chose; its reason replaces the overlap heuristic's,
            # because it cites real numbers from this relationship's history.
            match = {**match, "why": detected.reason,
                     "score": max(match["score"], detected.score)}
            scored = [match] + [c for c in scored if c is not match
                                and c["contact_id"] != detected.contact_id]
    chosen = scored[0]

    if chosen["score"] <= 0:
        events.emit(
            "error",
            "Planning stopped: no memory evidence supports reaching out to anyone",
            session_id=sid,
            payload={"candidates": [{"name": c["display_name"], "score": c["score"]} for c in scored]},
        )
        return {"ok": False, "reason": "no memory evidence to justify an outreach"}

    events.emit(
        "memory.retrieved",
        f"Selected {chosen['display_name']} (score {chosen['score']:.2f}) from stored memory",
        session_id=sid,
        payload={
            "why_this_person": chosen["why"],
            "memory_citations": chosen["citations"],
            "all_candidates": [
                {"name": c["display_name"], "score": round(c["score"], 2), "why": c["why"]}
                for c in scored
            ],
        },
    )

    # --- what ---
    activity = choose_activity(patient_id, chosen["contact_id"])
    if activity is None:
        events.emit(
            "error",
            "Planning stopped: no activity is supported by stored preferences",
            session_id=sid,
            payload={"contact": chosen["display_name"]},
        )
        return {"ok": False, "reason": "no activity supported by stored memories"}

    # --- when ---
    starts_at = next_slot(patient_id, chosen["contact_id"])
    if starts_at is None:
        events.emit(
            "error",
            "Planning stopped: no overlapping free time",
            session_id=sid,
            payload={"contact": chosen["display_name"]},
        )
        return {"ok": False, "reason": "no overlapping availability"}

    plan = plans.create_plan(
        patient_id,
        chosen["contact_id"],
        activity.label,
        starts_at,
        location=activity.location,
        rationale=(
            f"Why {chosen['display_name']}: {chosen['why']} | "
            f"Why {activity.label}: {activity.rationale}"
        ),
        why_contact=f"Chose {chosen['display_name']} because: {chosen['why']}",
        why_activity=f"Chose {activity.label} because: {activity.rationale}",
        memory_citations=sorted(set(chosen["citations"] + activity.citations)),
        session_id=sid,
    )

    # Walk the deterministic opening transitions.
    plans.set_state(plan["id"], PlanState.CONSENT_VERIFIED,
                    reason="contact opted in and passes all gates", session_id=sid)
    plans.set_state(plan["id"], PlanState.MEMORY_RETRIEVED,
                    reason=f"{len(plan['memory_citations'])} memories cited", session_id=sid)
    plans.set_state(plan["id"], PlanState.CANDIDATE_PLAN_CREATED,
                    reason="candidate ready for outreach", session_id=sid)

    return {
        "ok": True,
        "session_id": sid,
        "plan": plans.get_plan(plan["id"]),
        "contact": chosen,
        "activity": {
            "label": activity.label,
            "score": activity.score,
            "rationale": activity.rationale,
            "citations": activity.citations,
        },
    }
