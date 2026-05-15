"""Streamlit entrypoint.

Navigation is declared explicitly with ``st.navigation``. The page files live
in ``views/`` rather than ``pages/`` on purpose: a directory literally named
``pages/`` next to the entrypoint triggers Streamlit's automatic multi-page
discovery, which runs in addition to ``st.navigation`` and re-introduces the
entrypoint as an "app" item in the sidebar.
"""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from btctrack.db.session import get_engine

BITCOIN_ICON = Path(__file__).parent / "assets" / "bitcoin.svg"


def main() -> None:
    st.set_page_config(page_title="BTCTrack", page_icon=str(BITCOIN_ICON), layout="wide")
    # Trigger DB init before any page queries run.
    get_engine()

    nav = st.navigation(
        [
            st.Page("views/1_Dashboard.py", title="Dashboard", default=True),
            st.Page("views/2_Wallets.py", title="Wallets"),
            st.Page("views/3_Transactions.py", title="Transactions"),
            st.Page("views/4_Settings.py", title="Settings"),
        ]
    )
    nav.run()


if __name__ == "__main__":
    main()
else:
    main()
