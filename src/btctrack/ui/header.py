"""Page header with the Bitcoin logo on the right, used by every view."""

from __future__ import annotations

from pathlib import Path

import streamlit as st

BITCOIN_LOGO = Path(__file__).resolve().parent / "assets" / "bitcoin.svg"


def render_page_header(title: str) -> None:
    left, right = st.columns([9, 1], vertical_alignment="center")
    with left:
        st.title(title)
    with right:
        st.image(str(BITCOIN_LOGO), width=72)
