"""Build the bundled BTC price snapshot from Bitstamp + Frankfurter.

Run during `docker build` (or locally for development). Two no-account
public APIs feed this script:

* **Bitstamp** — daily BTC/USD OHLC closes, full history back to 2011
  (paginated, ~1000 candles per call).
* **Frankfurter** — the ECB's free FX reference-rate feed at
  https://api.frankfurter.dev. Single call returns daily USD→EUR and
  USD→CHF rates. ECB only publishes on weekdays, so weekend dates are
  forward-filled from the most recent weekday rate (matching how the ECB
  itself handles closures).

BTC-EUR / BTC-CHF are computed as BTC-USD × FX. The result is a single
joined CSV the runtime lookup module reads with `importlib.resources`.

Usage::

    python scripts/build_price_snapshot.py \
        --output src/btctrack/prices/data/btc_prices.csv
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx

# Order here defines column order in the emitted CSV (after "date").
QUOTE_CCYS: tuple[str, ...] = ("USD", "EUR", "CHF")

BITSTAMP_BASE_URL = "https://www.bitstamp.net"
FRANKFURTER_BASE_URL = "https://api.frankfurter.dev"

# Bitstamp BTC/USD candles begin 2011-08-18; querying earlier just returns
# the same earliest record. Use a comfortable lower bound.
HISTORY_START = datetime(2011, 1, 1, tzinfo=timezone.utc)
BITSTAMP_PAGE_LIMIT = 1000
SECONDS_PER_DAY = 86400
USER_AGENT = "btctrack-build-price-snapshot/1.0"
MAX_RETRIES = 4
PAGE_SLEEP_S = 0.5  # politeness between paginated requests


# ---------- low-level fetch helpers --------------------------------------


def _http_get_json(client: httpx.Client, url: str, params: dict, *, label: str) -> dict:
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    last_err: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = client.get(url, params=params, headers=headers, timeout=60.0)
            resp.raise_for_status()
            return resp.json()
        except (httpx.HTTPError, ValueError) as e:
            last_err = e
            if attempt == MAX_RETRIES:
                raise RuntimeError(
                    f"{label} request failed after {MAX_RETRIES} attempts: {e}"
                ) from e
            sleep_for = 2**attempt
            print(
                f"[build_price_snapshot] {label}: attempt {attempt} failed "
                f"({e!r}); sleeping {sleep_for}s",
                file=sys.stderr,
            )
            time.sleep(sleep_for)
    raise RuntimeError(f"unreachable: {last_err}")  # pragma: no cover


def _ts_to_date(ts_seconds: int) -> date:
    return datetime.fromtimestamp(ts_seconds, tz=timezone.utc).date()


# ---------- Bitstamp -----------------------------------------------------


def fetch_btc_usd(
    client: httpx.Client,
    base_url: str,
    *,
    max_pages: int = 50,
) -> dict[date, float]:
    """Walk Bitstamp's BTC/USD daily OHLC forward.

    Bitstamp returns up to 1000 candles per call but often fewer in the
    early years (sparse trading days). We can't use ``len(page) < limit``
    as the stop signal — instead we keep paginating until either the
    cursor advances past today or a page brings nothing new.
    """
    url = f"{base_url}/api/v2/ohlc/btcusd/"
    series: dict[date, float] = {}
    start_ts = int(HISTORY_START.timestamp())
    today_ts = int(datetime.now(tz=timezone.utc).timestamp())
    for _ in range(max_pages):
        payload = _http_get_json(
            client,
            url,
            params={"step": SECONDS_PER_DAY, "limit": BITSTAMP_PAGE_LIMIT, "start": start_ts},
            label=f"Bitstamp btcusd@{start_ts}",
        )
        ohlc = ((payload.get("data") or {}).get("ohlc")) or []
        if not ohlc:
            break
        new_records = 0
        last_ts = start_ts
        for candle in ohlc:
            ts = int(candle["timestamp"])
            d = _ts_to_date(ts)
            if d not in series:
                new_records += 1
            series[d] = float(candle["close"])
            if ts > last_ts:
                last_ts = ts
        if new_records == 0:
            break
        next_start = last_ts + SECONDS_PER_DAY
        if next_start > today_ts or next_start <= start_ts:
            break
        start_ts = next_start
        time.sleep(PAGE_SLEEP_S)
    if not series:
        raise RuntimeError("Bitstamp returned no BTC/USD candles.")
    return series


# ---------- Frankfurter (ECB FX) -----------------------------------------


def fetch_fx(
    client: httpx.Client,
    base_url: str,
    *,
    start: date,
    end: date,
    symbols: tuple[str, ...] = ("EUR", "CHF"),
) -> dict[date, dict[str, float]]:
    """One call → ECB-published daily USD→{symbols} rates between start..end."""
    url = f"{base_url}/v1/{start.isoformat()}..{end.isoformat()}"
    payload = _http_get_json(
        client,
        url,
        params={"base": "USD", "symbols": ",".join(symbols)},
        label="Frankfurter USD→FX",
    )
    rates_raw = payload.get("rates") or {}
    if not rates_raw:
        raise RuntimeError("Frankfurter returned no FX rates.")
    out: dict[date, dict[str, float]] = {}
    for d_str, day in rates_raw.items():
        out[date.fromisoformat(d_str)] = {sym: float(day[sym]) for sym in symbols if sym in day}
    return out


def forward_fill_fx(
    fx: dict[date, dict[str, float]],
    *,
    span: tuple[date, date],
    symbols: tuple[str, ...] = ("EUR", "CHF"),
) -> dict[date, dict[str, float]]:
    """Fill weekends/holidays by carrying forward the last published weekday rate.

    Anchors at the first available rate; days strictly before the first
    publication are dropped, since BTC dates predating ECB FX coverage just
    can't be priced in EUR/CHF.
    """
    start, end = span
    sorted_days = sorted(fx.keys())
    if not sorted_days:
        return {}
    cursor = sorted_days[0]
    if cursor > start:
        start = cursor
    out: dict[date, dict[str, float]] = {}
    last: dict[str, float] | None = None
    d = start
    one_day = timedelta(days=1)
    while d <= end:
        if d in fx:
            last = fx[d]
        if last is not None:
            out[d] = {sym: last[sym] for sym in symbols if sym in last}
        d += one_day
    return out


# ---------- merge + emit -------------------------------------------------


def build_rows(
    btc_usd: dict[date, float],
    fx: dict[date, dict[str, float]],
) -> list[dict[str, str]]:
    """Join BTC/USD with the forward-filled FX series into one row per date."""
    rows: list[dict[str, str]] = []
    for d in sorted(btc_usd):
        usd = btc_usd[d]
        rates = fx.get(d)
        if rates is None:
            # Drop dates we can't price in all three currencies — keeps the
            # CSV rectangular and the runtime simple.
            continue
        eur_rate = rates.get("EUR")
        chf_rate = rates.get("CHF")
        if eur_rate is None or chf_rate is None:
            continue
        rows.append({
            "date": d.isoformat(),
            "USD": f"{usd:.6f}",
            "EUR": f"{usd * eur_rate:.6f}",
            "CHF": f"{usd * chf_rate:.6f}",
        })
    return rows


def write_csv(rows: list[dict[str, str]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = ["date", *QUOTE_CCYS]
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def build(
    output: Path,
    *,
    bitstamp_base_url: str = BITSTAMP_BASE_URL,
    frankfurter_base_url: str = FRANKFURTER_BASE_URL,
) -> int:
    today = datetime.now(tz=timezone.utc).date()
    with httpx.Client() as client:
        btc_usd = fetch_btc_usd(client, bitstamp_base_url)
        fx_raw = fetch_fx(
            client,
            frankfurter_base_url,
            start=min(btc_usd),
            end=today,
        )
    fx = forward_fill_fx(fx_raw, span=(min(btc_usd), today))
    rows = build_rows(btc_usd, fx)
    write_csv(rows, output)
    return len(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Path to the CSV file to write.",
    )
    parser.add_argument(
        "--bitstamp-base-url",
        default=BITSTAMP_BASE_URL,
        help="Bitstamp base URL (default: %(default)s)",
    )
    parser.add_argument(
        "--frankfurter-base-url",
        default=FRANKFURTER_BASE_URL,
        help="Frankfurter base URL (default: %(default)s)",
    )
    args = parser.parse_args(argv)
    n = build(
        args.output,
        bitstamp_base_url=args.bitstamp_base_url,
        frankfurter_base_url=args.frankfurter_base_url,
    )
    print(f"[build_price_snapshot] wrote {n} rows to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
