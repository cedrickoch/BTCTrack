"""Regression: daily_series must not crash when block_time is offset-naive.

SQLite's plain `DateTime` column drops tzinfo on round-trip. Sync writes
block_time as UTC-aware, but the value comes back naive, and earlier
versions of `daily_series` compared it against a tz-aware day_end — which
raised `TypeError: can't compare offset-naive and offset-aware datetimes`
on the dashboard.
"""

from __future__ import annotations

from datetime import datetime

from btctrack.db.models import Address, Transaction, TxIO, Wallet
from btctrack.db.session import session_scope
from btctrack.ledger.performance import daily_series


def test_daily_series_handles_naive_block_time():
    with session_scope() as s:
        w = Wallet(
            label="A", kind="address", value="addrA",
            script_type="p2wpkh", gap_limit=20,
        )
        s.add(w)
        s.flush()
        s.add(Address(wallet_id=w.id, address="addrA", chain="receive"))

        # Naive datetime — what SQLite returns even when we wrote UTC-aware.
        naive_block_time = datetime(2024, 6, 1, 12, 0, 0)
        s.add(
            Transaction(
                txid="tx1",
                classification="external_in",
                block_time=naive_block_time,
                btc_price_fiat=60_000.0,
                base_ccy="CHF",
                fee_sats=0,
            )
        )
        s.add(
            TxIO(
                txid="tx1", direction="in", address="payer",
                amount_sats=100_000, owned=False, wallet_id=None,
            )
        )
        s.add(
            TxIO(
                txid="tx1", direction="out", address="addrA",
                amount_sats=99_000, owned=True, wallet_id=w.id,
            )
        )
    df = daily_series()
    assert not df.empty
    assert df.iloc[0]["holdings_btc"] > 0
