"""Cartesia Managed Agents adapter - the real PSTN outbound call path.

Verified against cartesia-python 4.2.0 and the Cartesia HTTP API
(cartesia-version 2026-08-14) on 2026-10-24.

IMPORTANT FINDINGS (see docs/TOOL_CARTESIA.md, docs/CARTESIA_SETUP.md):
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
  * VERIFIED 2026-10-24 (docs.cartesia.ai/api-reference/agents/calls/cancel-call):
    there IS a real cancel/hangup endpoint: `POST /agents/calls/{call_id}/cancel`,
    Cartesia-Version header, bearer auth, no body. Only calls in status
    `started` can be cancelled; the call then ends with `end_reason:
    "api_cancelled"`. Response: `{"cancelled": true}`.
  * VERIFIED 2026-10-24 (docs.cartesia.ai/line/infrastructure/observability#webhooks,
    docs.cartesia.ai/agents/tools.md, docs.cartesia.ai/agents/webhook-tools.md,
    docs.cartesia.ai/api-reference/agents/webhooks/create.md): Cartesia does
    **NOT** offer cryptographic (HMAC/timestamped) webhook signature
    verification anywhere in the Managed Agents product today:
      - The `agents/webhooks` subscription resource (for call-lifecycle
        events) takes a `secret` at creation time and echoes that exact
        string back, verbatim, in an `X-Webhook-Secret` header on every
        delivery. There is no signing algorithm, no timestamp, no nonce -
        it is a plain shared-secret comparison, not a signature.
      - Per-tool "webhook tools" (the four tools this agent calls mid-call)
        instead let you attach `authentication: {type: bearer | basic_auth}`
        to the outbound request Cartesia makes to our server, which again is
        just a shared credential in the `Authorization` header, not a
        signature over the body.
    This repo's existing `X-StillHere-Secret` shared-secret header is
    therefore already the same security class as anything Cartesia natively
    offers. `verify_webhook_signature()` below formalizes that comparison
    (and additionally recognizes Cartesia's own `X-Webhook-Secret` header
    name, in case the lead registers a native webhook subscription) so there
    is one well-tested place doing the comparison, but it does not and
    cannot upgrade this to real cryptographic verification - see
    docs/CARTESIA_SETUP.md ("Webhook authentication") for the full
    replay-resistance discussion.

This module NEVER decides whether a call is permitted. `services/voice.py`
enforces consent before calling in here.
"""
from __future__ import annotations

import hmac
import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Mapping

import httpx

from ..config import get_settings


class CartesiaNotConfigured(Exception):
    """Raised when a real call is requested but credentials are absent."""


class CartesiaCallError(Exception):
    def __init__(self, message: str, *, status: int | None = None, body: Any = None):
        super().__init__(message)
        self.status = status
        self.body = body


class CartesiaAuthError(CartesiaCallError):
    """The provider rejected our credentials (HTTP 401/403)."""


class CartesiaRateLimitError(CartesiaCallError):
    """The provider throttled us (HTTP 429)."""


class CartesiaServerError(CartesiaCallError):
    """The provider failed on its own side (HTTP 5xx)."""


class CartesiaTransportError(CartesiaCallError):
    """The request never got a response at all (DNS/TCP/TLS/timeout)."""


class CartesiaValidationError(CartesiaCallError):
    """Adapter-level input validation failed before any network call was made."""


@dataclass
class OutboundCallResult:
    ok: bool
    provider_call_id: str | None
    to_number: str
    error: str | None = None
    http_status: int | None = None
    raw: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Shared request plumbing: timeouts, bounded retries, typed errors
# ---------------------------------------------------------------------------

# Only retry things that are plausibly transient AND safe to retry. 429/5xx and
# bare transport failures (DNS/TCP/TLS/timeout - no response at all) qualify.
# 4xx (other than 429) never does: retrying a malformed/unauthorized request
# just repeats the same failure.
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}
_RETRY_BACKOFF_SECONDS = 0.05  # kept small so tests stay fast


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


_DIGITS_RE = re.compile(r"\+?\d{7,15}")


def _redact_payload(payload: Any) -> Any:
    """Recursively strip full phone numbers out of a provider response body.

    Defence in depth: Cartesia's outbound-call response echoes `to_number`/
    `number` straight back in `calls[]`, and `OutboundCallResult.raw` is both
    returned to callers AND persisted by `services/voice.py` into
    `calls.raw_provider_response` plus `agent_events.payload_json`. Neither
    may ever carry an un-redacted number, so every payload is scrubbed before
    it leaves this module - by key name first, then by a generic digit-run
    pattern as a backstop for shapes we haven't anticipated.
    """
    if isinstance(payload, dict):
        out: dict[str, Any] = {}
        for k, v in payload.items():
            if k in ("number", "to_number", "from_number", "phone_number") and isinstance(v, str):
                out[k] = _redact_number(v)
            else:
                out[k] = _redact_payload(v)
        return out
    if isinstance(payload, list):
        return [_redact_payload(v) for v in payload]
    if isinstance(payload, str):
        return _DIGITS_RE.sub(lambda m: _redact_number(m.group(0)), payload)
    return payload


def _request(
    method: str,
    url: str,
    *,
    timeout: float,
    max_retries: int = 2,
    json_body: dict[str, Any] | None = None,
) -> httpx.Response:
    """POST/GET with bounded retries on transport errors and 429/5xx only.

    Never used for `place_outbound_call` - see the note there on why dialing
    is deliberately NOT retried.
    """
    attempts = max_retries + 1
    last_exc: httpx.HTTPError | None = None
    resp: httpx.Response | None = None
    for attempt in range(attempts):
        try:
            with httpx.Client(timeout=timeout) as client:
                resp = client.request(method, url, headers=_headers(), json=json_body)
        except httpx.HTTPError as e:
            last_exc = e
            if attempt < attempts - 1:
                time.sleep(_RETRY_BACKOFF_SECONDS)
                continue
            raise CartesiaTransportError(
                f"{method} {url} failed after {attempt + 1} attempt(s): {e}"
            ) from e

        if resp.status_code in _RETRYABLE_STATUS and attempt < attempts - 1:
            time.sleep(_RETRY_BACKOFF_SECONDS)
            continue
        return resp

    # Unreachable in practice (the loop above always returns or raises), but
    # keeps type checkers honest.
    if last_exc is not None:
        raise CartesiaTransportError(str(last_exc)) from last_exc
    assert resp is not None
    return resp


def _raise_for_status(resp: httpx.Response, action: str) -> None:
    if resp.status_code < 400:
        return
    body = resp.text[:500]
    if resp.status_code in (401, 403):
        raise CartesiaAuthError(
            f"{action}: credentials rejected (HTTP {resp.status_code})",
            status=resp.status_code, body=body,
        )
    if resp.status_code == 429:
        raise CartesiaRateLimitError(
            f"{action}: rate limited (HTTP 429)", status=429, body=body,
        )
    if resp.status_code >= 500:
        raise CartesiaServerError(
            f"{action}: provider error (HTTP {resp.status_code})",
            status=resp.status_code, body=body,
        )
    raise CartesiaCallError(
        f"{action}: HTTP {resp.status_code}: {body}", status=resp.status_code, body=body,
    )


def _parse_json(resp: httpx.Response, action: str) -> Any:
    try:
        return resp.json()
    except (json.JSONDecodeError, ValueError) as e:
        raise CartesiaCallError(
            f"{action}: provider returned non-JSON body: {resp.text[:300]!r}",
            status=resp.status_code, body=resp.text[:500],
        ) from e


# ---------------------------------------------------------------------------
# Outbound dialing
# ---------------------------------------------------------------------------

_E164_RE = re.compile(r"^\+[1-9]\d{7,14}$")  # matches services/consent.py::E164 exactly


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

    Deliberately NOT retried on transport failure or 5xx, unlike every other
    function in this module. A dial is not idempotent and not safe to repeat
    blindly: if the request reached Cartesia but the response was lost (e.g.
    a timeout), retrying could ring a real person's phone twice. An ambiguous
    outcome here must surface to the caller as a failure, not be silently
    retried.
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
    if not _E164_RE.match(to_number or ""):
        raise CartesiaValidationError(
            f"to_number is not valid E.164: {_redact_number(to_number)!r}"
        )

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
    payload = _redact_payload(payload) if isinstance(payload, (dict, list)) else payload

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


def get_call(call_id: str, *, timeout: float = 15.0, max_retries: int = 2) -> dict[str, Any]:
    """Fetch live call status. Used to record real ring/answer/end timestamps.

    Idempotent read - safe to retry on transport error / 429 / 5xx.
    """
    s = get_settings()
    url = f"{s.cartesia_base_url}/agents/calls/{call_id}"
    resp = _request("GET", url, timeout=timeout, max_retries=max_retries)
    _raise_for_status(resp, "get_call")
    return _parse_json(resp, "get_call")


# Verified AgentCall shape (docs.cartesia.ai/api-reference/agents/calls/get-call,
# fetched 2026-10-24): status in {created, started, completed, failed};
# end_reason in {agent_hangup, voicemail_detected, client_hangup, api_cancelled,
# max_duration, call_inactivity, dial_no_answer, dial_busy, agent_error,
# network_error, ...}.
_END_REASON_TO_LOCAL_STATUS = {
    "dial_no_answer": "no_answer",
    "dial_busy": "busy",
    "voicemail_detected": "voicemail",
    "api_cancelled": "canceled",
}


def map_call_status(raw: dict[str, Any]) -> dict[str, Any]:
    """Map a Cartesia `AgentCall` object onto Still Here's own status vocabulary
    (`requested|ringing|answered|completed|canceled|failed|no_answer|busy|
    voicemail|unknown`), mirroring the mapping `api/voice.py::provider_webhook`
    already applies to webhook payloads. Provided so `get_call()` results can
    be reduced the same way without duplicating the table in two places.

    This function is informational only - it never writes to the database and
    never raises; an unrecognised shape maps to "unknown" rather than failing.
    """
    status = str(raw.get("status") or "").strip().lower()
    end_reason = str(raw.get("end_reason") or "").strip().lower()

    if status == "created":
        local = "requested"
    elif status == "started":
        local = "answered" if raw.get("start_time") else "ringing"
    elif status == "completed":
        local = _END_REASON_TO_LOCAL_STATUS.get(end_reason, "completed")
    elif status == "failed":
        local = "failed"
    else:
        local = "unknown"

    return {
        "status": local,
        "provider_status": status or None,
        "end_reason": end_reason or None,
    }


def cancel_call(call_id: str, *, timeout: float = 15.0, max_retries: int = 1) -> dict[str, Any]:
    """Hang up / cancel an in-progress call.

    VERIFIED real endpoint (docs.cartesia.ai/api-reference/agents/calls/
    cancel-call, fetched 2026-10-24): `POST {base}/agents/calls/{call_id}/cancel`,
    `Cartesia-Version` header, bearer/API-key auth, no request body. Only a
    call in status `started` can be cancelled; it then ends with
    `end_reason: "api_cancelled"`. Response on success: `{"cancelled": true}`.
    Cancelling is idempotent in effect (a second cancel on an already-ended
    call simply 400s), so unlike dialing this IS safe to retry.
    """
    s = get_settings()
    if not s.cartesia_api_key:
        raise CartesiaNotConfigured("CARTESIA_API_KEY is not set")
    url = f"{s.cartesia_base_url}/agents/calls/{call_id}/cancel"
    resp = _request("POST", url, timeout=timeout, max_retries=max_retries)
    payload = _parse_json(resp, "cancel_call") if resp.status_code < 400 else None
    if resp.status_code >= 400:
        _raise_for_status(resp, "cancel_call")
    return {"ok": True, "cancelled": bool((payload or {}).get("cancelled")), "raw": payload}


def list_agents(*, timeout: float = 15.0, max_retries: int = 2) -> dict[str, Any]:
    s = get_settings()
    url = f"{s.cartesia_base_url}/agents"
    resp = _request("GET", url, timeout=timeout, max_retries=max_retries)
    _raise_for_status(resp, "list_agents")
    return _parse_json(resp, "list_agents")


def list_phone_numbers(*, timeout: float = 15.0, max_retries: int = 2) -> dict[str, Any]:
    """Find `from_number_id`.

    The account-wide endpoint is preferred; the per-agent endpoint is
    deprecated but kept as a fallback for older accounts.
    """
    s = get_settings()
    if not s.cartesia_api_key:
        raise CartesiaNotConfigured("CARTESIA_API_KEY is not set")
    errors: list[str] = []
    for path in ("/agents/phone-numbers", "/agents/phone_numbers"):
        resp = _request(
            "GET", f"{s.cartesia_base_url}{path}", timeout=timeout, max_retries=max_retries
        )
        if resp.status_code < 400:
            return _parse_json(resp, "list_phone_numbers")
        errors.append(f"{path} -> HTTP {resp.status_code} {resp.text[:200]}")

    if s.cartesia_agent_id:
        resp = _request(
            "GET",
            f"{s.cartesia_base_url}/agents/{s.cartesia_agent_id}/phone-numbers",
            timeout=timeout, max_retries=max_retries,
        )
        if resp.status_code < 400:
            return _parse_json(resp, "list_phone_numbers")
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


# ---------------------------------------------------------------------------
# Webhook authentication
# ---------------------------------------------------------------------------


def verify_webhook_signature(headers: Mapping[str, str], secret: str) -> bool:
    """Verify an inbound webhook delivery claiming to be from Cartesia.

    HONEST NAME WARNING: despite the name (kept for symmetry with other
    providers' APIs), this is NOT a cryptographic signature check. As of
    2026-10-24, Cartesia's Managed Agents platform has no HMAC/timestamped
    webhook signing anywhere:
      - Its native call-lifecycle `agents/webhooks` subscription resource
        takes a `secret` string at creation and echoes it back UNCHANGED in
        an `X-Webhook-Secret` header on every delivery
        (docs.cartesia.ai/line/infrastructure/observability#webhooks).
      - Its per-tool "webhook tools" instead let you attach `bearer`/
        `basic_auth` credentials to the `Authorization` header Cartesia sends
        when it calls your tool endpoint (docs.cartesia.ai/agents/tools.md,
        docs.cartesia.ai/agents/webhook-tools.md). Also a plain credential,
        not a signature.
    Both mechanisms reduce to "does this header carry the secret we share
    with Cartesia", with no timestamp or nonce, so a captured header value
    could be replayed indefinitely until the secret is rotated. This function
    checks both the native `X-Webhook-Secret` header AND, for parity with
    this repo's existing `X-StillHere-Secret` convention, is deliberately
    header-name-flexible - callers pass whatever headers they received and
    this looks for either. See docs/CARTESIA_SETUP.md ("Webhook
    authentication") for the replay-resistance discussion and mitigations
    (HTTPS-only transport, secret rotation, and the fact that every tool
    endpoint this repo exposes is already idempotent per (call_id, tool,
    payload), which bounds the damage a replay can do to "re-read the same
    cached result", not "re-trigger a state change").

    Returns False (never raises) on any missing/empty input.
    """
    if not secret:
        return False
    lower = {str(k).lower(): v for k, v in (headers or {}).items()}
    candidate = lower.get("x-webhook-secret") or lower.get("x-stillhere-secret")
    if not candidate:
        return False
    return hmac.compare_digest(str(candidate), str(secret))
