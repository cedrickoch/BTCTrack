"""Build-script unit tests — no network calls.

The script is run during `docker build`, so the integration test for the
HTTP path is the docker build itself. These tests exercise the offline
glue: Bitstamp pagination, Frankfurter parse, weekend forward-fill, and
the BTC/USD × FX merge.
"""

from __future__ import annotations

import csv
import sys
from datetime import date
from pathlib import Path

import pytest

# scripts/ is not a package; load the module directly off disk.
SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))
import build_price_snapshot as bps  # type: ignore  # noqa: E402


class _FakeResp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status
    def raise_for_status(self): pass
    def json(self): return self._payload


# ---------- Bitstamp pagination ------------------------------------------


def test_fetch_btc_usd_paginates_forward_until_no_new_records():
    """Bitstamp may return < limit records on early pages (sparse early
    trading), so we paginate until a page contributes nothing new."""
    pages = [
        # First page: 800 records starting 2014-01-01 (1388534400)
        {"data": {"ohlc": [
            {"timestamp": str(1_388_534_400 + i * 86400), "close": str(100 + i)}
            for i in range(800)
        ]}},
        # Second page: 600 more records continuing forward
        {"data": {"ohlc": [
            {"timestamp": str(1_388_534_400 + (800 + i) * 86400), "close": str(2000 + i)}
            for i in range(600)
        ]}},
        # Third page: same records as second (no advancement) → stop.
        {"data": {"ohlc": [
            {"timestamp": str(1_388_534_400 + (800 + i) * 86400), "close": str(2000 + i)}
            for i in range(600)
        ]}},
    ]
    calls: list[dict] = []

    class _FakeClient:
        def get(self, url, params, headers, timeout):
            calls.append({"url": url, "params": params})
            return _FakeResp(pages.pop(0))

    out = bps.fetch_btc_usd(_FakeClient(), "https://bs.test")
    assert len(out) == 1400  # 800 + 600 unique
    assert len(calls) == 3   # third call brings nothing new → loop exits
    assert calls[1]["params"]["start"] > calls[0]["params"]["start"]
    assert out[date(2014, 1, 1)] == 100.0


def test_fetch_btc_usd_stops_immediately_on_empty_first_page():
    class _FakeClient:
        def get(self, url, params, headers, timeout):
            return _FakeResp({"data": {"ohlc": []}})

    with pytest.raises(RuntimeError, match="no BTC/USD candles"):
        bps.fetch_btc_usd(_FakeClient(), "https://bs.test")


def test_fetch_btc_usd_respects_max_pages():
    """Max-pages safety net should engage if neither cursor nor empty page fires."""
    page = {"data": {"ohlc": [
        {"timestamp": str(1_388_534_400 + i * 86400), "close": str(i)}
        for i in range(100)
    ]}}
    pages_seen = {"n": 0}

    class _FakeClient:
        def get(self, url, params, headers, timeout):
            pages_seen["n"] += 1
            # Return ever-advancing records so the "no new records" termination
            # never fires; we rely on max_pages to break out.
            offset = pages_seen["n"] * 100
            return _FakeResp({"data": {"ohlc": [
                {"timestamp": str(1_388_534_400 + (offset + i) * 86400), "close": str(i)}
                for i in range(100)
            ]}})

    out = bps.fetch_btc_usd(_FakeClient(), "https://bs.test", max_pages=3)
    assert pages_seen["n"] == 3
    assert len(out) == 300


# ---------- Frankfurter --------------------------------------------------


def test_fetch_fx_parses_frankfurter_response():
    captured: dict = {}

    class _FakeClient:
        def get(self, url, params, headers, timeout):
            captured["url"] = url
            captured["params"] = params
            return _FakeResp({"rates": {
                "2024-01-02": {"EUR": 0.91, "CHF": 0.85},
                "2024-01-03": {"EUR": 0.92, "CHF": 0.86},
            }})

    out = bps.fetch_fx(
        _FakeClient(),
        "https://fr.test",
        start=date(2024, 1, 1),
        end=date(2024, 1, 5),
    )
    assert captured["url"] == "https://fr.test/v1/2024-01-01..2024-01-05"
    assert captured["params"] == {"base": "USD", "symbols": "EUR,CHF"}
    assert out == {
        date(2024, 1, 2): {"EUR": 0.91, "CHF": 0.85},
        date(2024, 1, 3): {"EUR": 0.92, "CHF": 0.86},
    }


def test_fetch_fx_raises_when_empty():
    class _FakeClient:
        def get(self, url, params, headers, timeout):
            return _FakeResp({"rates": {}})

    with pytest.raises(RuntimeError, match="no FX rates"):
        bps.fetch_fx(_FakeClient(), "https://fr.test", start=date(2024, 1, 1), end=date(2024, 1, 2))


# ---------- weekend forward-fill -----------------------------------------


def test_forward_fill_fills_weekends_from_friday():
    """Friday rates carry through Saturday and Sunday."""
    fx = {
        date(2024, 1, 5): {"EUR": 0.91, "CHF": 0.85},   # Friday
        date(2024, 1, 8): {"EUR": 0.92, "CHF": 0.86},   # Monday
    }
    out = bps.forward_fill_fx(fx, span=(date(2024, 1, 5), date(2024, 1, 9)))
    assert out[date(2024, 1, 5)] == {"EUR": 0.91, "CHF": 0.85}
    assert out[date(2024, 1, 6)] == {"EUR": 0.91, "CHF": 0.85}   # Sat → Fri
    assert out[date(2024, 1, 7)] == {"EUR": 0.91, "CHF": 0.85}   # Sun → Fri
    assert out[date(2024, 1, 8)] == {"EUR": 0.92, "CHF": 0.86}   # Mon
    assert out[date(2024, 1, 9)] == {"EUR": 0.92, "CHF": 0.86}   # Tue → Mon (no Tue rate yet)


def test_forward_fill_drops_dates_before_first_rate():
    """Days before the first ECB publication just disappear — they can't be priced."""
    fx = {date(2024, 1, 5): {"EUR": 0.91, "CHF": 0.85}}
    out = bps.forward_fill_fx(fx, span=(date(2024, 1, 1), date(2024, 1, 6)))
    # Only the Jan-5/Jan-6 dates make it.
    assert sorted(out.keys()) == [date(2024, 1, 5), date(2024, 1, 6)]


# ---------- merge + write ------------------------------------------------


def test_build_rows_computes_eur_and_chf_from_usd():
    btc_usd = {
        date(2024, 1, 5): 40000.0,
        date(2024, 1, 6): 41000.0,
    }
    fx = {
        date(2024, 1, 5): {"EUR": 0.91, "CHF": 0.85},
        date(2024, 1, 6): {"EUR": 0.91, "CHF": 0.85},
    }
    rows = bps.build_rows(btc_usd, fx)
    assert [r["date"] for r in rows] == ["2024-01-05", "2024-01-06"]
    assert rows[0]["USD"] == "40000.000000"
    assert rows[0]["EUR"] == f"{40000.0 * 0.91:.6f}"
    assert rows[0]["CHF"] == f"{40000.0 * 0.85:.6f}"


def test_build_rows_drops_dates_with_no_fx():
    btc_usd = {date(2024, 1, 5): 40000.0, date(2024, 1, 6): 41000.0}
    fx = {date(2024, 1, 5): {"EUR": 0.91, "CHF": 0.85}}  # missing Jan 6
    rows = bps.build_rows(btc_usd, fx)
    assert [r["date"] for r in rows] == ["2024-01-05"]


def test_write_csv_round_trip(tmp_path):
    rows = [
        {"date": "2024-01-05", "USD": "40000.000000", "EUR": "36400.000000", "CHF": "34000.000000"},
    ]
    out = tmp_path / "btc_prices.csv"
    bps.write_csv(rows, out)

    with out.open() as f:
        reader = csv.DictReader(f)
        loaded = list(reader)
    assert loaded == rows
    with out.open() as f:
        assert f.readline().strip() == "date,USD,EUR,CHF"


def test_write_csv_creates_parent_dirs(tmp_path):
    out = tmp_path / "deep" / "nested" / "btc_prices.csv"
    bps.write_csv([{"date": "2024-01-01", "USD": "1", "EUR": "2", "CHF": "3"}], out)
    assert out.is_file()
