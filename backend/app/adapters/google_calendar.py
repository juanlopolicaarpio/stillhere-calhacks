"""Google Calendar adapter - real OAuth 2.0 + real free/busy.

Every endpoint, form field, response field and scope string below was read off
the official Google documentation on 2026-10-25. Nothing here is guessed; the
doc URLs are recorded in `docs/CALENDAR_INTEGRATION.md` and repeated inline
next to the thing they verify.

VERIFIED 2026-10-25
  * Authorization endpoint (developers.google.com/identity/protocols/oauth2/
    web-server):  GET https://accounts.google.com/o/oauth2/v2/auth
    params: client_id, redirect_uri, response_type=code, scope (space
    delimited), access_type=offline, state, prompt, include_granted_scopes,
    login_hint.
  * Token endpoint: POST https://oauth2.googleapis.com/token
    code exchange form: code, client_id, client_secret, redirect_uri,
    grant_type=authorization_code
    response: access_token, expires_in, refresh_token (ONLY when
    access_type=offline), scope, token_type ("Bearer").
    refresh form: refresh_token, client_id, client_secret,
    grant_type=refresh_token - the response does NOT include a new
    refresh_token, so we keep the stored one.
    A revoked/expired refresh token returns error "invalid_grant".
  * Free/busy (developers.google.com/workspace/calendar/api/v3/reference/
    freebusy/query):  POST https://www.googleapis.com/calendar/v3/freeBusy
    body: timeMin, timeMax, timeZone, items:[{id}], calendarExpansionMax,
    groupExpansionMax
    response: {kind:"calendar#freeBusy", timeMin, timeMax,
               calendars:{<id>:{busy:[{start,end}], errors:[{domain,reason}]}}}
  * events.insert (developers.google.com/workspace/calendar/api/v3/reference/
    events/insert):  POST https://www.googleapis.com/calendar/v3/calendars/
    {calendarId}/events  with body summary, location, description,
    start/end {dateTime,timeZone}, attendees[{email}], and query param
    sendUpdates.
  * "primary" is the documented keyword for the signed-in user's own calendar
    (developers.google.com/workspace/calendar/api/v3/reference/events/list).

SCOPES - least privilege, deliberately two separate consent steps:
  read  -> https://www.googleapis.com/auth/calendar.freebusy
           "View your availability in your calendars." This grants the
           freeBusy query and NOTHING else: no event titles, no attendees,
           no descriptions, no calendar list. It is the narrowest scope Google
           publishes that can answer "when is this person busy".
  write -> https://www.googleapis.com/auth/calendar.events.owned
           "See, create, change, and delete events on Google calendars you
           own." Requested ONLY when the user separately consents to letting
           Still Here put the agreed plan on their calendar. See
           docs/CALENDAR_INTEGRATION.md for why the even tighter
           `calendar.app.created` is not used yet (the frozen `calendar_tokens`
           schema has nowhere to persist an app-created calendar id).

This module never decides whether an action is *permitted* - consent gating
stays in `services/consent.py`. It only talks to Google.

NOTE ON FILE LAYOUT: specs/CONTRACTS_V3.md §7 gives agent H exactly two adapter
files, so the provider-neutral primitives (typed errors, the `Busy` shape, the
encrypted token store, busy->free inversion, the `availability` bridge) live
here with public names and `microsoft_graph.py` imports them. They are not
Google-specific despite the module they sit in.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable
from urllib.parse import urlencode

import httpx

from ..db import ex, new_id, now_iso, q, q1

PROVIDER = "google"

AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
REVOKE_ENDPOINT = "https://oauth2.googleapis.com/revoke"
FREEBUSY_ENDPOINT = "https://www.googleapis.com/calendar/v3/freeBusy"
EVENTS_ENDPOINT = "https://www.googleapis.com/calendar/v3/calendars/{calendar_id}/events"

#: Read-only free/busy. The whole point: no event content ever reaches us.
READ_SCOPES: tuple[str, ...] = ("https://www.googleapis.com/auth/calendar.freebusy",)
#: Write, requested as a SEPARATE consent step (see `auth_url(write=True)`).
WRITE_SCOPES: tuple[str, ...] = ("https://www.googleapis.com/auth/calendar.events.owned",)


# ---------------------------------------------------------------------------
# Typed errors (provider-neutral; microsoft_graph.py imports these)
# ---------------------------------------------------------------------------


class CalendarError(Exception):
    """Base class for every calendar adapter failure."""

    def __init__(self, message: str, *, status: int | None = None, body: Any = None):
        super().__init__(message)
        self.status = status
        self.body = body


class CalendarNotConfigured(CalendarError):
    """No OAuth client credentials, so no real calendar call is possible."""


class CalendarAuthError(CalendarError):
    """The provider rejected the credentials or the grant (HTTP 401/403)."""


class CalendarTokenError(CalendarError):
    """The stored grant is unusable: no token row, or a dead refresh token.

    Distinct from `CalendarAuthError` because the remedy is different - the
    user has to re-consent through `auth_url()`; retrying cannot help.
    """


class CalendarRateLimitError(CalendarError):
    """The provider throttled us (HTTP 429, or Google's 403 rateLimitExceeded)."""


class CalendarServerError(CalendarError):
    """The provider failed on its own side (HTTP 5xx)."""


class CalendarTransportError(CalendarError):
    """The request never got a response at all (DNS/TCP/TLS/timeout)."""


class CalendarTimeZoneError(CalendarError):
    """A provider timestamp carried a time zone this host cannot resolve.

    Raised instead of silently assuming UTC: guessing an offset would quietly
    move every free slot by hours, which is exactly the bug class calendar
    code is famous for.
    """


# ---------------------------------------------------------------------------
# Secret redaction. Applied to EVERY provider body before it can reach an
# exception message, a return value or a log line.
# ---------------------------------------------------------------------------

_SECRET_KEYS = {
    "access_token", "refresh_token", "id_token", "code", "client_secret",
    "client_id", "assertion", "authorization", "token",
}
#: A long opaque run that looks like a bearer token / auth code. Catches
#: token material that arrives under a key we did not anticipate.
_TOKENISH_RE = re.compile(r"[A-Za-z0-9_\-\.]{24,}")


def redact(payload: Any) -> Any:
    """Recursively mask token material. Keys first, then a generic pattern."""
    if isinstance(payload, dict):
        out: dict[str, Any] = {}
        for k, v in payload.items():
            if str(k).lower() in _SECRET_KEYS:
                out[k] = "***redacted***"
            else:
                out[k] = redact(v)
        return out
    if isinstance(payload, list):
        return [redact(v) for v in payload]
    if isinstance(payload, str):
        return _TOKENISH_RE.sub("***redacted***", payload)
    return payload


def _safe_body(resp: httpx.Response) -> str:
    """A short, token-free rendering of a response body for an error message."""
    try:
        parsed = resp.json()
    except Exception:
        return str(redact(resp.text[:400]))
    return json.dumps(redact(parsed))[:400]


# ---------------------------------------------------------------------------
# Encrypted-at-rest token storage
# ---------------------------------------------------------------------------
#
# This repo's virtualenv has no `cryptography` (checked 2026-10-25: not
# installed), and adding a dependency is out of scope for this agent, so AES-GCM
# and Fernet are unavailable. Rather than claim encryption we do not have, the
# sealing below is built from the standard library only:
#
#   HKDF-SHA256(master) -> (k_enc, k_mac)
#   keystream = HMAC-SHA256(k_enc, nonce || be32(counter)) blocks
#   ciphertext = plaintext XOR keystream
#   tag = HMAC-SHA256(k_mac, nonce || ciphertext)        (encrypt-then-MAC)
#
# Standard primitives in a textbook encrypt-then-MAC composition with a fresh
# 16-byte random nonce per value. It is NOT AES-GCM and is NOT FIPS-blessed;
# it is honest confidentiality-at-rest against someone who reads `stillhere.db`
# without `CALENDAR_TOKEN_KEY`. If `CALENDAR_TOKEN_KEY` is unset, tokens are
# stored in PLAINTEXT and `token_storage_mode()` says so - see
# docs/CALENDAR_INTEGRATION.md "Known limitations".

_SEAL_PREFIX = "shv1:"


def _env(name: str, default: str = "") -> str:
    """Read an env var at CALL time, stripped of stray quotes.

    Deliberately not routed through `config.Settings`: that class snapshots
    `os.getenv` at class-definition time and `config.py` is lead-owned
    (specs/CONTRACTS_V3.md §7). Reading here keeps `configured()` honest and
    keeps tests able to monkeypatch the environment.
    """
    v = os.environ.get(name, default)
    return (v or "").strip().strip('"').strip("'")


def _hkdf(master: bytes, info: bytes, length: int = 32) -> bytes:
    """HKDF-SHA256 (RFC 5869) with an empty salt."""
    prk = hmac.new(b"\x00" * 32, master, hashlib.sha256).digest()
    out, block, counter = b"", b"", 1
    while len(out) < length:
        block = hmac.new(prk, block + info + bytes([counter]), hashlib.sha256).digest()
        out += block
        counter += 1
    return out[:length]


def _keystream(k_enc: bytes, nonce: bytes, nbytes: int) -> bytes:
    out, counter = b"", 0
    while len(out) < nbytes:
        out += hmac.new(
            k_enc, nonce + counter.to_bytes(4, "big"), hashlib.sha256
        ).digest()
        counter += 1
    return out[:nbytes]


def token_storage_mode() -> str:
    """'sealed' when CALENDAR_TOKEN_KEY is set, else 'plaintext'. Never lies."""
    return "sealed" if _env("CALENDAR_TOKEN_KEY") else "plaintext"


def seal(value: str) -> str:
    """Encrypt-then-MAC a token for storage. Pass-through when no key is set."""
    key = _env("CALENDAR_TOKEN_KEY")
    if not key or not value:
        return value
    master = hashlib.sha256(key.encode("utf-8")).digest()
    k_enc = _hkdf(master, b"stillhere-calendar-enc")
    k_mac = _hkdf(master, b"stillhere-calendar-mac")
    nonce = secrets.token_bytes(16)
    raw = value.encode("utf-8")
    ct = bytes(a ^ b for a, b in zip(raw, _keystream(k_enc, nonce, len(raw))))
    tag = hmac.new(k_mac, nonce + ct, hashlib.sha256).digest()
    blob = base64.urlsafe_b64encode(nonce + tag + ct).decode("ascii")
    return _SEAL_PREFIX + blob


def unseal(stored: str) -> str:
    """Inverse of `seal`. Values without the prefix are returned unchanged, so
    a database written before a key existed still reads back.
    """
    if not stored or not stored.startswith(_SEAL_PREFIX):
        return stored
    key = _env("CALENDAR_TOKEN_KEY")
    if not key:
        raise CalendarTokenError(
            "stored calendar token is sealed but CALENDAR_TOKEN_KEY is not set; "
            "the user must reconnect their calendar"
        )
    master = hashlib.sha256(key.encode("utf-8")).digest()
    k_enc = _hkdf(master, b"stillhere-calendar-enc")
    k_mac = _hkdf(master, b"stillhere-calendar-mac")
    try:
        blob = base64.urlsafe_b64decode(stored[len(_SEAL_PREFIX):].encode("ascii"))
    except Exception as e:
        raise CalendarTokenError("stored calendar token is corrupt") from e
    if len(blob) < 48:
        raise CalendarTokenError("stored calendar token is corrupt")
    nonce, tag, ct = blob[:16], blob[16:48], blob[48:]
    if not hmac.compare_digest(tag, hmac.new(k_mac, nonce + ct, hashlib.sha256).digest()):
        raise CalendarTokenError(
            "stored calendar token failed its integrity check (wrong "
            "CALENDAR_TOKEN_KEY, or the row was tampered with)"
        )
    return bytes(a ^ b for a, b in zip(ct, _keystream(k_enc, nonce, len(ct)))).decode("utf-8")


# ---------------------------------------------------------------------------
# calendar_tokens table access
# ---------------------------------------------------------------------------


@dataclass
class StoredGrant:
    """A token row, decrypted. NEVER serialise this into a response or a log."""

    id: str
    user_id: str
    provider: str
    access_token: str
    refresh_token: str
    expires_at: str | None
    scopes: str
    created_at: str

    def expired(self, *, skew_seconds: int = 60, now: datetime | None = None) -> bool:
        """True when the access token is at/near expiry. Treated as expired when
        `expires_at` is missing, so we refresh rather than send a dead token.
        """
        if not self.expires_at:
            return True
        try:
            exp = parse_iso(self.expires_at)
        except Exception:
            return True
        ref = now or datetime.now(timezone.utc)
        return exp <= ref + timedelta(seconds=skew_seconds)

    def public(self) -> dict[str, Any]:
        """The ONLY representation allowed to leave the backend. No tokens."""
        return {
            "user_id": self.user_id,
            "provider": self.provider,
            "scopes": [s for s in self.scopes.split(" ") if s],
            "expires_at": self.expires_at,
            "connected_at": self.created_at,
            "has_refresh_token": bool(self.refresh_token),
            "token_storage": token_storage_mode(),
        }


def save_tokens(user_id: str, provider: str, tokens: dict[str, Any]) -> str:
    """Upsert one (user_id, provider) grant from a raw token response.

    `expires_at` is computed from `expires_in` (seconds, per both providers'
    docs) at the moment of storage. An absent `refresh_token` keeps whatever
    we already had: Google omits it on refresh, and Microsoft may too.
    """
    access = str(tokens.get("access_token") or "")
    if not access:
        raise CalendarTokenError(
            f"{provider} token response contained no access_token: "
            f"{json.dumps(redact(tokens))[:200]}"
        )
    refresh = str(tokens.get("refresh_token") or "")
    scopes = str(tokens.get("scope") or "")
    expires_at = None
    try:
        expires_in = int(tokens.get("expires_in"))  # type: ignore[arg-type]
        expires_at = (
            datetime.now(timezone.utc) + timedelta(seconds=expires_in)
        ).isoformat()
    except (TypeError, ValueError):
        expires_at = None

    existing = q1(
        "SELECT id, refresh_token, scopes FROM calendar_tokens"
        " WHERE user_id=? AND provider=?",
        (user_id, provider),
    )
    if existing is not None:
        if not refresh:
            refresh = unseal(existing["refresh_token"] or "")
        if not scopes:
            scopes = existing["scopes"] or ""
        ex(
            "UPDATE calendar_tokens SET access_token=?, refresh_token=?,"
            " expires_at=?, scopes=? WHERE id=?",
            (seal(access), seal(refresh), expires_at, scopes, existing["id"]),
        )
        return str(existing["id"])

    tid = new_id("caltok")
    ex(
        "INSERT INTO calendar_tokens (id, user_id, provider, access_token,"
        " refresh_token, expires_at, scopes, created_at) VALUES (?,?,?,?,?,?,?,?)",
        (tid, user_id, provider, seal(access), seal(refresh), expires_at, scopes, now_iso()),
    )
    return tid


def load_tokens(user_id: str, provider: str) -> StoredGrant | None:
    row = q1(
        "SELECT * FROM calendar_tokens WHERE user_id=? AND provider=?",
        (user_id, provider),
    )
    if row is None:
        return None
    return StoredGrant(
        id=row["id"],
        user_id=row["user_id"],
        provider=row["provider"],
        access_token=unseal(row["access_token"] or ""),
        refresh_token=unseal(row["refresh_token"] or ""),
        expires_at=row["expires_at"],
        scopes=row["scopes"] or "",
        created_at=row["created_at"],
    )


def delete_tokens(user_id: str, provider: str | None = None) -> int:
    """Forget a grant. Returns the number of rows removed."""
    if provider:
        rows = q(
            "SELECT id FROM calendar_tokens WHERE user_id=? AND provider=?",
            (user_id, provider),
        )
        ex("DELETE FROM calendar_tokens WHERE user_id=? AND provider=?", (user_id, provider))
    else:
        rows = q("SELECT id FROM calendar_tokens WHERE user_id=?", (user_id,))
        ex("DELETE FROM calendar_tokens WHERE user_id=?", (user_id,))
    return len(rows)


def connected_providers(user_id: str | None = None) -> list[str]:
    """Providers with a stored grant. Used for honest mode reporting."""
    if user_id:
        rows = q(
            "SELECT DISTINCT provider FROM calendar_tokens WHERE user_id=?", (user_id,)
        )
    else:
        rows = q("SELECT DISTINCT provider FROM calendar_tokens")
    return sorted(str(r["provider"]) for r in rows)


# ---------------------------------------------------------------------------
# OAuth state: signed, stateless, carries the user id
# ---------------------------------------------------------------------------


def _state_secret() -> str:
    return _env("CALENDAR_OAUTH_STATE_SECRET") or _env("WEBHOOK_SECRET") or "dev-only-change-me"


def make_state(user_id: str, provider: str) -> str:
    """`base64url(json).hmac` - CSRF nonce plus the user id, signed.

    Signed rather than stored so the callback needs no server-side session and
    an attacker cannot point someone else's consent at another user's row.
    """
    payload = {"u": user_id, "p": provider, "n": secrets.token_urlsafe(16), "t": int(time.time())}
    raw = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode()
    sig = hmac.new(_state_secret().encode(), raw.encode(), hashlib.sha256).hexdigest()[:32]
    return f"{raw}.{sig}"


def parse_state(state: str, *, max_age_seconds: int = 900) -> dict[str, Any]:
    """Verify and decode `state`. Raises `CalendarAuthError` if it is not ours."""
    try:
        raw, sig = str(state).rsplit(".", 1)
    except ValueError as e:
        raise CalendarAuthError("oauth state is malformed") from e
    expected = hmac.new(_state_secret().encode(), raw.encode(), hashlib.sha256).hexdigest()[:32]
    if not hmac.compare_digest(sig, expected):
        raise CalendarAuthError("oauth state signature does not verify")
    try:
        payload = json.loads(base64.urlsafe_b64decode(raw.encode()))
    except Exception as e:
        raise CalendarAuthError("oauth state payload is unreadable") from e
    issued = int(payload.get("t") or 0)
    if max_age_seconds and (time.time() - issued) > max_age_seconds:
        raise CalendarAuthError("oauth state has expired; start the connect flow again")
    return payload


# ---------------------------------------------------------------------------
# Time handling. See docs/CALENDAR_INTEGRATION.md "Timezones".
# ---------------------------------------------------------------------------

_FRACTION_RE = re.compile(r"(\.\d{1,6})\d+")


def parse_iso(ts: str) -> datetime:
    """Parse a provider timestamp into an aware UTC-comparable datetime.

    * `Z` and `+hh:mm` offsets are honoured exactly as sent.
    * Fractional seconds longer than 6 digits are truncated - Microsoft Graph
      sends 7 (`"2019-03-15T12:00:00.0000000"`), which older Pythons reject.
    * A naive timestamp is assumed UTC. This is safe for the paths we use:
      Google freeBusy always returns an offset, and the Graph adapter pins the
      response time zone to UTC with `Prefer: outlook.timezone="UTC"`.
    * Matches `services/scheduling.py::_parse` behaviour so the `availability`
      rows we write compare correctly against seeded ones.
    """
    s = str(ts).strip()
    if not s:
        raise CalendarError("empty timestamp from provider")
    s = _FRACTION_RE.sub(r"\1", s)
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError as e:
        raise CalendarError(f"unparseable provider timestamp: {s!r}") from e
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def to_utc_iso(d: datetime) -> str:
    return d.astimezone(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# The free/busy shape every provider normalises to
# ---------------------------------------------------------------------------


@dataclass
class Busy:
    """One busy block, always normalised to UTC ISO-8601.

    Carries no event content: not a title, not an attendee, not a description.
    The read scopes we request cannot even see those.
    """

    start_at: str
    end_at: str
    provider: str
    calendar_id: str = "primary"

    def as_dict(self) -> dict[str, str]:
        return {
            "start_at": self.start_at,
            "end_at": self.end_at,
            "provider": self.provider,
            "calendar_id": self.calendar_id,
        }

    @property
    def start(self) -> datetime:
        return parse_iso(self.start_at)

    @property
    def end(self) -> datetime:
        return parse_iso(self.end_at)


def busy_to_free_windows(
    busy: Iterable[Busy],
    window_start: str | datetime,
    window_end: str | datetime,
    *,
    min_minutes: int = 60,
) -> list[tuple[datetime, datetime]]:
    """Invert busy blocks into free windows inside [window_start, window_end].

    Overlapping and touching busy blocks are merged first, so a calendar with
    back-to-back meetings does not produce zero-length phantom gaps. Windows
    shorter than `min_minutes` are dropped, matching the `min_minutes`
    contract `services/scheduling.find_free_slots` already uses.
    """
    ws = window_start if isinstance(window_start, datetime) else parse_iso(window_start)
    we = window_end if isinstance(window_end, datetime) else parse_iso(window_end)
    ws, we = ws.astimezone(timezone.utc), we.astimezone(timezone.utc)
    if we <= ws:
        return []

    clipped: list[tuple[datetime, datetime]] = []
    for b in busy:
        a, z = max(b.start.astimezone(timezone.utc), ws), min(b.end.astimezone(timezone.utc), we)
        if z > a:
            clipped.append((a, z))
    clipped.sort(key=lambda t: t[0])

    merged: list[list[datetime]] = []
    for a, z in clipped:
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], z)
        else:
            merged.append([a, z])

    free: list[tuple[datetime, datetime]] = []
    cursor = ws
    for a, z in merged:
        if a - cursor >= timedelta(minutes=min_minutes):
            free.append((cursor, a))
        cursor = max(cursor, z)
    if we - cursor >= timedelta(minutes=min_minutes):
        free.append((cursor, we))
    return free


#: `availability.source` values written by a real calendar, so a sync can
#: replace only its own rows and never clobber the seeded demo data.
AVAILABILITY_SOURCES = {"google": "google_calendar", "microsoft": "microsoft_graph"}


def sync_availability(
    provider: str,
    user_id: str,
    window_start: str,
    window_end: str,
    *,
    busy: Iterable[Busy] | None = None,
    min_minutes: int = 60,
    replace: bool = True,
) -> list[dict[str, str]]:
    """Write real free windows into the `availability` table.

    This is the whole point of the integration: after this runs,
    `scheduling.find_free_slots` works unchanged on real calendar data,
    because it only ever reads `availability(user_id, start_at, end_at)`.

    `replace=True` first removes this provider's previous rows for the user, so
    re-syncing is idempotent. Seeded rows (`source='seed'`) are never touched -
    if a provider is not connected, seeded availability remains the source of
    truth exactly as specs/CONTRACTS_V3.md §3 requires.
    """
    source = AVAILABILITY_SOURCES.get(provider, f"{provider}_calendar")
    blocks = list(busy) if busy is not None else []
    free = busy_to_free_windows(blocks, window_start, window_end, min_minutes=min_minutes)
    if replace:
        ex("DELETE FROM availability WHERE user_id=? AND source=?", (user_id, source))
    written: list[dict[str, str]] = []
    for a, z in free:
        ex(
            "INSERT INTO availability (id, user_id, start_at, end_at, source, visibility)"
            " VALUES (?,?,?,?,?,'agent_internal')",
            (new_id("avail"), user_id, to_utc_iso(a), to_utc_iso(z), source),
        )
        written.append({"start_at": to_utc_iso(a), "end_at": to_utc_iso(z)})
    return written


# ---------------------------------------------------------------------------
# HTTP plumbing: bounded retries, typed errors, no secrets in messages
# ---------------------------------------------------------------------------

_RETRYABLE = {429, 500, 502, 503, 504}
_BACKOFF_SECONDS = 0.05  # small on purpose: the suite must stay fast


def request(
    method: str,
    url: str,
    *,
    action: str,
    headers: dict[str, str] | None = None,
    json_body: dict[str, Any] | None = None,
    form: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
    timeout: float = 20.0,
    max_retries: int = 2,
) -> httpx.Response:
    """One request with bounded retries on transport failures and 429/5xx.

    Other 4xx are never retried: repeating a rejected request just repeats the
    rejection. Reads and OAuth token calls are safe to retry; `create_event`
    deliberately passes `max_retries=0` (see there).
    """
    attempts = max_retries + 1
    resp: httpx.Response | None = None
    for attempt in range(attempts):
        try:
            with httpx.Client(timeout=timeout) as client:
                resp = client.request(
                    method, url, headers=headers, json=json_body, data=form, params=params
                )
        except httpx.HTTPError as e:
            if attempt < attempts - 1:
                time.sleep(_BACKOFF_SECONDS)
                continue
            raise CalendarTransportError(
                f"{action}: {method} failed after {attempt + 1} attempt(s): {e}"
            ) from e
        if resp.status_code in _RETRYABLE and attempt < attempts - 1:
            time.sleep(_BACKOFF_SECONDS)
            continue
        return resp
    assert resp is not None  # pragma: no cover
    return resp


def raise_for_status(resp: httpx.Response, action: str) -> None:
    """Map an HTTP failure onto this module's typed errors, body redacted.

    Google reports quota exhaustion as 403 with reason `rateLimitExceeded` /
    `userRateLimitExceeded` as well as as 429, so 403 is inspected rather than
    assumed to be an authorization problem.
    """
    if resp.status_code < 400:
        return
    body = _safe_body(resp)
    if resp.status_code == 429:
        raise CalendarRateLimitError(f"{action}: rate limited (HTTP 429)", status=429, body=body)
    if resp.status_code == 403 and "ratelimit" in body.lower().replace(" ", ""):
        raise CalendarRateLimitError(
            f"{action}: rate limited (HTTP 403 rateLimitExceeded)", status=403, body=body
        )
    if resp.status_code in (401, 403):
        raise CalendarAuthError(
            f"{action}: provider rejected the request (HTTP {resp.status_code}): {body}",
            status=resp.status_code, body=body,
        )
    if resp.status_code >= 500:
        raise CalendarServerError(
            f"{action}: provider error (HTTP {resp.status_code})",
            status=resp.status_code, body=body,
        )
    raise CalendarError(
        f"{action}: HTTP {resp.status_code}: {body}", status=resp.status_code, body=body
    )


def parse_json(resp: httpx.Response, action: str) -> Any:
    try:
        return resp.json()
    except Exception as e:
        raise CalendarError(
            f"{action}: provider returned a non-JSON body: "
            f"{redact(resp.text[:200])!r}",
            status=resp.status_code,
        ) from e


# ---------------------------------------------------------------------------
# Google: configuration
# ---------------------------------------------------------------------------


def client_id() -> str:
    return _env("GOOGLE_CLIENT_ID")


def client_secret() -> str:
    return _env("GOOGLE_CLIENT_SECRET")


def default_redirect_uri() -> str:
    """`GOOGLE_OAUTH_REDIRECT_URI`, else PUBLIC_BASE_URL + the callback route."""
    explicit = _env("GOOGLE_OAUTH_REDIRECT_URI")
    if explicit:
        return explicit
    base = _env("PUBLIC_BASE_URL") or "http://localhost:8000"
    return f"{base.rstrip('/')}/api/calendar/google/callback"


def target_calendar_id() -> str:
    """Calendar that `create_event` writes to. "primary" is Google's documented
    keyword for the signed-in user's own calendar.
    """
    return _env("GOOGLE_CALENDAR_ID") or "primary"


def configured() -> bool:
    """True only when a real Google OAuth exchange is actually possible.

    Both halves of a confidential-client credential are required. False here
    means `/api/status` reports `calendar_mode: "seeded"` and the seeded
    `availability` table stays the source of truth.
    """
    return bool(client_id() and client_secret())


def _require_configured() -> None:
    if not configured():
        raise CalendarNotConfigured(
            "Google Calendar is not configured: set GOOGLE_CLIENT_ID and "
            "GOOGLE_CLIENT_SECRET. Until then Still Here uses the seeded "
            "availability table and must not claim a calendar is connected."
        )


# ---------------------------------------------------------------------------
# Google: OAuth
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

    `write=False` (the default) asks ONLY for `calendar.freebusy`. Writing an
    event to someone's calendar is a separate, later consent step - call again
    with `write=True`, which adds `calendar.events.owned` and, via
    `include_granted_scopes=true`, keeps the already-granted read scope.

    `access_type=offline` is what makes Google return a `refresh_token`;
    without it the grant dies in an hour and nightly syncing is impossible.
    """
    _require_configured()
    scopes = list(READ_SCOPES) + (list(WRITE_SCOPES) if write else [])
    params = {
        "client_id": client_id(),
        "redirect_uri": redirect_uri or default_redirect_uri(),
        "response_type": "code",
        "scope": " ".join(scopes),
        "access_type": "offline",
        "include_granted_scopes": "true",
        "state": state or make_state(user_id, PROVIDER),
        "prompt": prompt,
    }
    return f"{AUTH_ENDPOINT}?{urlencode(params)}"


def exchange_code(code: str, redirect_uri: str | None = None) -> dict[str, Any]:
    """Swap an authorization code for tokens.

    Returns the raw token response (the frozen §3 signature says `dict #
    tokens`). The caller stores it with `save_tokens`; no API route may put any
    part of this dict in a response. Error bodies are redacted before they can
    reach an exception message.
    """
    _require_configured()
    if not code:
        raise CalendarError("exchange_code: no authorization code supplied")
    resp = request(
        "POST", TOKEN_ENDPOINT,
        action="google exchange_code",
        form={
            "code": code,
            "client_id": client_id(),
            "client_secret": client_secret(),
            "redirect_uri": redirect_uri or default_redirect_uri(),
            "grant_type": "authorization_code",
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    if resp.status_code >= 400:
        body = _safe_body(resp)
        if "invalid_grant" in body:
            raise CalendarTokenError(
                "google exchange_code: the authorization code is invalid, "
                "already used, or expired (invalid_grant). Start the connect "
                "flow again.",
                status=resp.status_code, body=body,
            )
        raise_for_status(resp, "google exchange_code")
    tokens = parse_json(resp, "google exchange_code")
    if not isinstance(tokens, dict) or not tokens.get("access_token"):
        raise CalendarTokenError(
            "google exchange_code: response contained no access_token: "
            f"{json.dumps(redact(tokens))[:200]}"
        )
    return tokens


def refresh_tokens(user_id: str) -> StoredGrant:
    """Exchange the stored refresh token for a fresh access token.

    Google's refresh response does NOT include a new `refresh_token`, so
    `save_tokens` keeps the stored one. A dead or revoked refresh token comes
    back as `invalid_grant`; that is unrecoverable without the user
    re-consenting, so it raises `CalendarTokenError` with a message that says
    exactly that - and never echoes the token.
    """
    _require_configured()
    grant = load_tokens(user_id, PROVIDER)
    if grant is None:
        raise CalendarTokenError(
            f"no Google calendar grant stored for user {user_id!r}; "
            "send them through /api/calendar/google/auth first"
        )
    if not grant.refresh_token:
        raise CalendarTokenError(
            f"the stored Google grant for user {user_id!r} has no refresh token "
            "(the consent screen was shown without access_type=offline); "
            "the user must reconnect"
        )
    resp = request(
        "POST", TOKEN_ENDPOINT,
        action="google refresh_tokens",
        form={
            "refresh_token": grant.refresh_token,
            "client_id": client_id(),
            "client_secret": client_secret(),
            "grant_type": "refresh_token",
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    if resp.status_code >= 400:
        body = _safe_body(resp)
        if "invalid_grant" in body:
            raise CalendarTokenError(
                "google refresh_tokens: the refresh token has been revoked or "
                "expired (invalid_grant). The user must reconnect their Google "
                "Calendar; no retry can fix this.",
                status=resp.status_code, body=body,
            )
        raise_for_status(resp, "google refresh_tokens")
    tokens = parse_json(resp, "google refresh_tokens")
    save_tokens(user_id, PROVIDER, tokens if isinstance(tokens, dict) else {})
    fresh = load_tokens(user_id, PROVIDER)
    assert fresh is not None  # just written
    return fresh


def access_token(user_id: str) -> str:
    """Return a usable access token, refreshing transparently when expired."""
    grant = load_tokens(user_id, PROVIDER)
    if grant is None:
        raise CalendarTokenError(
            f"no Google calendar grant stored for user {user_id!r}; "
            "send them through /api/calendar/google/auth first"
        )
    if grant.expired():
        grant = refresh_tokens(user_id)
    return grant.access_token


def revoke(user_id: str) -> dict[str, Any]:
    """Best-effort revoke at Google, then forget the row locally.

    The local delete happens even when the network call fails: a user asking to
    disconnect must end up disconnected here regardless of Google's mood.
    """
    result: dict[str, Any] = {"provider": PROVIDER, "remote_revoked": False}
    grant = load_tokens(user_id, PROVIDER)
    if grant is not None and configured():
        token = grant.refresh_token or grant.access_token
        try:
            resp = request(
                "POST", REVOKE_ENDPOINT,
                action="google revoke",
                form={"token": token},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                max_retries=0,
            )
            result["remote_revoked"] = resp.status_code < 400
            if resp.status_code >= 400:
                result["remote_error"] = f"HTTP {resp.status_code}"
        except CalendarError as e:
            result["remote_error"] = str(e)
    result["rows_removed"] = delete_tokens(user_id, PROVIDER)
    return result


# ---------------------------------------------------------------------------
# Google: free/busy
# ---------------------------------------------------------------------------


def free_busy(
    user_id: str,
    window_start: str,
    window_end: str,
    *,
    calendar_ids: list[str] | None = None,
    timeout: float = 20.0,
) -> list[Busy]:
    """Query real busy blocks for `user_id` between the two timestamps.

    `timeMin`/`timeMax` are sent as explicit UTC instants, so the answer does
    not depend on the calendar's display time zone. Per-calendar `errors` in
    the response (e.g. `notFound` for a calendar the grant cannot see) are
    surfaced as a `CalendarError` rather than silently returning "free" - a
    calendar we failed to read must never look like free time.
    """
    _require_configured()
    ids = calendar_ids or [target_calendar_id()]
    body = {
        "timeMin": to_utc_iso(parse_iso(window_start)),
        "timeMax": to_utc_iso(parse_iso(window_end)),
        "timeZone": "UTC",
        "items": [{"id": cid} for cid in ids],
    }
    resp = request(
        "POST", FREEBUSY_ENDPOINT,
        action="google free_busy",
        headers={
            "Authorization": f"Bearer {access_token(user_id)}",
            "Content-Type": "application/json",
        },
        json_body=body,
        timeout=timeout,
    )
    raise_for_status(resp, "google free_busy")
    payload = parse_json(resp, "google free_busy")
    calendars = (payload or {}).get("calendars") or {}

    out: list[Busy] = []
    problems: list[str] = []
    for cid, entry in calendars.items():
        for err in (entry or {}).get("errors") or []:
            problems.append(f"{cid}: {err.get('reason') or err}")
        for block in (entry or {}).get("busy") or []:
            start, end = block.get("start"), block.get("end")
            if not start or not end:
                continue
            out.append(
                Busy(
                    start_at=to_utc_iso(parse_iso(start)),
                    end_at=to_utc_iso(parse_iso(end)),
                    provider=PROVIDER,
                    calendar_id=str(cid),
                )
            )
    if problems:
        raise CalendarError(
            "google free_busy: the calendar could not be read, so its "
            "availability is unknown: " + "; ".join(problems)
        )
    out.sort(key=lambda b: b.start_at)
    return out


def sync_free_busy(
    user_id: str, window_start: str, window_end: str, *, min_minutes: int = 60
) -> list[dict[str, str]]:
    """`free_busy` -> `availability` rows `find_free_slots` already understands."""
    blocks = free_busy(user_id, window_start, window_end)
    return sync_availability(
        PROVIDER, user_id, window_start, window_end, busy=blocks, min_minutes=min_minutes
    )


# ---------------------------------------------------------------------------
# Google: create event (separate write consent)
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
    calendar_id: str | None = None,
    send_updates: str = "all",
    timeout: float = 20.0,
) -> dict[str, Any]:
    """Create a real event. Requires the separately-consented write scope.

    NOT retried (`max_retries=0`): events.insert is not idempotent, and a lost
    response plus a retry means two events on a real person's calendar. An
    ambiguous outcome surfaces as an error instead.

    `start`/`end` are sent as `dateTime` with an explicit UTC offset plus
    `timeZone: "UTC"`, so the event lands at the instant the plan agreed on no
    matter what the calendar's display zone is. No token, and no clinical or
    memory content, ever appears in the payload - only the fields passed in.
    """
    _require_configured()
    grant = load_tokens(user_id, PROVIDER)
    if grant is None:
        raise CalendarTokenError(
            f"no Google calendar grant stored for user {user_id!r}"
        )
    granted = set(s for s in grant.scopes.split(" ") if s)
    if granted and not granted.intersection(
        set(WRITE_SCOPES) | {"https://www.googleapis.com/auth/calendar",
                             "https://www.googleapis.com/auth/calendar.events"}
    ):
        raise CalendarAuthError(
            "google create_event: this grant only covers free/busy "
            f"({sorted(granted)}). Writing an event is a separate consent "
            "step - send the user to auth_url(write=True) first."
        )

    start_dt, end_dt = parse_iso(starts_at), parse_iso(ends_at)
    if end_dt <= start_dt:
        raise CalendarError("google create_event: ends_at must be after starts_at")

    body: dict[str, Any] = {
        "summary": title,
        "start": {"dateTime": to_utc_iso(start_dt), "timeZone": "UTC"},
        "end": {"dateTime": to_utc_iso(end_dt), "timeZone": "UTC"},
    }
    if location:
        body["location"] = location
    if description:
        body["description"] = description
    if attendees:
        body["attendees"] = [{"email": a} for a in attendees if a]

    resp = request(
        "POST", EVENTS_ENDPOINT.format(calendar_id=calendar_id or target_calendar_id()),
        action="google create_event",
        headers={
            "Authorization": f"Bearer {access_token(user_id)}",
            "Content-Type": "application/json",
        },
        json_body=body,
        params={"sendUpdates": send_updates},
        timeout=timeout,
        max_retries=0,
    )
    raise_for_status(resp, "google create_event")
    created = parse_json(resp, "google create_event")
    if not isinstance(created, dict):
        raise CalendarError("google create_event: unexpected response shape")
    return {
        "provider": PROVIDER,
        "event_id": created.get("id"),
        "html_link": created.get("htmlLink"),
        "status": created.get("status"),
        "ical_uid": created.get("iCalUID"),
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
        "redirect_uri": default_redirect_uri() if configured() else None,
        "grant": grant.public() if grant else None,
    }
