"""Rehearse the phone call in your terminal. No Cartesia, no real call, no cost.

  python scripts/sim_call.py

You type what Josh says; the agent answers. It runs the SAME code the live call
runs - `agents/conversation.interpret_turn()` for interpretation and
`services/voice.simulate_turn()` for the consequences - so an answer here is the
answer you would get on the phone. The only difference is the transport: text
instead of Cartesia's voice pipeline.

Every turn shows the detected intent, the extracted slots and the resulting plan
state, which the phone cannot show you. That makes this better than the real
call for debugging, and the real call better for demoing.

Commands:
  :state     the current plan, version and each party's response
  :memories  what the agent may and may not say to Josh
  :approve   approve as Sam (clearly simulated) and try to confirm
  :events    the last few real backend events
  :reset     reseed and start a fresh call
  :quit
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import httpx  # noqa: E402

BASE = "http://localhost:8000"


def _c(code: str, s: str) -> str:
    return f"\033[{code}m{s}\033[0m"


agent = lambda s: _c("36", s)   # noqa: E731
you = lambda s: _c("37", s)     # noqa: E731
meta = lambda s: _c("90", s)    # noqa: E731
good = lambda s: _c("32", s)    # noqa: E731
warn = lambda s: _c("33", s)    # noqa: E731
bad = lambda s: _c("31", s)     # noqa: E731

CLINICAL = ("hospital", "psychiatric", "depress", "diagnos", "discharge",
            "medication", "therapist", "inpatient")


def start() -> tuple[str, str, str]:
    """Reseed and open a simulated call. Returns (call_id, plan_id, opening)."""
    httpx.post(f"{BASE}/api/demo/reset", timeout=60)
    d = httpx.post(f"{BASE}/api/demo/start", json={"force_mode": "simulated"},
                   timeout=120).json()
    if not d.get("ok"):
        raise SystemExit(bad(f"could not start: {d.get('reason')}"))
    w = d.get("why", {})
    print(meta(f"  planner chose {w.get('contact')} -> {w.get('activity')}"))
    print(meta(f"  citing {len(w.get('memory_citations') or [])} stored memories"))
    return d["call_id"], d["plan_id"], d.get("opening") or ""


def show_state(plan_id: str) -> None:
    p = httpx.get(f"{BASE}/api/plans/{plan_id}", timeout=20).json()
    print(meta(f"  {p['activity']} @ {p['starts_at']}"))
    print(meta(f"  state v{p['version']}  {p['status']}"))
    for part in p["participants"]:
        tag = " (simulated)" if part.get("simulated") else ""
        print(meta(f"    {part['role']:8} {part['response'] or '-'}{tag}"))


def show_memories() -> None:
    owner = httpx.get(f"{BASE}/api/memories",
                      params={"user_id": "user_sam", "audience": "owner"},
                      timeout=20).json()
    contact = httpx.get(f"{BASE}/api/memories",
                        params={"user_id": "user_sam", "audience": "contact"},
                        timeout=20).json()
    shareable = {m["id"] for m in contact}
    print(meta(f"  {len(contact)} of {len(owner)} memories may be mentioned to Josh"))
    for m in owner:
        mark = good("  may say ") if m["id"] in shareable else bad("  WITHHELD")
        print(f"  {mark} [{m['visibility']}] {m['value'][:66]}")


def show_events(token: str) -> None:
    evs = httpx.get(f"{BASE}/api/events/recent",
                    params={"limit": 12, "dashboard_token": token},
                    timeout=20).json()
    for e in evs[-8:]:
        flag = warn(" SIM") if e.get("simulated") else ""
        print(meta(f"  {e['event_type']:28}{flag} {e['safe_summary'][:60]}"))


def approve(plan_id: str) -> None:
    r = httpx.post(f"{BASE}/api/demo/sam-response",
                   json={"plan_id": plan_id, "response": "accepted"},
                   timeout=60)
    if r.status_code >= 400:
        print(bad(f"  rejected: {r.json().get('error', {}).get('message')}"))
        return
    d = r.json()
    if d.get("confirmed"):
        print(good("  CONFIRMED") + meta(" - both parties agreed identical terms"))
        print(meta("    verify: spacetime sql --server local stillhere"
                   ' "SELECT * FROM plan_confirmations"'))
    else:
        print(warn("  not confirmed") + meta(f" - {d.get('reason') or d.get('state')}"))


def main() -> int:
    from app.config import get_settings

    token = get_settings().dashboard_token

    try:
        httpx.get(f"{BASE}/health", timeout=5)
    except Exception:
        print(bad("Backend is not running.") + " Start it with:")
        print("  ./.venv/Scripts/python.exe -m uvicorn app.main:app --app-dir backend --port 8000")
        return 2

    print("=" * 70)
    print("STILL HERE - call rehearsal (no real call, no provider cost)")
    print("=" * 70)
    call_id, plan_id, opening = start()

    print()
    print(agent("AGENT: ") + (opening or "(no opening)"))
    print(meta("\n  type what Josh says, or :help"))

    while True:
        try:
            text = input(you("\nJOSH: ")).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not text:
            continue

        if text in (":quit", ":q"):
            return 0
        if text == ":help":
            print(meta(__doc__.split("Commands:")[1]))
            continue
        if text == ":state":
            show_state(plan_id)
            continue
        if text == ":memories":
            show_memories()
            continue
        if text == ":events":
            show_events(token)
            continue
        if text == ":approve":
            approve(plan_id)
            continue
        if text == ":reset":
            call_id, plan_id, opening = start()
            print(agent("\nAGENT: ") + opening)
            continue
        if text.startswith(":"):
            print(meta(f"  unknown command {text}; try :help"))
            continue

        r = httpx.post(f"{BASE}/api/voice/simulate/turn",
                       json={"call_id": call_id, "text": text}, timeout=180)
        if r.status_code >= 400:
            print(bad(f"  error: {r.text[:200]}"))
            continue
        d = r.json()

        print(agent("AGENT: ") + d["reply"])

        bits = [f"intent={d.get('intent')}", f"mode={d.get('mode')}",
                f"state={d.get('state')}"]
        if d.get("slots"):
            keep = {k: v for k, v in d["slots"].items() if v}
            if keep:
                bits.append(f"slots={keep}")
        if d.get("tool_calls"):
            bits.append(f"tools={d['tool_calls']}")
        print(meta("  " + "  ".join(bits)))

        # The safety property that matters most, checked on every single turn.
        leaked = [t for t in CLINICAL if t in d["reply"].lower()]
        if leaked:
            print(bad(f"  !! CLINICAL LEAK: {leaked} - this is a bug, please report it"))

        if d.get("state") == "PATIENT_APPROVAL_PENDING":
            print(meta("  -> Josh is in. Type :approve to approve as Sam."))


if __name__ == "__main__":
    raise SystemExit(main())
