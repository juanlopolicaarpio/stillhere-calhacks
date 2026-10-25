"""Bring the whole demo up from cold, in one command.

  python scripts/demo_up.py

Starts SpacetimeDB, publishes the module, starts the backend and frontend, opens
a public tunnel, writes the tunnel URL into .env, and reports whether a real
call can be placed.

Why this exists: the credentials and the Cartesia agent persist, but four local
processes and an ephemeral tunnel URL do not. After a reboot the system is not
"broken", it is just not running - and remembering five commands in the right
order under demo pressure is how a demo gets lost. This is idempotent: anything
already healthy is left alone.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import httpx  # noqa: E402

PY = str(ROOT / ".venv" / "Scripts" / "python.exe")
CLOUDFLARED = ROOT / "tools" / "cloudflared.exe"
LOGS = ROOT / ".demo-logs"


def _c(code: str, s: str) -> str:
    return f"\033[{code}m{s}\033[0m"


ok = lambda s: _c("32", s)     # noqa: E731
bad = lambda s: _c("31", s)    # noqa: E731
warn = lambda s: _c("33", s)   # noqa: E731
dim = lambda s: _c("90", s)    # noqa: E731


def alive(url: str, timeout: float = 4.0) -> bool:
    try:
        return httpx.get(url, timeout=timeout).status_code == 200
    except Exception:
        return False


def spawn(args: list[str], log: str, cwd: Path | None = None) -> None:
    """Start a detached background process writing to its own log file."""
    LOGS.mkdir(exist_ok=True)
    handle = open(LOGS / log, "wb")
    kwargs: dict = {"stdout": handle, "stderr": subprocess.STDOUT,
                    "cwd": str(cwd or ROOT)}
    if os.name == "nt":
        # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP, so it outlives this script.
        kwargs["creationflags"] = 0x00000008 | 0x00000200
    else:  # pragma: no cover
        kwargs["start_new_session"] = True
    subprocess.Popen(args, **kwargs)


def find_spacetime() -> str | None:
    import shutil

    explicit = os.environ.get("SPACETIME_CLI")
    if explicit and Path(explicit).exists():
        return explicit
    found = shutil.which("spacetime")
    if found:
        return found
    local = Path(os.environ.get("LOCALAPPDATA", "")) / "SpacetimeDB" / "spacetime.exe"
    return str(local) if local.exists() else None


def wait_for(check, *, seconds: int, label: str) -> bool:
    for _ in range(seconds):
        if check():
            return True
        time.sleep(1)
    print(f"  {label:22}{bad('did not come up')}")
    return False


def set_env_var(key: str, value: str) -> None:
    env = ROOT / ".env"
    text = env.read_text(errors="replace") if env.exists() else ""
    if re.search(rf"^{key}=", text, re.M):
        text = re.sub(rf"^{key}=.*$", f"{key}={value}", text, flags=re.M)
    else:
        text = text.rstrip("\n") + f"\n{key}={value}\n"
    env.write_text(text, encoding="utf-8")


def main() -> int:
    skip_tunnel = "--no-tunnel" in sys.argv
    print("=" * 70)
    print("STILL HERE - bringing the demo up")
    print("=" * 70)

    # --- 1. SpacetimeDB ---
    cli = find_spacetime()
    if not alive("http://127.0.0.1:3000/v1/ping"):
        if cli is None:
            print(f"  {'spacetimedb':22}{bad('CLI not found')}")
            print(dim("    install: iwr https://windows.spacetimedb.com -useb | iex"))
        else:
            spawn([cli, "start", "--listen-addr", "127.0.0.1:3000"], "spacetime.log")
            wait_for(lambda: alive("http://127.0.0.1:3000/v1/ping"),
                     seconds=40, label="spacetimedb")
    if alive("http://127.0.0.1:3000/v1/ping"):
        print(f"  {'spacetimedb':22}{ok('up')}")
        if cli:
            # Idempotent: republishing an unchanged module is a no-op update.
            for db in ("stillhere", "stillhere-test"):
                r = subprocess.run([cli, "publish", "--server", "local", "--yes", db],
                                   cwd=str(ROOT / "spacetime"),
                                   capture_output=True, text=True, timeout=600,
                                   encoding="utf-8", errors="replace")
                mark = ok("published") if r.returncode == 0 else bad("FAILED")
                print(f"    {db:20}{mark}")

    # --- 2. backend ---
    if not alive("http://localhost:8000/health"):
        spawn([PY, "-m", "uvicorn", "app.main:app", "--app-dir", "backend",
               "--port", "8000"], "backend.log")
        wait_for(lambda: alive("http://localhost:8000/health"),
                 seconds=40, label="backend")
    if alive("http://localhost:8000/health"):
        print(f"  {'backend':22}{ok('up')}   http://localhost:8000")

    # --- 3. frontend ---
    if not alive("http://localhost:5173/"):
        npm = "npm.cmd" if os.name == "nt" else "npm"
        spawn([npm, "run", "dev"], "frontend.log", cwd=ROOT / "frontend")
        wait_for(lambda: alive("http://localhost:5173/"), seconds=90, label="frontend")
    if alive("http://localhost:5173/"):
        print(f"  {'frontend':22}{ok('up')}   http://localhost:5173")

    # --- 4. public tunnel (needed for mid-call tools) ---
    if skip_tunnel:
        print(f"  {'tunnel':22}{dim('skipped (--no-tunnel)')}")
    elif not CLOUDFLARED.exists():
        print(f"  {'tunnel':22}{warn('cloudflared missing')}")
        print(dim(f"    expected at {CLOUDFLARED}"))
    else:
        log = LOGS / "tunnel.log"
        # Quick tunnels expire. On Windows the old cloudflared process keeps
        # its redirected log handle open, so unlinking the log before stopping
        # that process raises WinError 32 and prevents recovery.
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/F", "/IM", "cloudflared.exe"],
                capture_output=True,
            )
            time.sleep(1)
        if log.exists():
            log.unlink()
        spawn([str(CLOUDFLARED), "tunnel", "--url", "http://localhost:8000",
               "--no-autoupdate"], "tunnel.log")
        url = None
        for _ in range(45):
            time.sleep(1)
            if log.exists():
                m = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com",
                              log.read_text(encoding="utf-8", errors="replace"))
                if m:
                    url = m.group(0)
                    break
        if url and wait_for(lambda: alive(f"{url}/health", timeout=15),
                            seconds=30, label="tunnel"):
            set_env_var("PUBLIC_BASE_URL", url)
            print(f"  {'tunnel':22}{ok('up')}   {url}")
            # The backend reads PUBLIC_BASE_URL at import, so it needs a restart
            # to hand Cartesia the new webhook base.
            print(dim("    restarting backend to pick up the new tunnel URL ..."))
            subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine "
                 "-match 'uvicorn' } | ForEach-Object { Stop-Process -Id "
                 "$_.ProcessId -Force }"],
                capture_output=True,
            )
            time.sleep(3)
            spawn([PY, "-m", "uvicorn", "app.main:app", "--app-dir", "backend",
                   "--port", "8000"], "backend.log")
            wait_for(lambda: alive("http://localhost:8000/health"),
                     seconds=40, label="backend")
        else:
            print(f"  {'tunnel':22}{warn('no URL yet')}")
            print(dim("    the call will still connect, but mid-call tools"))
            print(dim("    (memory lookup, recording the answer) will not reach us"))

    # --- 5. verdict ---
    print()
    try:
        s = httpx.get("http://localhost:8000/api/status", timeout=10).json()
        for key in ("voice_mode", "coordination_mode", "conversation_mode"):
            val = s.get(key)
            live = val in ("cartesia_pstn", "spacetimedb", "claude")
            print(f"  {key:22}{ok(str(val)) if live else warn(str(val))}")
        print()
        if s.get("real_call_possible"):
            print(ok("READY") + " - place a real call with:")
            print("    python scripts/call_now.py")
        else:
            print(warn("NO REAL CALL POSSIBLE") + " - rehearse without the phone:")
            print("    python scripts/sim_call.py")
            print(dim("  diagnose with: python scripts/discover_cartesia.py"))
        print()
        print(dim("  dashboard : http://localhost:5173/dashboard  "
                  f"(token {s.get('dashboard_token') or 'see .env'})"))
        print(dim("  sync proof: http://localhost:5173/sync-proof"))
        print(dim(f"  logs      : {LOGS}"))
    except Exception as e:
        print(bad(f"status unavailable: {e}"))
        return 1
    print("=" * 70)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
