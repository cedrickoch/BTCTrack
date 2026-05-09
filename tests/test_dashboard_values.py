"""Regression: dashboard `kpis()` and `daily_series()` must produce non-zero
fiat values when the user holds BTC.

Earlier versions read prices from the empty `PriceCache` table (nothing
populated it), so spot price was always 0 and every chart point read 0.
Prices now come straight from the bundled snapshot, which is the canonical
source for the rest of sync.
"""

from __future__ import annotations

from datetime import datetime, timezone

from btctrack.db.models import Address, Transaction, TxIO, Wallet
from btctrack.db.session import session_scope
from btctrack.ledger.performance import daily_series, kpis


def _seed_buy(amount_sats: int, btc_price_fiat: float, when: datetime) -> None:
    with session_scope() as s:
        w = Wallet(
            label="A", kind="address", value="addrA",
            script_type="p2wpkh", gap_limit=20,
        )
        s.add(w)
        s.flush()
        s.add(Address(wallet_id=w.id, address="addrA", chain="receive"))
        s.add(
            Transaction(
                txid="buy1",
                classification="external_in",
                block_time=when,
                btc_price_fiat=btc_price_fiat,
                base_ccy="CHF",
                fee_sats=0,
            )
        )
        s.add(
            TxIO(
                txid="buy1", direction="in", address="payer",
                amount_sats=amount_sats, owned=False, wallet_id=None,
            )
        )
        s.add(
            TxIO(
                txid="buy1", direction="out", address="addrA",
                amount_sats=amount_sats, owned=True, wallet_id=w.id,
            )
        )


def test_kpis_uses_snapshot_spot_price():
    _seed_buy(
        amount_sats=10_000_000,  # 0.1 BTC
        btc_price_fiat=50_000.0,
        when=datetime(2024, 6, 1, 12, 0, 0, tzinfo=timezone.utc),
    )
    k = kpis()
    assert k.total_btc == 0.1
    # Snapshot covers BASE_CURRENCY=CHF (set by conftest); spot must be > 0.
    assert k.portfolio_value_fiat > 0, (
        "portfolio_value_fiat should reflect snapshot spot price, not 0"
    )
    # Cost basis at acquisition: 0.1 BTC * 50_000 = 5_000.
    assert k.open_cost_basis_fiat == 5_000.0


def test_daily_series_value_fiat_nonzero():
    _seed_buy(
        amount_sats=10_000_000,
        btc_price_fiat=50_000.0,
        when=datetime(2024, 6, 1, 12, 0, 0, tzinfo=timezone.utc),
    )
    df = daily_series()
    assert not df.empty
    # Every day from 2024-06-01 onward holds 0.1 BTC priced from the snapshot,
    # so value_fiat must be > 0 on every row.
    assert (df["value_fiat"] > 0).all(), (
        "value_fiat should be > 0 once holdings exist and snapshot has prices"
    )
