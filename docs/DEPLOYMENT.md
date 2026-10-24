# Deployment

This describes how to actually run this codebase, verified against
`Makefile`, `backend/requirements.txt`, `frontend/package.json`, and `.env.example`.
There is no hosted deployment in this build — everything below is local/demo
deployment, which is the only mode the code has been run in.

## Toolchain (verified)

- Python 3.14.2, backend deps pinned minimum versions in
  `backend/requirements.txt`: `fastapi>=0.162`, `uvicorn[standard]>=0.54`,
  `pydantic>=2.14`, `httpx>=0.28`, `python-dotenv>=1.2`, `pytest>=9`,
  `pytest-asyncio>=1.4`, `anthropic>=2.3`, `cartesia>=4.2`, `sse-starlette>=3.5`,
  `qrcode[pil]>=8.2`.
- Node 24.12.0, frontend: React 19.3.1, react-router-dom 7.18.4, Vite 8.3.4+,
  Tailwind 4.3.3+, TypeScript ~6.0.3 (`frontend/package.json`).

## Setup

```
make install     # pip install -r backend/requirements.txt; npm install in frontend/
```

Copy `.env.example` to `.env` at the repo root and fill in what you have. Every
variable degrades gracefully when absent — see the table below.

## Environment variables

| variable | purpose | effect when unset |
|---|---|---|
| `CARTESIA_API_KEY` / `CARTESIA_AGENT_ID` / `CARTESIA_FROM_NUMBER_ID` | real PSTN calling | `voice_mode()` returns `"simulated"`; all three must be present for `"cartesia_pstn"` (`config.py:61-75`) |
| `CARTESIA_VERSION` | API version header | defaults to `2026-08-14` |
| `CARTESIA_VOICE_ID` / `CARTESIA_TTS_MODEL` | Sonic TTS voice preview | defaults provided |
| `MY_PHONE_E164` | demo target (developer's own phone) | falls back to the fake `+14155550101` seed number for Josh |
| `ANTHROPIC_API_KEY` | Claude companion + extraction | falls back to deterministic rule-based reply/extraction, `reasoning_mode: "fallback"` |
| `ANTHROPIC_MODEL` | model id | defaults to `claude-opus-5` |
| `PUBLIC_BASE_URL` | QR target, invite links, Cartesia webhook base | defaults to `http://localhost:8000` — must be a public HTTPS URL (ngrok/cloudflared) for Cartesia webhooks to reach this backend in a real call |
| `WEBHOOK_SECRET` | Cartesia tool/webhook auth | defaults to the obvious placeholder `dev-only-change-me` — change this before exposing the backend publicly |
| `DASHBOARD_TOKEN` | judge dashboard auth | defaults to `dev-dashboard-token` — same caveat |
| `TWILIO_ACCOUNT_SID` / `TWILIO_AUTH_TOKEN` / `TWILIO_FROM_NUMBER` | SMS delivery, number provider | not configured in this build; `twilio_configured` is `False`; all notification "sends" are labeled `simulated` |
| `DB_PATH` | SQLite file location | defaults to `<repo_root>/stillhere.db` |

## Running locally

```
make backend     # uvicorn --reload on :8000
make frontend     # vite dev server on :5173
```

CORS is hardcoded to `http://localhost:5173` and `http://127.0.0.1:5173`
(`main.py:39-45`) — the frontend must run on one of those origins against this
backend without further config changes.

```
make health       # curl /health and /api/status
```

## Testing

```
make test          # pytest backend/tests -q      -> 113 passed
make e2e            # scripts/e2e_demo.py          -> 49 passed, 0 failed
```

Tests run against an isolated temp SQLite file
(`backend/tests/conftest.py:13-21`) and explicitly clear any provider API keys
from the environment before running (`conftest.py:18-21`) — the test suite
never touches a real provider, by design.

## Demo seed / reset

```
make seed    # or: make reset  — same target
make agent    # (via scripts/setup_cartesia_agent.py) — configures a Cartesia Managed Agent, requires CARTESIA_API_KEY
make preflight # scripts/discover_cartesia.py — verifies Cartesia account capability before a demo
make call      # scripts/smoke_call.py — places one real test call, requires full Cartesia config
make clean     # removes stillhere.db and its WAL/SHM files
```

`make seed`/`make reset` run `services/demo_reset.reset()` directly — equivalent
to `POST /api/demo/reset` but without going through the running server.

## What a from-scratch deployment would still need (not implemented here)

- A process manager / production ASGI server config (this build runs
  `uvicorn --reload`, a dev server).
- TLS termination — see `docs/SECURITY.md`.
- A public HTTPS URL for `PUBLIC_BASE_URL` so Cartesia's webhook tools can reach
  the backend — local dev requires a tunnel (ngrok/cloudflared); none is
  configured in this repo.
- Real `WEBHOOK_SECRET`/`DASHBOARD_TOKEN` values replacing the dev placeholders.
- A production database (this build is single-file SQLite; fine for a demo,
  not for concurrent multi-instance deployment).
- Twilio (or another SMS provider) if real text delivery is required instead
  of the labeled `simulated` notification path.
