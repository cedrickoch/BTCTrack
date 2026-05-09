from __future__ import annotations

import streamlit as st

from btctrack.config import get_settings
from btctrack.sync import get_last_sync, run_sync

st.set_page_config(page_title="BTCTrack — Settings", page_icon="₿", layout="wide")
st.title("Settings")

settings = get_settings()

st.subheader("Configuration (from environment)")
st.write(
    {
        "ELECTRUM_HOST": settings.electrum_host,
        "ELECTRUM_PORT": settings.electrum_port,
        "ELECTRUM_USE_SSL": settings.electrum_use_ssl,
        "BASE_CURRENCY": settings.base_currency,
        "GAP_LIMIT": settings.gap_limit,
        "BTCTRACK_DB_PATH": str(settings.btctrack_db_path),
    }
)
st.caption(
    "Edit `.env` and restart the container to change these. The app intentionally "
    "does not write env vars at runtime."
)

st.divider()
st.subheader("Sync")

last = get_last_sync()
st.write(f"Last sync: **{last.strftime('%Y-%m-%d %H:%M:%S %Z') if last else 'never'}**")

if st.button("Run sync now", type="primary"):
    with st.spinner("Syncing — derive xpubs, fetch histories, ingest txs, look up prices…"):
        try:
            res = run_sync()
            st.success(
                f"Sync complete: {res.wallets} wallets · {res.addresses} new addresses · "
                f"{res.new_txs} new tx · {res.classified} (re)classified · "
                f"{res.prices_filled} prices filled from snapshot"
            )
        except Exception as e:
            st.error(f"Sync failed: {e}")
            st.exception(e)
