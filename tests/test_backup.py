from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest


def _seed(wallets: list[tuple[str, str]]) -> None:
    """Seed the live DB with a few wallets+addresses+a tx for each entry."""
    from btctrack.db.models import Address, Transaction, TxIO, Wallet
    from btctrack.db.session import session_scope

    with session_scope() as s:
        for label, value in wallets:
            w = Wallet(label=label, kind="xpub", value=value, script_type="p2wpkh", gap_limit=20)
            s.add(w)
            s.flush()
            s.add(Address(wallet_id=w.id, address=f"addr-{label}", chain="receive"))
            tx = Transaction(
                txid=("a" * 60) + label.ljust(4, "x")[:4],
                block_height=100,
                block_time=datetime(2024, 1, 1, tzinfo=timezone.utc),
                classification="external_in",
            )
            s.add(tx)
            s.flush()
            s.add(
                TxIO(
                    txid=tx.txid,
                    direction="out",
                    address=f"addr-{label}",
                    amount_sats=100_000,
                    owned=True,
                    wallet_id=w.id,
                )
            )


def _all_wallet_labels() -> list[str]:
    from sqlalchemy import select

    from btctrack.db.models import Wallet
    from btctrack.db.session import session_scope

    with session_scope() as s:
        return sorted(w.label for w in s.execute(select(Wallet)).scalars())


def test_round_trip_encrypted() -> None:
    from btctrack import backup

    _seed([("alpha", "xpub-alpha"), ("beta", "xpub-beta")])
    data = backup.export_bytes("hunter2")

    # Wipe and re-import.
    _seed([("gamma", "xpub-gamma")])  # noise that should be overwritten
    assert "gamma" in _all_wallet_labels()
    result = backup.import_bytes(data, "hunter2")

    assert result.wallets == 2
    assert result.addresses == 2
    assert result.transactions == 2
    assert _all_wallet_labels() == ["alpha", "beta"]


def test_round_trip_raw() -> None:
    from btctrack import backup

    _seed([("alpha", "xpub-alpha")])
    data = backup.export_bytes(None)

    # Header sanity: mode byte at offset 7 is 0 for raw.
    assert data[:6] == b"BTCTRK"
    assert data[6] == 1  # version
    assert data[7] == 0  # mode=raw
    # Payload immediately after the 8-byte header must be a SQLite file.
    assert data[8 : 8 + 16] == b"SQLite format 3\x00"

    result = backup.import_bytes(data, None)
    assert result.wallets == 1
    assert _all_wallet_labels() == ["alpha"]


def test_wrong_passphrase_rejected() -> None:
    from btctrack import backup

    _seed([("alpha", "xpub-alpha")])
    data = backup.export_bytes("right")
    with pytest.raises(backup.BackupError, match="wrong passphrase"):
        backup.import_bytes(data, "wrong")
    # Live DB untouched.
    assert _all_wallet_labels() == ["alpha"]


def test_tampered_ciphertext_rejected() -> None:
    from btctrack import backup

    _seed([("alpha", "xpub-alpha")])
    data = bytearray(backup.export_bytes("hunter2"))
    # Flip a byte inside the ciphertext region (past header+salt+nonce).
    flip_at = 8 + 16 + 12 + 4
    data[flip_at] ^= 0xFF
    with pytest.raises(backup.BackupError):
        backup.import_bytes(bytes(data), "hunter2")
    assert _all_wallet_labels() == ["alpha"]


def test_unknown_magic_rejected() -> None:
    from btctrack import backup

    with pytest.raises(backup.BackupError, match="not a BTCTrack backup"):
        backup.import_bytes(b"\x00" * 1024, "any")


def test_unsupported_version_rejected() -> None:
    from btctrack import backup

    # Valid magic + bogus version byte.
    payload = b"BTCTRK" + bytes([99, 0]) + b"\x00" * 32
    with pytest.raises(backup.BackupError, match="unsupported backup version"):
        backup.import_bytes(payload, None)


def test_empty_passphrase_on_export_raises_value_error() -> None:
    from btctrack import backup

    _seed([("alpha", "xpub-alpha")])
    with pytest.raises(ValueError):
        backup.export_bytes("")


def test_encrypted_without_passphrase_rejected() -> None:
    from btctrack import backup

    _seed([("alpha", "xpub-alpha")])
    data = backup.export_bytes("hunter2")
    with pytest.raises(backup.BackupError, match="passphrase required"):
        backup.import_bytes(data, None)


def test_schema_mismatch_rejected(tmp_path: Path) -> None:
    """Raw-mode envelope wrapping a SQLite file that doesn't have our schema."""
    from btctrack import backup

    # Build a SQLite DB with the wrong schema.
    foreign = tmp_path / "foreign.db"
    conn = sqlite3.connect(str(foreign))
    try:
        conn.execute("CREATE TABLE foo (id INTEGER PRIMARY KEY, name TEXT);")
        conn.commit()
    finally:
        conn.close()
    db_bytes = foreign.read_bytes()
    envelope = b"BTCTRK" + bytes([1, 0]) + db_bytes  # version=1, mode=raw

    _seed([("alpha", "xpub-alpha")])
    with pytest.raises(backup.BackupError, match="schema does not match"):
        backup.import_bytes(envelope, None)
    # Live DB untouched.
    assert _all_wallet_labels() == ["alpha"]


def test_atomic_failure_leaves_live_db_intact(monkeypatch: pytest.MonkeyPatch) -> None:
    from btctrack import backup
    from btctrack.config import get_settings

    _seed([("alpha", "xpub-alpha")])
    data = backup.export_bytes(None)

    def _boom(path: Path) -> None:
        raise backup.BackupError("simulated validation failure")

    monkeypatch.setattr(backup, "_validate_db_file", _boom)

    with pytest.raises(backup.BackupError, match="simulated"):
        backup.import_bytes(data, None)

    # Live DB unchanged, temp file cleaned up.
    db_path = Path(get_settings().btctrack_db_path).expanduser().resolve()
    tmp = db_path.with_suffix(db_path.suffix + ".import.tmp")
    assert not tmp.exists()
    assert _all_wallet_labels() == ["alpha"]


def test_bak_file_contains_previous_db() -> None:
    from btctrack import backup
    from btctrack.config import get_settings

    _seed([("alpha", "xpub-alpha")])
    data = backup.export_bytes(None)

    # Replace the live DB with a different state, then import the snapshot.
    from btctrack.db.session import session_scope

    with session_scope() as s:
        from sqlalchemy import delete

        from btctrack.db.models import Address, Transaction, TxIO, Wallet

        s.execute(delete(TxIO))
        s.execute(delete(Transaction))
        s.execute(delete(Address))
        s.execute(delete(Wallet))
    _seed([("beta", "xpub-beta")])
    assert _all_wallet_labels() == ["beta"]

    backup.import_bytes(data, None)
    assert _all_wallet_labels() == ["alpha"]

    # The .bak file must hold the "beta" state we just overwrote.
    db_path = Path(get_settings().btctrack_db_path).expanduser().resolve()
    bak = db_path.with_suffix(db_path.suffix + ".bak")
    assert bak.exists()
    conn = sqlite3.connect(str(bak))
    try:
        labels = sorted(r[0] for r in conn.execute("SELECT label FROM wallet;").fetchall())
    finally:
        conn.close()
    assert labels == ["beta"]


def test_is_encrypted_helper() -> None:
    from btctrack import backup

    _seed([("alpha", "xpub-alpha")])
    assert backup.is_encrypted(backup.export_bytes("hunter2")) is True
    assert backup.is_encrypted(backup.export_bytes(None)) is False
    assert backup.is_encrypted(b"random") is False
