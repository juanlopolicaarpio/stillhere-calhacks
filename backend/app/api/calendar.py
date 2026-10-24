"""Calendar connect/callback/status/disconnect routes (specs/CONTRACTS_V3.md §3).

Routes
    GET  /api/calendar/{provider}/auth?user_id=&write=
    GET  /api/calendar/{provider}/callback?code=&state=
    GET  /api/calendar/status?user_id=
    POST /api/calendar/disconnect
    POST /api/calendar/sync          (writes real free windows into `availability`)

TOKEN DISCIPLINE. Nothing in this file ever puts an access token, a refresh
token, an id_token or an authorization code in a response body, a log line, an
`agent_events` payload, or an exception message. The only grant representation
that leaves the backend is `StoredGrant.public()`, which carries scopes, expiry
and a boolean. `test_calendar.py` asserts this against every route.

HONEST DEGRADATION. With no OAuth client credentials both adapters report
`configured() is False`, `calendar_mode()` returns `"seeded"`, and the seeded
`availability` table stays the source of truth for
`services/scheduling.find_free_slots`. No route here claims a calendar is
connected when it is not.

The lead wires this in with:
    from .api import calendar as calendar_api
    app.include_router(calendar_api.router)
and can read `/api/status`'s new field from `calendar_api.calendar_mode()`.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

from ..adapters import google_calendar as gcal
from ..adapters import microsoft_graph as msgraph
from ..adapters.google_calendar import (
    CalendarAuthError,
    CalendarError,
    CalendarNotConfigured,
    CalendarRateLimitError,
    CalendarServerError,
    CalendarTokenError,
    CalendarTransportError,
)
from ..services import events as events_svc

router = APIRouter(prefix="/api/calendar", tags=["calendar"])

PROVIDERS: dict[str, Any] = {"google": gcal, "microsoft": msgraph}


# ---------------------------------------------------------------------------
# Error envelope (specs/CONTRACTS.md §4). `main.py` is lead-owned and has no
# handler for CalendarError, so the mapping lives here.
# ---------------------------------------------------------------------------

def _envelope(code: str, message: str, status: int) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


def _error_response(exc: Exception) -> JSONResponse:
    """Map an adapter error onto the shared envelope. Messages are already
    redacted by the adapters, so nothing secret can arrive here.
    """
    if isinstance(exc, CalendarNotConfigured):
        return _envelope("not_configured", str(exc), 503)
    if isinstance(exc, CalendarTokenError):
        return _envelope("reconnect_required", str(exc), 409)
    if isinstance(exc, CalendarAuthError):
        return _envelope("consent_denied", str(exc), 403)
    if isinstance(exc, CalendarRateLimitError):
        return _envelope("rate_limited", str(exc), 429)
    if isinstance(exc, (CalendarServerError, CalendarTransportError)):
        return _envelope("provider_error", str(exc), 502)
    if isinstance(exc, CalendarError):
        return _envelope("provider_error", str(exc), 502)
    raise exc


def _provider(name: str):
    mod = PROVIDERS.get((name or "").strip().lower())
    if mod is None:
        raise CalendarError(
            f"unknown calendar provider {name!r}; expected one of "
            f"{sorted(PROVIDERS)}"
        )
    return mod


# ---------------------------------------------------------------------------
# Honest mode reporting for /api/status
# ---------------------------------------------------------------------------


def calendar_mode(user_id: str | None = None) -> str:
    """`"google" | "microsoft" | "seeded"` for `/api/status`.

    A provider is only named when it is BOTH configured (real OAuth client
    credentials present) AND actually connected (a stored grant exists).
    Anything less is `"seeded"`, because the seeded `availability` table is
    then genuinely what scheduling is reading. Google wins a tie only because
    something has to; `calendar_status()` lists both honestly.
    """
    connected = set(gcal.connected_providers(user_id))
    if gcal.configured() and "google" in connected:
        return "google"
    if msgraph.configured() and "microsoft" in connected:
        return "microsoft"
    return "seeded"


def calendar_status(user_id: str | None = None) -> dict[str, Any]:
    """Full, token-free capability snapshot. Safe to log and safe to return."""
    mode = calendar_mode(user_id)
    return {
        "calendar_mode": mode,
        "providers": {
            "google": gcal.status(user_id),
            "microsoft": msgraph.status(user_id),
        },
        "availability_sources": gcal.AVAILABILITY_SOURCES,
        "note": (
            "Seeded availability is the source of truth; no real calendar is "
            "connected."
            if mode == "seeded"
            else f"Free/busy is read live from {mode} and written into the "
                 "availability table that scheduling already reads."
        ),
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/status")
def status(user_id: str | None = None):
    """Never fails and never lies. Reports `configured`/`connected` per provider."""
    return calendar_status(user_id)


@router.get("/{provider}/auth")
def auth(provider: str, user_id: str, write: bool = False, redirect: bool = False):
    """Build the provider consent URL.

    `write=false` (the default) asks for free/busy only. `write=true` is the
    SEPARATE, later consent step that also asks for permission to create the
    agreed event - never bundled into the first ask.

    Returns the URL as JSON by default so the frontend can show the user what
    they are about to consent to; `redirect=true` 302s straight there.
    """
    try:
        mod = _provider(provider)
        if not user_id:
            raise CalendarError("user_id is required")
        url = mod.auth_url(user_id, mod.default_redirect_uri(), write=bool(write))
    except Exception as exc:  # noqa: BLE001 - mapped to the error envelope
        return _error_response(exc)

    events_svc.emit(
        "calendar.auth_url_issued",
        f"Calendar consent link issued for {mod.PROVIDER}",
        payload={
            "provider": mod.PROVIDER,
            "user_id": user_id,
            "write_requested": bool(write),
            "scopes": list(mod.READ_SCOPES) + (list(mod.WRITE_SCOPES) if write else []),
        },
    )
    if redirect:
        return RedirectResponse(url, status_code=302)
    return {
        "provider": mod.PROVIDER,
        "auth_url": url,
        "redirect_uri": mod.default_redirect_uri(),
        "scopes_requested": list(mod.READ_SCOPES) + (list(mod.WRITE_SCOPES) if write else []),
        "write_consent": bool(write),
        "consent_note": (
            "Free/busy only: Still Here can see when you are busy, never what "
            "the events are."
            if not write
            else "This also lets Still Here add the agreed plan to your calendar."
        ),
    }


@router.get("/{provider}/callback")
def callback(
    provider: str,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
):
    """Finish the OAuth dance and store the grant.

    The user id comes from the HMAC-signed `state`, never from a query
    parameter, so a forged callback cannot attach someone else's calendar to
    another user's row. The response carries scopes and expiry - never a token.
    """
    try:
        mod = _provider(provider)
        if error:
            # The user declined, or the provider refused. A decline is a
            # first-class outcome here, not an exception to retry.
            events_svc.emit(
                "calendar.connect_declined",
                f"Calendar connection not completed for {mod.PROVIDER}",
                payload={"provider": mod.PROVIDER, "error": error},
            )
            return _envelope(
                "consent_denied",
                f"{mod.PROVIDER} did not grant access: {error}"
                + (f" ({error_description})" if error_description else ""),
                403,
            )
        if not code:
            raise CalendarError("callback is missing the authorization code")
        if not state:
            raise CalendarAuthError("callback is missing the oauth state")
        claims = gcal.parse_state(state)
        user_id = str(claims.get("u") or "")
        if not user_id:
            raise CalendarAuthError("oauth state carried no user id")
        if str(claims.get("p") or "") != mod.PROVIDER:
            raise CalendarAuthError("oauth state was issued for a different provider")

        tokens = mod.exchange_code(code, mod.default_redirect_uri())
        gcal.save_tokens(user_id, mod.PROVIDER, tokens)
        grant = gcal.load_tokens(user_id, mod.PROVIDER)
    except Exception as exc:  # noqa: BLE001
        return _error_response(exc)

    assert grant is not None  # just stored
    public = grant.public()
    events_svc.emit(
        "calendar.connected",
        f"{mod.PROVIDER} calendar connected",
        payload={"provider": mod.PROVIDER, "user_id": user_id,
                 "scopes": public["scopes"], "token_storage": public["token_storage"]},
    )
    return {"ok": True, "provider": mod.PROVIDER, "connected": True, "grant": public}


class DisconnectBody(BaseModel):
    user_id: str = Field(min_length=1)
    provider: str | None = Field(default=None, pattern="^(google|microsoft)$")


@router.post("/disconnect")
def disconnect(body: DisconnectBody):
    """Forget the stored grant (and revoke at the provider where possible).

    Always succeeds locally: someone asking to disconnect ends up disconnected
    here even if the provider call fails.
    """
    try:
        results: list[dict[str, Any]] = []
        targets = [body.provider] if body.provider else list(PROVIDERS)
        for name in targets:
            mod = _provider(str(name))
            results.append(mod.revoke(body.user_id))
    except Exception as exc:  # noqa: BLE001
        return _error_response(exc)

    removed = sum(int(r.get("rows_removed") or 0) for r in results)
    events_svc.emit(
        "calendar.disconnected",
        "Calendar grant(s) removed",
        payload={"user_id": body.user_id, "providers": targets, "rows_removed": removed},
    )
    return {"ok": True, "rows_removed": removed, "results": results,
            "calendar_mode": calendar_mode(body.user_id)}


class SyncBody(BaseModel):
    user_id: str = Field(min_length=1)
    provider: str | None = Field(default=None, pattern="^(google|microsoft)$")
    window_start: str | None = None
    window_end: str | None = None
    min_minutes: int = Field(default=60, ge=5, le=1440)


@router.post("/sync")
def sync(body: SyncBody):
    """Pull real free/busy and write the inverted free windows into
    `availability`, so `scheduling.find_free_slots` - and therefore group
    planning - works on real calendar data with no changes.

    Only rows whose `source` is this provider are replaced; seeded rows are
    never touched.
    """
    try:
        mode = calendar_mode(body.user_id)
        name = body.provider or (mode if mode != "seeded" else None)
        if not name:
            raise CalendarNotConfigured(
                "no calendar is connected for this user, so there is nothing to "
                "sync; seeded availability remains the source of truth"
            )
        mod = _provider(name)
        start = body.window_start or datetime.now(timezone.utc).isoformat()
        end = body.window_end or (
            datetime.now(timezone.utc) + timedelta(days=14)
        ).isoformat()
        written = mod.sync_free_busy(
            body.user_id, start, end, min_minutes=body.min_minutes
        )
    except Exception as exc:  # noqa: BLE001
        return _error_response(exc)

    events_svc.emit(
        "calendar.synced",
        f"{mod.PROVIDER} free/busy synced into availability",
        payload={
            "provider": mod.PROVIDER,
            "user_id": body.user_id,
            "free_windows": len(written),
            "window_start": start,
            "window_end": end,
        },
    )
    return {
        "ok": True,
        "provider": mod.PROVIDER,
        "source": gcal.AVAILABILITY_SOURCES.get(mod.PROVIDER, mod.PROVIDER),
        "window": {"start": start, "end": end},
        "free_windows": written,
        "calendar_mode": calendar_mode(body.user_id),
    }
