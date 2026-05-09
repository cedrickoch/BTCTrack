from __future__ import annotations

import altair as alt
import pandas as pd
import streamlit as st

from btctrack.ledger.performance import daily_series, kpis, per_wallet_balances

st.set_page_config(page_title="BTCTrack — Dashboard", page_icon="₿", layout="wide")
st.title("Dashboard")

k = kpis()
ccy = k.base_ccy

c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Total BTC", f"{k.total_btc:.8f}")
c2.metric(f"Value ({ccy})", f"{k.portfolio_value_fiat:,.2f}")
c3.metric(f"Cost basis ({ccy})", f"{k.open_cost_basis_fiat:,.2f}")
c4.metric(
    f"Unrealised P&L ({ccy})",
    f"{k.unrealised_pnl_fiat:,.2f}",
    delta=f"{(k.unrealised_pnl_fiat / k.open_cost_basis_fiat * 100):.2f}%"
    if k.open_cost_basis_fiat
    else None,
)
c5.metric(f"Realised P&L ({ccy})", f"{k.realised_pnl_fiat:,.2f}")

st.subheader("Portfolio value vs cost basis")
df = daily_series()
if df.empty:
    st.info("No external transactions yet. Add a wallet and run a sync.")
else:
    long = pd.melt(
        df,
        id_vars=["date"],
        value_vars=["value_fiat", "cost_basis_fiat"],
        var_name="series",
        value_name=f"{ccy}",
    )
    chart = (
        alt.Chart(long)
        .mark_line()
        .encode(
            x="date:T",
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
        .properties(height=320)
    )
    st.altair_chart(chart, use_container_width=True)

st.subheader("Holdings per wallet")
balances = per_wallet_balances()
if balances.empty:
    st.caption("No wallets yet.")
else:
    st.dataframe(
        balances[["label", "btc", "sats"]].rename(
            columns={"label": "Wallet", "btc": "BTC", "sats": "sats"}
        ),
        use_container_width=True,
        hide_index=True,
    )
