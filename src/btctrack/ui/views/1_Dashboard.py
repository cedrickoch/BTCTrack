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
    # Each line: source column, human-readable label, colour, and a swatch for
    # the toggle "legend". The first three are fiat (left axis); the last is the
    # BTC holdings line on its own right axis.
    series_meta = [
        ("value_fiat", "Portfolio value", "#1f77b4", "🟦"),
        ("cost_basis_fiat", "Cost of holdings", "#888", "⬜"),
        ("btc_price_fiat", "Bitcoin price", "#2ca02c", "🟩"),
        ("holdings_btc", "Bitcoin held", "#f7931a", "🟧"),
    ]
    color_scale = alt.Scale(
        domain=[m[1] for m in series_meta],
        range=[m[2] for m in series_meta],
    )

    # Custom legend: one toggle per line. Vega-Lite's own legend binding can't do
    # plain-click toggle (it replaces the selection on each click) nor show which
    # lines are hidden, so we drive visibility from these toggles and filter the
    # data — hidden lines disappear entirely and the axes rescale to what remains.
    st.caption("Show / hide lines")
    toggle_cols = st.columns(len(series_meta))
    visible = {
        label: col.toggle(f"{swatch} {label}", value=True, key=f"line_{key}")
        for col, (key, label, _color, swatch) in zip(toggle_cols, series_meta)
    }

    fiat_meta = series_meta[:3]
    long = pd.melt(
        df,
        id_vars=["date"],
        value_vars=[m[0] for m in fiat_meta],
        var_name="series",
        value_name=f"{ccy}",
    )
    long["series"] = long["series"].map({m[0]: m[1] for m in fiat_meta})
    long = long[long["series"].map(visible)]

    layers = []
    if not long.empty:
        layers.append(
            alt.Chart(long)
            .mark_line()
            .encode(
                x=alt.X("date:T", title="Date", axis=alt.Axis(format="%b %Y", labelAngle=-45)),
                y=alt.Y(f"{ccy}:Q", title=ccy, scale=alt.Scale(type=scale_type)),
                color=alt.Color("series:N", scale=color_scale, legend=None),
            )
        )
    if visible["Bitcoin held"]:
        holdings = df.assign(series="Bitcoin held")
        layers.append(
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
                color=alt.Color("series:N", scale=color_scale, legend=None),
            )
        )

    if layers:
        chart = alt.layer(*layers).resolve_scale(y="independent").properties(height=320)
        st.altair_chart(chart, width="stretch")
    else:
        st.info("All lines hidden — toggle one on above to show the chart.")

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
