"""xpub/ypub/zpub derivation and gap-limit scanning.

The script type is **independent** of the extended-key prefix. Sparrow (and
others) can export a native-segwit account in `xpub` format if SLIP-132 is
disabled, so we normalise the version bytes to whatever bip-utils' Bip##
class expects for the chosen script type before deriving. This means the
caller picks the script type explicitly and the input prefix doesn't matter.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable, Literal

from bip_utils import (
    Base58Decoder,
    Base58Encoder,
    Bip32KeyError,
    Bip44,
    Bip44Coins,
    Bip49,
    Bip49Coins,
    Bip84,
    Bip84Coins,
    Bip86,
    Bip86Coins,
)

ScriptType = Literal["p2pkh", "p2sh-p2wpkh", "p2wpkh", "p2tr"]
Chain = Literal["receive", "change"]

# Mainnet pub version bytes the corresponding BIP## class expects.
_PUB_VERSION_FOR_SCRIPT: dict[ScriptType, bytes] = {
    "p2pkh": bytes.fromhex("0488B21E"),         # xpub
    "p2sh-p2wpkh": bytes.fromhex("049D7CB2"),   # ypub
    "p2wpkh": bytes.fromhex("04B24746"),        # zpub
    "p2tr": bytes.fromhex("0488B21E"),          # xpub (BIP86 reuses standard xpub)
}


def _normalise_version(extended_key: str, target_version: bytes) -> str:
    """Re-encode `extended_key` with `target_version` as the version bytes.

    Used to feed any extended-pubkey format into the bip-utils class that
    expects a specific prefix. The rest of the 78-byte payload is untouched
    (depth, fingerprint, child number, chain code, key) so derivation is
    unchanged — only the SLIP-132 advisory prefix is rewritten.
    """
    raw = Base58Decoder.CheckDecode(extended_key)
    if len(raw) != 78:
        raise ValueError(f"Invalid extended key length: got {len(raw)}, expected 78")
    return Base58Encoder.CheckEncode(target_version + raw[4:])


def _ctx(xpub: str, script_type: ScriptType):
    """Build a bip-utils account context from an account-level xpub."""
    target = _PUB_VERSION_FOR_SCRIPT.get(script_type)
    if target is None:
        raise ValueError(f"Unknown script type: {script_type}")
    normalised = _normalise_version(xpub, target)
    if script_type == "p2pkh":
        return Bip44.FromExtendedKey(normalised, Bip44Coins.BITCOIN)
    if script_type == "p2sh-p2wpkh":
        return Bip49.FromExtendedKey(normalised, Bip49Coins.BITCOIN)
    if script_type == "p2wpkh":
        return Bip84.FromExtendedKey(normalised, Bip84Coins.BITCOIN)
    if script_type == "p2tr":
        return Bip86.FromExtendedKey(normalised, Bip86Coins.BITCOIN)
    raise ValueError(f"Unknown script type: {script_type}")


@dataclass(frozen=True)
class DerivedAddress:
    address: str
    chain: Chain
    index: int


def derive_address(xpub: str, script_type: ScriptType, chain: Chain, index: int) -> str:
    """Derive a single address. The xpub is expected at account level."""
    ctx = _ctx(xpub, script_type)
    chain_ctx = ctx.Change(_change_enum(chain))
    try:
        return chain_ctx.AddressIndex(index).PublicKey().ToAddress()
    except Bip32KeyError as e:  # pragma: no cover - defensive
        raise ValueError(f"Derivation failed at {chain}/{index}: {e}") from e


def _change_enum(chain: Chain):
    from bip_utils.bip.bip44_base import Bip44Changes

    return Bip44Changes.CHAIN_EXT if chain == "receive" else Bip44Changes.CHAIN_INT


def derive_chain(
    xpub: str, script_type: ScriptType, chain: Chain, count: int, start: int = 0
) -> list[DerivedAddress]:
    """Derive `count` addresses on one chain, starting at `start`."""
    return [
        DerivedAddress(derive_address(xpub, script_type, chain, i), chain, i)
        for i in range(start, start + count)
    ]


HasHistory = Callable[[str], Awaitable[bool]]


async def scan_chain_with_gap(
    xpub: str,
    script_type: ScriptType,
    chain: Chain,
    has_history: HasHistory,
    gap_limit: int = 20,
    max_addresses: int = 10_000,
) -> list[DerivedAddress]:
    """Walk a chain until `gap_limit` consecutive addresses have no history.

    `has_history(address)` is awaited for each derived address. The returned
    list contains every address scanned, with `is_used` recorded by callers
    using the `has_history` result; here we just return all addresses up to
    and including the gap window so callers can persist them.
    """
    discovered: list[DerivedAddress] = []
    consecutive_empty = 0
    i = 0
    while consecutive_empty < gap_limit and i < max_addresses:
        addr = derive_address(xpub, script_type, chain, i)
        used = await has_history(addr)
        discovered.append(DerivedAddress(addr, chain, i))
        if used:
            consecutive_empty = 0
        else:
            consecutive_empty += 1
        i += 1
    return discovered
