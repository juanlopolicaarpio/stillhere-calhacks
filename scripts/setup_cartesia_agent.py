"""Configure the Cartesia Managed Agent for the live call.

  python scripts/setup_cartesia_agent.py            # inspect + print config
  python scripts/setup_cartesia_agent.py --apply    # try to update via API

cartesia-python 4.2.0 exposes agents.update() (name/description/tts_voice/
tts_language) but NO create method, and tool registration is not in the SDK.
So this script:

  1. verifies the key and lists existing agents
  2. with --apply, updates the configured agent's voice/language via the SDK
  3. prints the exact system prompt and the four webhook tool definitions with
     your real PUBLIC_BASE_URL substituted, ready to paste into the Playground

It never invents endpoints. Anything it cannot do through a verified API it
tells you to do in the Playground.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.adapters import cartesia_adapter as ca  # noqa: E402
from app.config import get_settings  # noqa: E402

TOOLS = [
    ("get_support_context", "/api/voice/tools/get_support_context",
     '{"call_id": "{{session_id}}"}',
     "Contact-safe shared context with memory citations. Use for 'why me?'."),
    ("lookup_showtimes", "/api/voice/tools/lookup_showtimes",
     '{"movie": "<string>", "date": "<optional ISO date>"}',
     "MOCK showtimes. Illustrative only - nothing is booked."),
    ("record_friend_response", "/api/voice/tools/record_friend_response",
     '{"call_id": "{{session_id}}", "response": "accepted|declined|counterproposed",'
     ' "activity": "<optional>", "starts_at": "<optional ISO 8601>"}',
     "Records the friend's answer and advances the state machine."),
    ("request_patient_approval", "/api/voice/tools/request_patient_approval",
     '{"call_id": "{{session_id}}"}',
     "Requests the patient's final approval. Cannot confirm a plan."),
]


def main() -> int:
    apply = "--apply" in sys.argv
    s = get_settings()
    base = s.public_base_url.rstrip("/")

    print("=" * 70)
    print("CARTESIA MANAGED AGENT SETUP")
    print("=" * 70)

    if not s.cartesia_api_key:
        print("\nCARTESIA_API_KEY is not set - cannot talk to the API.")
        print("Create a key at https://play.cartesia.ai/keys and add it to .env")
        print("\nStill printing the configuration you'll need:\n")
    else:
        try:
            data = ca.list_agents()
            agents = data if isinstance(data, list) else (
                data.get("data") or data.get("agents") or []
            )
            print(f"\nAgents on this account: {len(agents)}")
            for a in agents:
                if isinstance(a, dict):
                    marker = "  <- configured" if a.get("id") == s.cartesia_agent_id else ""
                    print(f"  - {a.get('id')}  {a.get('name')!r}{marker}")
            if not agents:
                print("  (none) -> create one at play.cartesia.ai -> Agents")
        except Exception as e:
            print(f"\nCould not list agents: {e}")

        if apply and s.cartesia_agent_id:
            print(f"\nUpdating agent {s.cartesia_agent_id} voice/language via SDK ...")
            try:
                from cartesia import Cartesia

                client = Cartesia(api_key=s.cartesia_api_key)
                updated = client.agents.update(
                    s.cartesia_agent_id,
                    name="Still Here",
                    description="Consent-gated social reconnection outreach agent",
                    tts_voice=s.cartesia_voice_id,
                    tts_language="en",
                )
                print(f"  OK - updated: {updated}")
            except Exception as e:
                print(f"  FAILED - {e}")
                print("  -> set voice and language in the Playground instead")
        elif apply:
            print("\n--apply needs CARTESIA_AGENT_ID in .env")

    if base.startswith("http://localhost") or base.startswith("http://127."):
        print("\n" + "!" * 70)
        print("WARNING: PUBLIC_BASE_URL is local, so Cartesia cannot reach your")
        print("webhook tools during a call. Expose the backend first:")
        print("    ngrok http 8000")
        print("    cloudflared tunnel --url http://localhost:8000")
        print("then set PUBLIC_BASE_URL to the public https URL and re-run.")
        print("!" * 70)

    print("\n" + "=" * 70)
    print("1. VOICE SETTINGS (Playground -> Agents -> your agent)")
    print("=" * 70)
    print(f"  TTS model : {s.cartesia_tts_model}")
    print(f"  Voice id  : {s.cartesia_voice_id}")
    print("  Language  : en")
    print("  Interruptions: ENABLED  (the judge must be able to cut in)")

    print("\n" + "=" * 70)
    print("2. WEBHOOK TOOLS - add each with header X-StillHere-Secret")
    print("=" * 70)
    for name, path, body, desc in TOOLS:
        print(f"\n  {name}")
        print(f"    POST   {base}{path}")
        print(f"    body   {body}")
        print(f"    header X-StillHere-Secret: {'<WEBHOOK_SECRET>' if s.webhook_secret == 'dev-only-change-me' else '(set, matches .env)'}")
        print(f"    note   {desc}")
    print(f"\n  call status webhook")
    print(f"    POST   {base}/api/voice/webhook")
    print(f"    header X-StillHere-Secret")

    print("\n" + "=" * 70)
    print("3. SYSTEM PROMPT + WELCOME MESSAGE")
    print("=" * 70)
    prompt_file = ROOT / "prompts" / "OUTREACH_AGENT.md"
    print(f"  Paste the fenced system prompt from:\n    {prompt_file}")
    print("  (it contains the privacy rules that keep clinical context off the call)")

    print("\n" + "=" * 70)
    print("4. THEN")
    print("=" * 70)
    print("  python scripts/discover_cartesia.py    # resolve agent + number ids")
    print("  python scripts/smoke_call.py           # place a REAL call to your phone")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
