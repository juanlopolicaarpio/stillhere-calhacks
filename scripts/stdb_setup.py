"""SpacetimeDB preflight + publish. Run this BEFORE expecting coordination_mode
to be "spacetimedb".

  python scripts/stdb_setup.py
  python scripts/stdb_setup.py --fresh     # DESTROYS module data, then republishes

Checks the CLI, starts or validates the local server, publishes the `stillhere`
module, verifies the publishing identity really is a seeded service principal,
round-trips one reducer rejection to prove the module is live, and prints the
exact .env lines. Writes evidence/spacetimedb_setup.json.

Seeds no data. The only write it performs is `spacetime publish`.
Prints nothing secret: the bearer token is never printed, only its identity
(which is public) and its length.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.adapters import spacetimedb_adapter as st  # noqa: E402

MODULE_DIR = ROOT / "spacetime"
LISTEN_ADDR = "127.0.0.1:3000"


def find_cli() -> str | None:
    """Locate the `spacetime` executable without assuming it is on PATH."""
    explicit = os.environ.get("SPACETIME_CLI")
    if explicit and Path(explicit).exists():
        return explicit
    found = shutil.which("spacetime")
    if found:
        return found
    local = Path(os.environ.get("LOCALAPPDATA", "")) / "SpacetimeDB" / "spacetime.exe"
    if local.exists():
        return str(local)
    return None


def run(cli_args: list[str], *, cwd: Path, timeout: int = 180) -> tuple[int, str]:
    try:
        p = subprocess.run(
            cli_args,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            # The CLI emits UTF-8; Windows' default cp1252 decoder chokes on it.
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return 124, f"timed out after {timeout}s"
    return p.returncode, ((p.stdout or "") + (p.stderr or "")).strip()


def start_server(cli: str) -> bool:
    """Launch `spacetime start` detached and wait for /v1/ping."""
    creationflags = 0
    if os.name == "nt":
        # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP - survives this script.
        creationflags = 0x00000008 | 0x00000200
    try:
        subprocess.Popen(
            [cli, "start", "--listen-addr", LISTEN_ADDR],
            cwd=str(MODULE_DIR),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
        )
    except OSError:
        return False
    for _ in range(40):  # up to ~20s
        time.sleep(0.5)
        if st.health().get("reachable") or _ping_ok():
            return True
    return False


def _ping_ok() -> bool:
    import httpx

    try:
        return httpx.get(f"{st.host()}/v1/ping", timeout=2.0).status_code == 200
    except httpx.HTTPError:
        return False


def token_claims() -> dict:
    """Read the non-secret header claims out of the bearer token.

    NOTE: `sub` is the OIDC subject (a UUID for the built-in SpacetimeAuth
    issuer), NOT the SpacetimeDB Identity - the Identity is derived by hashing
    issuer+subject server-side, and 2.13.0 exposes no `whoami` endpoint
    (`GET /v1/identity/whoami` -> 404, measured). So this is diagnostic only;
    privilege is established EMPIRICALLY in step 6 by calling a privileged
    reducer and seeing whether the module says `not_a_service_principal`.

    The signature and the token itself never leave this function.
    """
    tok = st.token()
    if not tok or tok.count(".") != 2:
        return {}
    payload = tok.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    try:
        claims = json.loads(base64.urlsafe_b64decode(payload).decode("utf-8"))
    except Exception:
        return {}
    return {k: claims.get(k) for k in ("iss", "sub", "aud") if claims.get(k)}


def main() -> int:
    ap = argparse.ArgumentParser(description="SpacetimeDB preflight + publish")
    ap.add_argument(
        "--fresh",
        action="store_true",
        help="publish with --delete-data=always (DESTROYS all module data)",
    )
    ap.add_argument(
        "--no-publish", action="store_true", help="check only; do not publish"
    )
    args = ap.parse_args()

    report: dict = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "host": st.host(),
        "database": st.database(),
        "module_dir": str(MODULE_DIR),
        "steps": {},
    }

    print("=" * 68)
    print("SPACETIMEDB PREFLIGHT")
    print("=" * 68)
    print(f"host               : {st.host()}")
    print(f"database           : {st.database()}")
    print(f"module path        : {MODULE_DIR}")

    blockers: list[str] = []

    # --- Step 1: the CLI exists ---
    print("\n[1/6] locate `spacetime` CLI ...")
    cli = find_cli()
    if cli is None:
        print("      FAIL - not found on PATH, in SPACETIME_CLI, or in LOCALAPPDATA")
        print("      -> install from https://spacetimedb.com/install")
        report["steps"]["cli"] = {"ok": False}
        blockers.append("spacetime CLI not installed")
        return finish(report, blockers)
    code, out = run([cli, "--version"], cwd=ROOT, timeout=30)
    version = out.splitlines()[-1].strip() if out else ""
    print(f"      OK - {cli}")
    print(f"           {version}")
    report["steps"]["cli"] = {"ok": code == 0, "path": cli, "version": version}

    # --- Step 2: the module project is present ---
    print("\n[2/6] module project ...")
    entry = MODULE_DIR / "spacetimedb" / "src" / "index.ts"
    if not entry.exists():
        print(f"      FAIL - {entry} is missing")
        report["steps"]["module"] = {"ok": False, "entry": str(entry)}
        blockers.append("module source missing")
        return finish(report, blockers)
    deps = (MODULE_DIR / "spacetimedb" / "node_modules").exists()
    print(f"      OK - entry {entry.relative_to(ROOT)}")
    print(f"           node_modules installed: {deps}")
    if not deps:
        print("      -> run: npm install   (in spacetime/spacetimedb)")
        blockers.append("node_modules not installed in spacetime/spacetimedb")
    report["steps"]["module"] = {"ok": True, "node_modules": deps}

    # --- Step 3: local server reachable (start it if not) ---
    print(f"\n[3/6] local server on {LISTEN_ADDR} ...")
    if _ping_ok():
        print("      OK - already running (/v1/ping -> 200)")
        report["steps"]["server"] = {"ok": True, "started_by_us": False}
    else:
        print("      not running; starting `spacetime start` detached ...")
        if start_server(cli):
            print("      OK - started and answering /v1/ping")
            report["steps"]["server"] = {"ok": True, "started_by_us": True}
        else:
            print("      FAIL - could not reach the server after 20s")
            print(f"      -> run manually: spacetime start --listen-addr {LISTEN_ADDR}")
            report["steps"]["server"] = {"ok": False}
            blockers.append("local SpacetimeDB server unreachable")
            return finish(report, blockers)

    # --- Step 4: build + publish ---
    print("\n[4/6] build + publish ...")
    if args.no_publish:
        print("      SKIPPED (--no-publish)")
        report["steps"]["publish"] = {"ok": None, "skipped": True}
    elif blockers:
        print("      SKIPPED - earlier blocker makes a publish pointless")
        report["steps"]["publish"] = {"ok": None, "skipped": True}
    else:
        pub = [cli, "publish", "--server", "local", "--yes"]
        if args.fresh:
            pub.append("--delete-data=always")
            print("      --fresh: this DESTROYS all existing module data")
        code, out = run(pub, cwd=MODULE_DIR, timeout=300)
        tail = "\n".join(out.splitlines()[-6:])
        if code == 0:
            print("      OK -")
            for line in tail.splitlines():
                print(f"           {line}")
        else:
            print(f"      FAIL (exit {code}) -")
            for line in tail.splitlines():
                print(f"           {line}")
            blockers.append("spacetime publish failed")
        report["steps"]["publish"] = {"ok": code == 0, "output_tail": tail}

    # --- Step 5: credential present ---
    print("\n[5/6] credential ...")
    tok = st.token()
    src = (
        "SPACETIMEDB_TOKEN env"
        if os.environ.get("SPACETIMEDB_TOKEN")
        else "local CLI token (dev only)"
    )
    claims = token_claims()
    if not tok:
        print("      FAIL - no token in SPACETIMEDB_TOKEN and none in cli.toml")
        print("      -> run: spacetime login   (or set SPACETIMEDB_TOKEN)")
        report["steps"]["credential"] = {"ok": False}
        blockers.append("no SpacetimeDB token available")
    else:
        print(f"      token source     : {src}")
        print(f"      token length     : {len(tok)} chars (value never printed)")
        print(f"      issuer           : {claims.get('iss', 'unknown')}")
        print(f"      subject          : {claims.get('sub', 'unknown')}")
        try:
            principals = st.query_sql("SELECT * FROM service_principals")
            print(f"      seeded principals: {len(principals)}")
            report["steps"]["credential"] = {
                "ok": True,
                "source": src,
                "token_length": len(tok),
                "claims": claims,
                "seeded_principal_count": len(principals),
            }
        except Exception as e:
            print(f"      WARN - could not read service_principals: {e}")
            report["steps"]["credential"] = {
                "ok": True, "source": src, "token_length": len(tok), "claims": claims
            }

    # --- Step 6: live round-trip; this is where privilege is PROVEN ---
    print("\n[6/6] live reducer round-trip (also proves privilege) ...")
    h = st.health()
    print(f"      health: reachable={h['reachable']} detail={h['detail']}")
    report["steps"]["health"] = h
    if h["reachable"]:
        # Read-only proof. `confirm_if_matching` checks the sender's privilege
        # FIRST, so the reason it comes back with tells us both things at once:
        #   'not_a_service_principal' -> the token is not privileged
        #   'no_proposal'             -> privileged, and the gate is live
        # Either way nothing is written: there is no such proposal.
        try:
            st.call_reducer(
                "confirm_if_matching", ["__stdb_setup_probe__", 1, "__probe__"]
            )
            print("      UNEXPECTED - the probe confirm SUCCEEDED; that should be")
            print("                   impossible for a non-existent proposal")
            blockers.append("confirm_if_matching did not reject a bogus proposal")
            report["steps"]["round_trip"] = {"ok": False, "reason": "probe succeeded"}
        except st.SpacetimeError as e:
            if e.reason == "no_proposal":
                print("      OK - confirm_if_matching rejected the probe with the")
                print("           machine-readable reason 'no_proposal', which means")
                print("           this token IS a seeded service principal and the")
                print("           confirmation gate is live.")
                report["steps"]["round_trip"] = {"ok": True, "reason": e.reason}
            elif e.reason == "not_a_service_principal":
                print("      FAIL - this token is NOT a seeded service principal.")
                print("      -> `init` seeds only the identity that FIRST published")
                print("         the module. Republish from THIS identity:")
                print("           python scripts/stdb_setup.py --fresh")
                blockers.append("token identity is not a service principal")
                report["steps"]["round_trip"] = {"ok": False, "reason": e.reason}
            else:
                print(f"      FAIL - unexpected rejection: status={e.status} reason={e.reason}")
                blockers.append(f"confirm_if_matching probe returned {e.reason!r}")
                report["steps"]["round_trip"] = {"ok": False, "reason": e.reason}
        except st.SpacetimeNotConfigured as e:
            print(f"      FAIL - {e}")
            blockers.append("adapter reports not configured")
            report["steps"]["round_trip"] = {"ok": False}
    else:
        blockers.append("adapter health check says the module is not reachable")
        report["steps"]["round_trip"] = {"ok": False, "reason": "unreachable"}

    return finish(report, blockers)


def finish(report: dict, blockers: list[str]) -> int:
    print("\n" + "=" * 68)
    if not blockers:
        report["verdict"] = "ready"
        print("READY - SpacetimeDB is the live coordination backend.")
        print("\nPut these in .env:")
        print(f"  SPACETIMEDB_HOST={st.host()}")
        print(f"  SPACETIMEDB_DB={st.database()}")
        print("  SPACETIMEDB_TOKEN=<your token>   # optional locally; the adapter")
        print("                                   # falls back to the CLI token and")
        print("                                   # logs that it did (dev only)")
        print("\nVerify with:")
        print('  spacetime sql --server local stillhere "SELECT * FROM plan_confirmations"')
        print("  ./.venv/Scripts/python.exe -m pytest backend/tests/test_spacetimedb.py -q")
    else:
        report["verdict"] = "blocked"
        print("BLOCKED - coordination_mode will honestly report 'sqlite_fallback'.")
        print("Nothing in this repo may claim SpacetimeDB is live. Blockers:")
        for b in blockers:
            print(f"  - {b}")
    print("=" * 68)

    report["blockers"] = blockers
    (ROOT / "evidence").mkdir(exist_ok=True)
    out = ROOT / "evidence" / "spacetimedb_setup.json"
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nEvidence written to {out.relative_to(ROOT)}")
    return 0 if report["verdict"] == "ready" else 1



def publish_test_database() -> bool:
    """Publish the module a second time under the test database name.

    The suite points `SPACETIMEDB_DB` at `stillhere-test` so its per-test
    coordination reset can never wipe the database a running demo is using.
    Without this the two share state and a test run breaks a live demo.
    """
    cli = find_cli()
    if cli is None:
        print("  test database: SKIPPED (spacetime CLI not found)")
        return False
    code, out = run(
        [cli, "publish", "--server", "local", "--yes", "stillhere-test"],
        cwd=ROOT / "spacetime", timeout=600,
    )
    ok = code == 0
    print(f"  test database 'stillhere-test': {'OK' if ok else 'FAILED'}")
    if not ok:
        print("   ", out[-300:])
    return ok


if __name__ == "__main__":
    raise SystemExit(main())
