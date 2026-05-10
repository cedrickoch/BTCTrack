from __future__ import annotations

import altair as alt
import pandas as pd
import streamlit as st

from btctrack.ledger.performance import daily_series, kpis, per_wallet_balances
from btctrack.ui.privacy import (
    chart_placeholder,
    fmt_btc,
    fmt_fiat,
    is_unlocked,
    mask_dataframe,
    render_sidebar_lock,
)

st.set_page_config(page_title="BTCTrack — Dashboard", page_icon="₿", layout="wide")
render_sidebar_lock()
st.title("Dashboard")

k = kpis()
ccy = k.base_ccy
unlocked = is_unlocked()

c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Total BTC", fmt_btc(k.total_btc))
c2.metric(f"Value ({ccy})", fmt_fiat(k.portfolio_value_fiat, ""))
c3.metric(f"Cost basis ({ccy})", fmt_fiat(k.open_cost_basis_fiat, ""))
c4.metric(
    f"Unrealised P&L ({ccy})",
    fmt_fiat(k.unrealised_pnl_fiat, ""),
    delta=f"{(k.unrealised_pnl_fiat / k.open_cost_basis_fiat * 100):.2f}%"
    if unlocked and k.open_cost_basis_fiat
    else None,
)
c5.metric(f"Realised P&L ({ccy})", fmt_fiat(k.realised_pnl_fiat, ""))

st.subheader("Portfolio value vs cost basis")
df = daily_series()
if df.empty:
    st.info("No external transactions yet. Add a wallet and run a sync.")
elif not unlocked:
    chart_placeholder()
else:
    long = pd.melt(
        df,
        id_vars=["date"],
        value_vars=["value_fiat", "cost_basis_fiat"],
        var_name="series",
        value_name=f"{ccy}",
    )
    fiat_lines = (
        alt.Chart(long)
        .mark_line()
        .encode(
            x=alt.X("date:T", title="Date"),
            y=alt.Y(f"{ccy}:Q", title=ccy),
            color=alt.Color(
                "series:N",
                scale=alt.Scale(
                    domain=["value_fiat", "cost_basis_fiat"],
                    range=["#1f77b4", "#888"],
                ),
                legend=alt.Legend(title="Series"),
            ),
        )
    )
    btc_line = (
        alt.Chart(df)
        .mark_line(strokeDash=[4, 3], color="#f7931a")
        .encode(
            x="date:T",
            y=alt.Y(
                "holdings_btc:Q",
                title="BTC",
                axis=alt.Axis(titleColor="#f7931a", labelColor="#f7931a"),
            ),
        )
    )
    chart = alt.layer(fiat_lines, btc_line).resolve_scale(y="independent").properties(height=320)
    st.altair_chart(chart, width="stretch")

st.subheader("Holdings per wallet")
balances = per_wallet_balances()
if balances.empty:
    st.caption("No wallets yet.")
else:
    table = balances[["label", "btc", "sats"]].rename(
        columns={"label": "Wallet", "btc": "BTC", "sats": "sats"}
    )
    st.dataframe(
        mask_dataframe(table, btc_cols=("BTC",), sats_cols=("sats",)),
        width="stretch",
        hide_index=True,
    )
