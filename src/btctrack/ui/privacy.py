"""Privacy mode: mask fiat/BTC values until the user unlocks with a password.

The password is stored as a bcrypt hash in the ``setting`` table. It is set once
via the Settings page and is then immutable — it cannot be changed, shown, or
removed from the UI.
"""

from __future__ import annotations

from collections.abc import Iterable

import bcrypt
import pandas as pd
import streamlit as st

from btctrack.db.models import Setting
from btctrack.db.session import session_scope

MASK_PLACEHOLDER = "••••"
SESSION_KEY = "btctrack_unlocked"
_PW_INPUT_KEY = "_btctrack_pw"
_HASH_ERROR_KEY = "_btctrack_hash_malformed"
_SETTING_KEY = "mask_password_hash"
# Per-session cache of the stored hash. Holds the hash string, or "" once we have
# checked and found none set. Safe to cache because the password is immutable
# once set; a fresh browser session re-reads it lazily on first use.
_HASH_CACHE_KEY = "_btctrack_pw_hash"


def get_password_hash() -> str | None:
    """Return the stored bcrypt hash, or None if privacy mode is unconfigured."""
    with session_scope() as s:
        row = s.get(Setting, _SETTING_KEY)
        if row is None or not row.value:
            return None
        return row.value


def store_password_hash(hash_value: str) -> None:
    """Persist the bcrypt hash. Raises ValueError if one is already stored —
    the password is immutable once set."""
    with session_scope() as s:
        if s.get(Setting, _SETTING_KEY) is not None:
            raise ValueError("Privacy-mode password is already set.")
        s.add(Setting(key=_SETTING_KEY, value=hash_value))


def _password_hash() -> str | None:
    cached = st.session_state.get(_HASH_CACHE_KEY)
    if cached is not None:  # cache populated ("" means "checked, none set")
        return cached or None
    raw = get_password_hash()
    st.session_state[_HASH_CACHE_KEY] = raw or ""
    return raw


def set_password(plaintext: str) -> None:
    """Hash and store a new privacy-mode password, then update the session cache
    so it takes effect on the current rerun. Raises ValueError if a password is
    already set."""
    if is_feature_enabled():
        raise ValueError("Privacy-mode password is already set.")
    hashed = bcrypt.hashpw(plaintext.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    store_password_hash(hashed)
    st.session_state[_HASH_CACHE_KEY] = hashed


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
                    "Stored password hash is corrupted — restore from a backup "
                    "to reconfigure privacy mode."
                )
            else:
                st.error("Wrong password")
