"""Cartesia Managed Agents adapter - the real PSTN outbound call path.

Verified against cartesia-python 4.2.0 and the Cartesia HTTP API
(cartesia-version 2026-08-14) on 2026-10-24.

IMPORTANT FINDINGS (see docs/TOOL_CARTESIA.md):
  * cartesia-python 4.2.0 exposes agents.list / agents.retrieve / agents.update /
    agents.calls.list / agents.calls.retrieve / agents.calls.download_audio.
    It does NOT expose a create method for agents or for outbound calls.
  * Outbound dialing is therefore done over raw HTTP:
        POST https://api.cartesia.ai/agents/calls
    body: {from_number_id, agent_id, ringing_timeout_seconds, outbound_calls:[...]}
    resp: {calls:[{number, agent_call_id}]}  OR  {calls:[{number, error}]}
  * Per-call context is passed as `dynamic_variables`, which is how permissioned
    memory reaches the live agent WITHOUT private data ever leaving the backend.
  * Build on Managed Agents, not the Line SDK: Cartesia stops hosting Line
    agents on 2026-12-01.

This module NEVER decides whether a call is permitted. `services/voice.py`
enforces consent before calling in here.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import httpx

from ..config import get_settings


class CartesiaNotConfigured(Exception):
    """Raised when a real call is requested but credentials are absent."""


class CartesiaCallError(Exception):
    def __init__(self, message: str, *, status: int | None = None, body: Any = None):
        super().__init__(message)
        self.status = status
        self.body = body


@dataclass
class OutboundCallResult:
    ok: bool
    provider_call_id: str | None
    to_number: str
    error: str | None = None
    http_status: int | None = None
    raw: dict[str, Any] = field(default_factory=dict)


def _headers() -> dict[str, str]:
    s = get_settings()
    if not s.cartesia_api_key:
        raise CartesiaNotConfigured("CARTESIA_API_KEY is not set")
    return {
        # The REST API authenticates with X-API-Key; the Python SDK uses a
        # Bearer header. We send both so either gateway path authenticates.
        "X-API-Key": s.cartesia_api_key,
        "Authorization": f"Bearer {s.cartesia_api_key}",
        "Cartesia-Version": s.cartesia_version,
        "Content-Type": "application/json",
    }


def _redact_number(n: str) -> str:
    return f"***{n[-4:]}" if n and len(n) >= 4 else "***"


def place_outbound_call(
    to_number: str,
    *,
    agent_id: str | None = None,
    from_number_id: str | None = None,
    dynamic_variables: dict[str, Any] | None = None,
    ringing_timeout_seconds: int = 45,
    timeout: float = 30.0,
) -> OutboundCallResult:
    """Place ONE real outbound PSTN call via Cartesia Managed Agents.

    Returns an OutboundCallResult rather than raising for provider-level
    rejections, because the API reports per-number failures inside a 200
    response ("do not rely on response status alone").
    """
    s = get_settings()
    agent = agent_id or s.cartesia_agent_id
    from_id = from_number_id or s.cartesia_from_number_id

    if not s.cartesia_api_key:
        raise CartesiaNotConfigured("CARTESIA_API_KEY is not set")
    if not agent:
        raise CartesiaNotConfigured("CARTESIA_AGENT_ID is not set")
    if not from_id:
        raise CartesiaNotConfigured("CARTESIA_FROM_NUMBER_ID is not set")

    body: dict[str, Any] = {
        "agent_id": agent,
        "from_number_id": from_id,
        "ringing_timeout_seconds": ringing_timeout_seconds,
        "outbound_calls": [
            {
                "to_number": to_number,
                "dynamic_variables": dynamic_variables or {},
            }
        ],
    }

    url = f"{s.cartesia_base_url}/agents/calls"
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(url, headers=_headers(), json=body)
    except httpx.HTTPError as e:
        return OutboundCallResult(
            ok=False,
            provider_call_id=None,
            to_number=_redact_number(to_number),
            error=f"transport error: {e}",
        )

    try:
        payload = resp.json()
    except (json.JSONDecodeError, ValueError):
        payload = {"_raw_text": resp.text[:800]}

    if resp.status_code >= 400:
        return OutboundCallResult(
            ok=False,
            provider_call_id=None,
            to_number=_redact_number(to_number),
            error=f"HTTP {resp.status_code}: {json.dumps(payload)[:500]}",
            http_status=resp.status_code,
            raw=payload if isinstance(payload, dict) else {},
        )

    calls = (payload or {}).get("calls") or []
    if not calls:
        return OutboundCallResult(
            ok=False,
            provider_call_id=None,
            to_number=_redact_number(to_number),
            error=f"provider returned no call entries: {json.dumps(payload)[:400]}",
            http_status=resp.status_code,
            raw=payload if isinstance(payload, dict) else {},
        )

    entry = calls[0]
    if entry.get("error"):
        return OutboundCallResult(
            ok=False,
            provider_call_id=None,
            to_number=_redact_number(to_number),
            error=str(entry["error"]),
            http_status=resp.status_code,
            raw=payload,
        )

    call_id = entry.get("agent_call_id")
    if not call_id:
        return OutboundCallResult(
            ok=False,
            provider_call_id=None,
            to_number=_redact_number(to_number),
            error=f"no agent_call_id in response entry: {entry}",
            http_status=resp.status_code,
            raw=payload,
        )

    return OutboundCallResult(
        ok=True,
        provider_call_id=call_id,
        to_number=_redact_number(to_number),
        http_status=resp.status_code,
        raw=payload,
    )


def get_call(call_id: str, *, timeout: float = 15.0) -> dict[str, Any]:
    """Fetch live call status. Used to record real ring/answer/end timestamps."""
    s = get_settings()
    url = f"{s.cartesia_base_url}/agents/calls/{call_id}"
    with httpx.Client(timeout=timeout) as client:
        resp = client.get(url, headers=_headers())
    if resp.status_code >= 400:
        raise CartesiaCallError(
            f"get_call failed: HTTP {resp.status_code}",
            status=resp.status_code,
            body=resp.text[:500],
        )
    return resp.json()


def list_agents(*, timeout: float = 15.0) -> dict[str, Any]:
    s = get_settings()
    with httpx.Client(timeout=timeout) as client:
        resp = client.get(f"{s.cartesia_base_url}/agents", headers=_headers())
    if resp.status_code >= 400:
        raise CartesiaCallError(
            f"list_agents failed: HTTP {resp.status_code}",
            status=resp.status_code,
            body=resp.text[:500],
        )
    return resp.json()


def list_phone_numbers(*, timeout: float = 15.0) -> dict[str, Any]:
    """Find `from_number_id`.

    The account-wide endpoint is preferred; the per-agent endpoint is
    deprecated but kept as a fallback for older accounts.
    """
    s = get_settings()
    errors: list[str] = []
    with httpx.Client(timeout=timeout) as client:
        for path in ("/agents/phone-numbers", "/agents/phone_numbers"):
            resp = client.get(f"{s.cartesia_base_url}{path}", headers=_headers())
            if resp.status_code < 400:
                return resp.json()
            errors.append(f"{path} -> HTTP {resp.status_code} {resp.text[:200]}")

        if s.cartesia_agent_id:
            resp = client.get(
                f"{s.cartesia_base_url}/agents/{s.cartesia_agent_id}/phone-numbers",
                headers=_headers(),
            )
            if resp.status_code < 400:
                return resp.json()
            errors.append(f"per-agent -> HTTP {resp.status_code} {resp.text[:200]}")

    raise CartesiaCallError("could not list phone numbers: " + " | ".join(errors))


def synthesize_wav(text: str, out_path: str) -> str:
    """Sonic TTS via the official SDK. Used for the voice-preview feature and
    as evidence that the Cartesia SDK path works even before telephony is set up.
    """
    from cartesia import Cartesia

    s = get_settings()
    if not s.cartesia_api_key:
        raise CartesiaNotConfigured("CARTESIA_API_KEY is not set")
    client = Cartesia(api_key=s.cartesia_api_key)
    response = client.tts.generate(
        model_id=s.cartesia_tts_model,
        transcript=text,
        voice=s.cartesia_voice_id,
        output_format={
            "container": "wav",
            "encoding": "pcm_f32le",
            "sample_rate": 44100,
        },
    )
    response.write_to_file(out_path)
    return out_path
