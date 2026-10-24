"""Print the webhook tools to paste into the Cartesia Playground.

    python scripts/print_tools.py

This is the ONE step that cannot be automated. There is no Cartesia API for
registering an agent's webhook tools - verified 2026-10-25 by probing
`GET/POST /agents/{id}/tools`, `/agents/{id}/webhook-tools` and
`PATCH /agents/{id}` with a `tools` array; all are 404 or
"Unrecognized key: tools". Tool registration is Playground-only.

Why it matters: without these, a call connects and the agent talks, but it
cannot reach your backend. It cannot look up a memory, record that the friend
said yes, or ask for the patient's approval - so after a perfectly pleasant
conversation, nothing happens. That is exactly the symptom of an agent with no
tools, and it is invisible from the call itself.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.config import get_settings  # noqa: E402


def _c(code: str, s: str) -> str:
    return f"\033[{code}m{s}\033[0m"


hi = lambda s: _c("36;1", s)    # noqa: E731
ok = lambda s: _c("32;1", s)    # noqa: E731
warn = lambda s: _c("33", s)    # noqa: E731
dim = lambda s: _c("90", s)     # noqa: E731

# (name, path, body, what it is for)
TOOLS = [
    (
        "get_support_context",
        "/api/voice/tools/get_support_context",
        '{"call_id": "{{session_id}}"}',
        "What may I say about Sam? Returns only shareable memories.",
    ),
    (
        "record_friend_response",
        "/api/voice/tools/record_friend_response",
        '{"call_id": "{{session_id}}", "response": "accepted",'
        ' "activity": "", "starts_at": ""}',
        "THE IMPORTANT ONE. Records yes/no/counterproposal. Without it, "
        "agreeing on the phone changes nothing.",
    ),
    (
        "request_patient_approval",
        "/api/voice/tools/request_patient_approval",
        '{"call_id": "{{session_id}}"}',
        "Moves the plan to awaiting-Sam. Lights up the dashboard.",
    ),
    (
        "lookup_showtimes",
        "/api/voice/tools/lookup_showtimes",
        '{"movie": "Marvel", "date": ""}',
        "Illustrative options. Labelled MOCK everywhere.",
    ),
    (
        "identify_caller",
        "/api/inbound/identify",
        '{"from_number": "{{caller_number}}", "call_id": "{{session_id}}"}',
        "For INBOUND calls: who is ringing, and what may be said to them.",
    ),
]


def main() -> int:
    s = get_settings()
    base = s.public_base_url.rstrip("/")
    local = base.startswith("http://localhost") or base.startswith("http://127.")

    print("=" * 72)
    print(hi("CARTESIA WEBHOOK TOOLS - the one manual step"))
    print("=" * 72)
    print()
    print("Without these the agent can talk but cannot DO anything: a call")
    print("connects, the conversation goes fine, and then nothing happens.")
    print()

    if local:
        print(warn("STOP. Your PUBLIC_BASE_URL is local:"))
        print(f"  {base}")
        print("Cartesia cannot reach that. Run `python go.py up` first, which")
        print("opens a public tunnel and rewrites this value, then re-run me.")
        print("=" * 72)
        return 2

    print(f"{hi('1.')} Open " + hi("https://play.cartesia.ai")
          + " -> Agents -> " + hi(str(s.cartesia_agent_id)))
    print(f"{hi('2.')} Find the " + hi("Tools") + " section.")
    print(f"{hi('3.')} Add each tool below as a " + hi("webhook tool") + ".")
    print()
    print("   Every one uses:")
    print(f"     method  {ok('POST')}")
    print(f"     header  {ok('X-StillHere-Secret')}: {ok(s.webhook_secret)}")
    print()

    for i, (name, path, body, why) in enumerate(TOOLS, 1):
        print(dim("  " + "-" * 68))
        print(f"  {hi(f'{i}. {name}')}")
        print(f"     url   {base}{path}")
        print(f"     body  {body}")
        print(dim(f"     why   {why}"))
    print(dim("  " + "-" * 68))

    print()
    print(f"{hi('4.')} Also add the call-status webhook, if the Playground offers one:")
    print(f"     POST  {base}/api/voice/webhook")
    print(f"     header X-StillHere-Secret: {s.webhook_secret}")
    print()
    print(f"{hi('5.')} Save, then: " + ok("python go.py call"))
    print()
    print(dim("  Watch the dashboard. You should now see call.tool_invoked rows"))
    print(dim("  appear while you are still talking - that is the proof the"))
    print(dim("  agent reached your backend."))
    print("=" * 72)
    print()
    print(warn("Note: this cannot be scripted. There is no Cartesia API for"))
    print(warn("registering webhook tools - verified by probing every plausible"))
    print(warn("endpoint; they return 404 or 'Unrecognized key: tools'."))
    print("=" * 72)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
