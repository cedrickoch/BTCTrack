"""Streamlit entrypoint.

Navigation is declared explicitly with ``st.navigation`` so the entrypoint
script itself doesn't show up as its own "app" page in the sidebar — the app
opens directly on the Dashboard.
"""

from __future__ import annotations

import streamlit as st

from btctrack.db.session import get_engine


def main() -> None:
    st.set_page_config(page_title="BTCTrack", page_icon="₿", layout="wide")
    # Trigger DB init before any page queries run.
    get_engine()

    nav = st.navigation(
        [
            st.Page("pages/1_Dashboard.py", title="Dashboard", default=True),
            st.Page("pages/2_Wallets.py", title="Wallets"),
            st.Page("pages/3_Transactions.py", title="Transactions"),
            st.Page("pages/4_Settings.py", title="Settings"),
        ]
    )
    nav.run()


if __name__ == "__main__":
    main()
else:
    main()
