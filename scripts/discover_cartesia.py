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


def _verdict(report: dict, *, ready: bool, checklist: list[str], notes: list[str] | None = None) -> int:
    """Print ONE unambiguous READY/BLOCKED line, write evidence, return the
    exit code. BLOCKED always exits non-zero; never prints secret material
    (only `fp()` fingerprints appear anywhere in this script's output)."""
    report["verdict"] = "ready" if ready else "blocked"
    report["checklist"] = checklist
    report["notes"] = notes or []

    print("\n" + "=" * 68)
    if ready:
        print("VERDICT: READY - a real outbound call can be attempted.")
        resolved = report.get("resolved", {})
        print(f"  CARTESIA_AGENT_ID={resolved.get('agent_id', '')}")
        print(f"  CARTESIA_FROM_NUMBER_ID={resolved.get('from_number_id', '')}")
        for note in notes or []:
            print(f"  note: {note}")
        print("\nNext: python scripts/smoke_call.py")
    else:
        print("VERDICT: BLOCKED - a real outbound call cannot be attempted yet.")
        print("\nTo unblock, complete every item below:")
        for i, item in enumerate(checklist, 1):
            print(f"  {i}. {item}")
        for note in notes or []:
            print(f"  (optional) {note}")
    print("=" * 68)

    (ROOT / "evidence").mkdir(exist_ok=True)
    out = ROOT / "evidence" / "cartesia_discovery.json"
    out.write_text(json.dumps(report, indent=2))
    print(f"\nEvidence written to {out.relative_to(ROOT)}")
    return 0 if ready else 1


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

    checklist: list[str] = []

    if not s.cartesia_api_key:
        checklist.append("Create a key at https://play.cartesia.ai/keys and put it in .env as CARTESIA_API_KEY=...")
        report["steps"]["api_key"] = "missing"
        return _verdict(report, ready=False, checklist=checklist)

    print(f"API key            : {fp(s.cartesia_api_key)}")

    # --- Step 1: list agents (also validates the key) ---
    print("\n[1/2] GET /agents ...")
    agents: list = []
    try:
        data = ca.list_agents()
        agents = data if isinstance(data, list) else (data.get("summaries") or data.get("data") or data.get("agents") or [])
        report["steps"]["list_agents"] = {"ok": True, "count": len(agents)}
        print(f"      OK - {len(agents)} agent(s) on this account")
        for a in agents:
            if isinstance(a, dict):
                print(f"        - id={a.get('id')!r} name={a.get('name')!r}")
    except ca.CartesiaAuthError as e:
        report["steps"]["list_agents"] = {"ok": False, "status": e.status, "body": str(e.body)[:400]}
        print(f"      FAIL - {e}")
        checklist.append(
            "CARTESIA_API_KEY was rejected (401/403). Generate a fresh live key at "
            "https://play.cartesia.ai/keys and confirm it was pasted without quotes/whitespace."
        )
    except ca.CartesiaCallError as e:
        report["steps"]["list_agents"] = {"ok": False, "status": e.status, "body": str(e.body)[:400]}
        print(f"      FAIL - {e}")
        checklist.append(f"GET /agents failed ({e}). Check network access to api.cartesia.ai.")
    except Exception as e:
        report["steps"]["list_agents"] = {"ok": False, "error": str(e)[:400]}
        print(f"      FAIL - {e}")
        checklist.append(f"GET /agents raised an unexpected error: {e}")

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
        checklist.append(
            "No phone number found. Provision a Cartesia number (US-only) or import a "
            "Twilio number in the Playground -> Agents -> Phone Numbers."
        )

    # --- Verdict ---
    notes: list[str] = []
    agent_id = s.cartesia_agent_id or (
        agents[0].get("id") if agents and isinstance(agents[0], dict) else ""
    )
    number_id = s.cartesia_from_number_id or (
        numbers[0].get("id") if numbers and isinstance(numbers[0], dict) else ""
    )
    if not agent_id:
        checklist.append(
            "No Managed Agent found/configured. Create one at play.cartesia.ai -> Agents "
            "(the SDK has no create() method - this step is manual), then set "
            "CARTESIA_AGENT_ID in .env."
        )
    elif not s.cartesia_agent_id:
        notes.append(f"Set CARTESIA_AGENT_ID={agent_id} in .env to pin this agent explicitly.")
    if not number_id:
        checklist.append("Provision/import a phone number (see step 2 above), then set CARTESIA_FROM_NUMBER_ID in .env.")
    elif not s.cartesia_from_number_id:
        notes.append(f"Set CARTESIA_FROM_NUMBER_ID={number_id} in .env to pin this number explicitly.")
    if not s.my_phone_e164:
        notes.append("Set MY_PHONE_E164=+1XXXXXXXXXX in .env - the number scripts/smoke_call.py dials by default.")

    # The configured agent id must actually exist on the account. Without this
    # check a stale/incorrect CARTESIA_AGENT_ID (e.g. a phone-number id pasted
    # by mistake) produced a false READY verdict.
    agent_ids = {a.get("id") for a in agents if isinstance(a, dict)}
    if agent_id and agent_ids and agent_id not in agent_ids:
        checklist.append(
            f"CARTESIA_AGENT_ID={agent_id} does not exist on this account "
            f"(found: {sorted(agent_ids) or 'none'}). Note a phone-number id "
            f"starts with 'ap_' while an agent id starts with 'agent_'."
        )
    if agent_id and not agent_ids:
        checklist.append(
            f"CARTESIA_AGENT_ID={agent_id} is set but the account has NO agents. "
            "Create a Managed Agent at https://play.cartesia.ai -> Agents "
            "(the API/SDK cannot create a *managed* agent - it only creates "
            "Line-style agents that require a git deployment)."
        )

    report["resolved"] = {"agent_id": agent_id, "from_number_id": number_id}
    ready = bool(agent_id and number_id) and not checklist
    return _verdict(report, ready=ready, checklist=checklist, notes=notes)


if __name__ == "__main__":
    raise SystemExit(main())
