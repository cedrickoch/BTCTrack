"""Address derivation tests against published BIP test vectors.

Account-level extended keys for the standard mnemonic
"abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon about"
(passphrase: empty, account 0):

  BIP44  (m/44'/0'/0')  →  xpub6CUGRUonZSQ4TWtTMmzXdrXDtypWKiKrhko4egpiMZbpiaQL2jkwSB1icqYh2cfDfVxdx4df189oLKnC5fSwqPfgyP3hooxujYzAu3fDVmz
  BIP49  (m/49'/0'/0')  →  ypub6Ww3ibxVfGzLrAH1PNcjyAWenMTbbAosGNB6VvmSEgytSER9azLDWCxoJwW7Ke7icmizBMXrzBx9979FfaHxHcrArf3zbeJJJUZPf663zsP
  BIP84  (m/84'/0'/0')  →  zpub6rFR7y4Q2AijBEqTUquhVz398htDFrtymD9xYYfG1m4wAcvPhXNfE3EfH1r1ADqtfSdVCToUG868RvUUkgDKf31mGDtKsAYz2oz2AGutZYs

Expected first receive address (m/.../0/0):
  BIP44  →  1LqBGSKuX5yYUonjxT5qGfpUsXKYYWeabA
  BIP49  →  37VucYSaXLCAsxYyAPfbSi9eh4iEcbShgf
  BIP84  →  bc1qcr8te4kr609gcawutmrza0j4xv80jy8z306fyu
"""

from __future__ import annotations

import asyncio

import pytest

from btctrack.chain.derive import (
    _normalise_version,
    _PUB_VERSION_FOR_SCRIPT,
    derive_address,
    derive_chain,
    scan_chain_with_gap,
)

XPUB44 = "xpub6BosfCnifzxcFwrSzQiqu2DBVTshkCXacvNsWGYJVVhhawA7d4R5WSWGFNbi8Aw6ZRc1brxMyWMzG3DSSSSoekkudhUd9yLb6qx39T9nMdj"
YPUB49 = "ypub6Ww3ibxVfGzLrAH1PNcjyAWenMTbbAosGNB6VvmSEgytSER9azLDWCxoJwW7Ke7icmizBMXrzBx9979FfaHxHcrArf3zbeJJJUZPf663zsP"
ZPUB84 = "zpub6rFR7y4Q2AijBEqTUquhVz398htDFrtymD9xYYfG1m4wAcvPhXNfE3EfH1r1ADqtfSdVCToUG868RvUUkgDKf31mGDtKsAYz2oz2AGutZYs"


def test_bip44_first_receive():
    addr = derive_address(XPUB44, "p2pkh", "receive", 0)
    assert addr == "1LqBGSKuX5yYUonjxT5qGfpUsXKYYWeabA"


def test_bip49_first_receive():
    addr = derive_address(YPUB49, "p2sh-p2wpkh", "receive", 0)
    assert addr == "37VucYSaXLCAsxYyAPfbSi9eh4iEcbShgf"


def test_bip84_first_receive():
    addr = derive_address(ZPUB84, "p2wpkh", "receive", 0)
    assert addr == "bc1qcr8te4kr609gcawutmrza0j4xv80jy8z306fyu"


def test_derive_chain_returns_distinct_consecutive_addresses():
    addrs = derive_chain(ZPUB84, "p2wpkh", "receive", count=5)
    assert len(addrs) == 5
    assert len({a.address for a in addrs}) == 5
    assert [a.index for a in addrs] == [0, 1, 2, 3, 4]


def test_change_chain_differs_from_receive():
    r0 = derive_address(ZPUB84, "p2wpkh", "receive", 0)
    c0 = derive_address(ZPUB84, "p2wpkh", "change", 0)
    assert r0 != c0


def test_native_segwit_account_exported_as_xpub_still_derives_p2wpkh():
    """Sparrow with SLIP-132 disabled exports a BIP84 account as `xpub` format.

    The version bytes lie about the script type. Our derivation must trust the
    explicit script_type argument, not the prefix, and produce the same
    bech32 addresses as the canonical zpub.
    """
    xpub_form = _normalise_version(ZPUB84, _PUB_VERSION_FOR_SCRIPT["p2pkh"])
    assert xpub_form.startswith("xpub")
    # Explicit p2wpkh on the xpub-prefixed version yields the BIP84 address.
    addr = derive_address(xpub_form, "p2wpkh", "receive", 0)
    assert addr == "bc1qcr8te4kr609gcawutmrza0j4xv80jy8z306fyu"


def test_normalise_version_round_trips_payload():
    """Re-encoding must change only the first 4 bytes."""
    from bip_utils import Base58Decoder

    original = Base58Decoder.CheckDecode(ZPUB84)
    converted = _normalise_version(ZPUB84, _PUB_VERSION_FOR_SCRIPT["p2pkh"])
    rt = Base58Decoder.CheckDecode(converted)
    assert rt[4:] == original[4:]
    assert rt[:4] == _PUB_VERSION_FOR_SCRIPT["p2pkh"]


def test_address_to_scripthash_known_vectors():
    """Electrum scripthash = sha256(scriptPubKey)[::-1]. Known fixtures from electrumx docs."""
    from btctrack.chain.electrum import address_to_scripthash

    # Genesis-style P2PKH
    assert (
        address_to_scripthash("1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa")
        == "8b01df4e368ea28f8dc0423bcf7a4923e3a12d307c875e47a0cfbf90b5c39161"
    )
    # Round-trip: same address yields same scripthash.
    sh1 = address_to_scripthash("bc1qcr8te4kr609gcawutmrza0j4xv80jy8z306fyu")
    sh2 = address_to_scripthash("bc1qcr8te4kr609gcawutmrza0j4xv80jy8z306fyu")
    assert sh1 == sh2 and len(sh1) == 64


def test_gap_limit_scan_stops_after_gap():
    """Only the first address has 'history'; gap_limit=3 should stop after index 3."""
    seen: list[str] = []

    async def has_history(addr: str) -> bool:
        seen.append(addr)
        return len(seen) == 1  # only first call returns True

    discovered = asyncio.run(
        scan_chain_with_gap(
            xpub=ZPUB84,
            script_type="p2wpkh",
            chain="receive",
            has_history=has_history,
            gap_limit=3,
        )
    )
    # 1st has history, then 3 empty in a row → stop. Total 4 addresses scanned.
    assert len(discovered) == 4
    assert len(seen) == 4
