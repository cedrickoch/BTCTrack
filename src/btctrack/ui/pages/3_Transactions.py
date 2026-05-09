from __future__ import annotations

import streamlit as st
from sqlalchemy import select

from btctrack.db.models import Transaction, TxIO, Wallet
from btctrack.db.session import session_scope

st.set_page_config(page_title="BTCTrack — Transactions", page_icon="₿", layout="wide")
st.title("Transactions")

CLASS_BADGES = {
    "internal": "🔁 internal",
    "external_in": "⬇️ external_in",
    "external_out": "⬆️ external_out",
    "unknown": "❓ unknown",
}

with session_scope() as s:
    classes = ["all"] + sorted({t.classification for t in s.execute(select(Transaction)).scalars()})
    wallets = {w.id: w.label for w in s.execute(select(Wallet)).scalars()}

c1, c2 = st.columns(2)
sel_class = c1.selectbox("Classification", classes)
sel_wallet = c2.selectbox(
    "Wallet", ["all"] + [f"#{wid} — {label}" for wid, label in wallets.items()]
)

rows = []
with session_scope() as s:
    q = select(Transaction)
    if sel_class != "all":
        q = q.where(Transaction.classification == sel_class)
    txs = s.execute(q.order_by(Transaction.block_time.desc())).scalars().all()
    for t in txs:
        ios = list(t.ios)
        if sel_wallet != "all":
            wid = int(sel_wallet.split(" ")[0].lstrip("#"))
            if not any(io.wallet_id == wid for io in ios):
                continue
        owned_in = sum(io.amount_sats for io in ios if io.direction == "in" and io.owned)
        owned_out = sum(io.amount_sats for io in ios if io.direction == "out" and io.owned)
        net = owned_out - owned_in
        rows.append(
            {
                "block_time": t.block_time.isoformat(timespec="minutes") if t.block_time else "",
                "txid": t.txid[:8] + "…" + t.txid[-6:],
                "class": CLASS_BADGES.get(t.classification, t.classification),
                "net_btc": net / 100_000_000,
                "fee_sats": t.fee_sats,
                "btc_price": t.btc_price_fiat or 0.0,
                "fiat_value": (abs(net) / 100_000_000) * (t.btc_price_fiat or 0.0),
                "ccy": t.base_ccy or "",
            }
        )

if not rows:
    st.info("No transactions yet. Add a wallet and run a sync.")
else:
    st.dataframe(rows, width="stretch", hide_index=True)
