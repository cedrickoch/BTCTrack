from __future__ import annotations

import altair as alt
import pandas as pd
import streamlit as st

from btctrack.ledger.performance import daily_series, kpis, per_wallet_balances
from btctrack.ui.header import render_page_header
from btctrack.ui.privacy import (
    chart_placeholder,
    fmt_btc,
    fmt_fiat,
    is_unlocked,
    mask_dataframe,
    render_sidebar_lock,
)

render_sidebar_lock()
render_page_header("Dashboard")

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
total_pnl_fiat = k.realised_pnl_fiat + k.unrealised_pnl_fiat
c5.metric(
    f"Total P&L ({ccy})",
    fmt_fiat(total_pnl_fiat, ""),
    delta=f"{(total_pnl_fiat / k.open_cost_basis_fiat * 100):.2f}%"
    if unlocked and k.open_cost_basis_fiat
    else None,
)

_, scale_col = st.columns([3, 1])
y_scale = scale_col.radio(
    "Y-axis scale",
    ("Linear", "Logarithmic"),
    horizontal=True,
    label_visibility="collapsed",
)
scale_type = "log" if y_scale == "Logarithmic" else "linear"

df = daily_series()
if df.empty:
    st.info("No external transactions yet. Add a wallet and run a sync.")
elif not unlocked:
    chart_placeholder()
else:
    # Human-readable legend labels keyed off the raw column names.
    labels = {
        "value_fiat": "Portfolio value",
        "cost_basis_fiat": "Cost of holdings",
        "btc_price_fiat": "Bitcoin price",
        "holdings_btc": "Bitcoin held",
    }
    # One shared colour scale so the fiat lines and the BTC line collapse into a
    # single merged legend instead of two.
    color_scale = alt.Scale(
        domain=[
            "Portfolio value",
            "Cost of holdings",
            "Bitcoin price",
            "Bitcoin held",
        ],
        range=["#1f77b4", "#888", "#2ca02c", "#f7931a"],
    )
    legend = alt.Legend(title=None, orient="top")

    long = pd.melt(
        df,
        id_vars=["date"],
        value_vars=["value_fiat", "cost_basis_fiat", "btc_price_fiat"],
        var_name="series",
        value_name=f"{ccy}",
    )
    long["series"] = long["series"].map(labels)
    fiat_lines = (
        alt.Chart(long)
        .mark_line()
        .encode(
            x=alt.X("date:T", title="Date", axis=alt.Axis(format="%b %Y", labelAngle=-45)),
            y=alt.Y(f"{ccy}:Q", title=ccy, scale=alt.Scale(type=scale_type)),
            color=alt.Color("series:N", scale=color_scale, legend=legend),
        )
    )
    holdings = df.assign(series=labels["holdings_btc"])
    btc_line = (
        alt.Chart(holdings)
        .mark_line(strokeDash=[4, 3])
        .encode(
            x="date:T",
            y=alt.Y(
                "holdings_btc:Q",
                title="BTC",
                axis=alt.Axis(titleColor="#f7931a", labelColor="#f7931a"),
                scale=alt.Scale(type=scale_type),
            ),
            color=alt.Color("series:N", scale=color_scale, legend=legend),
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
