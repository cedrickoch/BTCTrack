from __future__ import annotations

import bcrypt
import pandas as pd
import pytest


def _set_hash(monkeypatch: pytest.MonkeyPatch, plaintext: str | None) -> None:
    from btctrack.config import reload_settings

    if plaintext is None:
        monkeypatch.delenv("MASK_PASSWORD_HASH", raising=False)
    else:
        h = bcrypt.hashpw(plaintext.encode(), bcrypt.gensalt(rounds=4)).decode()
        monkeypatch.setenv("MASK_PASSWORD_HASH", h)
    reload_settings()


def test_feature_disabled_when_env_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_hash(monkeypatch, None)
    from btctrack.ui import privacy

    assert privacy.is_feature_enabled() is False
    assert privacy.is_unlocked() is True


def test_feature_disabled_when_env_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MASK_PASSWORD_HASH", "")
    from btctrack.config import reload_settings
    from btctrack.ui import privacy

    reload_settings()
    assert privacy.is_feature_enabled() is False


def test_feature_enabled_when_env_set(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_hash(monkeypatch, "secret")
    from btctrack.ui import privacy

    assert privacy.is_feature_enabled() is True


def test_verify_password_correct(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_hash(monkeypatch, "secret")
    from btctrack.ui import privacy

    assert privacy.verify_password("secret") is True


def test_verify_password_wrong(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_hash(monkeypatch, "secret")
    from btctrack.ui import privacy

    assert privacy.verify_password("nope") is False


def test_verify_password_malformed_hash(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MASK_PASSWORD_HASH", "not-a-valid-hash")
    from btctrack.config import reload_settings
    from btctrack.ui import privacy

    reload_settings()
    assert privacy.verify_password("anything") is False


def test_fmt_returns_value_when_unlocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_hash(monkeypatch, None)  # feature disabled → is_unlocked() is True
    from btctrack.ui import privacy

    assert privacy.fmt_btc(0.12345678) == "0.12345678"
    assert privacy.fmt_fiat(1234.5, "CHF") == "1,234.50 CHF"
    assert privacy.fmt_sats(12345) == "12,345"


def test_fmt_returns_placeholder_when_locked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_hash(monkeypatch, "secret")
    from btctrack.ui import privacy

    monkeypatch.setattr(privacy, "is_unlocked", lambda: False)
    assert privacy.fmt_btc(0.123) == privacy.MASK_PLACEHOLDER
    assert privacy.fmt_fiat(1.0, "CHF") == privacy.MASK_PLACEHOLDER
    assert privacy.fmt_sats(1) == privacy.MASK_PLACEHOLDER


def test_mask_dataframe_passes_through_when_unlocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_hash(monkeypatch, None)
    from btctrack.ui import privacy

    df = pd.DataFrame({"label": ["a", "b"], "btc": [1.0, 2.0], "sats": [100, 200]})
    out = privacy.mask_dataframe(df, btc_cols=("btc",), sats_cols=("sats",))
    assert list(out["btc"]) == [1.0, 2.0]
    assert list(out["sats"]) == [100, 200]


def test_mask_dataframe_masks_named_cols_when_locked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_hash(monkeypatch, "secret")
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
    _set_hash(monkeypatch, "secret")
    from btctrack.ui import privacy

    monkeypatch.setattr(privacy, "is_unlocked", lambda: False)
    df = pd.DataFrame({"a": [1, 2]})
    out = privacy.mask_dataframe(df, btc_cols=("does_not_exist",))
    assert list(out["a"]) == [1, 2]
