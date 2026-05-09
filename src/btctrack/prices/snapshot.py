"""Local BTC price lookup against the bundled snapshot.

The snapshot CSV is generated at `docker build` time by
`scripts/build_price_snapshot.py` and shipped inside the package at
`btctrack/prices/data/btc_prices.csv`. There are no outbound HTTP calls
at runtime — dates that aren't in the snapshot return ``None`` and any
caller (currently `sync.py`) leaves the corresponding transaction unpriced.

CSV shape::

    date,USD,EUR,CHF
    2013-04-28,134.21,...,...

The file is read lazily on first lookup and cached for the process lifetime.
"""

from __future__ import annotations

import csv
from datetime import date, datetime, timezone
from importlib.resources import files
from typing import Iterable

_PACKAGE = "btctrack.prices.data"
_FILENAME = "btc_prices.csv"

# Populated lazily; tests can call _reset_cache() to force a re-read.
_CACHE: dict[date, dict[str, float]] | None = None


def date_of(ts: datetime | int | float) -> date:
    """Normalise a timestamp (UTC datetime or unix seconds) to a UTC date."""
    if isinstance(ts, (int, float)):
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).date()
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc).date()


def _load() -> dict[date, dict[str, float]]:
    """Read the bundled CSV. Returns ``{}`` when the file isn't present
    (so the runtime degrades gracefully if the snapshot wasn't baked in)."""
    try:
        resource = files(_PACKAGE).joinpath(_FILENAME)
    except (ModuleNotFoundError, FileNotFoundError):
        return {}
    if not resource.is_file():
        return {}
    out: dict[date, dict[str, float]] = {}
    with resource.open("r", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            d = date.fromisoformat(row["date"])
            entry: dict[str, float] = {}
            for key, val in row.items():
                if key == "date" or val in (None, ""):
                    continue
                entry[key.upper()] = float(val)
            out[d] = entry
    return out


def _get_data() -> dict[date, dict[str, float]]:
    global _CACHE
    if _CACHE is None:
        _CACHE = _load()
    return _CACHE


def _reset_cache() -> None:
    """Test helper — drop the in-memory snapshot so the next lookup re-reads."""
    global _CACHE
    _CACHE = None


def lookup_price(d: date, ccy: str) -> float | None:
    return _get_data().get(d, {}).get(ccy.upper())


def lookup_prices(dates: Iterable[date], ccy: str) -> dict[date, float]:
    """Look up many dates at once. Missing dates are omitted, not raised."""
    data = _get_data()
    key = ccy.upper()
    out: dict[date, float] = {}
    for d in dates:
        price = data.get(d, {}).get(key)
        if price is not None:
            out[d] = price
    return out


def latest_price(ccy: str) -> tuple[date, float] | None:
    """Most recent (date, price) for `ccy` in the bundled snapshot, or None
    if the snapshot is missing or doesn't carry that currency. Used as the
    spot price on the dashboard — the snapshot is the canonical price source."""
    data = _get_data()
    key = ccy.upper()
    best: tuple[date, float] | None = None
    for d, prices in data.items():
        price = prices.get(key)
        if price is None:
            continue
        if best is None or d > best[0]:
            best = (d, price)
    return best
