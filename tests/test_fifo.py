from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from btctrack.ledger.fifo import (
    SATS_PER_BTC,
    ExternalIn,
    ExternalOut,
    LedgerState,
    apply_external_in,
    apply_external_out,
    replay,
)


def _t(days: int) -> datetime:
    return datetime(2023, 1, 1, tzinfo=timezone.utc) + timedelta(days=days)


def test_single_lot_partial_sell():
    state = LedgerState()
    apply_external_in(state, ExternalIn("buy1", SATS_PER_BTC, btc_price_fiat=10_000.0, block_time=_t(0)))
    # Sell 0.4 BTC at 20_000
    apply_external_out(
        state,
        ExternalOut("sell1", sats=int(0.4 * SATS_PER_BTC), fee_sats=0,
                    btc_price_fiat=20_000.0, block_time=_t(10)),
    )
    assert state.total_open_sats() == int(0.6 * SATS_PER_BTC)
    # Realised gain = 0.4 * (20_000 - 10_000) = 4000
    assert state.total_realised_gain() == pytest.approx(4_000.0, rel=1e-6)
    assert state.total_open_cost_basis() == pytest.approx(0.6 * 10_000.0, rel=1e-6)


def test_multi_lot_sell_crossing_boundary():
    state = LedgerState()
    apply_external_in(state, ExternalIn("buy1", int(0.5 * SATS_PER_BTC), 10_000.0, _t(0)))
    apply_external_in(state, ExternalIn("buy2", int(0.5 * SATS_PER_BTC), 30_000.0, _t(5)))
    # Sell 0.7 BTC at 40_000 → consume all of lot1 (0.5) + 0.2 of lot2
    apply_external_out(
        state,
        ExternalOut("sell1", sats=int(0.7 * SATS_PER_BTC), fee_sats=0,
                    btc_price_fiat=40_000.0, block_time=_t(10)),
    )
    # Lot1 fully consumed: gain = 0.5 * (40_000 - 10_000) = 15_000
    # Lot2 0.2 consumed:    gain = 0.2 * (40_000 - 30_000) = 2_000
    assert state.total_realised_gain() == pytest.approx(17_000.0, rel=1e-6)
    assert state.total_open_sats() == int(0.3 * SATS_PER_BTC)
    assert state.total_open_cost_basis() == pytest.approx(0.3 * 30_000.0, rel=1e-6)


def test_fee_consumes_extra_sats():
    state = LedgerState()
    apply_external_in(state, ExternalIn("buy1", SATS_PER_BTC, 10_000.0, _t(0)))
    fee = 1_000  # sats
    apply_external_out(
        state,
        ExternalOut(
            "sell1",
            sats=int(0.5 * SATS_PER_BTC),
            fee_sats=fee,
            btc_price_fiat=20_000.0,
            block_time=_t(1),
        ),
    )
    assert state.total_open_sats() == SATS_PER_BTC - int(0.5 * SATS_PER_BTC) - fee


def test_full_liquidation_then_rebuy():
    state = LedgerState()
    apply_external_in(state, ExternalIn("buy1", SATS_PER_BTC, 10_000.0, _t(0)))
    apply_external_out(
        state,
        ExternalOut("sell1", SATS_PER_BTC, 0, 25_000.0, _t(10)),
    )
    assert state.total_open_sats() == 0
    assert state.total_realised_gain() == pytest.approx(15_000.0)
    apply_external_in(state, ExternalIn("buy2", SATS_PER_BTC // 2, 30_000.0, _t(20)))
    assert state.total_open_sats() == SATS_PER_BTC // 2
    # Realised gain unchanged after rebuy
    assert state.total_realised_gain() == pytest.approx(15_000.0)


def test_underflow_raises():
    state = LedgerState()
    apply_external_in(state, ExternalIn("buy1", SATS_PER_BTC // 2, 10_000.0, _t(0)))
    with pytest.raises(ValueError, match="FIFO underflow"):
        apply_external_out(
            state,
            ExternalOut("sell1", SATS_PER_BTC, 0, 20_000.0, _t(1)),
        )


def test_replay_orders_buys_before_sells_same_instant():
    events = [
        ExternalOut("sell1", SATS_PER_BTC // 2, 0, 20_000.0, _t(0)),
        ExternalIn("buy1", SATS_PER_BTC, 10_000.0, _t(0)),
    ]
    state = replay(events)
    # Buy applies first → sell can be served
    assert state.total_open_sats() == SATS_PER_BTC // 2
    assert state.total_realised_gain() == pytest.approx(0.5 * (20_000 - 10_000), rel=1e-6)
