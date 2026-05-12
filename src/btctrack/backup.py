"""Encrypted / raw export and import of the BTCTrack SQLite database.

File format — 8-byte header in both modes:

    [0:6]  ASCII "BTCTRK"
    [6]    version (currently 1)
    [7]    mode    (0 = raw, 1 = encrypted)

Encrypted payload (mode=1): 16-byte salt · 12-byte nonce · AES-256-GCM
ciphertext. Key = PBKDF2-HMAC-SHA256(passphrase, salt, 600_000, dklen=32).
The 8-byte header is authenticated as AEAD associated data so the version
byte can't be downgraded by an attacker.

Raw payload (mode=0): the SQLite file bytes verbatim. Convenient for
debugging (``tail -c +9 file.btctrk | sqlite3 -``) but leaks xpubs.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import tempfile
from dataclasses import dataclass
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from btctrack.config import get_settings
from btctrack.db.models import Base
from btctrack.db.session import get_engine, reset_engine

_MAGIC = b"BTCTRK"
_VERSION = 1
_MODE_RAW = 0
_MODE_ENCRYPTED = 1
_HEADER_LEN = 8
_SALT_LEN = 16
_NONCE_LEN = 12
_GCM_TAG_LEN = 16
_PBKDF2_ITERATIONS = 600_000
_KEY_LEN = 32
_SQLITE_MAGIC = b"SQLite format 3\x00"

# Pulled from SQLAlchemy metadata so a future schema change keeps this in sync.
_EXPECTED_TABLES = frozenset(Base.metadata.tables.keys())


class BackupError(Exception):
    """Raised when a backup file is malformed, tampered with, the passphrase
    is wrong, or the decoded payload isn't a usable BTCTrack DB."""


@dataclass
class ImportResult:
    wallets: int
    addresses: int
    transactions: int


def export_bytes(passphrase: str | None) -> bytes:
    """Snapshot the live DB and return the export envelope.

    ``passphrase=None`` → raw mode (no encryption). Non-empty string →
    AES-256-GCM with a PBKDF2-derived key. An empty string is rejected so
    a caller never silently exports raw when they meant to encrypt.
    """
    if passphrase is not None and passphrase == "":
        raise ValueError("passphrase must be None (raw) or a non-empty string")

    db_bytes = _snapshot_db()
    if passphrase is None:
        header = _MAGIC + bytes([_VERSION, _MODE_RAW])
        return header + db_bytes

    header = _MAGIC + bytes([_VERSION, _MODE_ENCRYPTED])
    salt = os.urandom(_SALT_LEN)
    nonce = os.urandom(_NONCE_LEN)
    key = hashlib.pbkdf2_hmac(
        "sha256", passphrase.encode("utf-8"), salt, _PBKDF2_ITERATIONS, dklen=_KEY_LEN
    )
    ct = AESGCM(key).encrypt(nonce, db_bytes, header)
    return header + salt + nonce + ct


def import_bytes(data: bytes, passphrase: str | None) -> ImportResult:
    """Validate the envelope, decrypt if needed, atomically replace the
    live DB, return row counts from the imported DB.

    The current DB (if any) is moved to ``<db_path>.bak`` first. Either
    the swap completes in full or the live DB is left untouched.
    """
    db_bytes = _decode_envelope(data, passphrase)
    _atomic_replace(db_bytes)
    return _row_counts()


def is_encrypted(data: bytes) -> bool:
    """Cheap header peek so the UI can hide the passphrase field for raw files."""
    if len(data) < _HEADER_LEN or data[:6] != _MAGIC:
        return False
    return data[7] == _MODE_ENCRYPTED


# --- internals ----------------------------------------------------------


def _snapshot_db() -> bytes:
    """Transactionally-consistent snapshot via SQLite's online backup API.

    Reading the file directly would race a concurrent sync mid-WAL; the
    backup API serialises with writers and produces a coherent file.
    """
    src_path = Path(get_settings().btctrack_db_path).expanduser().resolve()
    # Ensure the live DB exists (engine creates it on first access).
    get_engine()
    src_path.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_name = tempfile.mkstemp(
        prefix="btctrack-snap-", suffix=".db", dir=str(src_path.parent)
    )
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        src = sqlite3.connect(str(src_path))
        try:
            dst = sqlite3.connect(str(tmp_path))
            try:
                src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()
        return tmp_path.read_bytes()
    finally:
        tmp_path.unlink(missing_ok=True)


def _decode_envelope(data: bytes, passphrase: str | None) -> bytes:
    if len(data) < _HEADER_LEN or data[:6] != _MAGIC:
        raise BackupError("not a BTCTrack backup")
    version = data[6]
    if version != _VERSION:
        raise BackupError(f"unsupported backup version: {version}")
    mode = data[7]
    header = data[:_HEADER_LEN]
    body = data[_HEADER_LEN:]

    if mode == _MODE_RAW:
        return body

    if mode == _MODE_ENCRYPTED:
        if not passphrase:
            raise BackupError("backup is encrypted; passphrase required")
        if len(body) < _SALT_LEN + _NONCE_LEN + _GCM_TAG_LEN:
            raise BackupError("encrypted payload truncated")
        salt = body[:_SALT_LEN]
        nonce = body[_SALT_LEN : _SALT_LEN + _NONCE_LEN]
        ct = body[_SALT_LEN + _NONCE_LEN :]
        key = hashlib.pbkdf2_hmac(
            "sha256",
            passphrase.encode("utf-8"),
            salt,
            _PBKDF2_ITERATIONS,
            dklen=_KEY_LEN,
        )
        try:
            return AESGCM(key).decrypt(nonce, ct, header)
        except InvalidTag as e:
            raise BackupError("wrong passphrase or corrupted file") from e

    raise BackupError(f"unsupported backup mode: {mode}")


def _atomic_replace(new_db_bytes: bytes) -> None:
    if not new_db_bytes.startswith(_SQLITE_MAGIC):
        raise BackupError("decoded payload is not a SQLite database")

    db_path = Path(get_settings().btctrack_db_path).expanduser().resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = db_path.with_suffix(db_path.suffix + ".import.tmp")
    bak_path = db_path.with_suffix(db_path.suffix + ".bak")

    moved_to_bak = False
    try:
        tmp_path.write_bytes(new_db_bytes)
        _validate_db_file(tmp_path)
        # Release the live engine *after* validation so a malformed
        # payload doesn't take the engine offline.
        _release_engine()
        if db_path.exists():
            os.replace(db_path, bak_path)
            moved_to_bak = True
        try:
            os.replace(tmp_path, db_path)
        except OSError:
            # Should be vanishingly rare on same-FS rename, but if it
            # fails, roll the .bak back so the live DB still works.
            if moved_to_bak:
                os.replace(bak_path, db_path)
            raise
    finally:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
    # Re-init against the imported DB; create_all is a no-op on the existing schema.
    get_engine()


def _release_engine() -> None:
    """Dispose pooled connections, then clear the lru_cache so the next
    ``get_engine()`` opens a fresh engine against whatever file is in
    place at that time."""
    try:
        get_engine().dispose()
    except Exception:
        # Best effort — the cache clear below is what actually forces
        # subsequent code to re-open against the new file.
        pass
    reset_engine()


def _validate_db_file(path: Path) -> None:
    try:
        conn = sqlite3.connect(str(path))
    except sqlite3.DatabaseError as e:
        raise BackupError(f"imported file is not a valid SQLite database: {e}") from e
    try:
        row = conn.execute("PRAGMA integrity_check;").fetchone()
        if not row or row[0] != "ok":
            raise BackupError(
                f"integrity_check failed: {row[0] if row else '<empty>'}"
            )
        present = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table';"
            ).fetchall()
        }
        missing = _EXPECTED_TABLES - present
        if missing:
            raise BackupError(
                f"backup schema does not match (missing tables: {sorted(missing)})"
            )
    finally:
        conn.close()


def _row_counts() -> ImportResult:
    from sqlalchemy import func, select

    from btctrack.db.models import Address, Transaction, Wallet
    from btctrack.db.session import session_scope

    with session_scope() as s:
        wallets = s.execute(select(func.count(Wallet.id))).scalar_one()
        addresses = s.execute(select(func.count(Address.id))).scalar_one()
        txs = s.execute(select(func.count(Transaction.txid))).scalar_one()
    return ImportResult(wallets=wallets, addresses=addresses, transactions=txs)
