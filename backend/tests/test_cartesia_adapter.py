"""Unit tests for `app.adapters.cartesia_adapter`.

Everything here runs with NO network access: `httpx.Client` is replaced by a
small fake that pops pre-queued `httpx.Response` objects (or exceptions) in
order. This lets us exercise retry counts, typed-error mapping, and the
"don't trust HTTP status alone" provider contract deterministically.

`conftest.py`'s autouse `fresh_db` fixture already pops `CARTESIA_API_KEY`
from the environment, and `get_settings()` is `@lru_cache`d, so "configured"
tests monkeypatch the cached `Settings` instance's attributes directly rather
than the environment (env vars are read once at class-definition time, not
per instantiation).
"""
from __future__ import annotations

import pytest
import httpx

from app.adapters import cartesia_adapter as ca
from app.config import get_settings

GOOD_NUMBER = "+14155551234"
FAKE_KEY = "sk_test_do_not_use_1234567890"


# ---------------------------------------------------------------------------
# Fake transport
# ---------------------------------------------------------------------------

def _resp(status: int, json_body=None, text: str | None = None) -> httpx.Response:
    req = httpx.Request("GET", "https://api.cartesia.ai/x")
    if text is not None:
        return httpx.Response(status, text=text, request=req)
    return httpx.Response(status, json=json_body if json_body is not None else {}, request=req)


def _make_fake_client(queue: list):
    """Factory for a drop-in `httpx.Client` replacement bound to `queue`.

    Each `.request()/.get()/.post()` call pops the next queued item: either
    an `httpx.Response` to return, or an `Exception` instance to raise
    (simulating a transport failure).
    """

    class FakeClient:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def _next(self):
            if not queue:
                raise AssertionError("FakeClient queue exhausted: unexpected extra HTTP call")
            item = queue.pop(0)
            if isinstance(item, Exception):
                raise item
            return item

        def request(self, method, url, headers=None, json=None):
            return self._next()

        def get(self, url, headers=None):
            return self._next()

        def post(self, url, headers=None, json=None):
            return self._next()

    return FakeClient


@pytest.fixture
def queue():
    return []


@pytest.fixture
def fake_transport(monkeypatch, queue):
    monkeypatch.setattr(ca.httpx, "Client", _make_fake_client(queue))
    return queue


@pytest.fixture
def configured(monkeypatch):
    """A Cartesia-call-ready Settings instance, patched in place."""
    s = get_settings()
    monkeypatch.setattr(s, "cartesia_api_key", FAKE_KEY)
    monkeypatch.setattr(s, "cartesia_agent_id", "agent_test_1")
    monkeypatch.setattr(s, "cartesia_from_number_id", "num_test_1")
    return s


# ---------------------------------------------------------------------------
# Not configured -> CartesiaNotConfigured, for every public entry point
# ---------------------------------------------------------------------------

def test_place_outbound_call_not_configured_no_key():
    assert get_settings().cartesia_api_key == ""
    with pytest.raises(ca.CartesiaNotConfigured):
        ca.place_outbound_call(GOOD_NUMBER)


def test_place_outbound_call_not_configured_missing_agent(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "cartesia_api_key", FAKE_KEY)
    monkeypatch.setattr(s, "cartesia_agent_id", "")
    monkeypatch.setattr(s, "cartesia_from_number_id", "num_1")
    with pytest.raises(ca.CartesiaNotConfigured):
        ca.place_outbound_call(GOOD_NUMBER)


def test_get_call_not_configured():
    with pytest.raises(ca.CartesiaNotConfigured):
        ca.get_call("call_123")


def test_list_agents_not_configured():
    with pytest.raises(ca.CartesiaNotConfigured):
        ca.list_agents()


def test_list_phone_numbers_not_configured():
    with pytest.raises(ca.CartesiaNotConfigured):
        ca.list_phone_numbers()


def test_cancel_call_not_configured():
    with pytest.raises(ca.CartesiaNotConfigured):
        ca.cancel_call("call_123")


def test_synthesize_wav_not_configured(tmp_path):
    with pytest.raises(ca.CartesiaNotConfigured):
        ca.synthesize_wav("hello", str(tmp_path / "out.wav"))


# ---------------------------------------------------------------------------
# place_outbound_call: the "don't trust status alone" contract
# ---------------------------------------------------------------------------

def test_place_outbound_call_200_with_agent_call_id_is_ok(configured, fake_transport):
    fake_transport.append(
        _resp(200, {"calls": [{"number": GOOD_NUMBER, "agent_call_id": "call_abc123"}]})
    )
    result = ca.place_outbound_call(GOOD_NUMBER)
    assert result.ok is True
    assert result.provider_call_id == "call_abc123"
    assert result.error is None


def test_place_outbound_call_200_with_entry_error_is_not_ok(configured, fake_transport):
    """The critical case: HTTP 200, but the per-number entry carries `error`."""
    fake_transport.append(
        _resp(200, {"calls": [{"number": GOOD_NUMBER, "error": "invalid_phone_number"}]})
    )
    result = ca.place_outbound_call(GOOD_NUMBER)
    assert result.ok is False
    assert result.provider_call_id is None
    assert result.error == "invalid_phone_number"
    assert result.http_status == 200


def test_place_outbound_call_401_is_not_ok_not_raised(configured, fake_transport):
    fake_transport.append(_resp(401, {"message": "unauthorized"}))
    result = ca.place_outbound_call(GOOD_NUMBER)
    assert result.ok is False
    assert result.http_status == 401


def test_place_outbound_call_403_is_not_ok_not_raised(configured, fake_transport):
    fake_transport.append(_resp(403, {"message": "forbidden"}))
    result = ca.place_outbound_call(GOOD_NUMBER)
    assert result.ok is False
    assert result.http_status == 403


def test_place_outbound_call_5xx_is_not_ok_not_raised_and_not_retried(configured, fake_transport):
    """Dialing must never be silently retried - see the docstring in the
    adapter. A single 500 should produce exactly one failed result, and the
    fake transport queue (length 1) proves only one call was attempted."""
    fake_transport.append(_resp(500, {"message": "internal error"}))
    result = ca.place_outbound_call(GOOD_NUMBER)
    assert result.ok is False
    assert result.http_status == 500


def test_place_outbound_call_transport_exception_is_not_ok_not_retried(configured, fake_transport):
    fake_transport.append(httpx.ConnectError("connection refused"))
    result = ca.place_outbound_call(GOOD_NUMBER)
    assert result.ok is False
    assert "transport error" in result.error
    assert len(fake_transport) == 0  # exactly one attempt was consumed, no retry


def test_place_outbound_call_malformed_json_body(configured, fake_transport):
    fake_transport.append(_resp(200, text="not json at all"))
    result = ca.place_outbound_call(GOOD_NUMBER)
    assert result.ok is False
    assert "no call entries" in result.error


def test_place_outbound_call_empty_calls_array(configured, fake_transport):
    fake_transport.append(_resp(200, {"calls": []}))
    result = ca.place_outbound_call(GOOD_NUMBER)
    assert result.ok is False
    assert "no call entries" in result.error


def test_place_outbound_call_missing_agent_call_id(configured, fake_transport):
    fake_transport.append(_resp(200, {"calls": [{"number": GOOD_NUMBER}]}))
    result = ca.place_outbound_call(GOOD_NUMBER)
    assert result.ok is False
    assert "agent_call_id" in result.error


# ---------------------------------------------------------------------------
# E.164 validation (adapter-level, before any network call)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "bad_number",
    ["5551234567", "+1555123", "not-a-number", "", "+0123456789", "14155551234"],
)
def test_place_outbound_call_rejects_invalid_e164(configured, fake_transport, bad_number):
    with pytest.raises(ca.CartesiaValidationError):
        ca.place_outbound_call(bad_number)
    assert len(fake_transport) == 0  # queue never touched: no network call was made


def test_place_outbound_call_accepts_valid_e164(configured, fake_transport):
    fake_transport.append(_resp(200, {"calls": [{"number": GOOD_NUMBER, "agent_call_id": "c1"}]}))
    result = ca.place_outbound_call(GOOD_NUMBER)
    assert result.ok is True


# ---------------------------------------------------------------------------
# get_call: typed errors + retry behaviour
# ---------------------------------------------------------------------------

def test_get_call_ok(configured, fake_transport):
    fake_transport.append(_resp(200, {"id": "call_1", "status": "started"}))
    out = ca.get_call("call_1")
    assert out["status"] == "started"


def test_get_call_401_raises_auth_error(configured, fake_transport):
    fake_transport.append(_resp(401, {"message": "bad key"}))
    with pytest.raises(ca.CartesiaAuthError) as exc_info:
        ca.get_call("call_1", max_retries=2)
    assert exc_info.value.status == 401
    assert len(fake_transport) == 0  # 401 is never retried


def test_get_call_403_raises_auth_error(configured, fake_transport):
    fake_transport.append(_resp(403, {"message": "forbidden"}))
    with pytest.raises(ca.CartesiaAuthError):
        ca.get_call("call_1")


def test_get_call_5xx_retries_then_raises_server_error(configured, fake_transport):
    fake_transport.extend([_resp(500), _resp(502), _resp(503)])
    with pytest.raises(ca.CartesiaServerError) as exc_info:
        ca.get_call("call_1", max_retries=2)
    assert exc_info.value.status == 503
    assert len(fake_transport) == 0  # all 3 attempts (1 + 2 retries) were consumed


def test_get_call_5xx_then_success_recovers(configured, fake_transport):
    fake_transport.extend([_resp(500), _resp(200, {"id": "call_1", "status": "completed"})])
    out = ca.get_call("call_1", max_retries=2)
    assert out["status"] == "completed"
    assert len(fake_transport) == 0


def test_get_call_transport_exception_retries_then_raises(configured, fake_transport):
    fake_transport.extend(
        [httpx.ConnectError("refused"), httpx.ConnectTimeout("timed out")]
    )
    with pytest.raises(ca.CartesiaTransportError):
        ca.get_call("call_1", max_retries=1)
    assert len(fake_transport) == 0


def test_get_call_malformed_json_body_raises(configured, fake_transport):
    fake_transport.append(_resp(200, text="<html>not json</html>"))
    with pytest.raises(ca.CartesiaCallError):
        ca.get_call("call_1")


def test_get_call_429_is_retried(configured, fake_transport):
    fake_transport.extend([_resp(429), _resp(200, {"id": "call_1", "status": "completed"})])
    out = ca.get_call("call_1", max_retries=1)
    assert out["status"] == "completed"


def test_get_call_429_exhausted_raises_rate_limit_error(configured, fake_transport):
    fake_transport.extend([_resp(429), _resp(429)])
    with pytest.raises(ca.CartesiaRateLimitError):
        ca.get_call("call_1", max_retries=1)


def test_get_call_generic_4xx_not_retried_generic_error(configured, fake_transport):
    fake_transport.append(_resp(422, {"message": "bad request"}))
    with pytest.raises(ca.CartesiaCallError) as exc_info:
        ca.get_call("call_1", max_retries=2)
    assert not isinstance(exc_info.value, (ca.CartesiaAuthError, ca.CartesiaServerError))
    assert len(fake_transport) == 0  # 422 is not retried


# ---------------------------------------------------------------------------
# map_call_status
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "raw,expected",
    [
        ({"status": "created"}, "requested"),
        ({"status": "started", "start_time": "2026-10-24T00:00:00Z"}, "answered"),
        ({"status": "started"}, "ringing"),
        ({"status": "completed"}, "completed"),
        ({"status": "completed", "end_reason": "dial_no_answer"}, "no_answer"),
        ({"status": "completed", "end_reason": "dial_busy"}, "busy"),
        ({"status": "completed", "end_reason": "voicemail_detected"}, "voicemail"),
        ({"status": "completed", "end_reason": "api_cancelled"}, "canceled"),
        ({"status": "failed"}, "failed"),
        ({"status": "something_new"}, "unknown"),
        ({}, "unknown"),
    ],
)
def test_map_call_status(raw, expected):
    assert ca.map_call_status(raw)["status"] == expected


# ---------------------------------------------------------------------------
# cancel_call
# ---------------------------------------------------------------------------

def test_cancel_call_ok(configured, fake_transport):
    fake_transport.append(_resp(200, {"cancelled": True}))
    out = ca.cancel_call("call_1")
    assert out == {"ok": True, "cancelled": True, "raw": {"cancelled": True}}


def test_cancel_call_404_raises(configured, fake_transport):
    fake_transport.append(_resp(404, {"message": "not found"}))
    with pytest.raises(ca.CartesiaCallError):
        ca.cancel_call("call_1")


def test_cancel_call_400_raises_already_ended(configured, fake_transport):
    fake_transport.append(_resp(400, {"message": "call is not in started status"}))
    with pytest.raises(ca.CartesiaCallError):
        ca.cancel_call("call_1")


# ---------------------------------------------------------------------------
# list_agents / list_phone_numbers
# ---------------------------------------------------------------------------

def test_list_agents_ok(configured, fake_transport):
    fake_transport.append(_resp(200, {"data": [{"id": "agent_1", "name": "Still Here"}]}))
    out = ca.list_agents()
    assert out["data"][0]["id"] == "agent_1"


def test_list_agents_5xx_raises_server_error(configured, fake_transport):
    fake_transport.extend([_resp(500), _resp(500), _resp(500)])
    with pytest.raises(ca.CartesiaServerError):
        ca.list_agents()


def test_list_phone_numbers_falls_back_to_second_path(configured, fake_transport):
    fake_transport.extend(
        [_resp(404, {"message": "no such route"}), _resp(200, {"data": [{"id": "num_1"}]})]
    )
    out = ca.list_phone_numbers()
    assert out["data"][0]["id"] == "num_1"


def test_list_phone_numbers_all_paths_fail_raises(configured, fake_transport):
    fake_transport.extend([_resp(404), _resp(404), _resp(404)])
    with pytest.raises(ca.CartesiaCallError):
        ca.list_phone_numbers()


# ---------------------------------------------------------------------------
# No secret / full phone number ever appears in a returned value
# ---------------------------------------------------------------------------

def test_no_full_phone_number_leaks_on_success(configured, fake_transport):
    fake_transport.append(
        _resp(200, {"calls": [{"number": GOOD_NUMBER, "agent_call_id": "call_abc"}]})
    )
    result = ca.place_outbound_call(GOOD_NUMBER)
    blob = repr(result)
    assert GOOD_NUMBER not in blob
    assert GOOD_NUMBER not in result.to_number
    assert GOOD_NUMBER not in str(result.raw)
    assert FAKE_KEY not in blob


def test_no_full_phone_number_leaks_on_provider_error_entry(configured, fake_transport):
    fake_transport.append(
        _resp(200, {"calls": [{"number": GOOD_NUMBER, "error": "no_answer_capacity"}]})
    )
    result = ca.place_outbound_call(GOOD_NUMBER)
    assert GOOD_NUMBER not in repr(result)
    assert GOOD_NUMBER not in str(result.raw)


def test_no_full_phone_number_leaks_on_http_error_body(configured, fake_transport):
    fake_transport.append(
        _resp(400, {"message": "bad request", "calls": [{"number": GOOD_NUMBER, "error": "x"}]})
    )
    result = ca.place_outbound_call(GOOD_NUMBER)
    assert GOOD_NUMBER not in (result.error or "")
    assert GOOD_NUMBER not in str(result.raw)


def test_no_key_leaks_in_not_configured_error_message():
    try:
        ca.place_outbound_call(GOOD_NUMBER)
    except ca.CartesiaNotConfigured as e:
        assert "sk_" not in str(e)


# ---------------------------------------------------------------------------
# verify_webhook_signature
# ---------------------------------------------------------------------------

def test_verify_webhook_signature_native_header_matches():
    assert ca.verify_webhook_signature({"X-Webhook-Secret": "topsecret"}, "topsecret") is True


def test_verify_webhook_signature_stillhere_header_matches_case_insensitive():
    assert ca.verify_webhook_signature({"x-stillhere-secret": "topsecret"}, "topsecret") is True


def test_verify_webhook_signature_wrong_value():
    assert ca.verify_webhook_signature({"X-Webhook-Secret": "wrong"}, "topsecret") is False


def test_verify_webhook_signature_missing_header():
    assert ca.verify_webhook_signature({}, "topsecret") is False


def test_verify_webhook_signature_empty_secret():
    assert ca.verify_webhook_signature({"X-Webhook-Secret": "topsecret"}, "") is False


def test_verify_webhook_signature_never_raises_on_bad_input():
    assert ca.verify_webhook_signature(None, "x") is False  # type: ignore[arg-type]
