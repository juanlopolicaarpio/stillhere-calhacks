"""Unit tests for `app.adapters.booking`.

No network access anywhere: this module never imports `httpx` because it
never makes a real HTTP call (no real provider is integrated - see the
module docstring and docs/BOOKING.md). The "mocked provider" tests instead
monkeypatch the module's own internal extension points
(`_configured_provider_name` / `_execute_provider_booking`) to prove the
approval + idempotency contract in `book()` behaves correctly for the day a
real provider is added, without pretending one exists today.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import pytest

from app.adapters import booking as bk
from app.db import q1


FUTURE = (datetime.now(timezone.utc) + timedelta(days=3)).replace(microsecond=0)
STARTS_AT = FUTURE.isoformat()
DATE_STR = FUTURE.strftime("%Y-%m-%d")


def _fresh_approval(option: bk.Option, **overrides) -> bk.Approval:
    data = dict(
        option_id=option.id,
        price=option.price,
        currency=option.currency,
        approved_by="user_patient_1",
        approved_at=datetime.now(timezone.utc).isoformat(),
    )
    data.update(overrides)
    return bk.Approval(**data)


# ---------------------------------------------------------------------------
# configured() / providers() - empty by default, no credentials anywhere
# ---------------------------------------------------------------------------


def test_configured_false_with_no_env():
    assert bk.configured() is False


def test_providers_empty_by_default():
    assert bk.providers() == []


def test_booking_mode_is_unbooked_by_default():
    assert bk.booking_mode() == "unbooked"


# ---------------------------------------------------------------------------
# search() - real, well-formed, honest links
# ---------------------------------------------------------------------------


def test_search_requires_activity():
    with pytest.raises(bk.BookingValidationError):
        bk.search("", location="San Francisco", starts_at=STARTS_AT)


def test_search_requires_location():
    with pytest.raises(bk.BookingValidationError):
        bk.search("dinner", location="", starts_at=STARTS_AT)


def test_search_requires_valid_starts_at():
    with pytest.raises(bk.BookingValidationError):
        bk.search("dinner", location="San Francisco", starts_at="not-a-date")


def test_search_returns_unbooked_options_with_real_links():
    options = bk.search("dinner", location="San Francisco", starts_at=STARTS_AT)
    assert len(options) >= 2
    for opt in options:
        assert opt.source == "unbooked"
        assert "UNBOOKED" in opt.disclaimer
        parsed = urlparse(opt.url)
        assert parsed.scheme == "https"
        assert parsed.netloc  # a real host
        # The query must actually cite the activity and the date requested.
        assert "dinner" in opt.url.lower()
        assert DATE_STR in opt.url or DATE_STR.replace("-", "") in opt.url


def test_search_dining_includes_opentable_link():
    options = bk.search("coffee", location="Oakland", starts_at=STARTS_AT)
    urls = [o.url for o in options]
    assert any("opentable.com" in u for u in urls)
    assert any("google.com/maps" in u for u in urls)


def test_search_movie_includes_fandango_link():
    options = bk.search("movie night", location="Berkeley", starts_at=STARTS_AT)
    urls = [o.url for o in options]
    assert any("fandango.com" in u for u in urls)


def test_search_general_activity_falls_back_to_web_search():
    options = bk.search("go bowling", location="San Jose", starts_at=STARTS_AT)
    urls = [o.url for o in options]
    assert any("google.com/search" in u for u in urls)
    assert any("google.com/maps" in u for u in urls)


def test_search_options_have_distinct_ids():
    options = bk.search("dinner", location="San Francisco", starts_at=STARTS_AT)
    ids = [o.id for o in options]
    assert len(ids) == len(set(ids))


# ---------------------------------------------------------------------------
# reservation_link() - a real absolute https URL
# ---------------------------------------------------------------------------


def test_reservation_link_is_absolute_https():
    opt = bk.search("dinner", location="San Francisco", starts_at=STARTS_AT)[0]
    link = bk.reservation_link(opt)
    parsed = urlparse(link)
    assert parsed.scheme == "https"
    assert parsed.netloc


def test_reservation_link_accepts_dict_form():
    opt = bk.search("dinner", location="San Francisco", starts_at=STARTS_AT)[0]
    link = bk.reservation_link(opt.as_dict())
    assert link == opt.url


def test_reservation_link_rejects_non_https():
    bad = bk.Option(
        id="opt_bad", source="unbooked", activity="x", location="y",
        starts_at=STARTS_AT, title="t", url="http://insecure.example.com",
    )
    with pytest.raises(bk.BookingValidationError):
        bk.reservation_link(bad)


def test_reservation_link_rejects_malformed_url():
    bad = bk.Option(
        id="opt_bad2", source="unbooked", activity="x", location="y",
        starts_at=STARTS_AT, title="t", url="not a url",
    )
    with pytest.raises(bk.BookingValidationError):
        bk.reservation_link(bad)


# ---------------------------------------------------------------------------
# book() - refusals
# ---------------------------------------------------------------------------


@pytest.fixture
def option() -> bk.Option:
    return bk.search("dinner", location="San Francisco", starts_at=STARTS_AT)[0]


def test_book_refuses_with_no_approval(option):
    result = bk.book(option, idempotency_key="idem-1", approved_by=None)
    assert result.ok is False
    assert result.confirmed is False
    assert result.reason == "no_approval"
    assert result.source == "unbooked"
    # the honest, human-completable link is still handed back
    assert urlparse(result.reservation_link).scheme == "https"


def test_book_refuses_approval_for_a_different_option(option):
    other = bk.search("coffee", location="Oakland", starts_at=STARTS_AT)[0]
    approval = _fresh_approval(other)  # approves a DIFFERENT option id
    result = bk.book(option, idempotency_key="idem-2", approved_by=approval)
    assert result.ok is False
    assert result.reason == "approval_option_mismatch"


def test_book_refuses_on_price_mismatch(option):
    approval = _fresh_approval(option, price=999.0, currency="USD")
    result = bk.book(option, idempotency_key="idem-3", approved_by=approval)
    assert result.ok is False
    assert result.reason == "approval_price_mismatch"


def test_book_refuses_on_currency_mismatch(option):
    # option.price/currency are both None by default; approving a specific
    # price/currency the option doesn't carry must also be treated as a
    # mismatch, not silently accepted.
    approval = _fresh_approval(option, price=None, currency="USD")
    result = bk.book(option, idempotency_key="idem-3b", approved_by=approval)
    assert result.ok is False
    assert result.reason == "approval_price_mismatch"


def test_book_refuses_on_stale_approval(option, monkeypatch):
    monkeypatch.setenv("BOOKING_APPROVAL_MAX_AGE_SECONDS", "60")
    stale_time = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    approval = _fresh_approval(option, approved_at=stale_time)
    result = bk.book(option, idempotency_key="idem-4", approved_by=approval)
    assert result.ok is False
    assert result.reason == "approval_stale"


def test_book_refuses_on_future_approval_timestamp(option):
    future_time = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    approval = _fresh_approval(option, approved_at=future_time)
    result = bk.book(option, idempotency_key="idem-4b", approved_by=approval)
    assert result.ok is False
    assert result.reason == "approval_stale"


def test_book_refuses_on_empty_approved_by(option):
    approval = _fresh_approval(option, approved_by="")
    result = bk.book(option, idempotency_key="idem-5", approved_by=approval)
    assert result.ok is False
    assert result.reason == "approved_by_missing"


def test_book_with_exact_matching_approval_but_no_provider_is_honest_unbooked(option):
    """The approval is perfectly valid, but no real provider is configured
    (the default state of this module). That must NOT be a refusal - it is
    the correct, honest default."""
    approval = _fresh_approval(option)
    result = bk.book(option, idempotency_key="idem-6", approved_by=approval)
    assert result.ok is True
    assert result.confirmed is False
    assert result.source == "unbooked"
    assert result.reason == "no_provider_configured"
    assert result.approved_by == "user_patient_1"


# ---------------------------------------------------------------------------
# book() - success against a MOCKED provider (proves the contract, not a
# real integration: see module docstring on why no real provider ships)
# ---------------------------------------------------------------------------


def test_book_succeeds_with_mocked_provider_and_exact_approval(option, monkeypatch):
    priced = bk.Option(**{**option.as_dict(), "price": 42.0, "currency": "USD"})
    approval = _fresh_approval(priced)

    monkeypatch.setattr(bk, "_configured_provider_name", lambda: "testprov")

    captured = {}

    def fake_execute(provider, opt, *, idempotency_key):
        captured["provider"] = provider
        captured["option_id"] = opt.id
        captured["idempotency_key"] = idempotency_key
        return {"confirmation_id": "conf_abc123"}

    monkeypatch.setattr(bk, "_execute_provider_booking", fake_execute)

    result = bk.book(priced, idempotency_key="idem-7", approved_by=approval)
    assert result.ok is True
    assert result.confirmed is True
    assert result.source == "testprov"
    assert result.raw == {"confirmation_id": "conf_abc123"}
    assert captured == {
        "provider": "testprov",
        "option_id": priced.id,
        "idempotency_key": "idem-7",
    }


def test_book_mocked_provider_mismatched_approval_still_refused(option, monkeypatch):
    """Even with a provider configured, a bad approval must still refuse -
    provider configuration never bypasses the approval gate."""
    priced = bk.Option(**{**option.as_dict(), "price": 42.0, "currency": "USD"})
    wrong_price_approval = _fresh_approval(priced, price=1.0)

    monkeypatch.setattr(bk, "_configured_provider_name", lambda: "testprov")
    monkeypatch.setattr(
        bk, "_execute_provider_booking",
        lambda *a, **k: pytest.fail("must never call the provider on a bad approval"),
    )

    result = bk.book(priced, idempotency_key="idem-8", approved_by=wrong_price_approval)
    assert result.ok is False
    assert result.reason == "approval_price_mismatch"


def test_book_mocked_provider_failure_degrades_honestly(option, monkeypatch):
    priced = bk.Option(**{**option.as_dict(), "price": 42.0, "currency": "USD"})
    approval = _fresh_approval(priced)

    monkeypatch.setattr(bk, "_configured_provider_name", lambda: "testprov")

    def boom(*a, **k):
        raise RuntimeError("provider is down")

    monkeypatch.setattr(bk, "_execute_provider_booking", boom)

    result = bk.book(priced, idempotency_key="idem-9", approved_by=approval)
    assert result.ok is True  # approval was valid
    assert result.confirmed is False  # but nothing was actually booked
    assert result.source == "unbooked"
    assert result.reason == "provider_error:RuntimeError"


# ---------------------------------------------------------------------------
# book() - idempotency
# ---------------------------------------------------------------------------


def test_book_is_idempotent_on_replay_without_provider(option):
    approval = _fresh_approval(option)
    first = bk.book(option, idempotency_key="idem-10", approved_by=approval)
    second = bk.book(option, idempotency_key="idem-10", approved_by=approval)
    assert first == second


def test_book_is_idempotent_and_never_charges_twice_with_mocked_provider(
    option, monkeypatch
):
    priced = bk.Option(**{**option.as_dict(), "price": 42.0, "currency": "USD"})
    approval = _fresh_approval(priced)

    monkeypatch.setattr(bk, "_configured_provider_name", lambda: "testprov")

    call_count = {"n": 0}

    def fake_execute(provider, opt, *, idempotency_key):
        call_count["n"] += 1
        return {"confirmation_id": f"conf_{call_count['n']}"}

    monkeypatch.setattr(bk, "_execute_provider_booking", fake_execute)

    first = bk.book(priced, idempotency_key="idem-11", approved_by=approval)
    second = bk.book(priced, idempotency_key="idem-11", approved_by=approval)

    assert call_count["n"] == 1  # the provider was invoked exactly once
    assert first == second
    assert second.raw == {"confirmation_id": "conf_1"}


def test_book_replay_ignores_a_different_approval_object(option, monkeypatch):
    """Once a key is used, replaying it returns the FIRST result - even if a
    caller passes a different (or no) approval the second time."""
    approval = _fresh_approval(option)
    first = bk.book(option, idempotency_key="idem-12", approved_by=approval)
    assert first.reason == "no_provider_configured"

    # Replaying with no approval at all must still return the first result,
    # not re-evaluate and refuse.
    second = bk.book(option, idempotency_key="idem-12", approved_by=None)
    assert second == first


def test_book_rejects_missing_idempotency_key(option):
    with pytest.raises(bk.BookingValidationError):
        bk.book(option, idempotency_key="", approved_by=None)


def test_book_persists_result_in_idempotency_table(option):
    approval = _fresh_approval(option)
    result = bk.book(option, idempotency_key="idem-13", approved_by=approval)
    row = q1("SELECT * FROM idempotency WHERE key=? AND scope='booking'", ("idem-13",))
    assert row is not None
    stored = json.loads(row["result"])
    assert stored["idempotency_key"] == "idem-13"
    assert stored["ok"] == result.ok


# ---------------------------------------------------------------------------
# No payment detail or secret ever appears anywhere
# ---------------------------------------------------------------------------


def test_no_payment_detail_in_any_dataclass_fields():
    option_fields = {f.name for f in bk.Option.__dataclass_fields__.values()}
    approval_fields = {f.name for f in bk.Approval.__dataclass_fields__.values()}
    result_fields = {f.name for f in bk.BookingResult.__dataclass_fields__.values()}
    forbidden = {"card", "card_number", "cvv", "cvc", "account_number", "pan", "iban"}
    for fields in (option_fields, approval_fields, result_fields):
        for name in fields:
            assert name.lower() not in forbidden


def test_scrub_redacts_cardlike_digit_runs():
    payload = {"note": "card 4111111111111111 on file", "ok": True}
    scrubbed = bk._scrub(payload)
    assert "4111111111111111" not in json.dumps(scrubbed)
    assert "[REDACTED]" in scrubbed["note"]


def test_mocked_provider_cardlike_raw_is_scrubbed_before_returning(option, monkeypatch):
    priced = bk.Option(**{**option.as_dict(), "price": 42.0, "currency": "USD"})
    approval = _fresh_approval(priced)

    monkeypatch.setattr(bk, "_configured_provider_name", lambda: "testprov")
    monkeypatch.setattr(
        bk, "_execute_provider_booking",
        lambda *a, **k: {"confirmation_id": "abc", "leaked_card": "4111111111111111"},
    )

    result = bk.book(priced, idempotency_key="idem-14", approved_by=approval)
    blob = json.dumps(result.as_dict())
    assert "4111111111111111" not in blob
    row = q1("SELECT result FROM idempotency WHERE key=? AND scope='booking'", ("idem-14",))
    assert "4111111111111111" not in row["result"]


def test_no_secret_env_value_ever_appears_in_a_result(option, monkeypatch):
    monkeypatch.setenv("SOME_PROVIDER_SECRET", "sk_live_super_secret_value")
    approval = _fresh_approval(option)
    result = bk.book(option, idempotency_key="idem-15", approved_by=approval)
    blob = json.dumps(result.as_dict())
    assert "sk_live_super_secret_value" not in blob


# ---------------------------------------------------------------------------
# Approval / Option coercion from plain dicts (API-boundary round trip)
# ---------------------------------------------------------------------------


def test_book_accepts_dict_option_and_dict_approval(option):
    approval_dict = _fresh_approval(option).as_dict()
    result = bk.book(option.as_dict(), idempotency_key="idem-16", approved_by=approval_dict)
    assert result.reason == "no_provider_configured"
    assert result.ok is True


def test_coerce_option_rejects_garbage():
    with pytest.raises(bk.BookingValidationError):
        bk._coerce_option(12345)


def test_coerce_approval_rejects_garbage():
    with pytest.raises(bk.BookingValidationError):
        bk._coerce_approval(12345)
