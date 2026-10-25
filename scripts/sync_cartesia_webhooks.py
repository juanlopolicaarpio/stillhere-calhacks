"""Keep Cartesia webhook callbacks aligned with the current public tunnel.

Quick Cloudflare tunnel URLs change whenever the tunnel restarts. Cartesia
stores webhook-tool URLs, so a real call can still ring while every tool calls
the previous dead URL. This script uses Cartesia's documented APIs:

  GET/PATCH /v1/agents/tools/{tool_id}
  PATCH     /agents/webhooks/{webhook_id}

It never changes the agent prompt, introduction, voice, phone number, or tool
descriptions. Run without --apply for a read-only check.
"""
from __future__ import annotations

import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.config import get_settings  # noqa: E402


TOOL_PATHS = {
    "get_support_context": "/api/voice/tools/get_support_context",
    "lookup_showtimes": "/api/voice/tools/lookup_showtimes",
    "record_friend_response": "/api/voice/tools/record_friend_response",
    "request_patient_approval": "/api/voice/tools/request_patient_approval",
    "identify_caller": "/api/inbound/identify",
}


def main() -> int:
    apply = "--apply" in sys.argv
    s = get_settings()
    if not s.cartesia_api_key:
        print("BLOCKED: CARTESIA_API_KEY is not configured")
        return 2
    if not s.public_base_url.startswith("https://"):
        print("BLOCKED: PUBLIC_BASE_URL must be public HTTPS")
        return 2

    headers = {
        "Authorization": f"Bearer {s.cartesia_api_key}",
        "Cartesia-Version": s.cartesia_version,
        "Content-Type": "application/json",
    }
    base = s.public_base_url.rstrip("/")

    with httpx.Client(headers=headers, timeout=30) as client:
        response = client.get("https://api.cartesia.ai/v1/agents/tools")
        response.raise_for_status()
        tools = {t["name"]: t for t in response.json().get("data", [])}

        missing = sorted(set(TOOL_PATHS) - set(tools))
        if missing:
            print("BLOCKED: missing Cartesia tools: " + ", ".join(missing))
            return 2

        stale: list[str] = []
        for name, path in TOOL_PATHS.items():
            tool = tools[name]
            wanted = base + path
            current = (tool.get("api_schema") or {}).get("url")
            if current != wanted:
                stale.append(name)
            print(f"  {name:26} {'STALE' if current != wanted else 'ok'}")
            if apply:
                patch = {
                    "api_schema": {
                        "url": wanted,
                        "request_headers": {
                            "X-StillHere-Secret": {
                                "type": "secret",
                                "secret_value": s.webhook_secret,
                            }
                        },
                    }
                }
                updated = client.patch(
                    f"https://api.cartesia.ai/v1/agents/tools/{tool['id']}",
                    json=patch,
                )
                updated.raise_for_status()

        if not s.cartesia_agent_id:
            print("BLOCKED: CARTESIA_AGENT_ID is not configured")
            return 2
        agent = client.get(
            f"https://api.cartesia.ai/agents/{s.cartesia_agent_id}"
        )
        agent.raise_for_status()
        webhook_id = agent.json().get("webhook_id")
        if webhook_id:
            webhook = client.get(
                f"https://api.cartesia.ai/agents/webhooks/{webhook_id}"
            )
            webhook.raise_for_status()
            wanted = base + "/api/voice/webhook"
            current = webhook.json().get("url")
            is_stale = current != wanted
            print(f"  {'call-status webhook':26} {'STALE' if is_stale else 'ok'}")
            if is_stale:
                stale.append("call-status webhook")
            if apply:
                updated = client.patch(
                    f"https://api.cartesia.ai/agents/webhooks/{webhook_id}",
                    json={
                        "url": wanted,
                        "secret": s.webhook_secret,
                        "display_name": "Still Here call lifecycle",
                    },
                )
                updated.raise_for_status()
        else:
            print("  call-status webhook       MISSING")
            stale.append("call-status webhook missing")

    if apply:
        print(f"SYNCED: {len(TOOL_PATHS)} tools and the attached call-status webhook")
        return 0
    if stale:
        print("NEEDS SYNC: " + ", ".join(stale))
        print("Run with --apply after review.")
        return 1
    print("READY: Cartesia callbacks match PUBLIC_BASE_URL")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
