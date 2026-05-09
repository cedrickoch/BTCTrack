"""Async Electrum-protocol client.

Wraps `aiorpcx` to expose only the calls BTCTrack needs:
- scripthash → history
- transaction by txid (verbose, so we get block_height/time and prevout details)

The Electrum protocol indexes by *scripthash*, which is the SHA-256 of the
scriptPubKey reversed. We compute it locally so the address translation
never leaves the machine.
"""

from __future__ import annotations

import asyncio
import hashlib
import ssl
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, AsyncIterator

from aiorpcx import RPCSession, connect_rs
from bip_utils import Base58Encoder
from bip_utils.addr import (
    P2PKHAddrDecoder,
    P2SHAddrDecoder,
    P2TRAddrDecoder,
    P2WPKHAddrDecoder,
)
from bip_utils.bech32 import SegwitBech32Encoder
from bip_utils.coin_conf import CoinsConf


@dataclass
class ElectrumConfig:
    host: str
    port: int
    use_ssl: bool = True
    timeout: float = 30.0


def address_to_scripthash(address: str) -> str:
    """Convert a BTC address to an Electrum scripthash (hex, big-endian-reversed).

    Supports legacy (P2PKH), P2SH (incl. wrapped segwit), bech32 (P2WPKH/P2WSH),
    and bech32m (P2TR) mainnet addresses.
    """
    script = _address_to_script_pubkey(address)
    h = hashlib.sha256(script).digest()
    return h[::-1].hex()


def _address_to_script_pubkey(address: str) -> bytes:
    btc = CoinsConf.BitcoinMainNet
    p2wpkh_hrp = btc.ParamByKey("p2wpkh_hrp")
    p2tr_hrp = btc.ParamByKey("p2tr_hrp")
    p2pkh_ver = btc.ParamByKey("p2pkh_net_ver")
    p2sh_ver = btc.ParamByKey("p2sh_net_ver")

    addr_lower = address.lower()
    if addr_lower.startswith("bc1"):
        # bip_utils' P2WPKHAddrDecoder accepts witness-v0 with *any* program
        # length, so a P2WSH address (32-byte program) decodes successfully
        # too. We branch on the program length to emit the correct
        # scriptPubKey: PUSH_20 for P2WPKH, PUSH_32 for P2WSH. Any other
        # decoder failure (wrong witness version for P2TR, etc.) falls
        # through to the next attempt.
        try:
            program = P2WPKHAddrDecoder.DecodeAddr(address, hrp=p2wpkh_hrp)
        except Exception:
            program = None
        if program is not None:
            if len(program) == 20:
                return b"\x00\x14" + program
            if len(program) == 32:
                return b"\x00\x20" + program
            raise ValueError(
                f"Unsupported witness-v0 program length {len(program)} for {address}"
            )
        try:
            program = P2TRAddrDecoder.DecodeAddr(address, hrp=p2tr_hrp)
            return b"\x51\x20" + program
        except Exception:
            pass
        raise ValueError(f"Unrecognised bech32 address: {address}")
    try:
        h160 = P2PKHAddrDecoder.DecodeAddr(address, net_ver=p2pkh_ver)
        return b"\x76\xa9\x14" + h160 + b"\x88\xac"
    except Exception:
        pass
    try:
        h160 = P2SHAddrDecoder.DecodeAddr(address, net_ver=p2sh_ver)
        return b"\xa9\x14" + h160 + b"\x87"
    except Exception:
        pass
    raise ValueError(f"Unrecognised address: {address}")


def script_to_address(script_hex: str) -> str | None:
    """Decode a hex-encoded scriptPubKey to its canonical mainnet address.

    Used as a fallback when an Electrum server omits the `address`/`addresses`
    field on a `vout`. The five standard output templates are recognised:
    P2PKH, P2SH, P2WPKH, P2WSH, P2TR. Anything else (bare multisig, OP_RETURN,
    non-standard) returns None — those legitimately have no address.
    """
    try:
        script = bytes.fromhex(script_hex)
    except ValueError:
        return None
    btc = CoinsConf.BitcoinMainNet
    p2wpkh_hrp = btc.ParamByKey("p2wpkh_hrp")
    p2tr_hrp = btc.ParamByKey("p2tr_hrp")
    p2pkh_ver = btc.ParamByKey("p2pkh_net_ver")
    p2sh_ver = btc.ParamByKey("p2sh_net_ver")
    # P2PKH: OP_DUP OP_HASH160 PUSH(20) <h160> OP_EQUALVERIFY OP_CHECKSIG
    if (
        len(script) == 25
        and script[0] == 0x76
        and script[1] == 0xA9
        and script[2] == 0x14
        and script[23] == 0x88
        and script[24] == 0xAC
    ):
        return Base58Encoder.CheckEncode(p2pkh_ver + script[3:23])
    # P2SH: OP_HASH160 PUSH(20) <h160> OP_EQUAL
    if len(script) == 23 and script[0] == 0xA9 and script[1] == 0x14 and script[22] == 0x87:
        return Base58Encoder.CheckEncode(p2sh_ver + script[2:22])
    # P2WPKH: OP_0 PUSH(20) <program>
    if len(script) == 22 and script[0] == 0x00 and script[1] == 0x14:
        return SegwitBech32Encoder.Encode(p2wpkh_hrp, 0, script[2:])
    # P2WSH: OP_0 PUSH(32) <program>
    if len(script) == 34 and script[0] == 0x00 and script[1] == 0x20:
        return SegwitBech32Encoder.Encode(p2wpkh_hrp, 0, script[2:])
    # P2TR: OP_1 PUSH(32) <program> (bech32m)
    if len(script) == 34 and script[0] == 0x51 and script[1] == 0x20:
        return SegwitBech32Encoder.Encode(p2tr_hrp, 1, script[2:])
    return None


class ElectrumClient:
    """Thin async wrapper. One instance == one open connection."""

    def __init__(self, cfg: ElectrumConfig):
        self.cfg = cfg
        self._session: RPCSession | None = None
        self._cm = None

    async def __aenter__(self) -> "ElectrumClient":
        ssl_ctx: ssl.SSLContext | None = None
        if self.cfg.use_ssl:
            ssl_ctx = ssl.create_default_context()
            # Electrum servers commonly use self-signed certs.
            ssl_ctx.check_hostname = False
            ssl_ctx.verify_mode = ssl.CERT_NONE
        self._cm = connect_rs(self.cfg.host, self.cfg.port, ssl=ssl_ctx)
        self._session = await self._cm.__aenter__()
        # Handshake
        await self._session.send_request(
            "server.version", ["btctrack/0.1", "1.4"]
        )
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._cm is not None:
            await self._cm.__aexit__(exc_type, exc, tb)
        self._session = None
        self._cm = None

    async def get_history(self, address: str) -> list[dict[str, Any]]:
        sh = address_to_scripthash(address)
        return await self._call("blockchain.scripthash.get_history", [sh])

    async def get_transaction(self, txid: str, verbose: bool = True) -> Any:
        return await self._call("blockchain.transaction.get", [txid, verbose])

    async def get_block_header(self, height: int) -> dict[str, Any]:
        return await self._call("blockchain.block.header", [height])

    async def _call(self, method: str, params: list[Any]) -> Any:
        if self._session is None:
            raise RuntimeError("ElectrumClient not connected; use 'async with'.")
        return await asyncio.wait_for(
            self._session.send_request(method, params), timeout=self.cfg.timeout
        )


@asynccontextmanager
async def electrum_client(cfg: ElectrumConfig) -> AsyncIterator[ElectrumClient]:
    async with ElectrumClient(cfg) as c:
        yield c
