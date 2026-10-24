# Booking & Reservation Links

Owner: Agent J. Scope: `specs/CONTRACTS_V3.md` §5 (plus §0, §1, §7, §8). Module:
`backend/app/adapters/booking.py`. Tests: `backend/tests/test_booking.py` (38
tests, all passing, no network access).

## §1 status

**`implemented`.**

Booking itself (completing a real purchase/reservation through a third-party
API) is **not wired to any live provider** - see "Provider research" below for
why, and why that is the correct outcome here, not a shortfall. What *is*
implemented and real: honest unbooked search with genuine, live, working deep
links; an explicit, field-by-field `Approval` model; a `book()` state machine
that refuses or degrades honestly; and idempotency backed by the same
`idempotency` SQLite table `services/plans.py` and `api/voice.py` already use.
None of this is simulated or mocked - it is real logic, just not pointed at a
provider that doesn't exist yet.

## Why booking defaults to "unbooked, with a real link"

CONTRACTS_V3 §5 is explicit: *"No approval ⇒ return an honest **unbooked**
plan carrying a real reservation link the human can complete."* and *"Do not
fake a provider integration — that is the worst possible outcome."*

This matters for the population this product serves: adults in the first 30
days after a psychiatric hospitalization, often with limited executive
capacity and trust in automated systems. A plan that silently *claims* to have
booked a table when nothing happened is a much worse failure than a plan that
plainly says "here's a real link - open it and finish this yourself." The
honest-unbooked path is not a degraded fallback; it is the intended default
behavior of this module, and `providers()` returning `[]` is a correct,
expected result, not an error state.

## Provider research (fetched 2026-10-25)

CONTRACTS_V3 §5 allows integrating **at most one** real provider, **only if
credentials already exist**, and forbids signing up for anything or inventing
an API. None of the candidates named in the brief clears that bar today:

| Provider | Self-serve API? | Can a third party complete a purchase/reservation via API? | Verdict |
|---|---|---|---|
| **OpenTable** | No. Every API family (Booking, CRM, Directory, Sync, Voice AI, ...) sits behind a partner application, a use-case review, and a signed agreement before a sandbox key is even issued. [opentable.com/restaurant-solutions/api-partners/faqs](https://www.opentable.com/restaurant-solutions/api-partners/faqs/) | N/A - no key obtainable without a sales process | **Rejected**: not obtainable without "signing up for" a partner agreement |
| **Resy** | No public developer portal at all; the mobile app's `api.resy.com` endpoints are unsupported/reverse-engineered. Real integration is bilateral (POS/CRM/discovery) partner deals. [github.com/api-evangelist/resy](https://github.com/api-evangelist/resy) | N/A | **Rejected**: no public API exists to integrate against |
| **Fandango** | No public showtime/ticketing developer API. `developer.fandango.com` is the *Rotten Tomatoes* editorial API, a different product. Real showtime data requires a paid Fabric Origin partnership and Fandango's approval. [knowledgebase.fabricdata.com/.../fandango-api-docs](https://knowledgebase.fabricdata.com/origin/apis-all/fandango-api-docs) | N/A | **Rejected**: no public API; `developer.fandango.com` is unrelated to booking |
| **Eventbrite** | **Yes** - genuine self-serve REST API, OAuth, docs at [eventbrite.com/platform/docs](https://www.eventbrite.com/platform/new/api) | **No.** Eventbrite's own docs state a third-party app cannot create an order/complete checkout via the API for a paid event; purchase only happens through Eventbrite's Embedded Checkout widget or by redirecting to eventbrite.com. | **Rejected for `book()`**: even fully configured, it could never do more than the same honest unbooked link below |
| **Ticketmaster (Discovery API v2)** | **Yes** - genuine self-serve, free, ~5 minutes to get a Consumer Key, 5,000 calls/day, 5 req/s. [developer.ticketmaster.com](https://developer.ticketmaster.com/products-and-docs/apis/discovery-feed/) | **No.** Discovery API is read-only event/venue/attraction *search*; there is no commerce endpoint for third parties. A real key would only ever return the same `ticketmaster.com` purchase URL a human has to open themselves. | **Rejected for `book()`** for the same structural reason as Eventbrite |

**Conclusion:** no candidate is simultaneously (a) obtainable today without
signing up for anything new, and (b) capable of completing a real transaction
through an API as a third party. Per the brief's own instruction ("if none is
usable without credentials, say so plainly and ship the unbooked path"),
**zero real providers are integrated.** `providers()` always returns `[]`,
`configured()` is always `False`, and `booking_mode()` is always
`"unbooked"`.

`_configured_provider_name()` in `booking.py` is the single, explicit
extension point a future credentialed provider plugs into (it currently
always returns `None`). `_execute_provider_booking()` is the matching
execution hook; it unconditionally raises `NotImplementedError` because no
production code path can ever reach it. Both exist purely so the approval and
idempotency logic in `book()` can be proven correct *before* a real provider
exists, via tests that monkeypatch these two functions directly — never via a
fake HTTP response or an invented API shape. **This is deliberately not a
disguised integration**: there is no provider name, base URL, or credential
variable configured anywhere for Eventbrite or Ticketmaster today, precisely
because shipping dead code that merely *looks* wired in would be exactly the
"fake provider integration" the spec forbids.

## The `Approval` model, and why it's this strict

CONTRACTS_V3 §5: *"`book()` must refuse unless `approved_by` names a user who
approved *that exact* option and price."* The brief further directs: *"Model
this explicitly ... and compare all of it, not just a boolean."*

`book(option, *, idempotency_key, approved_by)` takes `approved_by` as an
`Approval` record (or an equivalent `dict`, or `None`), never a bare flag:

```python
@dataclass(frozen=True)
class Approval:
    option_id: str
    price: float | None
    currency: str | None
    approved_by: str   # who - a user id/display name, never empty
    approved_at: str   # ISO 8601 - when they approved it
```

`book()` refuses (`ok=False`, machine-readable `reason`, nothing happens, no
cost incurred) unless **every** field checks out against the `Option` being
booked:

| Condition | `reason` |
|---|---|
| No approval passed at all | `no_approval` |
| `approved_by` present but blank | `approved_by_missing` |
| `approval.option_id != option.id` (approving a *different* option) | `approval_option_mismatch` |
| `approval.price != option.price` or `approval.currency != option.currency` | `approval_price_mismatch` |
| `approval.approved_at` older than `BOOKING_APPROVAL_MAX_AGE_SECONDS` (default 900s / 15 min), or timestamped more than 60s in the future | `approval_stale` |

Only once all five hold does `book()` even look at whether a real provider is
configured. Since none is (see above), a perfectly valid approval today still
yields `ok=True, confirmed=False, source="unbooked", reason="no_provider_configured"`
— explicitly **not** a refusal (the human did everything right; the system
just has nothing to transact with) and explicitly **not** a confirmed booking
(nothing happened; the real link still has to be opened by a person).

If a real provider is ever wired, a configured-but-failing provider call
degrades the same way (`reason="provider_error:<ExceptionType>"`) rather than
raising into the caller - a transaction is never left ambiguous.

## Idempotency

`book()` is idempotent on `idempotency_key`, reusing the same `idempotency`
table (`key`, `scope`, `result`, `created_at`) that `services/plans.py`
(`scope='confirm_plan'`, `scope='send_confirmation'`) and `api/voice.py`
(`scope='voice_tool'`) already use - this module uses `scope='booking'`. On
any call, `book()` first checks for a stored result under that
`(key, scope='booking')` pair; if found, it is returned immediately,
**before** the approval passed this time is even looked at. This guarantees
replaying a key never re-evaluates, never re-calls a provider, and never
charges twice - even if a caller passes a different (or missing) approval on
the replay.

## Real links, not invented APIs or fabricated data

`search(activity, *, location, starts_at)` never invents venue names,
showtimes, or prices (contrast with `services/scheduling.lookup_showtimes`'s
intentionally-labelled `MOCK DATA`, which stays untouched and still exists for
the illustrative-showtimes case). Instead it returns 2 `Option`s per call,
each `source="unbooked"` with a real, absolute `https://` deep link built from
the actual activity/location/date, verified live on 2026-10-25:

- **Always:** a Google Maps place search -
  `https://www.google.com/maps/search/?api=1&query=<activity near location date>`
  - officially documented at
  [developers.google.com/maps/documentation/urls/get-started](https://developers.google.com/maps/documentation/urls/get-started).
- **Dining-classified activities** (coffee, dinner, lunch, brunch, restaurant,
  cafe, bar, ...): an **OpenTable** site search -
  `https://www.opentable.com/s?term=...&dateTime=...&covers=2&searchedLocationName=...`
  - this is the live *website's* search page, not an API, so no partner
  agreement is needed to construct and open the URL. Parameter names were
  cross-checked against real example OpenTable search URLs.
- **Movie-classified activities** (movie, cinema, film, screening,
  showtime): a **Fandango** site search -
  `https://www.fandango.com/search?q=<title> <date>` - fetched live during
  development (`q=dune` returned real *Dune: Part Two* / *Part Three*
  listings), confirming the endpoint works.
- **Everything else:** a plain Google web search -
  `https://www.google.com/search?q=<activity location date>`.

`reservation_link(option)` re-validates (never just trusts) that the option's
URL is absolute `https://` with a real host before returning it, raising
`BookingValidationError` otherwise.

## What a human does with an unbooked plan

1. Still Here surfaces the plan's `Option`s, each carrying its `disclaimer`
   ("UNBOOKED - no reservation or purchase has been made. Open this real link
   to complete it yourself.") and its real `url`.
2. The patient (or contact) opens the link and completes the reservation or
   purchase on the provider's own site, the same as if they had searched for
   it themselves.
3. Nothing in this module ever sees or needs a confirmation of that
   completion - there is no payment instrument or booking-confirmation
   callback modeled anywhere in `backend/app/adapters/booking.py`.

## Env variables

| Variable | Required? | Default | Purpose |
|---|---|---|---|
| `BOOKING_APPROVAL_MAX_AGE_SECONDS` | No | `900` (15 min) | How long an `Approval` stays valid after `approved_at` before `book()` refuses it as `approval_stale`. |

No provider credentials are read today because no provider is integrated.
**Recommendation to the lead** for `config.py` / `.env.example`: add
`BOOKING_APPROVAL_MAX_AGE_SECONDS` as an optional, documented-default entry
(this module reads `os.environ` directly per the file-ownership rule that
agent J does not edit `config.py`). If a real provider is ever added, that
work should add its own provider-specific key (e.g. a hypothetical
`TICKETMASTER_API_KEY`) alongside wiring `_configured_provider_name()` /
`_execute_provider_booking()` in `booking.py` - no other function's contract
needs to change.

## `/api/status` wiring

`booking.booking_mode() -> "<provider>" | "unbooked"` is the single function
`/api/status` (owned by the lead, `main.py`/`config.py`) should surface as
`booking_mode`, mirroring how `voice_mode()` / `coordination_mode()` /
`conversation_mode()` already work in `config.py`'s `redacted()`. Suggested
addition to `Settings.redacted()`:

```python
from .adapters import booking as booking_adapter
...
"booking_mode": booking_adapter.booking_mode(),
```

Today this always reports `"unbooked"`, which is the truthful answer.

## No payment instrument, ever

There is no card number, CVV, bank account, or any other payment-instrument
field anywhere in `Option`, `Approval`, or `BookingResult` — it is
structurally impossible to pass one through this module's public API. As
defense in depth against a future provider's response body accidentally
echoing something card-like, `_scrub()` redacts any 13-19-digit run before a
`BookingResult`'s `raw` field is ever constructed or persisted to the
`idempotency` table, mirroring `cartesia_adapter._redact_payload`'s approach
to phone numbers. `backend/tests/test_booking.py` asserts this directly:
no dataclass carries a payment-instrument-named field, `_scrub()` redacts
card-like digit runs, and a (mocked) provider response containing a card-like
number never survives into a returned `BookingResult` or the persisted
idempotency row.

## Known limitations

- **No real provider is wired.** Every option today is `source="unbooked"`.
  This is by design (see "Provider research" above), not a gap to be
  "finished" without new facts - none of the researched providers both grants
  self-serve access today *and* supports third-party purchase completion.
- **Link quality is best-effort, not guaranteed-perfect search results.**
  These are real, working search-page URLs, not a guarantee the first result
  on that page is the right venue; a human still has to pick. That is the
  intended tradeoff of the honest-unbooked design over inventing fake venue
  data.
- **The Fandango/OpenTable URL shapes are the live sites' public search
  pages, not documented partner APIs** (because no documented partner API is
  reachable without a sales process - see above). They were verified live on
  2026-10-25 but, like any undocumented page, could change shape over time;
  Google Maps search is kept as the first, officially-documented, most
  durable option on every result for that reason.
- **No model/LLM ever calls this module directly.** `backend/app/agents/tools.py`
  does not expose `booking.search`/`book` as a tool, and
  `MODEL_FORBIDDEN_TOOLS` already excludes anything backend-authorized-only;
  booking must only ever be invoked by the backend's own confirmed-plan flow
  (owned by the lead in `services/plans.py` / `api/**`), never by a model's
  tool call. (Agent J does not own `agents/tools.py` and made no edits there;
  this note is for whoever wires booking into the plan-confirmation path.)

## Test commands

```
./.venv/Scripts/python.exe -m pytest backend/tests/test_booking.py -q
./.venv/Scripts/python.exe -m pytest backend/tests -q
```
