"""Sync orchestrator.

Pipeline:
1. Load enabled wallets.
2. For each xpub wallet: derive addresses up to gap_limit on both chains.
3. Fetch histories for every owned address from Electrum (one connection).
4. For new txids: fetch the verbose tx, dereference inputs to get prevout
   addresses + amounts, persist tx + tx_io rows.
5. Refresh every TxIO's owned/wallet_id against the current owned-address
   set, then reclassify every tx. Doing this on each sync — not only on
   ingest — means that adding a second wallet later promotes prior A→B
   transfers from external_out to internal.
6. Fetch missing daily BTC/fiat prices from CoinGecko (cached).
7. Persist `last_sync_at` setting.

The FIFO ledger itself is rebuilt on demand by `ledger.performance.current_state()`.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

from sqlalchemy import select

from btctrack.chain.derive import (
    Chain,
    DerivedAddress,
    derive_address,
    scan_chain_with_gap,
)
from btctrack.chain.electrum import ElectrumClient, ElectrumConfig, script_to_address
from btctrack.chain.multisig import (
    Multisig,
    parse_descriptor,
    scan_multisig_chain_with_gap,
)
from btctrack.config import get_settings
from btctrack.db.models import Address, Setting, Transaction, TxIO, Wallet
from btctrack.db.session import session_scope
from btctrack.ledger.classify import IO, classify
from btctrack.prices.snapshot import date_of, lookup_prices


@dataclass
class SyncResult:
    wallets: int
    addresses: int
    new_txs: int
    classified: int
    prices_filled: int


def _settings_to_electrum_cfg() -> ElectrumConfig:
    s = get_settings()
    return ElectrumConfig(host=s.electrum_host, port=s.electrum_port, use_ssl=s.electrum_use_ssl)


async def _scan_xpub(
    client: ElectrumClient,
    xpub: str,
    script_type: str,
    gap_limit: int,
) -> dict[Chain, list[DerivedAddress]]:
    out: dict[Chain, list[DerivedAddress]] = {"receive": [], "change": []}
    for chain in ("receive", "change"):
        async def has_history(addr: str) -> bool:
            hist = await client.get_history(addr)
            return bool(hist)

        out[chain] = await scan_chain_with_gap(
            xpub=xpub,
            script_type=script_type,  # type: ignore[arg-type]
            chain=chain,  # type: ignore[arg-type]
            has_history=has_history,
            gap_limit=gap_limit,
        )
    return out


async def _scan_multisig(
    client: ElectrumClient,
    descriptor: str,
    gap_limit: int,
) -> dict[Chain, list[DerivedAddress]]:
    """Scan both chains of a multisig wallet using the parsed descriptor."""
    ms: Multisig = parse_descriptor(descriptor)
    out: dict[Chain, list[DerivedAddress]] = {"receive": [], "change": []}
    for chain in ("receive", "change"):
        async def has_history(addr: str) -> bool:
            hist = await client.get_history(addr)
            return bool(hist)

        out[chain] = await scan_multisig_chain_with_gap(
            ms=ms,
            chain=chain,  # type: ignore[arg-type]
            has_history=has_history,
            gap_limit=gap_limit,
        )
    return out


def _persist_addresses(wallet_id: int, derived: dict[Chain, list[DerivedAddress]]) -> int:
    """Insert derived addresses (idempotent on (wallet_id, address))."""
    inserted = 0
    with session_scope() as s:
        existing = {
            a.address
            for a in s.execute(select(Address).where(Address.wallet_id == wallet_id)).scalars()
        }
        for chain, addrs in derived.items():
            for d in addrs:
                if d.address in existing:
                    continue
                s.add(
                    Address(
                        wallet_id=wallet_id,
                        address=d.address,
                        derivation_index=d.index,
                        chain=chain,
                        is_used=False,
                    )
                )
                inserted += 1
    return inserted


def _all_owned_addresses() -> dict[str, int]:
    """address → wallet_id."""
    with session_scope() as s:
        return {
            a.address: a.wallet_id
            for a in s.execute(select(Address)).scalars()
        }


async def _ingest_tx(
    client: ElectrumClient,
    txid: str,
    owned: dict[str, int],
) -> tuple[Transaction, list[TxIO]]:
    raw = await client.get_transaction(txid, verbose=True)

    block_time = None
    if raw.get("blocktime"):
        block_time = datetime.fromtimestamp(raw["blocktime"], tz=timezone.utc)
    block_height = raw.get("height") or raw.get("confirmations") and None  # height not always present

    # Sum input value by dereferencing each prevout. For coinbase, skip.
    inputs: list[TxIO] = []
    total_in = 0
    for vin in raw.get("vin", []):
        if "coinbase" in vin:
            continue
        prev_txid = vin["txid"]
        vout_idx = vin["vout"]
        prev = await client.get_transaction(prev_txid, verbose=True)
        prev_out = prev["vout"][vout_idx]
        addr = _extract_addr(prev_out)
        amt_sats = int(round(float(prev_out["value"]) * 100_000_000))
        total_in += amt_sats
        inputs.append(
            TxIO(
                txid=txid,
                direction="in",
                address=addr,
                amount_sats=amt_sats,
                owned=addr in owned if addr else False,
                wallet_id=owned.get(addr) if addr else None,
            )
        )

    outputs: list[TxIO] = []
    total_out = 0
    for vout in raw.get("vout", []):
        addr = _extract_addr(vout)
        amt_sats = int(round(float(vout["value"]) * 100_000_000))
        total_out += amt_sats
        outputs.append(
            TxIO(
                txid=txid,
                direction="out",
                address=addr,
                amount_sats=amt_sats,
                owned=addr in owned if addr else False,
                wallet_id=owned.get(addr) if addr else None,
            )
        )

    fee_sats = max(total_in - total_out, 0) if total_in > 0 else 0
    tx = Transaction(
        txid=txid,
        block_height=block_height,
        block_time=block_time,
        fee_sats=fee_sats,
    )
    return tx, inputs + outputs


def _extract_addr(vout: dict) -> str | None:
    sp = vout.get("scriptPubKey", {})
    # Modern Electrum servers expose "address"; older expose "addresses": [...]
    if "address" in sp and sp["address"]:
        return sp["address"]
    if "addresses" in sp and sp["addresses"]:
        return sp["addresses"][0]
    # Fallback: not all Electrum implementations populate the address field
    # for every output type — notably some omit it for P2WSH (multisig). The
    # script hex is always present, so derive the canonical address from it.
    if "hex" in sp:
        return script_to_address(sp["hex"])
    return None


def reclassify_all() -> int:
    """Refresh TxIO.owned / wallet_id against the current address set, then
    reclassify every persisted transaction. Returns the count of transactions
    whose classification actually changed.

    Safe to call standalone (e.g. after adding/removing a wallet without a
    full chain resync) — it touches only local DB state.
    """
    current_owned = _all_owned_addresses()
    with session_scope() as s:
        for io in s.execute(select(TxIO)).scalars():
            should_own = io.address is not None and io.address in current_owned
            new_wallet_id = current_owned.get(io.address) if io.address else None
            if io.owned != should_own:
                io.owned = should_own
            if io.wallet_id != new_wallet_id:
                io.wallet_id = new_wallet_id

    changed = 0
    with session_scope() as s:
        for t in s.execute(select(Transaction)).scalars().all():
            ios = t.ios
            inputs = [
                IO(io.address, io.amount_sats, io.owned) for io in ios if io.direction == "in"
            ]
            outputs = [
                IO(io.address, io.amount_sats, io.owned) for io in ios if io.direction == "out"
            ]
            res = classify(inputs, outputs)
            if t.classification != res.classification:
                t.classification = res.classification
                changed += 1
    return changed


async def _do_sync() -> SyncResult:
    settings = get_settings()
    cfg = _settings_to_electrum_cfg()

    addresses_added = 0
    new_txs = 0
    classified = 0
    prices_filled = 0

    async with ElectrumClient(cfg) as client:
        # 1. derive addresses
        with session_scope() as s:
            wallets = s.execute(select(Wallet)).scalars().all()
            wallet_count = len(wallets)
            wallet_specs = [
                (w.id, w.kind, w.value, w.script_type, w.gap_limit) for w in wallets
            ]

        for wallet_id, kind, value, script_type, gap_limit in wallet_specs:
            if kind == "xpub":
                derived = await _scan_xpub(client, value, script_type, gap_limit)
                addresses_added += _persist_addresses(wallet_id, derived)
            elif kind == "multisig":
                derived = await _scan_multisig(client, value, gap_limit)
                addresses_added += _persist_addresses(wallet_id, derived)
            else:
                # single address — ensure row exists
                with session_scope() as s:
                    existing = s.execute(
                        select(Address).where(
                            Address.wallet_id == wallet_id, Address.address == value
                        )
                    ).scalar_one_or_none()
                    if not existing:
                        s.add(
                            Address(
                                wallet_id=wallet_id,
                                address=value,
                                derivation_index=None,
                                chain="receive",
                                is_used=False,
                            )
                        )
                        addresses_added += 1

        # 2. histories → discover txids
        owned = _all_owned_addresses()
        seen_txids: set[str] = set()
        for addr in owned.keys():
            hist = await client.get_history(addr)
            for h in hist:
                seen_txids.add(h["tx_hash"])

        # 3. ingest new txs
        with session_scope() as s:
            existing_txids = {
                t.txid for t in s.execute(select(Transaction.txid)).all()
            }  # type: ignore[misc]
        # The above is awkward (Result rows of strings). Re-query cleanly:
        with session_scope() as s:
            existing_txids = {
                row[0] for row in s.execute(select(Transaction.txid)).all()
            }
        to_fetch = sorted(seen_txids - existing_txids)
        for txid in to_fetch:
            tx, ios = await _ingest_tx(client, txid, owned)
            with session_scope() as s:
                s.add(tx)
                for io in ios:
                    s.add(io)
            new_txs += 1

    # 4. refresh TxIO ownership against the *current* owned-address set, then
    # reclassify every tx. Without the refresh, IOs ingested before a wallet
    # was added stay flagged owned=False forever, which mis-classifies wallet→
    # wallet transfers as external_out / external_in even after both wallets
    # are known. Reclassifying every tx every sync is cheap and idempotent.
    classified += reclassify_all()

    # 5. fetch prices for external txs missing one
    with session_scope() as s:
        ext = s.execute(
            select(Transaction).where(
                Transaction.classification.in_(("external_in", "external_out")),
                Transaction.btc_price_fiat.is_(None),
                Transaction.block_time.is_not(None),
            )
        ).scalars().all()
        dates = sorted({date_of(t.block_time) for t in ext if t.block_time})

    if dates:
        prices = lookup_prices(dates, settings.base_currency)
        with session_scope() as s:
            ext = s.execute(
                select(Transaction).where(
                    Transaction.classification.in_(("external_in", "external_out")),
                    Transaction.btc_price_fiat.is_(None),
                    Transaction.block_time.is_not(None),
                )
            ).scalars().all()
            for t in ext:
                d = date_of(t.block_time)  # type: ignore[arg-type]
                if d in prices:
                    t.btc_price_fiat = prices[d]
                    t.base_ccy = settings.base_currency
                    prices_filled += 1

    # 6. mark address used flags
    with session_scope() as s:
        used_addresses = {
            row[0]
            for row in s.execute(
                select(TxIO.address).where(TxIO.address.is_not(None))
            ).all()
        }
        for a in s.execute(select(Address)).scalars():
            if a.address in used_addresses and not a.is_used:
                a.is_used = True

    # 7. last sync
    with session_scope() as s:
        existing = s.get(Setting, "last_sync_at")
        now_iso = datetime.now(timezone.utc).isoformat()
        if existing:
            existing.value = now_iso
        else:
            s.add(Setting(key="last_sync_at", value=now_iso))

    return SyncResult(
        wallets=wallet_count,
        addresses=addresses_added,
        new_txs=new_txs,
        classified=classified,
        prices_filled=prices_filled,
    )


def run_sync() -> SyncResult:
    """Synchronous entry point used by Streamlit and CLI."""
    return asyncio.run(_do_sync())


def add_wallet(
    *,
    label: str,
    kind: str,
    value: str,
    script_type: str | None = None,
    gap_limit: int | None = None,
) -> int:
    """Persist a new wallet. Returns the new wallet id.

    For `kind="multisig"`, `value` must be a Sparrow-style output descriptor
    (wsh / sh(wsh) / sh wrapping multi or sortedmulti). The script type is
    inferred from the descriptor and stored on the wallet for display.
    """
    settings = get_settings()
    if kind == "xpub":
        if not script_type:
            # Sparrow with SLIP-132 disabled exports native-segwit accounts as
            # `xpub` even though they're p2wpkh. Inferring from the prefix is
            # unsafe; default to the most common Sparrow setup instead.
            script_type = "p2wpkh"
        st = script_type
    elif kind == "address":
        st = script_type or "p2wpkh"
    elif kind == "multisig":
        # Validate the descriptor early so the user gets immediate feedback,
        # and pull the script type out of it for the wallet row.
        ms = parse_descriptor(value)
        st = ms.script_type
    else:
        raise ValueError(f"Unknown wallet kind: {kind}")
    gl = gap_limit if gap_limit is not None else settings.gap_limit
    with session_scope() as s:
        w = Wallet(
            label=label,
            kind=kind,
            value=value,
            script_type=st,
            gap_limit=gl,
        )
        s.add(w)
        s.flush()
        return w.id


def remove_wallet(wallet_id: int) -> None:
    with session_scope() as s:
        w = s.get(Wallet, wallet_id)
        if w:
            s.delete(w)


def get_last_sync() -> datetime | None:
    with session_scope() as s:
        row = s.get(Setting, "last_sync_at")
        if not row:
            return None
        try:
            return datetime.fromisoformat(row.value)
        except ValueError:
            return None
