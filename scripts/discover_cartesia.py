"""Cartesia preflight. Run this FIRST, before any call attempt.

  python scripts/discover_cartesia.py

Verifies the API key, lists agents and phone numbers on the account, and prints
the exact .env lines to paste. Writes evidence/cartesia_discovery.json.

Prints nothing secret: keys are shown only as a short fingerprint.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.adapters import cartesia_adapter as ca  # noqa: E402
from app.config import get_settings  # noqa: E402


def fp(key: str) -> str:
    return f"{key[:7]}…{key[-4:]} (len={len(key)})" if len(key) > 12 else "set"


def main() -> int:
    s = get_settings()
    report: dict = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "cartesia_version": s.cartesia_version,
        "sdk": None,
        "api_key_present": bool(s.cartesia_api_key),
        "steps": {},
    }

    try:
        import cartesia
        report["sdk"] = f"cartesia-python {cartesia.__version__}"
    except Exception as e:  # pragma: no cover
        report["sdk"] = f"import failed: {e}"

    print("=" * 68)
    print("CARTESIA PREFLIGHT")
    print("=" * 68)
    print(f"SDK                : {report['sdk']}")
    print(f"cartesia-version   : {s.cartesia_version}")

    if not s.cartesia_api_key:
        print("\nFAIL: CARTESIA_API_KEY is not set.")
        print("  1. Create a key at https://play.cartesia.ai/keys")
        print("  2. Put it in .env as CARTESIA_API_KEY=...")
        report["steps"]["api_key"] = "missing"
        (ROOT / "evidence").mkdir(exist_ok=True)
        (ROOT / "evidence" / "cartesia_discovery.json").write_text(
            json.dumps(report, indent=2)
        )
        return 2

    print(f"API key            : {fp(s.cartesia_api_key)}")

    # --- Step 1: list agents (also validates the key) ---
    print("\n[1/2] GET /agents ...")
    agents: list = []
    try:
        data = ca.list_agents()
        agents = data if isinstance(data, list) else (data.get("data") or data.get("agents") or [])
        report["steps"]["list_agents"] = {"ok": True, "count": len(agents)}
        print(f"      OK - {len(agents)} agent(s) on this account")
        for a in agents:
            if isinstance(a, dict):
                print(f"        - id={a.get('id')!r} name={a.get('name')!r}")
    except ca.CartesiaCallError as e:
        report["steps"]["list_agents"] = {
            "ok": False, "status": e.status, "body": str(e.body)[:400]
        }
        print(f"      FAIL - {e}")
        if e.status in (401, 403):
            print("      -> Key rejected. Check it is a live key from play.cartesia.ai/keys")
    except Exception as e:
        report["steps"]["list_agents"] = {"ok": False, "error": str(e)[:400]}
        print(f"      FAIL - {e}")

    # --- Step 2: list phone numbers ---
    print("\n[2/2] GET phone numbers ...")
    numbers: list = []
    try:
        data = ca.list_phone_numbers()
        numbers = data if isinstance(data, list) else (
            data.get("data") or data.get("phone_numbers") or []
        )
        report["steps"]["list_phone_numbers"] = {"ok": True, "count": len(numbers)}
        print(f"      OK - {len(numbers)} number(s) on this account")
        for n in numbers:
            if isinstance(n, dict):
                # Print the id (needed for from_number_id) and a masked number.
                raw = str(n.get("number") or n.get("phone_number") or "")
                masked = f"***{raw[-4:]}" if len(raw) >= 4 else "***"
                print(f"        - id={n.get('id')!r} number={masked} keys={sorted(n.keys())}")
        report["phone_number_shapes"] = [
            sorted(n.keys()) for n in numbers if isinstance(n, dict)
        ]
    except Exception as e:
        report["steps"]["list_phone_numbers"] = {"ok": False, "error": str(e)[:400]}
        print(f"      FAIL - {e}")
        print("      -> You likely have no provisioned number yet.")
        print("         Provision one in the Playground, or import a Twilio number.")

    # --- Verdict ---
    print("\n" + "=" * 68)
    agent_id = s.cartesia_agent_id or (
        agents[0].get("id") if agents and isinstance(agents[0], dict) else ""
    )
    number_id = s.cartesia_from_number_id or (
        numbers[0].get("id") if numbers and isinstance(numbers[0], dict) else ""
    )

    if agent_id and number_id:
        print("READY to attempt a REAL outbound call. Put these in .env:")
        print(f"  CARTESIA_AGENT_ID={agent_id}")
        print(f"  CARTESIA_FROM_NUMBER_ID={number_id}")
        print(f"  MY_PHONE_E164={s.my_phone_e164 or '+1XXXXXXXXXX'}")
        print("\nThen run:  python scripts/smoke_call.py")
        report["verdict"] = "ready"
    else:
        print("NOT READY for a real call. Missing:")
        if not agent_id:
            print("  - a Managed Agent. Create one at play.cartesia.ai -> Agents")
        if not number_id:
            print("  - a phone number. Provision in Playground or import from Twilio")
        report["verdict"] = "blocked"
    print("=" * 68)

    report["resolved"] = {"agent_id": agent_id, "from_number_id": number_id}
    (ROOT / "evidence").mkdir(exist_ok=True)
    out = ROOT / "evidence" / "cartesia_discovery.json"
    out.write_text(json.dumps(report, indent=2))
    print(f"\nEvidence written to {out.relative_to(ROOT)}")
    return 0 if report["verdict"] == "ready" else 1


if __name__ == "__main__":
    raise SystemExit(main())
