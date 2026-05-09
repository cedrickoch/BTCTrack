"""Streamlit entrypoint. Pages are auto-loaded from `pages/`."""

from __future__ import annotations

import streamlit as st

from btctrack.config import get_settings
from btctrack.db.session import get_engine
from btctrack.sync import get_last_sync


def main() -> None:
    st.set_page_config(page_title="BTCTrack", page_icon="₿", layout="wide")
    settings = get_settings()
    # Trigger DB init
    get_engine()

    st.title("BTCTrack")
    st.caption("Self-hosted, privacy-preserving Bitcoin portfolio tracker")

    last = get_last_sync()
    cols = st.columns(3)
    cols[0].metric("Base currency", settings.base_currency)
    cols[1].metric("Electrum host", f"{settings.electrum_host}:{settings.electrum_port}")
    cols[2].metric(
        "Last sync",
        last.strftime("%Y-%m-%d %H:%M") if last else "never",
    )

    st.info(
        "Use the sidebar to navigate. Add wallets in **Wallets**, then run a sync from "
        "**Settings** to populate the dashboard."
    )


if __name__ == "__main__":
    main()
else:
    main()
