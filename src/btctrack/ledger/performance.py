"""Daily portfolio time-series + KPIs.

Reads classified transactions and the price cache from SQLite to build a
pandas DataFrame: holdings (BTC), portfolio value (fiat), open-lot cost
basis (fiat), realised P&L (cumulative). Inputs/outputs are kept here as
pure functions so they're easy to test and reuse from the UI.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

import pandas as pd
from sqlalchemy import select

from btctrack.config import get_settings
from btctrack.db.models import (
    RealizedGain,
    Transaction,
    TxIO,
    Wallet,
)
from btctrack.db.session import session_scope
from btctrack.ledger.fifo import (
    SATS_PER_BTC,
    ExternalIn,
    ExternalOut,
    LedgerState,
    replay,
)
from btctrack.prices.snapshot import latest_price, lookup_prices


@dataclass
class Kpis:
    total_btc: float
    portfolio_value_fiat: float
    open_cost_basis_fiat: float
    unrealised_pnl_fiat: float
    realised_pnl_fiat: float
    base_ccy: str


def _as_utc(dt: datetime) -> datetime:
    """Normalize a datetime to UTC-aware. Sync writes block_time with
    `tz=timezone.utc`, but SQLite's plain `DateTime` column drops the tzinfo
    on round-trip, so we reattach it on read."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _load_external_events() -> list[ExternalIn | ExternalOut]:
    events: list[ExternalIn | ExternalOut] = []
    with session_scope() as s:
        txs = (
            s.execute(
                select(Transaction).where(
                    Transaction.classification.in_(("external_in", "external_out"))
                )
            )
            .scalars()
            .all()
        )
        for tx in txs:
            if tx.btc_price_fiat is None or tx.block_time is None:
                # Skip tx with no price yet; sync should have fetched one.
                continue
            block_time = _as_utc(tx.block_time)
            owned_in = sum(io.amount_sats for io in tx.ios if io.direction == "in" and io.owned)
            owned_out = sum(io.amount_sats for io in tx.ios if io.direction == "out" and io.owned)
            if tx.classification == "external_in":
                # net received = owned_out - owned_in (>0)
                sats = owned_out - owned_in
                if sats <= 0:
                    continue
                events.append(
                    ExternalIn(
                        txid=tx.txid,
                        sats=sats,
                        btc_price_fiat=tx.btc_price_fiat,
                        block_time=block_time,
                    )
                )
            else:  # external_out
                # net sent = owned_in - owned_out (>0). Fee is paid out of inputs.
                sats = owned_in - owned_out - tx.fee_sats
                if sats < 0:
                    sats = 0
                events.append(
                    ExternalOut(
                        txid=tx.txid,
                        sats=sats,
                        fee_sats=tx.fee_sats,
                        btc_price_fiat=tx.btc_price_fiat,
                        block_time=block_time,
                    )
                )
    return events


def current_state() -> LedgerState:
    return replay(_load_external_events())


def total_owned_sats() -> int:
    """Sum of net sats currently held, derived from owned IOs over external txs only.

    Internal txs cancel out (every internal owned-out has a matching internal
    owned-in elsewhere in the same wallet), so we restrict to external_in/out
    plus internal — internal contributes a zero net by definition, so we can
    simplify by including everything classified except `unknown`.
    """
    state = current_state()
    return state.total_open_sats()


def kpis() -> Kpis:
    settings = get_settings()
    state = current_state()
    sats = state.total_open_sats()
    btc = sats / SATS_PER_BTC

    spot_pair = latest_price(settings.base_currency)
    spot = spot_pair[1] if spot_pair else 0.0
    value = btc * spot
    open_basis = state.total_open_cost_basis()
    realised = state.total_realised_gain()
    return Kpis(
        total_btc=btc,
        portfolio_value_fiat=value,
        open_cost_basis_fiat=open_basis,
        unrealised_pnl_fiat=value - open_basis,
        realised_pnl_fiat=realised,
        base_ccy=settings.base_currency,
    )


def daily_series() -> pd.DataFrame:
    """Return a daily DataFrame with columns: date, holdings_btc, value_fiat,
    cost_basis_fiat, realised_cum_fiat. Empty DF if no external txs."""
    settings = get_settings()
    events = sorted(_load_external_events(), key=lambda e: e.block_time)
    if not events:
        return pd.DataFrame(
            columns=["date", "holdings_btc", "value_fiat", "cost_basis_fiat", "realised_cum_fiat"]
        )

    start = events[0].block_time.date()
    end = date.today()
    days = pd.date_range(start, end, freq="D").date

    # Read prices straight from the bundled snapshot — the canonical source.
    # Days beyond the snapshot's last build date stay unpriced; the loop
    # forward-fills from the previous day so the chart doesn't drop to zero.
    price_by_date = lookup_prices(days, settings.base_currency)

    last_price = 0.0
    records = []
    state = LedgerState()
    ev_idx = 0

    realised_cum = 0.0
    for day in days:
        day_end = datetime.combine(day, datetime.max.time(), tzinfo=timezone.utc)
        # Apply all events whose block_time is on/before end of this day
        while ev_idx < len(events) and events[ev_idx].block_time <= day_end:
            ev = events[ev_idx]
            if isinstance(ev, ExternalIn):
                from btctrack.ledger.fifo import apply_external_in
                apply_external_in(state, ev)
            else:
                from btctrack.ledger.fifo import apply_external_out
                apply_external_out(state, ev)
            ev_idx += 1

        sats = state.total_open_sats()
        basis = state.total_open_cost_basis()
        realised_cum = state.total_realised_gain()
        if day in price_by_date:
            last_price = price_by_date[day]
        btc = sats / SATS_PER_BTC
        records.append(
            {
                "date": day,
                "holdings_btc": btc,
                "value_fiat": btc * last_price,
                "cost_basis_fiat": basis,
                "realised_cum_fiat": realised_cum,
            }
        )

    return pd.DataFrame.from_records(records)


def per_wallet_balances() -> pd.DataFrame:
    """Sats held per wallet (sum of owned outputs minus owned inputs, ignoring `unknown`)."""
    with session_scope() as s:
        wallets = s.execute(select(Wallet)).scalars().all()
        if not wallets:
            return pd.DataFrame(columns=["wallet", "label", "sats", "btc"])
        records = []
        for w in wallets:
            received = s.execute(
                select(TxIO).where(
                    TxIO.wallet_id == w.id, TxIO.direction == "out", TxIO.owned.is_(True)
                )
            ).scalars().all()
            spent = s.execute(
                select(TxIO).where(
                    TxIO.wallet_id == w.id, TxIO.direction == "in", TxIO.owned.is_(True)
                )
            ).scalars().all()
            sats = sum(r.amount_sats for r in received) - sum(sp.amount_sats for sp in spent)
            records.append(
                {
                    "wallet": w.id,
                    "label": w.label,
                    "sats": sats,
                    "btc": sats / SATS_PER_BTC,
                }
            )
        return pd.DataFrame.from_records(records)
