"""Watch three weeks pass in thirty seconds.

  python scripts/timewarp.py                 # day-by-day for 21 days, dry run
  python scripts/timewarp.py --days 42       # six weeks
  python scripts/timewarp.py --live          # let it place a REAL call when it decides
  python scripts/timewarp.py --dormant       # keep the already-quiet seed
  python scripts/timewarp.py --reset         # back to the real present

Still Here's central claim is longitudinal: it notices that a friendship has
gone quiet *relative to its own history*, and reaches out on its own. You
cannot evaluate that by waiting three weeks.

So this advances the simulated clock one day at a time and, at each step, asks
the real detector what it sees and the real scheduler what it would do. Same
code, same data, same consent gates - a different instant.

Nothing is faked and nothing is fast-forwarded past. If the scheduler decides
to act on day 9, you see it decide on day 9, with the numbers that convinced it.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import httpx  # noqa: E402

from app.config import get_settings  # noqa: E402

BASE = "http://localhost:8000"


def _c(code: str, s: str) -> str:
    return f"\033[{code}m{s}\033[0m"


ok = lambda s: _c("32", s)    # noqa: E731
warn = lambda s: _c("33", s)  # noqa: E731
bad = lambda s: _c("31", s)   # noqa: E731
dim = lambda s: _c("90", s)   # noqa: E731
hi = lambda s: _c("36", s)    # noqa: E731


def main() -> int:
    args = sys.argv[1:]
    live = "--live" in args
    reset_only = "--reset" in args
    # --dormant keeps the stock 47-day-quiet seed (fires immediately).
    dormant = "--dormant" in args
    days = 21
    if "--days" in args:
        try:
            days = int(args[args.index("--days") + 1])
        except (IndexError, ValueError):
            print(bad("--days needs a number"))
            return 2

    token = get_settings().dashboard_token
    H = {"X-Dashboard-Token": token}

    try:
        httpx.get(f"{BASE}/health", timeout=5)
    except Exception:
        print(bad("Backend is not running.") + " Start it with: python scripts/demo_up.py")
        return 2

    if reset_only:
        s = httpx.post(f"{BASE}/api/sim/clock/reset", headers=H, timeout=20).json()
        print(ok("Clock reset to real time.") + dim(f"  now={s['simulated_now'][:19]}"))
        return 0

    print("=" * 76)
    print(f"TIMEWARP - simulating {days} days"
          + ("" if live else dim("  (dry run: it decides, but places no call)")))
    print("=" * 76)

    # Fresh scenario, real present, real seeded history.
    r = httpx.post(f"{BASE}/api/demo/reset", timeout=90).json()
    httpx.post(f"{BASE}/api/sim/clock/reset", headers=H, timeout=20)
    print(dim(f"  seeded {r.get('memories_seeded')} memories, "
              f"{r.get('contact_events_seeded', '?')} contact events"))

    if not dormant:
        # Start from a HEALTHY friendship, so the drift is something you watch
        # happen. The default seed is already 47 days quiet, which makes the
        # detector fire on day 0 and shows the conclusion instead of the
        # reasoning.
        b = httpx.post(f"{BASE}/api/sim/seed-recent", headers=H, json={}, timeout=60).json()
        print(dim(f"  reseeded Josh as in-touch: last contact "
                  f"{b.get('current_gap_days')}d ago, median "
                  f"{b.get('median_gap_days')}d"))

    patient = "user_sam"
    acted_day: int | None = None

    print()
    print(dim("  day   contact        gap/median   ratio  state"))
    print(dim("  " + "-" * 68))

    for day in range(0, days + 1):
        if day > 0:
            httpx.post(f"{BASE}/api/sim/clock/advance", headers=H,
                       json={"days": 1, "reason": f"timewarp day {day}"}, timeout=20)

        opp = httpx.get(f"{BASE}/api/sim/opportunities", headers=H,
                        params={"patient_id": patient}, timeout=60).json()

        rows = opp.get("opportunities") or []
        if not rows:
            print(f"  {day:>3}   {dim('(no contacts with history yet)')}")
            continue

        # Show the most interesting contact each day.
        rows.sort(key=lambda o: (o["suppressed"], -(o["score"] or 0)))
        top = rows[0]
        name = (top.get("display_name") or top["contact_id"])[:14]
        cur = top.get("current_gap_days")
        med = top.get("median_gap_days")
        ratio = top.get("deviation_ratio")
        gap = f"{cur:.0f}/{med:.0f}d" if cur is not None and med is not None else "-"
        rr = f"{ratio:.2f}x" if ratio is not None else "-"

        if top["suppressed"]:
            why = (top.get("suppressed_reason") or
                   top.get("below_floor_reason") or "holding back")
            state = warn("held back") + dim(f"  {why[:34]}")
        elif top["score"] and top["score"] > 0:
            state = hi(f"score {top['score']:.2f}")
        else:
            state = dim("quiet")

        print(f"  {day:>3}   {name:<14} {gap:>10} {rr:>7}  {state}")

        # Ask the real scheduler, not a reimplementation of it.
        tick = httpx.post(f"{BASE}/api/sim/tick", headers=H,
                          json={"dry_run": not live,
                                "force_mode": None if live else "simulated"},
                          timeout=180).json()

        if tick.get("acted_on"):
            a = tick["acted_on"][0]
            acted_day = day
            print()
            print("  " + ok("=" * 68))
            print("  " + ok(f"DAY {day}: Still Here decided to reach out, on its own."))
            print(f"    to     : {a.get('display_name') or a.get('contact_id')}")
            print(f"    because: {(a.get('reason') or '')[:100]}")
            if live and a.get("provider_call_id"):
                print("    " + ok(f"REAL CALL PLACED: {a['provider_call_id']}"))
                print("    your phone should be ringing")
            elif live:
                print("    " + warn("no provider call id - check /api/status"))
            else:
                print("    " + dim("dry run, so no call was placed; rerun with --live"))
            print("  " + ok("=" * 68))
            break

    print()
    state = httpx.get(f"{BASE}/api/sim/clock", headers=H, timeout=20).json()
    offset = state.get("offset_days", 0.0)
    print(f"  simulated clock: {warn(f'{offset:+.1f} days')} from real time")
    print(dim(f"  simulated now  : {state['simulated_now'][:19]}"))

    if acted_day is None:
        print()
        print(warn(f"  No outreach in {days} days."))
        print(dim("  That is a real outcome, not a bug: it means nothing had drifted"))
        print(dim("  far enough, or a cooling-off window was still open. Try --days 42,"))
        print(dim("  or check /api/sim/opportunities for the suppression reasons."))

    print()
    print(dim("  reset with: python scripts/timewarp.py --reset"))
    print("=" * 76)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
