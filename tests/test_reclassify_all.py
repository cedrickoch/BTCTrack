"""Regression tests for `reclassify_all`.

When a second owned wallet is added after a tx has already been ingested,
the previously-stored TxIO rows still flag that wallet's outputs as
`owned=False`. Without the refresh step, the tx stays classified as
`external_out` / `external_in` forever. These tests pin the corrected
behavior: refresh + reclassify every sync.
"""

from __future__ import annotations

from btctrack.db.models import Address, Transaction, TxIO, Wallet
from btctrack.db.session import session_scope
from btctrack.sync import reclassify_all


def _seed_wallet(label: str, address: str) -> int:
    with session_scope() as s:
        w = Wallet(
            label=label, kind="address", value=address, script_type="p2wpkh", gap_limit=20
        )
        s.add(w)
        s.flush()
        s.add(Address(wallet_id=w.id, address=address, chain="receive"))
        return w.id


def _seed_transfer(txid: str, in_addr: str, out_addr: str) -> None:
    with session_scope() as s:
        s.add(Transaction(txid=txid, classification="unknown", fee_sats=1_000))
        s.add(
            TxIO(
                txid=txid, direction="in", address=in_addr,
                amount_sats=100_000, owned=False, wallet_id=None,
            )
        )
        s.add(
            TxIO(
                txid=txid, direction="out", address=out_addr,
                amount_sats=99_000, owned=False, wallet_id=None,
            )
        )


def test_internal_detected_after_second_wallet_added():
    """A→B transfer ingested with only A known should flip to `internal`
    after wallet B is added and reclassify runs."""
    _seed_wallet("A", "addrA")
    _seed_transfer("tx1", in_addr="addrA", out_addr="addrB")

    # First pass: only A is owned, so the tx is external_out.
    reclassify_all()
    with session_scope() as s:
        assert s.get(Transaction, "tx1").classification == "external_out"

    # User adds the second wallet — the input/output addresses don't change,
    # but the owned set now covers both ends.
    _seed_wallet("B", "addrB")
    reclassify_all()

    with session_scope() as s:
        tx = s.get(Transaction, "tx1")
        assert tx.classification == "internal"
        # And the IO rows themselves got refreshed, not just the tx label.
        out_io = next(io for io in tx.ios if io.address == "addrB")
        assert out_io.owned is True
        assert out_io.wallet_id is not None


def test_reclassify_handles_removed_wallet():
    """If a wallet is removed (cascading its addresses), its previously-owned
    IOs must be downgraded to owned=False and the tx reclassified."""
    wA_id = _seed_wallet("A", "addrA")
    _seed_wallet("B", "addrB")
    _seed_transfer("tx1", in_addr="addrA", out_addr="addrB")

    reclassify_all()
    with session_scope() as s:
        assert s.get(Transaction, "tx1").classification == "internal"

    # Drop wallet A. Cascade removes its Address row.
    with session_scope() as s:
        s.delete(s.get(Wallet, wA_id))

    reclassify_all()
    with session_scope() as s:
        tx = s.get(Transaction, "tx1")
        # Inputs from addrA are now unowned → external_in (net positive to B).
        assert tx.classification == "external_in"
        in_io = next(io for io in tx.ios if io.address == "addrA")
        assert in_io.owned is False
        assert in_io.wallet_id is None
