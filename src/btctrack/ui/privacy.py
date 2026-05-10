"""Privacy mode: mask fiat/BTC values until the user unlocks with a password."""

from __future__ import annotations

from collections.abc import Iterable

import bcrypt
import pandas as pd
import streamlit as st

from btctrack.config import get_settings

MASK_PLACEHOLDER = "••••"
SESSION_KEY = "btctrack_unlocked"
_PW_INPUT_KEY = "_btctrack_pw"
_HASH_ERROR_KEY = "_btctrack_hash_malformed"


def _password_hash() -> str | None:
    raw = get_settings().mask_password_hash
    return raw or None


def is_feature_enabled() -> bool:
    return _password_hash() is not None


def is_unlocked() -> bool:
    if not is_feature_enabled():
        return True
    return bool(st.session_state.get(SESSION_KEY, False))


def verify_password(plaintext: str) -> bool:
    pw_hash = _password_hash()
    if pw_hash is None:
        return False
    try:
        return bcrypt.checkpw(plaintext.encode("utf-8"), pw_hash.encode("utf-8"))
    except ValueError:
        st.session_state[_HASH_ERROR_KEY] = True
        return False


def fmt_fiat(value: float, ccy: str) -> str:
    if not is_unlocked():
        return MASK_PLACEHOLDER
    return f"{value:,.2f} {ccy}" if ccy else f"{value:,.2f}"


def fmt_btc(value: float) -> str:
    if not is_unlocked():
        return MASK_PLACEHOLDER
    return f"{value:.8f}"


def fmt_sats(value: int) -> str:
    if not is_unlocked():
        return MASK_PLACEHOLDER
    return f"{int(value):,}"


def mask_dataframe(
    df: pd.DataFrame,
    *,
    fiat_cols: Iterable[str] = (),
    btc_cols: Iterable[str] = (),
    sats_cols: Iterable[str] = (),
) -> pd.DataFrame:
    out = df.copy()
    if is_unlocked():
        return out
    for col in (*fiat_cols, *btc_cols, *sats_cols):
        if col in out.columns:
            out[col] = MASK_PLACEHOLDER
    return out


def chart_placeholder() -> None:
    st.info("🔒 Chart hidden — unlock in sidebar to view.")


def render_sidebar_lock() -> None:
    if not is_feature_enabled():
        return

    with st.sidebar.container():
        if is_unlocked():
            st.caption("🔓 Values visible")
            if st.button("Lock", key="_btctrack_lock_btn", use_container_width=True):
                st.session_state[SESSION_KEY] = False
                st.session_state.pop(_PW_INPUT_KEY, None)
                st.rerun()
            return

        st.caption("🔒 Privacy mode")
        st.text_input("Password", type="password", key=_PW_INPUT_KEY)
        if st.button("Unlock", key="_btctrack_unlock_btn", use_container_width=True):
            entered = st.session_state.get(_PW_INPUT_KEY, "")
            st.session_state[_HASH_ERROR_KEY] = False
            if verify_password(entered):
                st.session_state[SESSION_KEY] = True
                st.session_state.pop(_PW_INPUT_KEY, None)
                st.rerun()
            elif st.session_state.get(_HASH_ERROR_KEY):
                st.error(
                    "Password hash in .env is malformed — regenerate with "
                    "scripts/hash_mask_password.py"
                )
            else:
                st.error("Wrong password")
