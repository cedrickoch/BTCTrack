from __future__ import annotations

import bcrypt
import pandas as pd
import pytest


@pytest.fixture(autouse=True)
def _clear_privacy_cache():
    """Clear the per-session privacy cache so it does not leak between tests."""
    import streamlit as st

    from btctrack.ui import privacy

    for key in (privacy._HASH_CACHE_KEY, privacy.SESSION_KEY, privacy._HASH_ERROR_KEY):
        st.session_state.pop(key, None)
    yield
    for key in (privacy._HASH_CACHE_KEY, privacy.SESSION_KEY, privacy._HASH_ERROR_KEY):
        st.session_state.pop(key, None)


def _set_hash(plaintext: str | None) -> None:
    """Configure (or clear) the privacy-mode password in the test database."""
    from btctrack.db.models import Setting
    from btctrack.db.session import session_scope
    from btctrack.ui.privacy import _SETTING_KEY

    with session_scope() as s:
        row = s.get(Setting, _SETTING_KEY)
        if plaintext is None:
            if row is not None:
                s.delete(row)
        else:
            h = bcrypt.hashpw(plaintext.encode(), bcrypt.gensalt(rounds=4)).decode()
            if row is not None:
                row.value = h
            else:
                s.add(Setting(key=_SETTING_KEY, value=h))


def _set_raw_setting(value: str) -> None:
    """Write a raw value into the password setting row (for malformed-hash tests)."""
    from btctrack.db.models import Setting
    from btctrack.db.session import session_scope
    from btctrack.ui.privacy import _SETTING_KEY

    with session_scope() as s:
        row = s.get(Setting, _SETTING_KEY)
        if row is not None:
            row.value = value
        else:
            s.add(Setting(key=_SETTING_KEY, value=value))


def test_feature_disabled_when_no_password() -> None:
    _set_hash(None)
    from btctrack.ui import privacy

    assert privacy.is_feature_enabled() is False
    assert privacy.is_unlocked() is True


def test_feature_disabled_when_setting_blank() -> None:
    _set_raw_setting("")
    from btctrack.ui import privacy

    assert privacy.is_feature_enabled() is False


def test_feature_enabled_when_password_set() -> None:
    _set_hash("secret")
    from btctrack.ui import privacy

    assert privacy.is_feature_enabled() is True


def test_store_and_get_password_hash_round_trip() -> None:
    from btctrack.ui import privacy

    assert privacy.get_password_hash() is None
    h = bcrypt.hashpw(b"secret", bcrypt.gensalt(rounds=4)).decode()
    privacy.store_password_hash(h)
    assert privacy.get_password_hash() == h


def test_store_password_hash_is_immutable() -> None:
    from btctrack.ui import privacy

    h = bcrypt.hashpw(b"secret", bcrypt.gensalt(rounds=4)).decode()
    privacy.store_password_hash(h)
    with pytest.raises(ValueError):
        privacy.store_password_hash(h)


def test_verify_password_correct() -> None:
    _set_hash("secret")
    from btctrack.ui import privacy

    assert privacy.verify_password("secret") is True


def test_verify_password_wrong() -> None:
    _set_hash("secret")
    from btctrack.ui import privacy

    assert privacy.verify_password("nope") is False


def test_verify_password_malformed_hash() -> None:
    _set_raw_setting("not-a-valid-hash")
    from btctrack.ui import privacy

    assert privacy.verify_password("anything") is False


def test_fmt_returns_value_when_unlocked() -> None:
    _set_hash(None)  # feature disabled → is_unlocked() is True
    from btctrack.ui import privacy

    assert privacy.fmt_btc(0.12345678) == "0.12345678"
    assert privacy.fmt_fiat(1234.5, "CHF") == "1,234.50 CHF"
    assert privacy.fmt_sats(12345) == "12,345"


def test_fmt_returns_placeholder_when_locked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_hash("secret")
    from btctrack.ui import privacy

    monkeypatch.setattr(privacy, "is_unlocked", lambda: False)
    assert privacy.fmt_btc(0.123) == privacy.MASK_PLACEHOLDER
    assert privacy.fmt_fiat(1.0, "CHF") == privacy.MASK_PLACEHOLDER
    assert privacy.fmt_sats(1) == privacy.MASK_PLACEHOLDER


def test_mask_dataframe_passes_through_when_unlocked() -> None:
    _set_hash(None)
    from btctrack.ui import privacy

    df = pd.DataFrame({"label": ["a", "b"], "btc": [1.0, 2.0], "sats": [100, 200]})
    out = privacy.mask_dataframe(df, btc_cols=("btc",), sats_cols=("sats",))
    assert list(out["btc"]) == [1.0, 2.0]
    assert list(out["sats"]) == [100, 200]


def test_mask_dataframe_masks_named_cols_when_locked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_hash("secret")
    from btctrack.ui import privacy

    monkeypatch.setattr(privacy, "is_unlocked", lambda: False)
    df = pd.DataFrame(
        {"label": ["a", "b"], "btc": [1.0, 2.0], "sats": [100, 200], "fiat": [9.0, 8.0]}
    )
    out = privacy.mask_dataframe(
        df, btc_cols=("btc",), sats_cols=("sats",), fiat_cols=("fiat",)
    )
    assert list(out["label"]) == ["a", "b"]
    assert list(out["btc"]) == [privacy.MASK_PLACEHOLDER] * 2
    assert list(out["sats"]) == [privacy.MASK_PLACEHOLDER] * 2
    assert list(out["fiat"]) == [privacy.MASK_PLACEHOLDER] * 2
    # input untouched
    assert list(df["btc"]) == [1.0, 2.0]


def test_mask_dataframe_ignores_missing_cols(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_hash("secret")
    from btctrack.ui import privacy

    monkeypatch.setattr(privacy, "is_unlocked", lambda: False)
    df = pd.DataFrame({"a": [1, 2]})
    out = privacy.mask_dataframe(df, btc_cols=("does_not_exist",))
    assert list(out["a"]) == [1, 2]
