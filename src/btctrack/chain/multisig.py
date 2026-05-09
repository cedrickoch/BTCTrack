"""Multisig wallet support — Sparrow-style output descriptors.

Supported descriptor shapes:

  wsh(sortedmulti(M, key1, key2, ...))           → P2WSH        (bc1q…)
  sh(wsh(sortedmulti(M, key1, key2, ...)))       → P2SH-P2WSH   (3…)
  sh(sortedmulti(M, key1, key2, ...))            → P2SH legacy  (3…)

`multi(...)` (no BIP67 sorting) is also accepted. Each cosigner key may carry
optional origin info `[fingerprint/path]` and a derivation suffix that will
be expanded with the chain (0/1) and address index. Both single-path
(`/0/*` and `/1/*`) and BIP389 multipath (`/<0;1>/*`) suffixes work.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Awaitable, Callable, List, Literal

from bip_utils import (
    Base58Decoder,
    Base58Encoder,
    Bip32Secp256k1,
)
from bip_utils.bech32 import SegwitBech32Encoder

from btctrack.chain.derive import DerivedAddress

ScriptType = Literal["p2sh", "p2sh-p2wsh", "p2wsh"]
Chain = Literal["receive", "change"]

XPUB_VERSION_MAINNET = bytes.fromhex("0488B21E")


@dataclass(frozen=True)
class Cosigner:
    xpub: str          # extended public key (any format/depth bip-utils accepts)
    deriv_suffix: str  # e.g. "/0/*", "/1/*", "/<0;1>/*", or "" for plain xpub


@dataclass(frozen=True)
class Multisig:
    script_type: ScriptType
    threshold: int
    cosigners: List[Cosigner]
    sorted_keys: bool

    @property
    def n(self) -> int:
        return len(self.cosigners)


def _strip_checksum(desc: str) -> str:
    return desc.split("#", 1)[0].strip()


def _promote_single_path_to_multipath(suffix: str) -> str:
    """Sparrow's *Show Descriptor* shows the receive-only path `/0/*`. For
    portfolio tracking we always want both receive and change scanned, so we
    rewrite a single-path receive/change suffix into the BIP389 multipath
    form. Other suffixes (already multipath, plain xpub, custom paths) pass
    through unchanged.
    """
    if suffix in ("/0/*", "/1/*"):
        return "/<0;1>/*"
    return suffix


# `xpub|ypub|zpub|tpub|upub|vpub|Ypub|Zpub|Vpub|Upub` etc. We accept any base58-y
# extended key, then normalise version bytes when actually using it.
_KEY_RE = re.compile(
    r"""^
    (?:\[[^\]]+\])?              # optional origin info [fingerprint/path]
    ([1-9A-HJ-NP-Za-km-z]+)      # the extended key (base58, no 0/O/I/l)
    (/[\d/<>;*'h]*)?             # optional derivation suffix
    $""",
    re.VERBOSE,
)


def _split_top_level(body: str) -> list[str]:
    """Split on commas at paren depth 0 (and not inside [...] / <...>)."""
    parts: list[str] = []
    buf: list[str] = []
    depth = 0
    for ch in body:
        if ch in "([<":
            depth += 1
            buf.append(ch)
        elif ch in ")]>":
            depth -= 1
            buf.append(ch)
        elif ch == "," and depth == 0:
            parts.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
    if buf:
        parts.append("".join(buf).strip())
    return parts


def parse_descriptor(desc: str) -> Multisig:
    raw = _strip_checksum(desc)
    if raw.startswith("sh(wsh(") and raw.endswith("))"):
        script_type: ScriptType = "p2sh-p2wsh"
        inner = raw[len("sh(wsh(") : -2]
    elif raw.startswith("wsh(") and raw.endswith(")"):
        script_type = "p2wsh"
        inner = raw[len("wsh(") : -1]
    elif raw.startswith("sh(") and raw.endswith(")"):
        script_type = "p2sh"
        inner = raw[len("sh(") : -1]
    else:
        raise ValueError(
            "Unsupported descriptor wrapper. Expected wsh(...), sh(wsh(...)), or sh(...)."
        )

    if inner.startswith("sortedmulti(") and inner.endswith(")"):
        sorted_keys = True
        body = inner[len("sortedmulti(") : -1]
    elif inner.startswith("multi(") and inner.endswith(")"):
        sorted_keys = False
        body = inner[len("multi(") : -1]
    else:
        raise ValueError(
            "Unsupported inner expression. Expected multi(...) or sortedmulti(...)."
        )

    parts = _split_top_level(body)
    if len(parts) < 2:
        raise ValueError("Multisig must have a threshold and at least one key.")

    try:
        threshold = int(parts[0])
    except ValueError as e:
        raise ValueError(f"Invalid threshold: {parts[0]!r}") from e

    cosigners: list[Cosigner] = []
    for keyspec in parts[1:]:
        m = _KEY_RE.match(keyspec.strip())
        if not m:
            raise ValueError(f"Could not parse cosigner: {keyspec!r}")
        xpub = m.group(1)
        suffix = _promote_single_path_to_multipath(m.group(2) or "")
        cosigners.append(Cosigner(xpub=xpub, deriv_suffix=suffix))

    if not 1 <= threshold <= len(cosigners):
        raise ValueError(
            f"Threshold {threshold} out of range for {len(cosigners)} cosigners."
        )

    return Multisig(
        script_type=script_type,
        threshold=threshold,
        cosigners=cosigners,
        sorted_keys=sorted_keys,
    )


def _normalise_to_xpub(extended_key: str) -> str:
    """Rewrite version bytes to standard `xpub` so Bip32Secp256k1 accepts the key."""
    raw = Base58Decoder.CheckDecode(extended_key)
    if len(raw) != 78:
        raise ValueError(f"Invalid extended key length: {len(raw)}")
    return Base58Encoder.CheckEncode(XPUB_VERSION_MAINNET + raw[4:])


def _resolve_path(deriv_suffix: str, chain_idx: int, index: int) -> str:
    """Resolve a descriptor derivation suffix against (chain, index)."""
    if not deriv_suffix:
        # No suffix: treat the xpub as already at the parent of (chain, index).
        return f"{chain_idx}/{index}"
    parts = [p for p in deriv_suffix.lstrip("/").split("/") if p]
    out: list[str] = []
    for p in parts:
        if p == "*":
            out.append(str(index))
        elif p.startswith("<") and p.endswith(">"):
            opts = p[1:-1].split(";")
            if chain_idx >= len(opts):
                raise ValueError(
                    f"Multipath {p!r} doesn't define a value for chain index {chain_idx}"
                )
            out.append(opts[chain_idx])
        else:
            out.append(p)
    return "/".join(out)


def _derive_pubkey(cosigner: Cosigner, chain_idx: int, index: int) -> bytes:
    canonical = _normalise_to_xpub(cosigner.xpub)
    ctx = Bip32Secp256k1.FromExtendedKey(canonical)
    path = _resolve_path(cosigner.deriv_suffix, chain_idx, index)
    if path:
        ctx = ctx.DerivePath(path)
    return ctx.PublicKey().RawCompressed().ToBytes()


def build_multisig_script(threshold: int, pubkeys: list[bytes]) -> bytes:
    """OP_M <pk1> ... <pkN> OP_N OP_CHECKMULTISIG (compressed pubkeys)."""
    n = len(pubkeys)
    if not 1 <= threshold <= n <= 16:
        raise ValueError(f"Multisig must satisfy 1 ≤ M ≤ N ≤ 16 (got M={threshold}, N={n}).")
    out = bytearray()
    out.append(0x50 + threshold)  # OP_M (OP_1=0x51 → 0x50+1)
    for pk in pubkeys:
        if len(pk) != 33:
            raise ValueError("Compressed pubkey must be 33 bytes.")
        out.append(0x21)  # push 33 bytes
        out += pk
    out.append(0x50 + n)  # OP_N
    out.append(0xAE)  # OP_CHECKMULTISIG
    return bytes(out)


def _hash160(data: bytes) -> bytes:
    return hashlib.new("ripemd160", hashlib.sha256(data).digest()).digest()


def _p2wsh_address(witness_script: bytes) -> str:
    program = hashlib.sha256(witness_script).digest()
    return SegwitBech32Encoder.Encode("bc", 0, program)


def _p2sh_address(redeem_script: bytes) -> str:
    return Base58Encoder.CheckEncode(b"\x05" + _hash160(redeem_script))


def derive_multisig_address(ms: Multisig, chain: Chain, index: int) -> str:
    chain_idx = 0 if chain == "receive" else 1
    pubkeys = [_derive_pubkey(c, chain_idx, index) for c in ms.cosigners]
    if ms.sorted_keys:
        pubkeys.sort()
    script = build_multisig_script(ms.threshold, pubkeys)
    if ms.script_type == "p2wsh":
        return _p2wsh_address(script)
    if ms.script_type == "p2sh-p2wsh":
        wprog = hashlib.sha256(script).digest()
        redeem = b"\x00\x20" + wprog
        return _p2sh_address(redeem)
    if ms.script_type == "p2sh":
        return _p2sh_address(script)
    raise ValueError(f"Unknown multisig script type: {ms.script_type}")


HasHistory = Callable[[str], Awaitable[bool]]


async def scan_multisig_chain_with_gap(
    ms: Multisig,
    chain: Chain,
    has_history: HasHistory,
    gap_limit: int = 20,
    max_addresses: int = 10_000,
) -> list[DerivedAddress]:
    """Walk one chain (receive/change) of a multisig wallet until gap_limit empty.

    Returns a `DerivedAddress` for every address scanned, so callers can
    persist them with the correct chain tag without re-wrapping.
    """
    discovered: list[DerivedAddress] = []
    consecutive_empty = 0
    i = 0
    while consecutive_empty < gap_limit and i < max_addresses:
        addr = derive_multisig_address(ms, chain, i)
        used = await has_history(addr)
        discovered.append(DerivedAddress(address=addr, chain=chain, index=i))  # type: ignore[arg-type]
        if used:
            consecutive_empty = 0
        else:
            consecutive_empty += 1
        i += 1
    return discovered
