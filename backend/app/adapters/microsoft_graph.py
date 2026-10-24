"""Microsoft Graph calendar adapter - real Entra ID OAuth + real free/busy.

Same public surface as `google_calendar` (specs/CONTRACTS_V3.md §3):
`configured()`, `auth_url()`, `exchange_code()`, `free_busy()`,
`create_event()`.

Every endpoint, parameter and permission name below was read off Microsoft
Learn on 2026-10-25. Doc URLs are recorded in docs/CALENDAR_INTEGRATION.md.

VERIFIED 2026-10-25
  * Authorize (learn.microsoft.com/entra/identity-platform/
    v2-oauth2-auth-code-flow):
      GET https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize
      params: client_id, response_type=code, redirect_uri, response_mode=query,
      scope (space separated), state, prompt.
  * Token:
      POST https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token
      code form:    client_id, scope, code, redirect_uri,
                    grant_type=authorization_code, client_secret
      refresh form: client_id, scope, refresh_token,
                    grant_type=refresh_token, client_secret
      response: access_token, token_type ("Bearer"), expires_in, scope,
                refresh_token (ONLY when offline_access was requested),
                id_token (only when openid was requested).
      A dead/revoked refresh token or code returns `error: "invalid_grant"`.
      Microsoft MAY rotate the refresh token on every refresh and the docs say
      to discard the old one, so `save_tokens` overwrites it when one is sent.
  * Free/busy (learn.microsoft.com/graph/api/calendar-getschedule):
      POST https://graph.microsoft.com/v1.0/me/calendar/getSchedule
      body: schedules (SMTP addresses), startTime/endTime as
            dateTimeTimeZone {dateTime, timeZone}, availabilityViewInterval
      header: `Prefer: outlook.timezone` controls the response time zone -
            "If not specified, those time values are returned in UTC."
      response: {"value":[{scheduleId, availabilityView,
            scheduleItems:[{status, start:{dateTime,timeZone},
            end:{dateTime,timeZone}, subject?, location?, isPrivate?}],
            workingHours:{...}}]}
      status values seen in the reference example: busy, free, tentative,
            workingElsewhere, oof.
  * Create event (learn.microsoft.com/graph/api/user-post-events):
      POST https://graph.microsoft.com/v1.0/me/events
      body: subject, body{contentType,content}, start/end{dateTime,timeZone},
            location{displayName}, attendees[{emailAddress{address,name},
            type}], allowNewTimeProposals, transactionId.

PERMISSIONS - least privilege, two separate consent steps:
  read  -> https://graph.microsoft.com/Calendars.ReadBasic
           Documented as the LEAST PRIVILEGED permission for getSchedule.
           "Allows the app to read events in user calendars, except for
           properties such as body, attachments, and extensions." No admin
           consent required. We never read /me/events with it.
        +  offline_access  (not calendar data; it is the only way Microsoft
           will issue a refresh token, without which a nightly sync is
           impossible)
  write -> https://graph.microsoft.com/Calendars.ReadWrite
           The ONLY permission Microsoft documents for POST /me/events - there
           is no create-only calendar permission. Requested solely when the
           user separately consents to Still Here putting the agreed plan on
           their calendar.

HONEST LIMITATION, verified in the getSchedule permissions table: getSchedule
is "Not supported" for delegated **personal** Microsoft accounts. Free/busy
therefore works for work/school (Entra ID) accounts only. See
docs/CALENDAR_INTEGRATION.md.

Provider-neutral primitives (typed errors, `Busy`, the sealed token store,
busy->free inversion, the `availability` bridge) are imported from
`google_calendar` because specs/CONTRACTS_V3.md §7 grants agent H exactly two
adapter files and they must not be duplicated.
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode

from .google_calendar import (  # provider-neutral primitives - see module docstring
    AVAILABILITY_SOURCES,
    Busy,
    CalendarAuthError,
    CalendarError,
    CalendarNotConfigured,
    CalendarRateLimitError,
    CalendarServerError,
    CalendarTimeZoneError,
    CalendarTokenError,
    CalendarTransportError,
    StoredGrant,
    busy_to_free_windows,
    connected_providers,
    delete_tokens,
    load_tokens,
    make_state,
    parse_iso,
    parse_json,
    parse_state,
    raise_for_status,
    redact,
    request,
    save_tokens,
    sync_availability,
    to_utc_iso,
    token_storage_mode,
)

__all__ = [
    "PROVIDER", "READ_SCOPES", "WRITE_SCOPES", "configured", "auth_url",
    "exchange_code", "free_busy", "create_event", "status", "refresh_tokens",
    "access_token", "revoke", "sync_free_busy", "WINDOWS_TO_IANA",
    "CalendarError", "CalendarNotConfigured", "CalendarAuthError",
    "CalendarTokenError", "CalendarRateLimitError", "CalendarServerError",
    "CalendarTransportError", "CalendarTimeZoneError", "Busy", "StoredGrant",
    "busy_to_free_windows", "sync_availability", "load_tokens", "save_tokens",
    "delete_tokens", "connected_providers", "make_state", "parse_state",
    "AVAILABILITY_SOURCES",
]

PROVIDER = "microsoft"

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
LOGIN_BASE = "https://login.microsoftonline.com"
GETSCHEDULE_ENDPOINT = f"{GRAPH_BASE}/me/calendar/getSchedule"
EVENTS_ENDPOINT = f"{GRAPH_BASE}/me/events"

READ_SCOPES: tuple[str, ...] = (
    "offline_access",
    "https://graph.microsoft.com/Calendars.ReadBasic",
)
WRITE_SCOPES: tuple[str, ...] = ("https://graph.microsoft.com/Calendars.ReadWrite",)

#: Graph `scheduleItem.status` values that mean "do not schedule over this".
#: `tentative` counts as busy on purpose: proposing a time a friend has
#: tentatively held is the kind of small pressure this product exists to avoid.
#: `free` and `workingElsewhere` are available.
BUSY_STATUSES = frozenset({"busy", "oof"})
TENTATIVE_STATUSES = frozenset({"tentative"})


def _env(name: str, default: str = "") -> str:
    v = os.environ.get(name, default)
    return (v or "").strip().strip('"').strip("'")


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def tenant() -> str:
    """`MICROSOFT_TENANT_ID`, default `common`.

    `common` lets both work/school and personal accounts sign in; note that
    getSchedule is documented as unsupported for personal accounts, so a real
    deployment should pin this to its tenant id (or `organizations`).
    """
    return _env("MICROSOFT_TENANT_ID") or "common"


def client_id() -> str:
    return _env("MICROSOFT_CLIENT_ID")


def client_secret() -> str:
    return _env("MICROSOFT_CLIENT_SECRET")


def default_redirect_uri() -> str:
    explicit = _env("MICROSOFT_OAUTH_REDIRECT_URI")
    if explicit:
        return explicit
    base = _env("PUBLIC_BASE_URL") or "http://localhost:8000"
    return f"{base.rstrip('/')}/api/calendar/microsoft/callback"


def mailbox_address() -> str:
    """SMTP address passed in getSchedule's `schedules` array.

    getSchedule has no documented "self" alias - it takes SMTP addresses - and
    the frozen `calendar_tokens` schema (specs/CONTRACTS_V3.md §3) has no
    column to persist the connected mailbox. So the address comes from
    `MICROSOFT_CALENDAR_ADDRESS`, or is passed explicitly to `free_busy`.
    Recorded as a known limitation in docs/CALENDAR_INTEGRATION.md.
    """
    return _env("MICROSOFT_CALENDAR_ADDRESS")


def authorize_endpoint() -> str:
    return f"{LOGIN_BASE}/{tenant()}/oauth2/v2.0/authorize"


def token_endpoint() -> str:
    return f"{LOGIN_BASE}/{tenant()}/oauth2/v2.0/token"


def configured() -> bool:
    """True only when a real Microsoft OAuth exchange is actually possible."""
    return bool(client_id() and client_secret())


def _require_configured() -> None:
    if not configured():
        raise CalendarNotConfigured(
            "Microsoft Graph calendar is not configured: set "
            "MICROSOFT_CLIENT_ID and MICROSOFT_CLIENT_SECRET. Until then Still "
            "Here uses the seeded availability table and must not claim a "
            "calendar is connected."
        )


# ---------------------------------------------------------------------------
# Time zones. THE place calendar code breaks, so it is explicit here.
# ---------------------------------------------------------------------------
#
# Graph returns `dateTimeTimeZone`: a time string with NO offset plus a
# separate `timeZone` name, which may be a Windows name ("Pacific Standard
# Time") rather than IANA. Resolving a Windows name needs a tz database, and
# this repo's interpreter has no `tzdata` installed (checked 2026-10-25), so
# we do two things:
#   1. Ask Graph for UTC up front with `Prefer: outlook.timezone="UTC"`. The
#      reference explicitly documents that header, and that UTC is the default
#      when it is absent. This is the normal path and needs no tz database.
#   2. If a non-UTC zone still arrives, map Windows -> IANA and resolve with
#      `zoneinfo`. If that is impossible, raise `CalendarTimeZoneError`
#      instead of assuming an offset - a silent wrong guess would move every
#      free slot by hours.

#: Windows -> IANA, from the CLDR `windowsZones.xml` mapping
#: (github.com/unicode-org/cldr/blob/main/common/supplemental/windowsZones.xml).
#: Deliberately a short list of the zones a demo is likely to see rather than a
#: vendored copy of CLDR; an unlisted zone raises rather than guesses.
WINDOWS_TO_IANA: dict[str, str] = {
    "UTC": "Etc/UTC",
    "GMT Standard Time": "Europe/London",
    "Greenwich Standard Time": "Atlantic/Reykjavik",
    "W. Europe Standard Time": "Europe/Berlin",
    "Central Europe Standard Time": "Europe/Budapest",
    "Romance Standard Time": "Europe/Paris",
    "Eastern Standard Time": "America/New_York",
    "Central Standard Time": "America/Chicago",
    "Mountain Standard Time": "America/Denver",
    "Pacific Standard Time": "America/Los_Angeles",
    "Alaskan Standard Time": "America/Anchorage",
    "Hawaiian Standard Time": "Pacific/Honolulu",
    "India Standard Time": "Asia/Kolkata",
    "Tokyo Standard Time": "Asia/Tokyo",
    "China Standard Time": "Asia/Shanghai",
    "AUS Eastern Standard Time": "Australia/Sydney",
    "New Zealand Standard Time": "Pacific/Auckland",
}

_UTC_ALIASES = {"utc", "etc/utc", "gmt", "etc/gmt", "z", "coordinated universal time"}


def resolve_timezone(name: str):
    """Return a tzinfo for a Graph `timeZone` name, or None if unresolvable.

    Separated out so tests can exercise both branches without depending on
    whether the host happens to have a tz database.
    """
    key = (name or "").strip()
    if not key or key.lower() in _UTC_ALIASES:
        return timezone.utc
    iana = WINDOWS_TO_IANA.get(key, key)
    if iana.lower() in _UTC_ALIASES:
        return timezone.utc
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(iana)
    except Exception:
        return None


def parse_graph_datetime(dtz: dict[str, Any], *, field: str = "start") -> datetime:
    """Convert one Graph `dateTimeTimeZone` into an aware UTC datetime."""
    if not isinstance(dtz, dict) or not dtz.get("dateTime"):
        raise CalendarError(f"microsoft free_busy: {field} has no dateTime")
    raw = str(dtz["dateTime"])
    zone_name = str(dtz.get("timeZone") or "UTC")
    naive_or_aware = parse_iso(raw)
    # `parse_iso` assumes UTC for a naive string; re-stamp it with the zone
    # Graph actually named.
    if "+" not in raw and "-" not in raw[10:] and "Z" not in raw.upper():
        tz = resolve_timezone(zone_name)
        if tz is None:
            raise CalendarTimeZoneError(
                f"microsoft free_busy: cannot resolve time zone {zone_name!r} on "
                "this host (no tz database and no CLDR mapping entry). The "
                "adapter sends Prefer: outlook.timezone=\"UTC\" to avoid this; "
                "install `tzdata` or add the zone to WINDOWS_TO_IANA."
            )
        naive_or_aware = naive_or_aware.replace(tzinfo=None).replace(tzinfo=tz)
    return naive_or_aware.astimezone(timezone.utc)


# ---------------------------------------------------------------------------
# OAuth
# ---------------------------------------------------------------------------


def auth_url(
    user_id: str,
    redirect_uri: str | None = None,
    *,
    write: bool = False,
    state: str | None = None,
    prompt: str = "consent",
) -> str:
    """Build the consent URL the human visits.

    `write=False` (the default) asks only for `Calendars.ReadBasic` +
    `offline_access`. Writing an event is a separate consent step: call again
    with `write=True`, which adds `Calendars.ReadWrite`.
    """
    _require_configured()
    scopes = list(READ_SCOPES) + (list(WRITE_SCOPES) if write else [])
    params = {
        "client_id": client_id(),
        "response_type": "code",
        "redirect_uri": redirect_uri or default_redirect_uri(),
        "response_mode": "query",
        "scope": " ".join(scopes),
        "state": state or make_state(user_id, PROVIDER),
        "prompt": prompt,
    }
    return f"{authorize_endpoint()}?{urlencode(params)}"


def _token_request(action: str, form: dict[str, Any]) -> dict[str, Any]:
    resp = request(
        "POST", token_endpoint(),
        action=action,
        form=form,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    if resp.status_code >= 400:
        try:
            parsed = resp.json()
        except Exception:
            parsed = {}
        code = str((parsed or {}).get("error") or "")
        detail = json.dumps(redact(parsed))[:300] if parsed else "(no JSON body)"
        if code == "invalid_grant":
            raise CalendarTokenError(
                f"{action}: Microsoft returned invalid_grant - the code or "
                "refresh token is expired, revoked, or was already used. The "
                f"user must reconnect; no retry can fix this. {detail}",
                status=resp.status_code, body=detail,
            )
        raise_for_status(resp, action)
    tokens = parse_json(resp, action)
    if not isinstance(tokens, dict) or not tokens.get("access_token"):
        raise CalendarTokenError(
            f"{action}: response contained no access_token: "
            f"{json.dumps(redact(tokens))[:200]}"
        )
    return tokens


def exchange_code(code: str, redirect_uri: str | None = None) -> dict[str, Any]:
    """Swap an authorization code for tokens (frozen §3 signature).

    Returns the raw token response. The caller stores it with `save_tokens`;
    no API route may put any part of it in a response.
    """
    _require_configured()
    if not code:
        raise CalendarError("exchange_code: no authorization code supplied")
    return _token_request(
        "microsoft exchange_code",
        {
            "client_id": client_id(),
            "client_secret": client_secret(),
            "scope": " ".join(READ_SCOPES),
            "code": code,
            "redirect_uri": redirect_uri or default_redirect_uri(),
            "grant_type": "authorization_code",
        },
    )


def refresh_tokens(user_id: str) -> StoredGrant:
    """Refresh transparently. A dead refresh token raises `CalendarTokenError`.

    Microsoft may return a NEW refresh token and the docs say to discard the
    old one; `save_tokens` overwrites whenever one is present.
    """
    _require_configured()
    grant = load_tokens(user_id, PROVIDER)
    if grant is None:
        raise CalendarTokenError(
            f"no Microsoft calendar grant stored for user {user_id!r}; "
            "send them through /api/calendar/microsoft/auth first"
        )
    if not grant.refresh_token:
        raise CalendarTokenError(
            f"the stored Microsoft grant for user {user_id!r} has no refresh "
            "token (offline_access was not consented to); the user must reconnect"
        )
    scope = grant.scopes or " ".join(READ_SCOPES)
    tokens = _token_request(
        "microsoft refresh_tokens",
        {
            "client_id": client_id(),
            "client_secret": client_secret(),
            "scope": scope,
            "refresh_token": grant.refresh_token,
            "grant_type": "refresh_token",
        },
    )
    save_tokens(user_id, PROVIDER, tokens)
    fresh = load_tokens(user_id, PROVIDER)
    assert fresh is not None  # just written
    return fresh


def access_token(user_id: str) -> str:
    """A usable access token, refreshed transparently when expired."""
    grant = load_tokens(user_id, PROVIDER)
    if grant is None:
        raise CalendarTokenError(
            f"no Microsoft calendar grant stored for user {user_id!r}; "
            "send them through /api/calendar/microsoft/auth first"
        )
    if grant.expired():
        grant = refresh_tokens(user_id)
    return grant.access_token


def revoke(user_id: str) -> dict[str, Any]:
    """Forget the grant locally.

    Microsoft's documented revocation story is per-user sign-out / admin
    revocation (`POST /users/{id}/revokeSignInSessions`, which needs directory
    permissions this app does not and should not hold), so there is no
    token-revoke endpoint to call with only calendar scopes. We delete the row
    and say so honestly rather than pretending a remote revoke happened. The
    user can also remove the app at
    https://myaccount.microsoft.com/organizations.
    """
    return {
        "provider": PROVIDER,
        "remote_revoked": False,
        "remote_note": (
            "Microsoft Graph has no token-revoke endpoint available to a "
            "calendar-scoped app; the stored grant was deleted locally. The "
            "user can also revoke consent at "
            "https://myaccount.microsoft.com/organizations"
        ),
        "rows_removed": delete_tokens(user_id, PROVIDER),
    }


# ---------------------------------------------------------------------------
# Free/busy
# ---------------------------------------------------------------------------


def free_busy(
    user_id: str,
    window_start: str,
    window_end: str,
    *,
    schedules: list[str] | None = None,
    availability_view_interval: int = 30,
    include_tentative: bool = True,
    timeout: float = 20.0,
) -> list[Busy]:
    """Query real busy blocks for `user_id` between the two timestamps.

    `startTime`/`endTime` are sent as UTC `dateTimeTimeZone` objects and the
    response is pinned to UTC with `Prefer: outlook.timezone="UTC"`, so no
    Windows time-zone mapping is needed on the happy path.

    `scheduleItems` whose `status` is `free` or `workingElsewhere` are skipped;
    `busy` and `oof` are always busy; `tentative` is busy unless
    `include_tentative=False`.
    """
    _require_configured()
    addresses = schedules or ([mailbox_address()] if mailbox_address() else [])
    if not addresses:
        raise CalendarError(
            "microsoft free_busy: getSchedule needs at least one SMTP address. "
            "Set MICROSOFT_CALENDAR_ADDRESS or pass schedules=[...] - Graph has "
            "no 'self' alias for this endpoint."
        )
    ws, we = parse_iso(window_start), parse_iso(window_end)
    body = {
        "schedules": addresses,
        "startTime": {"dateTime": to_utc_iso(ws), "timeZone": "UTC"},
        "endTime": {"dateTime": to_utc_iso(we), "timeZone": "UTC"},
        "availabilityViewInterval": int(availability_view_interval),
    }
    resp = request(
        "POST", GETSCHEDULE_ENDPOINT,
        action="microsoft free_busy",
        headers={
            "Authorization": f"Bearer {access_token(user_id)}",
            "Content-Type": "application/json",
            "Prefer": 'outlook.timezone="UTC"',
        },
        json_body=body,
        timeout=timeout,
    )
    raise_for_status(resp, "microsoft free_busy")
    payload = parse_json(resp, "microsoft free_busy")
    entries = (payload or {}).get("value")
    if entries is None:
        raise CalendarError(
            "microsoft free_busy: response had no `value` collection: "
            f"{json.dumps(redact(payload))[:200]}"
        )

    busy_set = set(BUSY_STATUSES) | (set(TENTATIVE_STATUSES) if include_tentative else set())
    out: list[Busy] = []
    for entry in entries:
        schedule_id = str((entry or {}).get("scheduleId") or "")
        err = (entry or {}).get("error")
        if err:
            raise CalendarError(
                f"microsoft free_busy: {schedule_id or 'schedule'} could not be "
                f"read, so its availability is unknown: {json.dumps(redact(err))[:200]}"
            )
        for item in (entry or {}).get("scheduleItems") or []:
            status_name = str((item or {}).get("status") or "").strip().lower()
            if status_name not in busy_set:
                continue
            start = parse_graph_datetime(item.get("start") or {}, field="start")
            end = parse_graph_datetime(item.get("end") or {}, field="end")
            if end <= start:
                continue
            out.append(
                Busy(
                    start_at=to_utc_iso(start),
                    end_at=to_utc_iso(end),
                    provider=PROVIDER,
                    calendar_id=schedule_id or "me",
                )
            )
    out.sort(key=lambda b: b.start_at)
    return out


def sync_free_busy(
    user_id: str,
    window_start: str,
    window_end: str,
    *,
    schedules: list[str] | None = None,
    min_minutes: int = 60,
) -> list[dict[str, str]]:
    """`free_busy` -> `availability` rows `find_free_slots` already understands."""
    blocks = free_busy(user_id, window_start, window_end, schedules=schedules)
    return sync_availability(
        PROVIDER, user_id, window_start, window_end, busy=blocks, min_minutes=min_minutes
    )


# ---------------------------------------------------------------------------
# Create event (separate write consent)
# ---------------------------------------------------------------------------


def create_event(
    user_id: str,
    *,
    title: str,
    starts_at: str,
    ends_at: str,
    attendees: list[str] | None = None,
    location: str | None = None,
    description: str | None = None,
    transaction_id: str | None = None,
    allow_new_time_proposals: bool = True,
    timeout: float = 20.0,
) -> dict[str, Any]:
    """Create a real event. Requires the separately-consented write permission.

    Not retried: POST /me/events is not idempotent for us. Graph does offer a
    `transactionId` property "to reduce unnecessary retries on the server", so
    one is always sent (a uuid4 unless the caller supplies a stable key), which
    is the documented way to make a duplicate submission safe.

    `allowNewTimeProposals` is left on so an invitee can counter-propose a time
    rather than feel cornered - which is the whole posture of this product.
    """
    _require_configured()
    grant = load_tokens(user_id, PROVIDER)
    if grant is None:
        raise CalendarTokenError(
            f"no Microsoft calendar grant stored for user {user_id!r}"
        )
    granted = {s for s in grant.scopes.split(" ") if s}
    if granted and not any("readwrite" in s.lower() for s in granted):
        raise CalendarAuthError(
            "microsoft create_event: this grant only covers free/busy "
            f"({sorted(granted)}). Writing an event is a separate consent step "
            "- send the user to auth_url(write=True) first."
        )

    start_dt, end_dt = parse_iso(starts_at), parse_iso(ends_at)
    if end_dt <= start_dt:
        raise CalendarError("microsoft create_event: ends_at must be after starts_at")

    body: dict[str, Any] = {
        "subject": title,
        "start": {"dateTime": to_utc_iso(start_dt), "timeZone": "UTC"},
        "end": {"dateTime": to_utc_iso(end_dt), "timeZone": "UTC"},
        "allowNewTimeProposals": bool(allow_new_time_proposals),
        "transactionId": transaction_id or str(uuid.uuid4()),
    }
    if description:
        body["body"] = {"contentType": "text", "content": description}
    if location:
        body["location"] = {"displayName": location}
    if attendees:
        body["attendees"] = [
            {"emailAddress": {"address": a}, "type": "required"} for a in attendees if a
        ]

    resp = request(
        "POST", EVENTS_ENDPOINT,
        action="microsoft create_event",
        headers={
            "Authorization": f"Bearer {access_token(user_id)}",
            "Content-Type": "application/json",
            "Prefer": 'outlook.timezone="UTC"',
        },
        json_body=body,
        timeout=timeout,
        max_retries=0,
    )
    raise_for_status(resp, "microsoft create_event")
    created = parse_json(resp, "microsoft create_event")
    if not isinstance(created, dict):
        raise CalendarError("microsoft create_event: unexpected response shape")
    return {
        "provider": PROVIDER,
        "event_id": created.get("id"),
        "html_link": created.get("webLink"),
        "status": created.get("responseStatus", {}).get("response")
        if isinstance(created.get("responseStatus"), dict) else None,
        "ical_uid": created.get("iCalUId"),
        "starts_at": to_utc_iso(start_dt),
        "ends_at": to_utc_iso(end_dt),
    }


def status(user_id: str | None = None) -> dict[str, Any]:
    """Honest, token-free snapshot for `/api/calendar/status` and `/api/status`."""
    grant = load_tokens(user_id, PROVIDER) if user_id else None
    return {
        "provider": PROVIDER,
        "configured": configured(),
        "connected": grant is not None,
        "token_storage": token_storage_mode(),
        "read_scopes": list(READ_SCOPES),
        "write_scopes": list(WRITE_SCOPES),
        "tenant": tenant() if configured() else None,
        "mailbox_configured": bool(mailbox_address()),
        "redirect_uri": default_redirect_uri() if configured() else None,
        "grant": grant.public() if grant else None,
        "note": (
            "getSchedule is not supported for personal Microsoft accounts; a "
            "work or school (Entra ID) account is required."
        ),
    }
