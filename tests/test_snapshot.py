"""Runtime snapshot lookup tests.

The runtime path must not hit the network. Tests stub the loader to return
an in-memory dict so they don't depend on whether the bundled CSV was
generated at build time.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from btctrack.prices import snapshot


@pytest.fixture
def fake_snapshot(monkeypatch):
    """Replace the on-disk loader with a tiny in-memory series."""
    data = {
        date(2017, 12, 17): {"USD": 19000.0, "EUR": 16000.0, "CHF": 18800.0},
        date(2020, 3, 12): {"USD": 4900.0, "EUR": 4400.0, "CHF": 4700.0},
        date(2024, 1, 1): {"USD": 42500.0, "EUR": 38500.0, "CHF": 36000.0},
    }
    monkeypatch.setattr(snapshot, "_load", lambda: data)
    snapshot._reset_cache()
    yield data
    snapshot._reset_cache()


def test_lookup_price_hit(fake_snapshot):
    assert snapshot.lookup_price(date(2024, 1, 1), "USD") == 42500.0


def test_lookup_price_currency_case_insensitive(fake_snapshot):
    assert snapshot.lookup_price(date(2020, 3, 12), "eur") == 4400.0
    assert snapshot.lookup_price(date(2020, 3, 12), "Eur") == 4400.0


def test_lookup_price_missing_date_returns_none(fake_snapshot):
    assert snapshot.lookup_price(date(1999, 1, 1), "USD") is None


def test_lookup_price_missing_currency_returns_none(fake_snapshot):
    assert snapshot.lookup_price(date(2024, 1, 1), "GBP") is None


def test_lookup_prices_omits_missing_dates(fake_snapshot):
    requested = [date(2017, 12, 17), date(2018, 6, 1), date(2020, 3, 12)]
    out = snapshot.lookup_prices(requested, "CHF")
    assert out == {date(2017, 12, 17): 18800.0, date(2020, 3, 12): 4700.0}


def test_lookup_prices_empty_input(fake_snapshot):
    assert snapshot.lookup_prices([], "USD") == {}


def test_date_of_unix_seconds():
    assert snapshot.date_of(0) == date(1970, 1, 1)
    # 1700000000 → 2023-11-14 UTC
    assert snapshot.date_of(1_700_000_000) == date(2023, 11, 14)


def test_date_of_naive_datetime_treated_as_utc():
    naive = datetime(2024, 6, 1, 12, 0, 0)
    assert snapshot.date_of(naive) == date(2024, 6, 1)


def test_date_of_aware_datetime_converted_to_utc():
    # 2024-06-01 01:00 UTC+02:00 → 2024-05-31 23:00 UTC
    from datetime import timedelta, timezone as tz_module

    tz = tz_module(timedelta(hours=2))
    aware = datetime(2024, 6, 1, 1, 0, 0, tzinfo=tz)
    assert snapshot.date_of(aware) == date(2024, 5, 31)


def test_loader_handles_missing_file(monkeypatch):
    """If the bundled CSV isn't present (e.g. dev checkout, image not built),
    lookups must return None rather than crash."""
    snapshot._reset_cache()
    # Force the resource lookup to claim the file isn't there.
    monkeypatch.setattr(snapshot, "_load", lambda: {})
    snapshot._reset_cache()
    try:
        assert snapshot.lookup_price(date(2024, 1, 1), "USD") is None
        assert snapshot.lookup_prices([date(2024, 1, 1)], "USD") == {}
    finally:
        snapshot._reset_cache()


def test_csv_loader_round_trip(tmp_path, monkeypatch):
    """End-to-end check that the actual CSV parser handles the file shape
    that build_price_snapshot.py emits."""
    csv_path = tmp_path / "btc_prices.csv"
    csv_path.write_text(
        "date,USD,EUR,CHF\n"
        "2024-01-01,42500.000000,38500.000000,36000.000000\n"
        "2024-01-02,43000.000000,39000.000000,36500.000000\n"
    )

    class _FakeResource:
        def __init__(self, p): self._p = p
        def is_file(self): return True
        def open(self, *a, **kw): return self._p.open(*a, **kw)

    class _FakePackage:
        def __init__(self, p): self._p = p
        def joinpath(self, _name): return _FakeResource(self._p)

    monkeypatch.setattr(snapshot, "files", lambda _pkg: _FakePackage(csv_path))
    snapshot._reset_cache()
    try:
        assert snapshot.lookup_price(date(2024, 1, 1), "USD") == 42500.0
        assert snapshot.lookup_price(date(2024, 1, 2), "CHF") == 36500.0
    finally:
        snapshot._reset_cache()
