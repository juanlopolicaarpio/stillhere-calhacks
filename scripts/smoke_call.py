"""REAL outbound phone call smoke test.

  python scripts/smoke_call.py            # calls MY_PHONE_E164
  python scripts/smoke_call.py +14155551234

This places an actual PSTN call through Cartesia Managed Agents and then polls
the call record for real status transitions. It writes evidence/real_call_<id>.json
containing the provider call id and observed timestamps.

Honesty rule: this script reports exactly what the provider returned. If it
prints FAILED, no call happened and nothing in the repo may claim otherwise.
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.adapters import cartesia_adapter as ca  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.services.consent import validate_e164  # noqa: E402


def _blocked(checklist: list[str]) -> int:
    """Print ONE unambiguous BLOCKED verdict with a precise checklist and
    exit non-zero. Never prints secret material."""
    print("\n" + "=" * 68)
    print("VERDICT: BLOCKED - no call was attempted.")
    print("\nTo unblock, complete every item below:")
    for i, item in enumerate(checklist, 1):
        print(f"  {i}. {item}")
    print("=" * 68)
    return 2


def main() -> int:
    s = get_settings()
    target = sys.argv[1] if len(sys.argv) > 1 else s.my_phone_e164

    print("=" * 68)
    print("REAL OUTBOUND CALL SMOKE TEST")
    print("=" * 68)

    if not target:
        return _blocked([
            "No target number. Set MY_PHONE_E164=+1XXXXXXXXXX in .env, or pass "
            "one as an argument: python scripts/smoke_call.py +1XXXXXXXXXX",
        ])
    try:
        target = validate_e164(target)
    except Exception as e:
        return _blocked([f"The target number is not valid E.164: {e}"])

    if not s.cartesia_call_ready:
        checklist = []
        if not s.cartesia_api_key:
            checklist.append(
                "Create a key at https://play.cartesia.ai/keys and set CARTESIA_API_KEY in .env."
            )
        if not s.cartesia_agent_id:
            checklist.append(
                "Create a Managed Agent at play.cartesia.ai -> Agents and set "
                "CARTESIA_AGENT_ID in .env (run python scripts/setup_cartesia_agent.py "
                "for the exact config to paste)."
            )
        if not s.cartesia_from_number_id:
            checklist.append(
                "Provision or import a phone number in the Playground and set "
                "CARTESIA_FROM_NUMBER_ID in .env."
            )
        checklist.append("Then re-verify with: python scripts/discover_cartesia.py")
        return _blocked(checklist)

    masked = f"***{target[-4:]}"
    print(f"target             : {masked}")
    print(f"agent_id           : {s.cartesia_agent_id}")
    print(f"from_number_id     : {s.cartesia_from_number_id}")
    print(f"cartesia-version   : {s.cartesia_version}")
    print("\nPlacing call ... (your phone should ring)")

    requested_at = datetime.now(timezone.utc).isoformat()
    t0 = time.monotonic()

    # Minimal, non-sensitive context. Nothing clinical is ever sent.
    result = ca.place_outbound_call(
        target,
        dynamic_variables={
            "contact_first_name": "Josh",
            "purpose": "smoke_test",
            "is_test": "true",
        },
        ringing_timeout_seconds=45,
    )
    elapsed_ms = int((time.monotonic() - t0) * 1000)

    evidence = {
        "test": "real_outbound_call_smoke_test",
        "requested_at": requested_at,
        "request_latency_ms": elapsed_ms,
        "target_redacted": masked,
        "agent_id": s.cartesia_agent_id,
        "from_number_id": s.cartesia_from_number_id,
        "cartesia_version": s.cartesia_version,
        "ok": result.ok,
        "provider_call_id": result.provider_call_id,
        "error": result.error,
        "http_status": result.http_status,
        "provider_response": result.raw,
        "status_polls": [],
    }

    (ROOT / "evidence").mkdir(exist_ok=True)

    if not result.ok:
        print("\n" + "=" * 68)
        print(f"VERDICT: BLOCKED - provider rejected the call after {elapsed_ms}ms.")
        print(f"  error: {result.error}")
        print("\nNO CALL WAS PLACED. Do not claim a live call in any doc or demo.")
        print("=" * 68)
        out = ROOT / "evidence" / "real_call_FAILED.json"
        out.write_text(json.dumps(evidence, indent=2))
        print(f"Evidence: {out.relative_to(ROOT)}")
        return 1

    print(f"\nACCEPTED by provider in {elapsed_ms}ms")
    print(f"  provider_call_id: {result.provider_call_id}")
    print("\nPolling call status for 60s (answer the phone!) ...")

    terminal = {"completed", "failed", "no_answer", "busy", "canceled", "ended"}
    for i in range(20):
        time.sleep(3)
        try:
            info = ca.get_call(result.provider_call_id)
        except Exception as e:
            print(f"  [{i:02d}] status poll error: {e}")
            evidence["status_polls"].append({"at": datetime.now(timezone.utc).isoformat(),
                                             "error": str(e)[:300]})
            continue
        status = info.get("status") or info.get("state") or "unknown"
        evidence["status_polls"].append({
            "at": datetime.now(timezone.utc).isoformat(),
            "status": status,
            "duration": info.get("duration"),
        })
        print(f"  [{i:02d}] status={status}")
        if str(status).lower() in terminal:
            evidence["final_status"] = status
            evidence["final_record"] = info
            break

    out = ROOT / "evidence" / f"real_call_{result.provider_call_id}.json"
    out.write_text(json.dumps(evidence, indent=2))

    print("\n" + "=" * 68)
    print("VERDICT: READY/PLACED - provider accepted and issued a real call id.")
    print(f"  call id : {result.provider_call_id}")
    print(f"  status  : {evidence.get('final_status', 'see polls')}")
    print(f"  evidence: {out.relative_to(ROOT)}")
    print("\nIf your phone actually rang and you spoke with the agent, the real")
    print("PSTN path is VERIFIED. Record that in docs/PROVIDER_STATUS.md.")
    print("=" * 68)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
