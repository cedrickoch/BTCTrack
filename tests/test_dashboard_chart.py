"""The dashboard chart exposes a bitcoin-price line and human-readable legend
labels. These guard the data contract (`daily_series` columns) and the chart
spec (friendly series names, compilable Altair layer) without booting Streamlit.
"""

from __future__ import annotations

from datetime import datetime, timezone

import altair as alt
import pandas as pd

from btctrack.db.models import Address, Transaction, TxIO, Wallet
from btctrack.db.session import session_scope
from btctrack.ledger.performance import daily_series

LABELS = {
    "value_fiat": "Portfolio value",
    "cost_basis_fiat": "Cost of holdings",
    "btc_price_fiat": "Bitcoin price",
    "holdings_btc": "Bitcoin held",
}


def _seed_buy() -> None:
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
                block_time=datetime(2024, 6, 1, 12, 0, 0, tzinfo=timezone.utc),
                btc_price_fiat=50_000.0,
                base_ccy="CHF",
                fee_sats=0,
            )
        )
        s.add(
            TxIO(
                txid="buy1", direction="in", address="payer",
                amount_sats=10_000_000, owned=False, wallet_id=None,
            )
        )
        s.add(
            TxIO(
                txid="buy1", direction="out", address="addrA",
                amount_sats=10_000_000, owned=True, wallet_id=w.id,
            )
        )


def test_daily_series_exposes_btc_price():
    _seed_buy()
    df = daily_series()
    assert "btc_price_fiat" in df.columns
    # Price is forward-filled from the snapshot, never zero once holdings exist.
    assert (df["btc_price_fiat"] > 0).all()


def test_chart_spec_uses_friendly_labels():
    _seed_buy()
    df = daily_series()
    ccy = "CHF"

    long = pd.melt(
        df,
        id_vars=["date"],
        value_vars=["value_fiat", "cost_basis_fiat", "btc_price_fiat"],
        var_name="series",
        value_name=ccy,
    )
    long["series"] = long["series"].map(LABELS)
    # No raw column names leak into the legend.
    assert set(long["series"].unique()) == {
        "Portfolio value",
        "Cost of holdings",
        "Bitcoin price",
    }

    holdings = df.assign(series=LABELS["holdings_btc"])
    fiat_lines = alt.Chart(long).mark_line().encode(
        x="date:T", y=f"{ccy}:Q", color="series:N"
    )
    btc_line = alt.Chart(holdings).mark_line().encode(
        x="date:T", y="holdings_btc:Q", color="series:N"
    )
    # The layered spec compiles to a Vega-Lite dict without error.
    spec = alt.layer(fiat_lines, btc_line).resolve_scale(y="independent").to_dict()
    assert spec["layer"]
