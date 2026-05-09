from __future__ import annotations

import os
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolate_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Each test gets its own SQLite file and a fresh settings cache."""
    db = tmp_path / "test.db"
    monkeypatch.setenv("BTCTRACK_DB_PATH", str(db))
    monkeypatch.setenv("BASE_CURRENCY", "CHF")
    monkeypatch.setenv("ELECTRUM_HOST", "127.0.0.1")
    monkeypatch.setenv("ELECTRUM_PORT", "50002")
    monkeypatch.setenv("ELECTRUM_USE_SSL", "true")

    from btctrack.config import reload_settings
    from btctrack.db.session import reset_engine

    reset_engine()
    reload_settings()
    yield
    reset_engine()
