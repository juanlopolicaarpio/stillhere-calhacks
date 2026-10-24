"""Still Here - FastAPI application entrypoint."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .config import get_settings
from .db import init_db, q1
from .domain.state_machine import TransitionError
from .services import events as events_svc
from .services.consent import ConsentError
from .services.memory import MemoryError_
from .services.plans import PlanError
from .services.voice import VoiceError


def error(code: str, message: str, status: int) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    events_svc.bind_loop(asyncio.get_running_loop())
    yield


app = FastAPI(
    title="Still Here API",
    version="1.0.0",
    description="Opt-in, consent-gated AI companion for social reconnection.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- error envelope per specs/CONTRACTS.md section 4 ---
@app.exception_handler(ConsentError)
async def _consent_err(_: Request, exc: ConsentError):
    return error("validation_error", str(exc), 400)


@app.exception_handler(VoiceError)
async def _voice_err(_: Request, exc: VoiceError):
    msg = str(exc)
    if "consent denied" in msg:
        return error("consent_denied", msg, 403)
    return error("provider_error", msg, 502)


@app.exception_handler(TransitionError)
async def _transition_err(_: Request, exc: TransitionError):
    return error("illegal_transition", str(exc), 409)


@app.exception_handler(PlanError)
async def _plan_err(_: Request, exc: PlanError):
    return error("not_found", str(exc), 404)


@app.exception_handler(MemoryError_)
async def _memory_err(_: Request, exc: MemoryError_):
    return error("validation_error", str(exc), 400)


@app.exception_handler(ValueError)
async def _value_err(_: Request, exc: ValueError):
    return error("validation_error", str(exc), 400)


@app.get("/health")
def health():
    s = get_settings()
    try:
        q1("SELECT 1 AS ok")
        db_ok = "ok"
    except Exception as e:  # pragma: no cover
        db_ok = f"error: {e}"
    return {"status": "ok", "db": db_ok, "voice_mode": s.voice_mode()}


@app.get("/api/status")
def status():
    """Capability snapshot. The UI's honesty banner reads this.

    Three independent mode flags, each answering one question truthfully:
      voice_mode        - is the phone call real?
      coordination_mode - is shared state really in SpacetimeDB?
      conversation_mode - is the dialogue really driven by Claude?
    """
    s = get_settings()
    snapshot = s.redacted()

    notes: list[str] = []
    id_problems = s.cartesia_id_problems()
    if snapshot["voice_mode"] == "cartesia_pstn":
        notes.append(
            "Real PSTN calling via Cartesia Managed Agents is configured "
            "(run `make preflight` to confirm the agent exists on the account)."
        )
    elif id_problems:
        notes.append("Cartesia is misconfigured: " + " ".join(id_problems))
    else:
        notes.append(
            "No real phone call can be placed. Voice runs in the local simulator. "
            "Set CARTESIA_API_KEY, CARTESIA_AGENT_ID and CARTESIA_FROM_NUMBER_ID "
            "to enable real calls."
        )

    if snapshot["coordination_mode"] == "spacetimedb":
        notes.append(
            f"Shared coordination state is authoritative in SpacetimeDB "
            f"(database {s.spacetimedb_db!r})."
        )
    else:
        notes.append(
            "SpacetimeDB is NOT reachable; coordination is running on the SQLite "
            "fallback. Run 'make stdb' to publish the module."
        )

    if snapshot["conversation_mode"] == "claude":
        notes.append("Conversation is interpreted by Claude with tool calling.")
    else:
        notes.append(
            "No ANTHROPIC_API_KEY, so conversation runs a labeled deterministic "
            "fallback rather than Claude interpretation."
        )

    from .services import clock

    cs = clock.offset_state()
    if cs.simulated:
        notes.append(cs.note)

    return {
        **snapshot,
        "real_call_possible": s.cartesia_call_ready,
        "simulated_clock": cs.simulated,
        "clock_offset_days": cs.offset_days,
        "simulated_now": cs.simulated_now,
        "notes": " ".join(notes),
    }


from .api import calendar as calendar_api  # noqa: E402
from .api import companion as companion_api  # noqa: E402
from .api import demo as demo_api  # noqa: E402
from .api import events as events_api  # noqa: E402
from .api import inbound as inbound_api  # noqa: E402
from .api import simulate as simulate_api  # noqa: E402
from .api import onboarding as onboarding_api  # noqa: E402
from .api import voice as voice_api  # noqa: E402

app.include_router(onboarding_api.router)
app.include_router(calendar_api.router)
app.include_router(companion_api.router)
app.include_router(demo_api.router)
app.include_router(events_api.router)
app.include_router(inbound_api.router)
app.include_router(simulate_api.router)
app.include_router(voice_api.router)
