"""FIFO lot tracking for realised gains.

The ledger is rebuilt deterministically from the set of external transactions
ordered by block_time. `apply_external_in` opens a lot, `apply_external_out`
consumes from the oldest open lot(s). Network fees on outgoing txs are
considered part of the disposed amount (so the fee crystallises a small gain
or loss against its source lot).

All numbers stay as integer sats / float fiat. Cost basis is computed as
`sats * price / 1e8`.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable

SATS_PER_BTC = 100_000_000


@dataclass
class OpenLot:
    txid: str
    amount_sats: int
    cost_basis_fiat: float
    acquired_at: datetime
    remaining_sats: int


@dataclass
class RealisedRow:
    txid: str
    lot_txid: str
    sats_sold: int
    proceeds_fiat: float
    cost_basis_fiat: float
    gain_fiat: float
    sold_at: datetime


@dataclass
class ExternalIn:
    txid: str
    sats: int
    btc_price_fiat: float
    block_time: datetime


@dataclass
class ExternalOut:
    txid: str
    sats: int          # sats leaving owned set (does not include fee, fee added below)
    fee_sats: int
    btc_price_fiat: float
    block_time: datetime


@dataclass
class LedgerState:
    open_lots: deque[OpenLot] = field(default_factory=deque)
    realised: list[RealisedRow] = field(default_factory=list)

    def total_open_sats(self) -> int:
        return sum(l.remaining_sats for l in self.open_lots)

    def total_open_cost_basis(self) -> float:
        # Cost basis of remaining sats only (proportional)
        return sum(
            l.cost_basis_fiat * (l.remaining_sats / l.amount_sats)
            for l in self.open_lots
            if l.amount_sats > 0
        )

    def total_realised_gain(self) -> float:
        return sum(r.gain_fiat for r in self.realised)


def apply_external_in(state: LedgerState, ev: ExternalIn) -> None:
    cost_basis = ev.sats * ev.btc_price_fiat / SATS_PER_BTC
    state.open_lots.append(
        OpenLot(
            txid=ev.txid,
            amount_sats=ev.sats,
            cost_basis_fiat=cost_basis,
            acquired_at=ev.block_time,
            remaining_sats=ev.sats,
        )
    )


def apply_external_out(state: LedgerState, ev: ExternalOut) -> None:
    """Consume `ev.sats + ev.fee_sats` sats FIFO. Each consumed slice realises a gain."""
    sats_to_dispose = ev.sats + ev.fee_sats
    if sats_to_dispose <= 0:
        return

    while sats_to_dispose > 0:
        if not state.open_lots:
            raise ValueError(
                f"FIFO underflow on tx {ev.txid}: trying to send {sats_to_dispose} sats "
                "but no open lots remain. Are you missing a wallet import?"
            )
        lot = state.open_lots[0]
        take = min(lot.remaining_sats, sats_to_dispose)
        # Cost basis of the slice = take/amount_sats * lot.cost_basis_fiat
        slice_basis = lot.cost_basis_fiat * (take / lot.amount_sats)
        slice_proceeds = take * ev.btc_price_fiat / SATS_PER_BTC
        state.realised.append(
            RealisedRow(
                txid=ev.txid,
                lot_txid=lot.txid,
                sats_sold=take,
                proceeds_fiat=slice_proceeds,
                cost_basis_fiat=slice_basis,
                gain_fiat=slice_proceeds - slice_basis,
                sold_at=ev.block_time,
            )
        )
        lot.remaining_sats -= take
        sats_to_dispose -= take
        if lot.remaining_sats == 0:
            state.open_lots.popleft()


def replay(events: Iterable[ExternalIn | ExternalOut]) -> LedgerState:
    state = LedgerState()
    # Sort events by block_time then by type (ins before outs for same instant
    # so a same-block buy can fund a same-block send).
    ordered = sorted(
        events,
        key=lambda e: (e.block_time, 0 if isinstance(e, ExternalIn) else 1),
    )
    for ev in ordered:
        if isinstance(ev, ExternalIn):
            apply_external_in(state, ev)
        else:
            apply_external_out(state, ev)
    return state
