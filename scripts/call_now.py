"""Demo trigger: reset the fictional seed and place ONE real call, immediately.

  python scripts/call_now.py              # real PSTN call via Cartesia
  python scripts/call_now.py --simulated  # same flow, local simulator, no call
  python scripts/call_now.py --check      # arm-check only, places no call

This is the button to press during the demo. It goes through the SAME product
path as everything else - planner, consent gate, memory filter, coordination
reducer - so what the judge hears is the real system, not a shortcut. The only
thing this script adds is doing it in one step with a readable summary.

Honesty: it prints exactly what the provider returned. If the call did not
happen, it says so and exits non-zero.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import httpx  # noqa: E402

from app.config import get_settings  # noqa: E402

BASE = "http://localhost:8000"


def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m"


ok = lambda s: _c("32", s)       # noqa: E731
bad = lambda s: _c("31", s)      # noqa: E731
warn = lambda s: _c("33", s)     # noqa: E731
dim = lambda s: _c("90", s)      # noqa: E731


def preflight(simulated: bool) -> list[str]:
    """Everything that must be true before the phone can ring."""
    s = get_settings()
    problems: list[str] = []

    print("ARM CHECK")
    try:
        h = httpx.get(f"{BASE}/health", timeout=5).json()
        print(f"  backend            {ok('up')}  (voice_mode={h.get('voice_mode')})")
    except Exception as e:
        print(f"  backend            {bad('DOWN')}  {e}")
        problems.append(
            "Backend is not running. Start it:\n"
            "      ./.venv/Scripts/python.exe -m uvicorn app.main:app --app-dir backend --port 8000"
        )
        return problems

    status = httpx.get(f"{BASE}/api/status", timeout=10).json()
    for key in ("voice_mode", "coordination_mode", "conversation_mode"):
        val = status.get(key)
        good = val in ("cartesia_pstn", "spacetimedb", "claude")
        print(f"  {key:19}{ok(val) if good else warn(str(val))}")

    if simulated:
        return problems

    if not status.get("real_call_possible"):
        problems.append(
            "Cartesia is not call-ready. Run: python scripts/discover_cartesia.py"
        )
    for p in s.cartesia_id_problems():
        problems.append(p)
    if not s.my_phone_e164:
        problems.append("MY_PHONE_E164 is not set in .env")

    # The tunnel is what lets Cartesia reach our tool webhooks mid-call. The
    # call still connects without it, but the agent cannot read memory or
    # record an answer, so warn loudly rather than failing.
    base = s.public_base_url
    if base.startswith("http://localhost") or base.startswith("http://127."):
        print(f"  webhook url        {warn('LOCAL ONLY')} {base}")
        print(dim("                     the call will connect, but mid-call tools"))
        print(dim("                     will be unreachable. Start cloudflared."))
    else:
        try:
            r = httpx.get(f"{base}/health", timeout=15)
            reachable = r.status_code == 200
        except Exception:
            reachable = False
        print(f"  webhook url        {ok('public') if reachable else warn('UNREACHABLE')} {base}")
        if not reachable:
            print(dim("                     tunnel looks dead; restart cloudflared"))
    return problems


def main() -> int:
    simulated = "--simulated" in sys.argv
    check_only = "--check" in sys.argv

    print("=" * 68)
    print("STILL HERE - CALL NOW" + ("  (SIMULATED)" if simulated else ""))
    print("=" * 68)

    problems = preflight(simulated)
    if problems:
        print("\n" + bad("NOT ARMED") + " - fix these first:")
        for i, p in enumerate(problems, 1):
            print(f"  {i}. {p}")
        print("=" * 68)
        return 2

    if check_only:
        print("\n" + ok("ARMED") + " - run without --check to place the call.")
        print("=" * 68)
        return 0

    print("\nSeeding fictional demo data ...")
    r = httpx.post(f"{BASE}/api/demo/reset", timeout=60).json()
    print(f"  {r.get('memories_seeded')} memories, Josh's number is your phone: "
          f"{r.get('josh_phone_is_real')}")

    print("\nPlanning from stored memory ...")
    body = {"force_mode": "simulated"} if simulated else {}
    t0 = time.monotonic()
    d = httpx.post(f"{BASE}/api/demo/start", json=body, timeout=120).json()
    elapsed = int((time.monotonic() - t0) * 1000)

    if not d.get("ok"):
        print("\n" + bad("BLOCKED") + f" - {d.get('reason')}")
        if d.get("detail"):
            print(f"  detail: {d['detail']}")
        print("=" * 68)
        return 1

    why = d.get("why", {})
    print(f"  chose    : {why.get('contact')} -> {why.get('activity')}")
    print(f"  because  : {(why.get('why_activity') or '')[:90]}")
    print(f"  citing   : {len(why.get('memory_citations') or [])} stored memories")

    print(f"\nCall placed in {elapsed}ms")
    print(f"  mode            : {d.get('mode')}")
    print(f"  status          : {d.get('status')}")
    print(f"  provider_call_id: {d.get('provider_call_id')}")
    if d.get("error"):
        print(bad(f"  error           : {d['error']}"))

    if d.get("mode") == "cartesia_pstn" and d.get("provider_call_id"):
        print("\n" + ok("YOUR PHONE SHOULD BE RINGING.") + " Answer it and talk normally.")
        print(dim("  Try: ask who it is - say a theatre is too much - suggest coffee"))
        print(dim("       Sunday - change your mind - or just say no. It handles all of it."))
    elif d.get("mode") == "simulated":
        print("\n" + warn("SIMULATED") + " - no real call was placed. Drive it with:")
        print(dim(f"  curl -X POST {BASE}/api/voice/simulate/turn \\"))
        print(dim(f"       -H 'Content-Type: application/json' \\"))
        print(dim(f"       -d '{{\"call_id\":\"{d.get('call_id')}\",\"text\":\"yes\"}}'"))
    else:
        print("\n" + bad("NO CALL WAS PLACED.") + " Do not claim one happened.")

    print("\n" + dim("plan_id: " + str(d.get("plan_id"))))
    print(dim("dashboard: http://localhost:5173/dashboard"))
    print(dim("after the call, approve as Sam:"))
    print(dim(f"  curl -X POST {BASE}/api/demo/sam-response -H 'Content-Type: application/json' \\"))
    print(dim(f"       -d '{{\"plan_id\":\"{d.get('plan_id')}\",\"response\":\"accepted\"}}'"))
    print("=" * 68)
    return 0 if d.get("provider_call_id") or simulated else 1


if __name__ == "__main__":
    raise SystemExit(main())
