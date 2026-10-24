"""Booking & reservation links — real deep links by default, no fake provider.

Scope: `specs/CONTRACTS_V3.md` §5. This module answers four questions:
  * which real booking providers are genuinely configured right now
    (`providers()`)
  * what are the candidate options for an activity (`search()`)
  * what real link can a human open to finish the job (`reservation_link()`)
  * can we actually transact on a user's behalf (`book()`)

PROVIDER RESEARCH (fetched 2026-10-25, see docs/BOOKING.md for the full
writeup and citations):
  * OpenTable  - partner-gated only. No self-serve developer portal; every
    API family requires a formal partner agreement before a sandbox key is
    issued (opentable.com/restaurant-solutions/api-partners/faqs).
  * Resy       - no documented public API at all; integration is bilateral
    partner deals (POS/CRM/discovery vendors), not something a user can sign
    up for.
  * Fandango   - no public developer API for showtimes/ticketing; the
    developer.fandango.com surface is Rotten Tomatoes editorial content, not
    booking. Access to real showtime/ticketing data requires a paid Fabric
    Origin partnership.
  * Eventbrite - DOES have a genuine self-serve REST API (eventbrite.com/
    platform/docs), but it explicitly cannot complete a ticket purchase for a
    third-party app — orders/checkout only happen through Eventbrite's own
    Embedded Checkout widget or by redirecting to eventbrite.com. A "booking"
    call here would still end in a human completing checkout on Eventbrite's
    site, i.e. no better than the honest unbooked path below.
  * Ticketmaster - the Discovery API v2 is genuinely free and self-serve
    (developer.ticketmaster.com; register, get a Consumer Key, 5000 calls/
    day). But it is read-only discovery: there is no commerce/purchase
    endpoint for third parties, so even a fully configured Ticketmaster key
    could never make `book()` do more than return the same real
    ticketmaster.com purchase URL a human has to open themselves.
  * Conclusion: no candidate is BOTH (a) obtainable without signing up for
    anything new today and (b) capable of completing a real transaction
    through an API. Per CONTRACTS_V3 §5 ("ship the unbooked path" is the
    correct outcome, not a failure), this module integrates ZERO real
    providers today. `providers()` is always `[]`; `booking_mode()` is always
    `"unbooked"`. The architecture below (`_configured_provider_name` /
    `_execute_provider_booking`) is the single, explicit extension point a
    future credentialed provider plugs into — it must never be used to
    fake one.

HARD RULES (test-enforced):
  * No cost or transaction without an exact, fresh `Approval` matching the
    option's id, price and currency. A missing/mismatched/stale approval is
    REFUSED with a machine-readable `reason`, never silently allowed.
  * Default to honest and unbooked: every `Option` returned by `search()` has
    `source="unbooked"` and a REAL, absolute https deep link a human can open
    to finish the job themselves (Google Maps / Google Search / OpenTable /
    Fandango search pages — never an invented API or fabricated venue).
  * `book()` is idempotent on `idempotency_key`: replaying it returns the
    first stored `BookingResult` and never re-evaluates, never transacts
    twice.
  * No payment instrument is ever modeled, accepted, logged or returned.
    There is no card/account field anywhere in this module's data shapes,
    and `_scrub()` defensively redacts any card-like digit run that might
    ever appear in a (future, currently-nonexistent) provider's raw response.
"""
from __future__ import annotations

import dataclasses
import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote_plus, urlencode, urlparse

from ..db import ex, new_id, now_iso, q1

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class BookingError(Exception):
    """Base class for booking-adapter errors."""


class BookingValidationError(BookingError):
    """Adapter-level input validation failed before any decision was made."""


# ---------------------------------------------------------------------------
# Data shapes
# ---------------------------------------------------------------------------


@dataclass
class Option:
    """A candidate way to do `activity`. Always `source="unbooked"` today."""

    id: str
    source: str  # "unbooked" today; a provider name iff a real one is ever wired
    activity: str
    location: str
    starts_at: str
    title: str
    url: str
    price: float | None = None
    currency: str | None = None
    disclaimer: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


@dataclass(frozen=True)
class Approval:
    """Explicit model of "who approved exactly what, at what price, when".

    `book()` compares every field against the `Option` being booked - never
    a bare boolean. This is deliberately a *record*, not a flag, per
    CONTRACTS_V3 §5's instruction to "model this explicitly".
    """

    option_id: str
    price: float | None
    currency: str | None
    approved_by: str  # who - a user id or display name, never empty
    approved_at: str  # ISO 8601 timestamp of when they approved it

    def as_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


@dataclass
class BookingResult:
    """Outcome of `book()`. Three distinct states, never collapsed to a bool:

      * `ok=False`                    - REFUSED: the approval didn't check out
        (missing, wrong option/price/currency, stale, or no named approver).
        `reason` is machine-readable. Nothing happened. No cost was incurred.
      * `ok=True, confirmed=False`    - the approval was valid, but no real
        provider is configured (today: always this state, by design) or the
        configured provider itself failed. Still unbooked; `reservation_link`
        is the real link a human finishes it with. This is the honest
        default outcome, not an error.
      * `ok=True, confirmed=True`     - a real, configured provider actually
        completed the transaction. `source` names that provider.
    """

    ok: bool
    confirmed: bool
    source: str  # "unbooked" or a real provider name
    option_id: str
    reservation_link: str
    reason: str | None
    approved_by: str | None
    idempotency_key: str
    raw: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


# ---------------------------------------------------------------------------
# Provider configuration - the ONE extension point for a future real provider
# ---------------------------------------------------------------------------


def _configured_provider_name() -> str | None:
    """The one real, genuinely configured booking provider, or None.

    Reads `os.environ` directly (this module does not own `config.py`).
    Returns None unconditionally today: no real provider is integrated (see
    the module docstring for the researched reasons why). This function is
    the single place a future credentialed provider would plug into -
    nothing else in `book()`/`search()` needs to change.
    """
    return None


def providers() -> list[str]:
    """Only genuinely configured providers. Empty by default."""
    name = _configured_provider_name()
    return [name] if name else []


def configured() -> bool:
    return bool(providers())


def booking_mode() -> str:
    """For `/api/status`: `"<provider>"` if one is genuinely configured,
    else `"unbooked"`. Never claims a real provider when none is wired."""
    ps = providers()
    return ps[0] if ps else "unbooked"


def _execute_provider_booking(
    provider: str, option: "Option", *, idempotency_key: str
) -> dict[str, Any]:
    """Perform the actual provider-side booking call.

    Always raises today: `_configured_provider_name()` always returns None,
    so no production code path ever reaches this function - there is nothing
    here to accidentally call a real API and nothing here that fakes one.
    Tests monkeypatch `_configured_provider_name` AND this function TOGETHER
    to exercise the approval + idempotency contract in `book()` end-to-end,
    proving it is correct for the day a real provider is added, without
    shipping a pretend integration in the meantime.
    """
    raise NotImplementedError(
        f"no booking execution is implemented for provider {provider!r}"
    )


# ---------------------------------------------------------------------------
# Approval freshness
# ---------------------------------------------------------------------------

#: How long an approval stays valid after it was given. Configurable because
#: "fresh" is a product decision, not a constant worth hardcoding; read
#: directly from the environment since this module does not own config.py.
_DEFAULT_APPROVAL_MAX_AGE_SECONDS = 900  # 15 minutes
#: An approval timestamped more than this far in the future is also rejected
#: - it cannot be a genuine past approval, only clock skew or a bad input.
_CLOCK_SKEW_TOLERANCE_SECONDS = 60


def _approval_max_age_seconds() -> int:
    raw = (os.environ.get("BOOKING_APPROVAL_MAX_AGE_SECONDS") or "").strip()
    if not raw:
        return _DEFAULT_APPROVAL_MAX_AGE_SECONDS
    try:
        return max(1, int(raw))
    except ValueError:
        return _DEFAULT_APPROVAL_MAX_AGE_SECONDS


def _parse_dt(ts: str) -> datetime:
    if not ts or not str(ts).strip():
        raise BookingValidationError("empty timestamp")
    try:
        d = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError as e:
        raise BookingValidationError(f"not a valid ISO 8601 timestamp: {ts!r}") from e
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _is_stale(approved_at: str) -> bool:
    try:
        dt = _parse_dt(approved_at)
    except BookingValidationError:
        return True
    age = (datetime.now(timezone.utc) - dt).total_seconds()
    if age < -_CLOCK_SKEW_TOLERANCE_SECONDS:
        return True  # approved in the future - not a real past approval
    return age > _approval_max_age_seconds()


# ---------------------------------------------------------------------------
# Real deep-link construction - no invented API, no fabricated venues
# ---------------------------------------------------------------------------

_DINING_KEYWORDS = (
    "coffee", "cafe", "café", "dinner", "lunch", "brunch", "restaurant",
    "dine", "dining", "eat", "meal", "tea", "bakery", "bar",
)
_MOVIE_KEYWORDS = ("movie", "cinema", "film", "screening", "showtime")
_EVENT_KEYWORDS = (
    "concert", "show", "game", "match", "festival", "gig", "performance",
    "ticket", "event", "theater", "theatre",
)


def _classify(activity: str) -> str:
    low = (activity or "").lower()
    if any(k in low for k in _MOVIE_KEYWORDS):
        return "movie"
    if any(k in low for k in _EVENT_KEYWORDS):
        return "event"
    if any(k in low for k in _DINING_KEYWORDS):
        return "dining"
    return "general"


def _human_date(starts_at: str) -> str:
    try:
        return _parse_dt(starts_at).strftime("%Y-%m-%d %H:%M")
    except BookingValidationError:
        return str(starts_at)


def _maps_link(activity: str, location: str, starts_at: str) -> str:
    """Google Maps place search. Officially documented
    (developers.google.com/maps/documentation/urls/get-started): the
    `https://www.google.com/maps/search/?api=1&query=<text>` form always
    works for any free-text place query - verified live 2026-10-25.
    """
    query = f"{activity} near {location} {_human_date(starts_at)}".strip()
    return f"https://www.google.com/maps/search/?api=1&query={quote_plus(query)}"


def _web_search_link(activity: str, location: str, starts_at: str) -> str:
    """Plain Google web search - universally real, no API/auth needed."""
    query = f"{activity} {location} {_human_date(starts_at)}".strip()
    return f"https://www.google.com/search?q={quote_plus(query)}"


def _opentable_link(activity: str, location: str, starts_at: str) -> str:
    """OpenTable's live site search page (not an API - no partner agreement
    needed to open a URL). Parameter names verified against real example
    OpenTable search URLs (dateTime, covers, term, searchedLocationName) -
    fetched 2026-10-25; see docs/BOOKING.md.
    """
    dt = _parse_dt(starts_at)
    params = {
        "term": activity,
        "dateTime": dt.strftime("%Y-%m-%dT%H:%M:%S"),
        "covers": "2",
        "searchedLocationName": location,
    }
    return "https://www.opentable.com/s?" + urlencode(params)


def _fandango_link(activity: str, starts_at: str) -> str:
    """Fandango's live site search page. Verified live 2026-10-25: fetching
    `https://www.fandango.com/search?q=<query>` returns a real results page
    (confirmed with q="dune" returning actual Dune movie listings).
    """
    query = f"{activity} {_human_date(starts_at)}".strip()
    return f"https://www.fandango.com/search?q={quote_plus(query)}"


_UNBOOKED_DISCLAIMER = (
    "UNBOOKED - no reservation or purchase has been made. Open this real "
    "link to complete it yourself."
)


def search(activity: str, *, location: str, starts_at: str) -> list[Option]:
    """Candidate ways to do `activity`. Every option is honest and unbooked
    unless a real provider is genuinely configured (never true today).

    Returns real working deep links built from the activity/location/date -
    never invented API fields, never fabricated venue names or showtimes
    (contrast with `services/scheduling.lookup_showtimes`'s MOCK data, which
    this module deliberately does not imitate).
    """
    if not activity or not str(activity).strip():
        raise BookingValidationError("activity is required")
    if not location or not str(location).strip():
        raise BookingValidationError("location is required")
    _parse_dt(starts_at)  # validates format; raises BookingValidationError

    activity = str(activity).strip()
    location = str(location).strip()

    category = _classify(activity)
    maps_url = _maps_link(activity, location, starts_at)
    if category == "dining":
        secondary_url = _opentable_link(activity, location, starts_at)
        secondary_title = f"Reserve '{activity}' via OpenTable search near {location}"
    elif category == "movie":
        secondary_url = _fandango_link(activity, starts_at)
        secondary_title = f"Find showtimes for '{activity}' on Fandango"
    else:
        secondary_url = _web_search_link(activity, location, starts_at)
        secondary_title = f"Search the web for '{activity}' near {location}"

    return [
        Option(
            id=new_id("opt"),
            source="unbooked",
            activity=activity,
            location=location,
            starts_at=starts_at,
            title=f"Find '{activity}' near {location} on Google Maps",
            url=maps_url,
            disclaimer=_UNBOOKED_DISCLAIMER,
        ),
        Option(
            id=new_id("opt"),
            source="unbooked",
            activity=activity,
            location=location,
            starts_at=starts_at,
            title=secondary_title,
            url=secondary_url,
            disclaimer=_UNBOOKED_DISCLAIMER,
        ),
    ]


def reservation_link(option: Option | dict[str, Any]) -> str:
    """A real, absolute https deep link a human can open. Validates - never
    trusts - the URL shape before returning it."""
    opt = _coerce_option(option)
    parsed = urlparse(opt.url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise BookingValidationError(f"not a real absolute https url: {opt.url!r}")
    return opt.url


# ---------------------------------------------------------------------------
# Coercion helpers (accept either the dataclass or its dict form, mirroring
# `scheduling.Slot` call sites that round-trip through `.as_dict()`)
# ---------------------------------------------------------------------------


def _coerce_option(option: Any) -> Option:
    if isinstance(option, Option):
        return option
    if isinstance(option, dict):
        allowed = {f.name for f in dataclasses.fields(Option)}
        try:
            return Option(**{k: v for k, v in option.items() if k in allowed})
        except TypeError as e:
            raise BookingValidationError(f"malformed option: {e}") from e
    raise BookingValidationError(
        f"option must be a booking.Option or dict, got {type(option).__name__}"
    )


def _coerce_approval(approved_by: Any) -> Approval | None:
    if approved_by is None:
        return None
    if isinstance(approved_by, Approval):
        return approved_by
    if isinstance(approved_by, dict):
        allowed = {f.name for f in dataclasses.fields(Approval)}
        try:
            return Approval(**{k: v for k, v in approved_by.items() if k in allowed})
        except TypeError as e:
            raise BookingValidationError(f"malformed approval: {e}") from e
    raise BookingValidationError(
        "approved_by must be a booking.Approval, dict, or None, got "
        f"{type(approved_by).__name__}"
    )


# ---------------------------------------------------------------------------
# Payment-instrument / secret defense in depth
# ---------------------------------------------------------------------------

#: No field in `Option`, `Approval` or `BookingResult` is a payment
#: instrument - there is structurally nowhere to put a card number. This is
#: a backstop for the `raw` passthrough from a (currently nonexistent) real
#: provider's response, mirroring `cartesia_adapter._redact_payload`.
_CARDLIKE_RE = re.compile(r"\b\d{13,19}\b")


def _scrub(payload: Any) -> Any:
    if isinstance(payload, dict):
        return {k: _scrub(v) for k, v in payload.items()}
    if isinstance(payload, list):
        return [_scrub(v) for v in payload]
    if isinstance(payload, str):
        return _CARDLIKE_RE.sub("[REDACTED]", payload)
    return payload


# ---------------------------------------------------------------------------
# book() - the only place money or a real-world action could ever happen
# ---------------------------------------------------------------------------

_IDEMPOTENCY_SCOPE = "booking"


def _idempotency_get(key: str) -> BookingResult | None:
    row = q1(
        "SELECT result FROM idempotency WHERE key=? AND scope=?",
        (key, _IDEMPOTENCY_SCOPE),
    )
    if row is None:
        return None
    return BookingResult(**json.loads(row["result"]))


def _idempotency_store(key: str, result: BookingResult) -> None:
    ex(
        "INSERT OR REPLACE INTO idempotency (key, scope, result, created_at) "
        "VALUES (?,?,?,?)",
        (key, _IDEMPOTENCY_SCOPE, json.dumps(result.as_dict()), now_iso()),
    )


def book(
    option: Option | dict[str, Any],
    *,
    idempotency_key: str,
    approved_by: Approval | dict[str, Any] | None = None,
) -> BookingResult:
    """Book `option`, or honestly refuse / degrade to unbooked.

    Never transacts without an `Approval` whose `option_id`, `price` and
    `currency` match `option` exactly and whose `approved_at` is still
    fresh. Idempotent on `idempotency_key`: replaying the same key returns
    the first stored result and never re-evaluates or re-transacts.
    """
    if not idempotency_key or not str(idempotency_key).strip():
        raise BookingValidationError("idempotency_key is required")

    opt = _coerce_option(option)
    link = reservation_link(opt)  # validates the link before anything else

    cached = _idempotency_get(idempotency_key)
    if cached is not None:
        return cached

    approval = _coerce_approval(approved_by)
    result = _evaluate_and_book(opt, approval, idempotency_key, link)
    _idempotency_store(idempotency_key, result)
    return result


def _refuse(
    opt: Option, link: str, idempotency_key: str, approval: Approval | None, reason: str
) -> BookingResult:
    return BookingResult(
        ok=False,
        confirmed=False,
        source="unbooked",
        option_id=opt.id,
        reservation_link=link,
        reason=reason,
        approved_by=(approval.approved_by if approval else None),
        idempotency_key=idempotency_key,
    )


def _evaluate_and_book(
    opt: Option, approval: Approval | None, idempotency_key: str, link: str
) -> BookingResult:
    if approval is None:
        return _refuse(opt, link, idempotency_key, approval, "no_approval")
    if not approval.approved_by or not str(approval.approved_by).strip():
        return _refuse(opt, link, idempotency_key, approval, "approved_by_missing")
    if approval.option_id != opt.id:
        return _refuse(opt, link, idempotency_key, approval, "approval_option_mismatch")
    if approval.price != opt.price or approval.currency != opt.currency:
        return _refuse(opt, link, idempotency_key, approval, "approval_price_mismatch")
    if _is_stale(approval.approved_at):
        return _refuse(opt, link, idempotency_key, approval, "approval_stale")

    provider = _configured_provider_name()
    if provider is None:
        # Approval was genuinely valid - this is NOT a refusal. It is the
        # honest default: no real provider exists to transact with, so the
        # plan stays unbooked and the human finishes it via `link`.
        return BookingResult(
            ok=True,
            confirmed=False,
            source="unbooked",
            option_id=opt.id,
            reservation_link=link,
            reason="no_provider_configured",
            approved_by=approval.approved_by,
            idempotency_key=idempotency_key,
        )

    try:
        raw = _execute_provider_booking(provider, opt, idempotency_key=idempotency_key)
    except Exception as e:  # provider failed - degrade honestly, don't crash the plan
        return BookingResult(
            ok=True,
            confirmed=False,
            source="unbooked",
            option_id=opt.id,
            reservation_link=link,
            reason=f"provider_error:{type(e).__name__}",
            approved_by=approval.approved_by,
            idempotency_key=idempotency_key,
        )

    return BookingResult(
        ok=True,
        confirmed=True,
        source=provider,
        option_id=opt.id,
        reservation_link=link,
        reason=None,
        approved_by=approval.approved_by,
        idempotency_key=idempotency_key,
        raw=_scrub(raw) if isinstance(raw, dict) else {},
    )
