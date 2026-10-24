"""One command. Everything.

    python go.py              start everything, then place a real call
    python go.py call         just place the call (things already running)
    python go.py up           just start everything
    python go.py sim          rehearse the conversation in the terminal, no call
    python go.py weeks        watch three weeks pass and see it decide
    python go.py stop         shut it all down
    python go.py tools        print the ONE manual setup step
    python go.py check        is it ready?

That is the whole operator surface. The ten scripts underneath still exist for
when something specific breaks, but you should never need to know their names.

Why this file exists: the product has almost no friction - scan a code, type a
number, tap "call me". The *operator* side had accumulated ten scripts, two
tokens and a tunnel, which is exactly the kind of complexity that loses a demo
at the moment you can least afford it.
"""
from __future__ import annotations

import subprocess
import sys
import time
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PY = str(ROOT / ".venv" / "Scripts" / "python.exe")
if not Path(PY).exists():  # non-Windows fallback
    PY = sys.executable

sys.path.insert(0, str(ROOT / "backend"))


def _c(code: str, s: str) -> str:
    return f"\033[{code}m{s}\033[0m"


ok = lambda s: _c("32;1", s)   # noqa: E731
bad = lambda s: _c("31;1", s)  # noqa: E731
warn = lambda s: _c("33", s)   # noqa: E731
dim = lambda s: _c("90", s)    # noqa: E731
hi = lambda s: _c("36;1", s)   # noqa: E731


def run(script: str, *extra: str) -> int:
    return subprocess.call([PY, str(ROOT / "scripts" / script), *extra])


def alive(url: str, timeout: float = 4.0) -> bool:
    try:
        import httpx

        return httpx.get(url, timeout=timeout).status_code == 200
    except Exception:
        return False


def everything_up() -> bool:
    return (
        alive("http://localhost:8000/health")
        and alive("http://localhost:5173/")
        and alive("http://127.0.0.1:3000/v1/ping")
    )


def ensure_up(*, want_tunnel: bool = True) -> bool:
    """Start whatever is not already running. Idempotent."""
    if everything_up() and (not want_tunnel or _tunnel_ok()):
        print(ok("Already running."))
        return True
    print(dim("Starting services ... (about 40 seconds from cold)"))
    code = run("demo_up.py", *([] if want_tunnel else ["--no-tunnel"]))
    return code == 0


def _tunnel_ok() -> bool:
    """Is the public webhook URL reachable? Needed for mid-call tools."""
    try:
        from app.config import get_settings

        base = get_settings().public_base_url
    except Exception:
        return False
    if base.startswith("http://localhost") or base.startswith("http://127."):
        return False
    return alive(f"{base}/health", timeout=15)


def open_dashboard() -> None:
    url = "http://localhost:5173/dashboard"
    print(hi("  dashboard: ") + url + dim("  (opening in your browser)"))
    try:
        webbrowser.open(url)
    except Exception:
        pass


def cmd_up() -> int:
    if not ensure_up():
        return 1
    open_dashboard()
    print()
    print(dim("  then: python go.py call"))
    return 0


def cmd_call() -> int:
    if not ensure_up():
        return 1
    print()
    return run("call_now.py")


def cmd_check() -> int:
    if not everything_up():
        print(warn("Not running.") + " Start with: python go.py up")
        return 2
    return run("call_now.py", "--check")


def cmd_sim() -> int:
    if not ensure_up(want_tunnel=False):
        return 1
    print()
    return run("sim_call.py")


def cmd_weeks() -> int:
    if not ensure_up(want_tunnel=False):
        return 1
    print()
    return run("timewarp.py", *sys.argv[2:])


def cmd_tools() -> int:
    """The one step that cannot be automated: registering webhook tools."""
    if not everything_up():
        ensure_up()
    return run("print_tools.py")


def cmd_stop() -> int:
    """Stop the local processes. Leaves data and credentials alone."""
    print(dim("Stopping backend, frontend, tunnel and SpacetimeDB ..."))
    subprocess.call(
        ["powershell", "-NoProfile", "-Command",
         "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match "
         "'uvicorn|cloudflared|spacetime start|vite' } | ForEach-Object { "
         "Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    time.sleep(2)
    print(ok("Stopped.") + dim("  Your .env, seeded data and Cartesia agent are untouched."))
    return 0


def cmd_default() -> int:
    """The one people will actually type: bring it up and call."""
    print("=" * 70)
    print(hi("STILL HERE"))
    print("=" * 70)
    if not ensure_up():
        return 1
    open_dashboard()
    print()
    print(dim("Giving the dashboard a moment to connect ..."))
    time.sleep(3)
    print()
    return run("call_now.py")


COMMANDS = {
    "": cmd_default,
    "call": cmd_call,
    "up": cmd_up,
    "check": cmd_check,
    "tools": cmd_tools,
    "sim": cmd_sim,
    "weeks": cmd_weeks,
    "stop": cmd_stop,
}


def main() -> int:
    arg = (sys.argv[1] if len(sys.argv) > 1 else "").lower()
    if arg in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    fn = COMMANDS.get(arg)
    if fn is None:
        print(bad(f"Unknown command {arg!r}."))
        print(dim("Try: python go.py            (start everything and call)"))
        print(dim("     python go.py call | up | sim | weeks | tools | check | stop"))
        return 2
    return fn()


if __name__ == "__main__":
    raise SystemExit(main())
