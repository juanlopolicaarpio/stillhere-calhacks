"""Live event feed. SSE stream + replay endpoint.

Only events that a backend function actually emitted are served here. There is
no synthetic or replayed demo data path.

Auth note: the browser's native EventSource cannot set custom headers, so these
endpoints accept the dashboard token EITHER as the `X-Dashboard-Token` header
OR as a `dashboard_token` query parameter. The query-param form exists
specifically for the SSE connection.
"""
from __future__ import annotations

import asyncio
import json
import secrets

from fastapi import APIRouter, Header, HTTPException, Request
from sse_starlette.sse import EventSourceResponse

from ..config import get_settings
from ..services import events as events_svc

router = APIRouter(prefix="/api", tags=["events"])


def _auth_dashboard(header_token: str | None, query_token: str | None) -> None:
    expected = get_settings().dashboard_token
    supplied = header_token or query_token
    if not supplied or not secrets.compare_digest(str(supplied), str(expected)):
        raise HTTPException(
            status_code=401,
            detail={"error": {"code": "unauthorized",
                              "message": "dashboard token required"}},
        )


@router.get("/events/recent")
def recent(
    limit: int = 200,
    dashboard_token: str | None = None,
    x_dashboard_token: str | None = Header(default=None),
):
    _auth_dashboard(x_dashboard_token, dashboard_token)
    return events_svc.recent(min(max(limit, 1), 1000))


@router.get("/events/stream")
async def stream(
    request: Request,
    replay: int = 50,
    dashboard_token: str | None = None,
    x_dashboard_token: str | None = Header(default=None),
):
    """SSE stream of real agent events.

    Sends the last `replay` persisted events first so a dashboard opened
    mid-demo is accurate, then streams live ones.
    """
    _auth_dashboard(x_dashboard_token, dashboard_token)
    queue = events_svc.subscribe()

    async def gen():
        try:
            for evt in events_svc.recent(replay):
                yield {"event": "agent_event", "data": json.dumps(evt)}
            while True:
                if await request.is_disconnected():
                    break
                try:
                    evt = await asyncio.wait_for(queue.get(), timeout=15.0)
                    yield {"event": "agent_event", "data": json.dumps(evt)}
                except asyncio.TimeoutError:
                    # Keep-alive so proxies don't drop an idle demo stream.
                    yield {"event": "ping", "data": "{}"}
        finally:
            events_svc.unsubscribe(queue)

    return EventSourceResponse(gen())
