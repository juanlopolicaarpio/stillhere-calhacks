"""Unit tests for the calendar integration (specs/CONTRACTS_V3.md §3).

NO NETWORK. `httpx.Client` is replaced by a fake that pops pre-queued
`httpx.Response` objects (or exceptions) in order and records every request, so
retry counts, typed-error mapping, request shapes and token discipline are all
deterministic. The pattern mirrors `test_cartesia_adapter.py`.

`conftest.py` blanks the Cartesia/Anthropic/Twilio secrets but does not know
about the calendar ones, so this module blanks them at import time the same way
and for the same reason: `app.config` calls `load_dotenv()` at import, and
python-dotenv will repopulate a *deleted* key but not overwrite an existing
one. An explicit empty string is sticky and keeps the suite hermetic even on a
machine with real Google/Microsoft credentials in `.env`.
"""
from __future__ import annotations

import json
import os

# Must happen before `app.config` (and therefore load_dotenv) is imported.
for _k in (
    "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_OAUTH_REDIRECT_URI",
    "GOOGLE_CALENDAR_ID",
    "MICROSOFT_CLIENT_ID", "MICROSOFT_CLIENT_SECRET", "MICROSOFT_TENANT_ID",
    "MICROSOFT_OAUTH_REDIRECT_URI", "MICROSOFT_CALENDAR_ADDRESS",
    "CALENDAR_TOKEN_KEY", "CALENDAR_OAUTH_STATE_SECRET",
):
    os.environ[_k] = ""

from datetime import datetime, timedelta, timezone  # noqa: E402
from urllib.parse import parse_qs, urlparse  # noqa: E402

import httpx  # noqa: E402
import pytest  # noqa: E402

from app.adapters import google_calendar as gcal  # noqa: E402
from app.adapters import microsoft_graph as msgraph  # noqa: E402
from app.api import calendar as calendar_api  # noqa: E402
from app.db import ex, new_id, now_iso, q, q1  # noqa: E402
from app.services import scheduling  # noqa: E402

USER = "cal_sam"

# Obviously-fake but realistically-shaped secrets. Every "no leak" assertion
# greps returned objects, exception messages and the event log for these.
FAKE_ACCESS = "ya29.FAKEaccessTOKENvalue0123456789abcdefGHIJKL"
FAKE_REFRESH = "1//FAKErefreshTOKENvalue9876543210zyxwvuTSRQPO"
FAKE_CODE = "4/0AFAKEauthorizationCODEvalue1234567890abcdef"
SECRETS = (FAKE_ACCESS, FAKE_REFRESH, FAKE_CODE, "fake-google-client-secret",
           "fake-ms-client-secret")


# ---------------------------------------------------------------------------
# Fake transport
# ---------------------------------------------------------------------------


def _resp(status: int, json_body=None, text: str | None = None) -> httpx.Response:
    req = httpx.Request("POST", "https://example.invalid/x")
    if text is not None:
        return httpx.Response(status, text=text, request=req)
    return httpx.Response(status, json=json_body if json_body is not None else {}, request=req)


class _Recorder(list):
    """Queue of responses/exceptions, plus a log of what was actually sent."""

    def __init__(self):
        super().__init__()
        self.sent: list[dict] = []


def _make_fake_client(rec: _Recorder):
    class FakeClient:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def request(self, method, url, headers=None, json=None, data=None, params=None, **kw):
            rec.sent.append({
                "method": method, "url": url, "headers": dict(headers or {}),
                "json": json, "form": dict(data or {}), "params": dict(params or {}),
            })
            if not rec:
                raise AssertionError(
                    f"FakeClient queue exhausted: unexpected {method} {url}"
                )
            item = rec.pop(0)
            if isinstance(item, Exception):
                raise item
            return item

    return FakeClient


@pytest.fixture
def http() -> _Recorder:
    return _Recorder()


@pytest.fixture(autouse=True)
def clean_calendar_tokens():
    """`db.wipe_all()` (called by `fresh_db` via demo_reset) does not clear
    `calendar_tokens`, and on Windows the test database file cannot be
    unlinked while the cached connection holds it open - so grants would leak
    between tests. Clearing here keeps this module hermetic without editing
    lead-owned `db.py` beyond the one permitted table addition.
    """
    ex("DELETE FROM calendar_tokens")
    yield
    ex("DELETE FROM calendar_tokens")


@pytest.fixture(autouse=True)
def no_network(monkeypatch, http):
    monkeypatch.setattr(gcal.httpx, "Client", _make_fake_client(http))
    monkeypatch.setattr(gcal, "_BACKOFF_SECONDS", 0.0)
    return http


@pytest.fixture
def google_configured(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "fake-google-client-id.apps.googleusercontent.com")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "fake-google-client-secret")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://stillhere.example")


@pytest.fixture
def ms_configured(monkeypatch):
    monkeypatch.setenv("MICROSOFT_CLIENT_ID", "00001111-aaaa-2222-bbbb-3333cccc4444")
    monkeypatch.setenv("MICROSOFT_CLIENT_SECRET", "fake-ms-client-secret")
    monkeypatch.setenv("MICROSOFT_TENANT_ID", "contoso.onmicrosoft.com")
    monkeypatch.setenv("MICROSOFT_CALENDAR_ADDRESS", "sam@contoso.com")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://stillhere.example")


@pytest.fixture
def api_client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    # A local app rather than `app.main`: the lead owns main.py and wires the
    # router there, so these tests must not depend on that having happened.
    app = FastAPI()
    app.include_router(calendar_api.router)
    with TestClient(app) as c:
        yield c


def _seed_user(user_id: str = USER) -> str:
    """Idempotent: `availability` has a FK to users, `calendar_tokens` does not.

    Deliberately NOT `demo_reset.SAM` - these tests must not collide with the
    demo seed data that `conftest.py`'s `fresh_db` fixture re-creates.
    """
    ex(
        "INSERT OR IGNORE INTO users (id, display_name, role, created_at)"
        " VALUES (?,?,?,?)",
        (user_id, "Cal Sam", "patient", now_iso()),
    )
    return user_id


def _store_grant(provider: str, *, expires_in: int = 3600, scopes: str | None = None,
                 refresh: str = FAKE_REFRESH, user_id: str = USER) -> None:
    default_scopes = (
        " ".join(gcal.READ_SCOPES) if provider == "google" else " ".join(msgraph.READ_SCOPES)
    )
    gcal.save_tokens(user_id, provider, {
        "access_token": FAKE_ACCESS,
        "refresh_token": refresh,
        "expires_in": expires_in,
        "scope": default_scopes if scopes is None else scopes,
        "token_type": "Bearer",
    })


def _assert_no_secrets(blob) -> None:
    text = blob if isinstance(blob, str) else json.dumps(blob, default=str)
    for s in SECRETS:
        assert s not in text, f"secret material leaked: {s[:12]}..."


# ===========================================================================
# 1. configured() is False with no credentials - honest degradation
# ===========================================================================


def test_configured_false_without_env():
    assert gcal.configured() is False
    assert msgraph.configured() is False


def test_not_configured_blocks_every_real_call():
    for mod in (gcal, msgraph):
        with pytest.raises(gcal.CalendarNotConfigured):
            mod.auth_url(USER, "https://x.example/cb")
        with pytest.raises(gcal.CalendarNotConfigured):
            mod.exchange_code(FAKE_CODE, "https://x.example/cb")
        with pytest.raises(gcal.CalendarNotConfigured):
            mod.free_busy(USER, "2026-11-02T00:00:00Z", "2026-11-03T00:00:00Z")


def test_calendar_mode_is_seeded_without_credentials():
    assert calendar_api.calendar_mode(USER) == "seeded"
    snap = calendar_api.calendar_status(USER)
    assert snap["calendar_mode"] == "seeded"
    assert snap["providers"]["google"]["configured"] is False
    assert snap["providers"]["google"]["connected"] is False
    assert "Seeded availability is the source of truth" in snap["note"]


def test_calendar_mode_needs_configured_and_connected(google_configured):
    # A stored grant alone must not make the mode claim a live calendar when
    # credentials are gone, and credentials alone must not claim a connection.
    assert calendar_api.calendar_mode(USER) == "seeded"
    _store_grant("google")
    assert calendar_api.calendar_mode(USER) == "google"


def test_status_route_never_fails_and_never_lies(api_client):
    r = api_client.get("/api/calendar/status", params={"user_id": USER})
    assert r.status_code == 200
    body = r.json()
    assert body["calendar_mode"] == "seeded"
    _assert_no_secrets(body)


# ===========================================================================
# 2. auth_url - verified endpoints, minimal scopes, signed state
# ===========================================================================


def test_google_auth_url_shape_and_minimal_scopes(google_configured):
    url = gcal.auth_url(USER, "https://stillhere.example/api/calendar/google/callback")
    parsed = urlparse(url)
    assert f"{parsed.scheme}://{parsed.netloc}{parsed.path}" == gcal.AUTH_ENDPOINT
    qs = parse_qs(parsed.query)
    assert qs["response_type"] == ["code"]
    assert qs["access_type"] == ["offline"]          # required for a refresh token
    assert qs["include_granted_scopes"] == ["true"]
    assert qs["redirect_uri"] == ["https://stillhere.example/api/calendar/google/callback"]
    # Free/busy ONLY. No event-content scope in the first consent step.
    assert qs["scope"] == ["https://www.googleapis.com/auth/calendar.freebusy"]
    assert "calendar.events" not in qs["scope"][0]
    assert "calendar.readonly" not in qs["scope"][0]
    claims = gcal.parse_state(qs["state"][0])
    assert claims["u"] == USER and claims["p"] == "google"


def test_google_write_scope_is_a_separate_consent_step(google_configured):
    read_only = parse_qs(urlparse(gcal.auth_url(USER)).query)["scope"][0]
    writeable = parse_qs(urlparse(gcal.auth_url(USER, write=True)).query)["scope"][0]
    assert read_only == "https://www.googleapis.com/auth/calendar.freebusy"
    assert "https://www.googleapis.com/auth/calendar.events.owned" in writeable
    assert "https://www.googleapis.com/auth/calendar.freebusy" in writeable


def test_microsoft_auth_url_shape_and_minimal_scopes(ms_configured):
    url = msgraph.auth_url(USER, "https://stillhere.example/cb")
    parsed = urlparse(url)
    assert f"{parsed.scheme}://{parsed.netloc}{parsed.path}" == (
        "https://login.microsoftonline.com/contoso.onmicrosoft.com/oauth2/v2.0/authorize"
    )
    qs = parse_qs(parsed.query)
    assert qs["response_type"] == ["code"]
    assert qs["response_mode"] == ["query"]
    assert qs["redirect_uri"] == ["https://stillhere.example/cb"]
    scope = qs["scope"][0]
    assert "https://graph.microsoft.com/Calendars.ReadBasic" in scope
    assert "offline_access" in scope                      # required for refresh
    assert "Calendars.ReadWrite" not in scope             # separate consent
    assert "Calendars.Read " not in scope + " "           # not the broader read
    assert gcal.parse_state(qs["state"][0])["p"] == "microsoft"


def test_microsoft_write_scope_is_a_separate_consent_step(ms_configured):
    scope = parse_qs(urlparse(msgraph.auth_url(USER, write=True)).query)["scope"][0]
    assert "https://graph.microsoft.com/Calendars.ReadWrite" in scope


def test_oauth_state_is_tamper_evident(google_configured):
    state = gcal.make_state(USER, "google")
    raw, sig = state.rsplit(".", 1)
    with pytest.raises(gcal.CalendarAuthError):
        gcal.parse_state(f"{raw}.{'0' * len(sig)}")
    with pytest.raises(gcal.CalendarAuthError):
        gcal.parse_state("not-a-state")


def test_auth_route_returns_url_and_logs_no_secret(api_client, google_configured):
    r = api_client.get("/api/calendar/google/auth", params={"user_id": USER})
    assert r.status_code == 200
    body = r.json()
    assert body["auth_url"].startswith(gcal.AUTH_ENDPOINT)
    assert body["scopes_requested"] == ["https://www.googleapis.com/auth/calendar.freebusy"]
    assert body["write_consent"] is False
    _assert_no_secrets(body)


def test_auth_route_unknown_provider(api_client, google_configured):
    r = api_client.get("/api/calendar/nope/auth", params={"user_id": USER})
    assert r.status_code == 502
    assert "unknown calendar provider" in r.json()["error"]["message"]


def test_auth_route_not_configured_is_503(api_client):
    r = api_client.get("/api/calendar/google/auth", params={"user_id": USER})
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "not_configured"


# ===========================================================================
# 3. Token exchange - realistic payloads
# ===========================================================================


#: Verbatim field names from developers.google.com/identity/protocols/oauth2/web-server
GOOGLE_TOKEN_PAYLOAD = {
    "access_token": FAKE_ACCESS,
    "expires_in": 3599,
    "refresh_token": FAKE_REFRESH,
    "scope": "https://www.googleapis.com/auth/calendar.freebusy",
    "token_type": "Bearer",
}

#: Verbatim field names from learn.microsoft.com/entra/identity-platform/v2-oauth2-auth-code-flow
MS_TOKEN_PAYLOAD = {
    "access_token": FAKE_ACCESS,
    "token_type": "Bearer",
    "expires_in": 3599,
    "scope": "https%3A%2F%2Fgraph.microsoft.com%2FCalendars.ReadBasic",
    "refresh_token": FAKE_REFRESH,
}


def test_google_exchange_code_parses_real_payload(google_configured, http):
    http.append(_resp(200, GOOGLE_TOKEN_PAYLOAD))
    tokens = gcal.exchange_code(FAKE_CODE, "https://stillhere.example/cb")
    assert tokens["access_token"] == FAKE_ACCESS
    sent = http.sent[0]
    assert sent["url"] == gcal.TOKEN_ENDPOINT
    assert sent["form"]["grant_type"] == "authorization_code"
    assert sent["form"]["code"] == FAKE_CODE
    assert sent["form"]["redirect_uri"] == "https://stillhere.example/cb"
    assert sent["form"]["client_secret"] == "fake-google-client-secret"


def test_microsoft_exchange_code_parses_real_payload(ms_configured, http):
    http.append(_resp(200, MS_TOKEN_PAYLOAD))
    tokens = msgraph.exchange_code(FAKE_CODE, "https://stillhere.example/cb")
    assert tokens["refresh_token"] == FAKE_REFRESH
    sent = http.sent[0]
    assert sent["url"] == (
        "https://login.microsoftonline.com/contoso.onmicrosoft.com/oauth2/v2.0/token"
    )
    assert sent["form"]["grant_type"] == "authorization_code"
    assert "offline_access" in sent["form"]["scope"]


def test_save_tokens_computes_expiry_and_keeps_tokens_out_of_public_view():
    _store_grant("google", expires_in=3599)
    grant = gcal.load_tokens(USER, "google")
    assert grant is not None
    assert grant.access_token == FAKE_ACCESS
    assert grant.expired() is False
    _assert_no_secrets(grant.public())
    assert grant.public()["has_refresh_token"] is True
    assert grant.public()["scopes"] == [gcal.READ_SCOPES[0]]


def test_save_tokens_rejects_response_without_access_token():
    with pytest.raises(gcal.CalendarTokenError):
        gcal.save_tokens(USER, "google", {"expires_in": 60})


def test_save_tokens_upserts_and_keeps_existing_refresh_token():
    _store_grant("google")
    # Google omits refresh_token on refresh; the stored one must survive.
    gcal.save_tokens(USER, "google", {"access_token": "ya29.second", "expires_in": 60})
    rows = q("SELECT * FROM calendar_tokens WHERE user_id=? AND provider='google'", (USER,))
    assert len(rows) == 1
    grant = gcal.load_tokens(USER, "google")
    assert grant is not None
    assert grant.access_token == "ya29.second"
    assert grant.refresh_token == FAKE_REFRESH


def test_callback_route_stores_grant_and_returns_no_token(api_client, google_configured, http):
    http.append(_resp(200, GOOGLE_TOKEN_PAYLOAD))
    state = gcal.make_state(USER, "google")
    r = api_client.get("/api/calendar/google/callback",
                       params={"code": FAKE_CODE, "state": state})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["connected"] is True
    assert body["grant"]["scopes"] == [gcal.READ_SCOPES[0]]
    _assert_no_secrets(body)
    assert gcal.load_tokens(USER, "google") is not None
    # The event log must be clean too.
    for row in q("SELECT payload FROM agent_events"):
        _assert_no_secrets(row["payload"] or "")


def test_callback_route_rejects_forged_state(api_client, google_configured):
    r = api_client.get("/api/calendar/google/callback",
                       params={"code": FAKE_CODE, "state": "forged.deadbeef"})
    assert r.status_code == 403
    assert gcal.load_tokens(USER, "google") is None


def test_callback_route_handles_user_decline(api_client, google_configured):
    r = api_client.get("/api/calendar/google/callback",
                       params={"error": "access_denied",
                               "error_description": "the user canceled"})
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "consent_denied"


def test_callback_route_rejects_state_from_other_provider(api_client, ms_configured):
    state = gcal.make_state(USER, "google")
    r = api_client.get("/api/calendar/microsoft/callback",
                       params={"code": FAKE_CODE, "state": state})
    assert r.status_code == 403
    assert "different provider" in r.json()["error"]["message"]


# ===========================================================================
# 4+5. Refresh: transparent when expired, clear error when the grant is dead
# ===========================================================================


def test_expired_access_token_is_refreshed_transparently(google_configured, http):
    _store_grant("google", expires_in=-120)         # already expired
    refreshed = dict(GOOGLE_TOKEN_PAYLOAD, access_token="ya29.REFRESHEDaccess")
    refreshed.pop("refresh_token")                   # Google omits it on refresh
    http.append(_resp(200, refreshed))
    http.append(_resp(200, {"kind": "calendar#freeBusy",
                            "calendars": {"primary": {"busy": []}}}))

    blocks = gcal.free_busy(USER, "2026-11-02T00:00:00Z", "2026-11-03T00:00:00Z")
    assert blocks == []
    assert len(http.sent) == 2
    assert http.sent[0]["form"]["grant_type"] == "refresh_token"
    assert http.sent[0]["form"]["refresh_token"] == FAKE_REFRESH
    # The refreshed token is what actually gets used on the API call.
    assert http.sent[1]["headers"]["Authorization"] == "Bearer ya29.REFRESHEDaccess"
    # ...and the stored refresh token survived the response that omitted it.
    grant = gcal.load_tokens(USER, "google")
    assert grant is not None and grant.refresh_token == FAKE_REFRESH


def test_microsoft_refresh_accepts_rotated_refresh_token(ms_configured, http):
    _store_grant("microsoft", expires_in=-5)
    rotated = dict(MS_TOKEN_PAYLOAD, access_token="eyJ.rotated", refresh_token="NEWrefreshTOKEN")
    http.append(_resp(200, rotated))
    grant = msgraph.refresh_tokens(USER)
    assert grant.access_token == "eyJ.rotated"
    assert grant.refresh_token == "NEWrefreshTOKEN"   # docs: discard the old one


def test_missing_expiry_is_treated_as_expired(google_configured, http):
    gcal.save_tokens(USER, "google", {
        "access_token": FAKE_ACCESS, "refresh_token": FAKE_REFRESH,
        "scope": gcal.READ_SCOPES[0],
    })
    grant = gcal.load_tokens(USER, "google")
    assert grant is not None and grant.expired() is True
    http.append(_resp(200, GOOGLE_TOKEN_PAYLOAD))
    assert gcal.access_token(USER) == FAKE_ACCESS
    assert http.sent[0]["form"]["grant_type"] == "refresh_token"


def test_dead_refresh_token_raises_reconnect_error_google(google_configured, http):
    _store_grant("google", expires_in=-60)
    http.append(_resp(400, {"error": "invalid_grant",
                            "error_description": "Token has been expired or revoked."}))
    with pytest.raises(gcal.CalendarTokenError) as ei:
        gcal.free_busy(USER, "2026-11-02T00:00:00Z", "2026-11-03T00:00:00Z")
    msg = str(ei.value)
    assert "invalid_grant" in msg and "reconnect" in msg
    _assert_no_secrets(msg)
    assert len(http.sent) == 1                       # 400 is never retried


def test_dead_refresh_token_raises_reconnect_error_microsoft(ms_configured, http):
    _store_grant("microsoft", expires_in=-60)
    http.append(_resp(400, {
        "error": "invalid_grant",
        "error_description": "AADSTS700082: The refresh token has expired due to inactivity.",
        "error_codes": [700082],
    }))
    with pytest.raises(gcal.CalendarTokenError) as ei:
        msgraph.free_busy(USER, "2026-11-02T00:00:00Z", "2026-11-03T00:00:00Z")
    assert "invalid_grant" in str(ei.value)
    _assert_no_secrets(str(ei.value))


def test_no_grant_at_all_is_a_reconnect_error(google_configured):
    with pytest.raises(gcal.CalendarTokenError) as ei:
        gcal.free_busy(USER, "2026-11-02T00:00:00Z", "2026-11-03T00:00:00Z")
    assert "no Google calendar grant stored" in str(ei.value)


def test_grant_without_refresh_token_says_reconnect(google_configured):
    _store_grant("google", expires_in=-60, refresh="")
    with pytest.raises(gcal.CalendarTokenError) as ei:
        gcal.refresh_tokens(USER)
    assert "no refresh token" in str(ei.value)


def test_expired_code_exchange_reports_invalid_grant(google_configured, http):
    http.append(_resp(400, {"error": "invalid_grant", "error_description": "Bad Request"}))
    with pytest.raises(gcal.CalendarTokenError) as ei:
        gcal.exchange_code(FAKE_CODE, "https://stillhere.example/cb")
    assert "invalid_grant" in str(ei.value)


# ===========================================================================
# 6. Free/busy parsing: all-day, multi-day, non-UTC
# ===========================================================================

#: Verbatim response shape from the freebusy/query reference. The timestamps
#: are a real Google-style answer in a NON-UTC offset (-07:00, US Pacific DST),
#: and include an all-day block (local midnight to midnight) and a multi-day
#: span.
GOOGLE_FREEBUSY_PAYLOAD = {
    "kind": "calendar#freeBusy",
    "timeMin": "2026-11-02T00:00:00.000Z",
    "timeMax": "2026-11-09T00:00:00.000Z",
    "calendars": {
        "primary": {
            "busy": [
                # all-day event: local midnight -> local midnight, -07:00
                {"start": "2026-11-03T00:00:00-07:00", "end": "2026-11-04T00:00:00-07:00"},
                # ordinary meeting in a non-UTC offset
                {"start": "2026-11-05T09:30:00-07:00", "end": "2026-11-05T10:30:00-07:00"},
                # multi-day span (a trip)
                {"start": "2026-11-06T18:00:00-07:00", "end": "2026-11-08T09:00:00-07:00"},
            ]
        }
    },
}


def test_google_free_busy_parses_offsets_all_day_and_multi_day(google_configured, http):
    _store_grant("google")
    http.append(_resp(200, GOOGLE_FREEBUSY_PAYLOAD))
    blocks = gcal.free_busy(USER, "2026-11-02T00:00:00Z", "2026-11-09T00:00:00Z")

    assert [b.provider for b in blocks] == ["google"] * 3
    # Everything is normalised to UTC: 00:00-07:00 is 07:00Z the same day.
    assert blocks[0].start_at == "2026-11-03T07:00:00+00:00"
    assert blocks[0].end_at == "2026-11-04T07:00:00+00:00"
    assert (blocks[0].end - blocks[0].start) == timedelta(hours=24)   # all-day
    assert blocks[1].start_at == "2026-11-05T16:30:00+00:00"
    assert (blocks[2].end - blocks[2].start) == timedelta(days=1, hours=15)  # multi-day

    sent = http.sent[0]
    assert sent["url"] == gcal.FREEBUSY_ENDPOINT
    assert sent["json"]["items"] == [{"id": "primary"}]
    assert sent["json"]["timeZone"] == "UTC"
    assert sent["json"]["timeMin"] == "2026-11-02T00:00:00+00:00"
    assert sent["headers"]["Authorization"] == f"Bearer {FAKE_ACCESS}"


def test_google_free_busy_surfaces_per_calendar_errors(google_configured, http):
    _store_grant("google")
    http.append(_resp(200, {
        "kind": "calendar#freeBusy",
        "calendars": {"primary": {"errors": [{"domain": "global", "reason": "notFound"}],
                                  "busy": []}},
    }))
    # An unreadable calendar must NOT silently look like free time.
    with pytest.raises(gcal.CalendarError) as ei:
        gcal.free_busy(USER, "2026-11-02T00:00:00Z", "2026-11-03T00:00:00Z")
    assert "notFound" in str(ei.value)


#: Verbatim response shape from the calendar: getSchedule reference, with the
#: UTC time zone the adapter asks for via `Prefer: outlook.timezone="UTC"` and
#: Graph's 7-digit fractional seconds.
MS_GETSCHEDULE_PAYLOAD = {
    "@odata.context": "https://graph.microsoft.com/v1.0/$metadata#Collection(microsoft.graph.scheduleInformation)",
    "value": [{
        "scheduleId": "sam@contoso.com",
        "availabilityView": "000220130",
        "scheduleItems": [
            {"status": "busy", "subject": "Let's go for lunch",
             "start": {"dateTime": "2026-11-05T19:00:00.0000000", "timeZone": "UTC"},
             "end": {"dateTime": "2026-11-05T21:00:00.0000000", "timeZone": "UTC"}},
            {"status": "free",
             "start": {"dateTime": "2026-11-05T17:00:00.0000000", "timeZone": "UTC"},
             "end": {"dateTime": "2026-11-05T18:00:00.0000000", "timeZone": "UTC"}},
            {"status": "workingElsewhere",
             "start": {"dateTime": "2026-11-05T18:00:00.0000000", "timeZone": "UTC"},
             "end": {"dateTime": "2026-11-05T19:00:00.0000000", "timeZone": "UTC"}},
            {"status": "tentative",
             "start": {"dateTime": "2026-11-05T22:00:00.0000000", "timeZone": "UTC"},
             "end": {"dateTime": "2026-11-05T23:00:00.0000000", "timeZone": "UTC"}},
            # oof spanning two days (an out-of-office trip)
            {"status": "oof",
             "start": {"dateTime": "2026-11-06T16:00:00.0000000", "timeZone": "UTC"},
             "end": {"dateTime": "2026-11-08T16:00:00.0000000", "timeZone": "UTC"}},
            # all-day block, local midnight to midnight expressed in UTC
            {"status": "busy",
             "start": {"dateTime": "2026-11-03T00:00:00.0000000", "timeZone": "UTC"},
             "end": {"dateTime": "2026-11-04T00:00:00.0000000", "timeZone": "UTC"}},
        ],
        "workingHours": {"daysOfWeek": ["monday"], "startTime": "08:00:00.0000000",
                         "endTime": "17:00:00.0000000", "timeZone": {"name": "UTC"}},
    }],
}


def test_microsoft_free_busy_parses_statuses_and_graph_fractions(ms_configured, http):
    _store_grant("microsoft")
    http.append(_resp(200, MS_GETSCHEDULE_PAYLOAD))
    blocks = msgraph.free_busy(USER, "2026-11-02T00:00:00Z", "2026-11-09T00:00:00Z")

    # free + workingElsewhere are dropped; busy, tentative and oof are kept.
    assert len(blocks) == 4
    starts = [b.start_at for b in blocks]
    assert starts == sorted(starts)
    assert blocks[0].start_at == "2026-11-03T00:00:00+00:00"       # all-day
    assert (blocks[0].end - blocks[0].start) == timedelta(hours=24)
    assert blocks[-1].start_at == "2026-11-06T16:00:00+00:00"      # multi-day oof
    assert (blocks[-1].end - blocks[-1].start) == timedelta(days=2)
    assert all(b.calendar_id == "sam@contoso.com" for b in blocks)

    sent = http.sent[0]
    assert sent["url"] == msgraph.GETSCHEDULE_ENDPOINT
    assert sent["headers"]["Prefer"] == 'outlook.timezone="UTC"'
    assert sent["json"]["schedules"] == ["sam@contoso.com"]
    assert sent["json"]["startTime"] == {"dateTime": "2026-11-02T00:00:00+00:00",
                                        "timeZone": "UTC"}
    assert sent["json"]["availabilityViewInterval"] == 30


def test_microsoft_free_busy_can_exclude_tentative(ms_configured, http):
    _store_grant("microsoft")
    http.append(_resp(200, MS_GETSCHEDULE_PAYLOAD))
    blocks = msgraph.free_busy(USER, "2026-11-02T00:00:00Z", "2026-11-09T00:00:00Z",
                               include_tentative=False)
    assert len(blocks) == 3


def test_microsoft_free_busy_needs_a_mailbox_address(monkeypatch, ms_configured):
    monkeypatch.setenv("MICROSOFT_CALENDAR_ADDRESS", "")
    _store_grant("microsoft")
    with pytest.raises(gcal.CalendarError) as ei:
        msgraph.free_busy(USER, "2026-11-02T00:00:00Z", "2026-11-09T00:00:00Z")
    assert "MICROSOFT_CALENDAR_ADDRESS" in str(ei.value)


def test_graph_non_utc_timezone_is_converted_not_guessed(monkeypatch):
    # A Windows zone name resolved through the CLDR map + a real tzinfo.
    monkeypatch.setattr(
        msgraph, "resolve_timezone",
        lambda name: timezone(timedelta(hours=-8)) if name == "Pacific Standard Time" else None,
    )
    got = msgraph.parse_graph_datetime(
        {"dateTime": "2026-11-05T09:00:00.0000000", "timeZone": "Pacific Standard Time"}
    )
    assert got == datetime(2026, 11, 5, 17, 0, tzinfo=timezone.utc)


def test_graph_unresolvable_timezone_raises_rather_than_assuming_utc(monkeypatch):
    monkeypatch.setattr(msgraph, "resolve_timezone", lambda name: None)
    with pytest.raises(gcal.CalendarTimeZoneError) as ei:
        msgraph.parse_graph_datetime(
            {"dateTime": "2026-11-05T09:00:00.0000000", "timeZone": "Mars Standard Time"}
        )
    assert "cannot resolve time zone" in str(ei.value)


def test_windows_to_iana_map_is_cldr_correct():
    assert msgraph.WINDOWS_TO_IANA["Pacific Standard Time"] == "America/Los_Angeles"
    assert msgraph.WINDOWS_TO_IANA["Eastern Standard Time"] == "America/New_York"
    assert msgraph.WINDOWS_TO_IANA["GMT Standard Time"] == "Europe/London"
    assert msgraph.resolve_timezone("UTC") is timezone.utc
    assert msgraph.resolve_timezone("") is timezone.utc


def test_graph_offset_bearing_datetime_is_honoured():
    got = msgraph.parse_graph_datetime(
        {"dateTime": "2026-11-05T09:00:00-07:00", "timeZone": "Pacific Standard Time"}
    )
    assert got == datetime(2026, 11, 5, 16, 0, tzinfo=timezone.utc)


def test_parse_iso_truncates_graph_seven_digit_fractions():
    assert gcal.parse_iso("2026-11-05T09:00:00.1234567") == datetime(
        2026, 11, 5, 9, 0, 0, 123456, tzinfo=timezone.utc
    )


# ===========================================================================
# 7. busy -> free inversion, and the bridge into `availability`
# ===========================================================================


def _busy(start: str, end: str) -> gcal.Busy:
    return gcal.Busy(start_at=start, end_at=end, provider="google")


def test_busy_to_free_windows_inverts_merges_and_filters():
    free = gcal.busy_to_free_windows(
        [
            _busy("2026-11-02T10:00:00Z", "2026-11-02T11:00:00Z"),
            # overlapping + touching blocks must merge, not create slivers
            _busy("2026-11-02T10:30:00Z", "2026-11-02T12:00:00Z"),
            _busy("2026-11-02T12:00:00Z", "2026-11-02T13:00:00Z"),
            # extends past the window end
            _busy("2026-11-02T20:00:00Z", "2026-11-04T09:00:00Z"),
        ],
        "2026-11-02T08:00:00Z", "2026-11-02T22:00:00Z", min_minutes=60,
    )
    as_iso = [(a.isoformat(), b.isoformat()) for a, b in free]
    assert as_iso == [
        ("2026-11-02T08:00:00+00:00", "2026-11-02T10:00:00+00:00"),
        ("2026-11-02T13:00:00+00:00", "2026-11-02T20:00:00+00:00"),
    ]


def test_busy_to_free_windows_drops_gaps_shorter_than_min_minutes():
    free = gcal.busy_to_free_windows(
        [
            _busy("2026-11-02T09:00:00Z", "2026-11-02T10:00:00Z"),
            _busy("2026-11-02T10:30:00Z", "2026-11-02T22:00:00Z"),
        ],
        "2026-11-02T09:00:00Z", "2026-11-02T22:00:00Z", min_minutes=60,
    )
    assert free == []          # only a 30-minute gap exists


def test_busy_to_free_windows_fully_busy_and_fully_free():
    window = ("2026-11-02T09:00:00Z", "2026-11-02T17:00:00Z")
    assert gcal.busy_to_free_windows(
        [_busy("2026-11-01T00:00:00Z", "2026-11-03T00:00:00Z")], *window) == []
    free = gcal.busy_to_free_windows([], *window)
    assert len(free) == 1 and (free[0][1] - free[0][0]) == timedelta(hours=8)
    assert gcal.busy_to_free_windows([], window[1], window[0]) == []   # inverted window


def test_sync_availability_feeds_find_free_slots_unchanged(google_configured, http):
    """The whole point of §3: real calendar data reaches group scheduling with
    no change to `services/scheduling.py`.
    """
    _seed_user(USER)
    friend = _seed_user("cal_alex")
    scheduling.add_availability(friend, "2026-11-02T08:00:00+00:00",
                                "2026-11-02T22:00:00+00:00", source="seed")
    _store_grant("google")
    http.append(_resp(200, {
        "kind": "calendar#freeBusy",
        "calendars": {"primary": {"busy": [
            {"start": "2026-11-02T09:00:00+00:00", "end": "2026-11-02T13:00:00+00:00"},
        ]}},
    }))

    written = gcal.sync_free_busy(USER, "2026-11-02T08:00:00Z", "2026-11-02T18:00:00Z")
    assert written == [
        {"start_at": "2026-11-02T08:00:00+00:00", "end_at": "2026-11-02T09:00:00+00:00"},
        {"start_at": "2026-11-02T13:00:00+00:00", "end_at": "2026-11-02T18:00:00+00:00"},
    ]
    rows = q("SELECT * FROM availability WHERE user_id=?", (USER,))
    assert {r["source"] for r in rows} == {"google_calendar"}
    assert all(r["visibility"] == "agent_internal" for r in rows)

    # The friend is free all day; the intersection is exactly the two windows
    # the real Google calendar left open.
    slots = scheduling.find_free_slots([USER, friend], min_minutes=60)
    assert [(s.start_at, s.end_at) for s in slots] == [
        ("2026-11-02T08:00:00+00:00", "2026-11-02T09:00:00+00:00"),
        ("2026-11-02T13:00:00+00:00", "2026-11-02T18:00:00+00:00"),
    ]
    # ...and a 4-hour ask is correctly impossible before the 09:00 meeting.
    assert [
        (s.start_at, s.end_at)
        for s in scheduling.find_free_slots([USER, friend], min_minutes=240)
    ] == [("2026-11-02T13:00:00+00:00", "2026-11-02T18:00:00+00:00")]


def test_sync_is_idempotent_and_never_touches_seeded_rows(google_configured, http):
    _seed_user(USER)
    scheduling.add_availability(USER, "2026-11-02T08:00:00+00:00",
                                "2026-11-02T22:00:00+00:00", source="seed")
    _store_grant("google")
    payload = {"kind": "calendar#freeBusy",
               "calendars": {"primary": {"busy": [
                   {"start": "2026-11-02T09:00:00+00:00",
                    "end": "2026-11-02T13:00:00+00:00"}]}}}
    for _ in range(2):
        http.append(_resp(200, payload))
        gcal.sync_free_busy(USER, "2026-11-02T08:00:00Z", "2026-11-02T18:00:00Z")

    rows = q("SELECT source FROM availability WHERE user_id=?", (USER,))
    assert sum(1 for r in rows if r["source"] == "seed") == 1
    assert sum(1 for r in rows if r["source"] == "google_calendar") == 2


def test_sync_route_without_connection_is_honest(api_client, google_configured):
    r = api_client.post("/api/calendar/sync", json={"user_id": USER})
    assert r.status_code == 503
    assert "nothing to" in r.json()["error"]["message"]


def test_sync_route_writes_availability(api_client, google_configured, http):
    _seed_user(USER)
    _store_grant("google")
    http.append(_resp(200, {"kind": "calendar#freeBusy",
                            "calendars": {"primary": {"busy": []}}}))
    r = api_client.post("/api/calendar/sync", json={
        "user_id": USER,
        "window_start": "2026-11-02T08:00:00Z",
        "window_end": "2026-11-02T18:00:00Z",
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["calendar_mode"] == "google"
    assert body["source"] == "google_calendar"
    assert len(body["free_windows"]) == 1
    _assert_no_secrets(body)


# ===========================================================================
# 8. create_event request shape (separate write consent)
# ===========================================================================


def test_google_create_event_request_shape(google_configured, http):
    _store_grant("google", scopes=" ".join(gcal.READ_SCOPES + gcal.WRITE_SCOPES))
    http.append(_resp(200, {
        "kind": "calendar#event", "id": "evt_abc123", "status": "confirmed",
        "htmlLink": "https://www.google.com/calendar/event?eid=abc",
        "iCalUID": "abc123@google.com",
    }))
    out = gcal.create_event(
        USER,
        title="Coffee with Alex",
        starts_at="2026-11-05T17:00:00+00:00",
        ends_at="2026-11-05T18:00:00+00:00",
        attendees=["alex@example.com"],
        location="Four Barrel Coffee",
        description="Low-pressure catch-up.",
    )
    assert out == {
        "provider": "google", "event_id": "evt_abc123",
        "html_link": "https://www.google.com/calendar/event?eid=abc",
        "status": "confirmed", "ical_uid": "abc123@google.com",
        "starts_at": "2026-11-05T17:00:00+00:00",
        "ends_at": "2026-11-05T18:00:00+00:00",
    }
    sent = http.sent[0]
    assert sent["url"] == (
        "https://www.googleapis.com/calendar/v3/calendars/primary/events"
    )
    assert sent["params"] == {"sendUpdates": "all"}
    assert sent["json"]["summary"] == "Coffee with Alex"
    assert sent["json"]["start"] == {"dateTime": "2026-11-05T17:00:00+00:00",
                                    "timeZone": "UTC"}
    assert sent["json"]["attendees"] == [{"email": "alex@example.com"}]
    assert sent["json"]["location"] == "Four Barrel Coffee"
    _assert_no_secrets(sent["json"])          # no token in the event payload


def test_google_create_event_refuses_a_freebusy_only_grant(google_configured, http):
    _store_grant("google")                    # read scope only
    with pytest.raises(gcal.CalendarAuthError) as ei:
        gcal.create_event(USER, title="x", starts_at="2026-11-05T17:00:00Z",
                          ends_at="2026-11-05T18:00:00Z")
    assert "separate consent step" in str(ei.value)
    assert http.sent == []                    # nothing was ever sent


def test_create_event_validates_the_time_range(google_configured):
    _store_grant("google", scopes=" ".join(gcal.READ_SCOPES + gcal.WRITE_SCOPES))
    with pytest.raises(gcal.CalendarError):
        gcal.create_event(USER, title="x", starts_at="2026-11-05T18:00:00Z",
                          ends_at="2026-11-05T17:00:00Z")


def test_microsoft_create_event_request_shape(ms_configured, http):
    _store_grant("microsoft", scopes=" ".join(msgraph.READ_SCOPES + msgraph.WRITE_SCOPES))
    http.append(_resp(201, {
        "id": "AAMkAD==", "webLink": "https://outlook.office365.com/owa/?itemid=AAMkAD",
        "iCalUId": "040000008200E00074C5B7101A82E008", "responseStatus": {"response": "organizer"},
    }))
    out = msgraph.create_event(
        USER,
        title="Coffee with Alex",
        starts_at="2026-11-05T17:00:00+00:00",
        ends_at="2026-11-05T18:00:00+00:00",
        attendees=["alex@example.com"],
        location="Four Barrel Coffee",
        description="Low-pressure catch-up.",
        transaction_id="plan_v1_abc",
    )
    assert out["event_id"] == "AAMkAD=="
    assert out["status"] == "organizer"
    sent = http.sent[0]
    assert sent["url"] == "https://graph.microsoft.com/v1.0/me/events"
    body = sent["json"]
    assert body["subject"] == "Coffee with Alex"
    assert body["start"] == {"dateTime": "2026-11-05T17:00:00+00:00", "timeZone": "UTC"}
    assert body["body"] == {"contentType": "text", "content": "Low-pressure catch-up."}
    assert body["location"] == {"displayName": "Four Barrel Coffee"}
    assert body["attendees"] == [
        {"emailAddress": {"address": "alex@example.com"}, "type": "required"}
    ]
    assert body["allowNewTimeProposals"] is True
    assert body["transactionId"] == "plan_v1_abc"
    _assert_no_secrets(body)


def test_microsoft_create_event_refuses_a_freebusy_only_grant(ms_configured, http):
    _store_grant("microsoft")
    with pytest.raises(gcal.CalendarAuthError):
        msgraph.create_event(USER, title="x", starts_at="2026-11-05T17:00:00Z",
                             ends_at="2026-11-05T18:00:00Z")
    assert http.sent == []


def test_create_event_is_never_retried(google_configured, http):
    _store_grant("google", scopes=" ".join(gcal.READ_SCOPES + gcal.WRITE_SCOPES))
    http.append(_resp(500, {"error": {"code": 500, "message": "Backend Error"}}))
    with pytest.raises(gcal.CalendarServerError):
        gcal.create_event(USER, title="x", starts_at="2026-11-05T17:00:00Z",
                          ends_at="2026-11-05T18:00:00Z")
    # Exactly one attempt: a lost response plus a retry means two real events.
    assert len(http.sent) == 1


# ===========================================================================
# 9. HTTP failure mapping: 401 / 403 / 429 / 5xx / transport
# ===========================================================================


def test_401_maps_to_auth_error(google_configured, http):
    _store_grant("google")
    http.append(_resp(401, {"error": {"code": 401, "message": "Invalid Credentials"}}))
    with pytest.raises(gcal.CalendarAuthError) as ei:
        gcal.free_busy(USER, "2026-11-02T00:00:00Z", "2026-11-03T00:00:00Z")
    assert ei.value.status == 401
    assert len(http.sent) == 1          # 401 is never retried
    _assert_no_secrets(str(ei.value))


def test_403_permission_denied_maps_to_auth_error(google_configured, http):
    _store_grant("google")
    http.append(_resp(403, {"error": {"errors": [
        {"domain": "global", "reason": "forbidden", "message": "Forbidden"}], "code": 403}}))
    with pytest.raises(gcal.CalendarAuthError):
        gcal.free_busy(USER, "2026-11-02T00:00:00Z", "2026-11-03T00:00:00Z")


def test_403_rate_limit_exceeded_maps_to_rate_limit_error(google_configured, http):
    _store_grant("google")
    http.append(_resp(403, {"error": {"errors": [
        {"domain": "usageLimits", "reason": "rateLimitExceeded",
         "message": "Rate Limit Exceeded"}], "code": 403}}))
    with pytest.raises(gcal.CalendarRateLimitError) as ei:
        gcal.free_busy(USER, "2026-11-02T00:00:00Z", "2026-11-03T00:00:00Z")
    assert ei.value.status == 403


def test_429_is_retried_then_raises_rate_limit_error(google_configured, http):
    _store_grant("google")
    for _ in range(3):
        http.append(_resp(429, {"error": {"code": 429, "message": "Too Many Requests"}}))
    with pytest.raises(gcal.CalendarRateLimitError):
        gcal.free_busy(USER, "2026-11-02T00:00:00Z", "2026-11-03T00:00:00Z")
    assert len(http.sent) == 3          # one attempt + two retries


def test_5xx_is_retried_then_raises_server_error(ms_configured, http):
    _store_grant("microsoft")
    for _ in range(3):
        http.append(_resp(503, {"error": {"code": "serviceNotAvailable"}}))
    with pytest.raises(gcal.CalendarServerError):
        msgraph.free_busy(USER, "2026-11-02T00:00:00Z", "2026-11-03T00:00:00Z")
    assert len(http.sent) == 3


def test_5xx_then_success_recovers(google_configured, http):
    _store_grant("google")
    http.append(_resp(500, {"error": {"code": 500}}))
    http.append(_resp(200, {"kind": "calendar#freeBusy",
                            "calendars": {"primary": {"busy": []}}}))
    assert gcal.free_busy(USER, "2026-11-02T00:00:00Z", "2026-11-03T00:00:00Z") == []
    assert len(http.sent) == 2


def test_transport_failure_maps_to_transport_error(google_configured, http):
    _store_grant("google")
    for _ in range(3):
        http.append(httpx.ConnectError("connection refused"))
    with pytest.raises(gcal.CalendarTransportError):
        gcal.free_busy(USER, "2026-11-02T00:00:00Z", "2026-11-03T00:00:00Z")
    assert len(http.sent) == 3


def test_non_json_body_is_reported_without_leaking(google_configured, http):
    _store_grant("google")
    http.append(_resp(200, text="<html>upstream proxy error</html>"))
    with pytest.raises(gcal.CalendarError) as ei:
        gcal.free_busy(USER, "2026-11-02T00:00:00Z", "2026-11-03T00:00:00Z")
    assert "non-JSON" in str(ei.value)


def test_graph_response_without_value_collection_errors(ms_configured, http):
    _store_grant("microsoft")
    http.append(_resp(200, {"unexpected": True}))
    with pytest.raises(gcal.CalendarError) as ei:
        msgraph.free_busy(USER, "2026-11-02T00:00:00Z", "2026-11-03T00:00:00Z")
    assert "`value`" in str(ei.value)


def test_route_maps_rate_limit_to_429(api_client, google_configured, http):
    _seed_user(USER)
    _store_grant("google")
    for _ in range(3):
        http.append(_resp(429, {"error": {"code": 429}}))
    r = api_client.post("/api/calendar/sync", json={"user_id": USER})
    assert r.status_code == 429
    assert r.json()["error"]["code"] == "rate_limited"


# ===========================================================================
# 10. Token discipline: never returned, never logged, never in a message
# ===========================================================================


def test_tokens_never_appear_in_any_route_response(api_client, google_configured, http):
    _seed_user(USER)
    http.append(_resp(200, GOOGLE_TOKEN_PAYLOAD))
    responses = [
        api_client.get("/api/calendar/google/callback",
                       params={"code": FAKE_CODE, "state": gcal.make_state(USER, "google")}),
        api_client.get("/api/calendar/status", params={"user_id": USER}),
        api_client.get("/api/calendar/google/auth", params={"user_id": USER}),
        api_client.get("/api/calendar/microsoft/auth", params={"user_id": USER}),
    ]
    for r in responses:
        _assert_no_secrets(r.text)
    # And the stored row really does hold the token - we are testing redaction,
    # not an empty database.
    grant = gcal.load_tokens(USER, "google")
    assert grant is not None and grant.access_token == FAKE_ACCESS


def test_tokens_never_appear_in_the_event_log(google_configured, http):
    _seed_user(USER)
    http.append(_resp(200, GOOGLE_TOKEN_PAYLOAD))
    tokens = gcal.exchange_code(FAKE_CODE, "https://stillhere.example/cb")
    gcal.save_tokens(USER, "google", tokens)
    from app.services import events as events_svc

    events_svc.emit("calendar.connected", "connected", payload={"provider": "google"})
    for row in q("SELECT safe_summary, payload FROM agent_events"):
        _assert_no_secrets(f"{row['safe_summary']} {row['payload'] or ''}")


def test_redact_masks_token_bearing_structures():
    out = gcal.redact({
        "access_token": FAKE_ACCESS,
        "refresh_token": FAKE_REFRESH,
        "nested": [{"id_token": "eyJabc", "client_secret": "s3cret"}],
        "error_description": f"bad token {FAKE_ACCESS}",
        "keep": "rateLimitExceeded",
    })
    _assert_no_secrets(out)
    assert out["access_token"] == "***redacted***"
    assert out["nested"][0]["id_token"] == "***redacted***"
    assert out["keep"] == "rateLimitExceeded"   # short, meaningful values survive


def test_provider_error_bodies_are_redacted_before_reaching_a_message(google_configured, http):
    _store_grant("google")
    # A provider that (wrongly) echoes the token back must not leak it onwards.
    http.append(_resp(400, {"error": "invalid_request",
                            "error_description": f"token {FAKE_ACCESS} is malformed"}))
    with pytest.raises(gcal.CalendarError) as ei:
        gcal.free_busy(USER, "2026-11-02T00:00:00Z", "2026-11-03T00:00:00Z")
    _assert_no_secrets(str(ei.value))
    _assert_no_secrets(str(ei.value.body))


def test_stored_grant_public_view_has_no_token():
    _store_grant("google")
    grant = gcal.load_tokens(USER, "google")
    assert grant is not None
    public = grant.public()
    assert set(public) == {
        "user_id", "provider", "scopes", "expires_at", "connected_at",
        "has_refresh_token", "token_storage",
    }
    _assert_no_secrets(public)


def test_adapter_status_snapshots_carry_no_token(google_configured, ms_configured):
    _store_grant("google")
    _store_grant("microsoft")
    _assert_no_secrets(gcal.status(USER))
    _assert_no_secrets(msgraph.status(USER))
    _assert_no_secrets(calendar_api.calendar_status(USER))


# ---------------------------------------------------------------------------
# At-rest sealing
# ---------------------------------------------------------------------------


def test_token_storage_mode_is_honest(monkeypatch):
    assert gcal.token_storage_mode() == "plaintext"
    monkeypatch.setenv("CALENDAR_TOKEN_KEY", "a" * 32)
    assert gcal.token_storage_mode() == "sealed"


def test_sealed_tokens_round_trip_and_are_unreadable_in_the_db(monkeypatch):
    monkeypatch.setenv("CALENDAR_TOKEN_KEY", "unit-test-master-key-0123456789")
    _store_grant("google")
    row = q1("SELECT access_token, refresh_token FROM calendar_tokens WHERE user_id=?", (USER,))
    assert row is not None
    assert row["access_token"].startswith("shv1:")
    _assert_no_secrets(row["access_token"] + row["refresh_token"])
    grant = gcal.load_tokens(USER, "google")
    assert grant is not None and grant.access_token == FAKE_ACCESS


def test_sealed_token_detects_tampering(monkeypatch):
    monkeypatch.setenv("CALENDAR_TOKEN_KEY", "unit-test-master-key-0123456789")
    sealed = gcal.seal(FAKE_ACCESS)
    flipped = sealed[:-4] + ("AAAA" if sealed[-4:] != "AAAA" else "BBBB")
    with pytest.raises(gcal.CalendarTokenError) as ei:
        gcal.unseal(flipped)
    assert "integrity" in str(ei.value) or "corrupt" in str(ei.value)


def test_sealed_token_with_wrong_key_fails_loudly(monkeypatch):
    monkeypatch.setenv("CALENDAR_TOKEN_KEY", "key-one")
    sealed = gcal.seal(FAKE_ACCESS)
    monkeypatch.setenv("CALENDAR_TOKEN_KEY", "key-two")
    with pytest.raises(gcal.CalendarTokenError):
        gcal.unseal(sealed)


def test_plaintext_rows_still_read_after_a_key_is_introduced(monkeypatch):
    _store_grant("google")                                   # no key: plaintext
    monkeypatch.setenv("CALENDAR_TOKEN_KEY", "introduced-later")
    grant = gcal.load_tokens(USER, "google")
    assert grant is not None and grant.access_token == FAKE_ACCESS


def test_sealed_row_without_the_key_says_reconnect(monkeypatch):
    monkeypatch.setenv("CALENDAR_TOKEN_KEY", "will-be-lost")
    _store_grant("google")
    monkeypatch.setenv("CALENDAR_TOKEN_KEY", "")
    with pytest.raises(gcal.CalendarTokenError) as ei:
        gcal.load_tokens(USER, "google")
    assert "reconnect" in str(ei.value)


# ---------------------------------------------------------------------------
# Disconnect
# ---------------------------------------------------------------------------


def test_disconnect_removes_the_grant_and_revokes_at_google(api_client, google_configured, http):
    _store_grant("google")
    http.append(_resp(200, {}))
    r = api_client.post("/api/calendar/disconnect",
                        json={"user_id": USER, "provider": "google"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["rows_removed"] == 1
    assert body["results"][0]["remote_revoked"] is True
    assert body["calendar_mode"] == "seeded"
    assert gcal.load_tokens(USER, "google") is None
    assert http.sent[0]["url"] == gcal.REVOKE_ENDPOINT
    _assert_no_secrets(body)


def test_disconnect_succeeds_locally_even_if_the_provider_fails(
    api_client, google_configured, http
):
    _store_grant("google")
    http.append(httpx.ConnectError("no route to host"))
    r = api_client.post("/api/calendar/disconnect",
                        json={"user_id": USER, "provider": "google"})
    assert r.status_code == 200
    assert r.json()["rows_removed"] == 1
    assert gcal.load_tokens(USER, "google") is None


def test_disconnect_microsoft_is_honest_about_no_remote_revoke(api_client, ms_configured):
    _store_grant("microsoft")
    r = api_client.post("/api/calendar/disconnect",
                        json={"user_id": USER, "provider": "microsoft"})
    assert r.status_code == 200
    result = r.json()["results"][0]
    assert result["remote_revoked"] is False
    assert "no token-revoke endpoint" in result["remote_note"]
    assert gcal.load_tokens(USER, "microsoft") is None


def test_disconnect_all_providers_by_default(api_client, google_configured, ms_configured, http):
    _store_grant("google")
    _store_grant("microsoft")
    http.append(_resp(200, {}))
    r = api_client.post("/api/calendar/disconnect", json={"user_id": USER})
    assert r.status_code == 200
    assert r.json()["rows_removed"] == 2
    assert gcal.connected_providers(USER) == []


def test_connected_providers_reports_only_stored_grants():
    assert gcal.connected_providers(USER) == []
    _store_grant("google")
    _store_grant("microsoft")
    assert gcal.connected_providers(USER) == ["google", "microsoft"]
    assert gcal.delete_tokens(USER) == 2
    assert gcal.connected_providers() == []


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


def test_calendar_tokens_table_matches_the_frozen_contract():
    cols = {r["name"]: r for r in q("PRAGMA table_info(calendar_tokens)")}
    assert set(cols) == {
        "id", "user_id", "provider", "access_token", "refresh_token",
        "expires_at", "scopes", "created_at",
    }


def test_one_grant_per_user_and_provider():
    _store_grant("google")
    _store_grant("google")
    rows = q("SELECT id FROM calendar_tokens WHERE user_id=? AND provider='google'", (USER,))
    assert len(rows) == 1
