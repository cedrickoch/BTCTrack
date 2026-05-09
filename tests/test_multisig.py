"""Multisig descriptor parsing + address derivation tests.

The three building blocks (Bip32Secp256k1 derivation, bech32 encoding,
base58check encoding) are each anchored by their own test in
test_derive.py. Here we focus on the multisig-specific glue:
descriptor parsing, redeem-script construction, sortedmulti behaviour,
and address-format correctness.
"""

from __future__ import annotations

import pytest
from bip_utils import Base58Decoder

from btctrack.chain.multisig import (
    Cosigner,
    Multisig,
    build_multisig_script,
    derive_multisig_address,
    parse_descriptor,
)

# Three distinct BIP84 account-level zpubs from the same well-known test
# mnemonic ("abandon × 11 about") at accounts 0/1/2. They are *real* keys with
# valid base58 checksums — required for parse + derive to actually work.
ZPUB_A = "zpub6rFR7y4Q2AijBEqTUquhVz398htDFrtymD9xYYfG1m4wAcvPhXNfE3EfH1r1ADqtfSdVCToUG868RvUUkgDKf31mGDtKsAYz2oz2AGutZYs"
ZPUB_B = "zpub6rFR7y4Q2AijF6Gk1bofHLs1d66hKFamhXWdWBup1Em25wfabZqkDqvaieV63fDQFaYmaatCG7jVNUpUiM2hAMo6SAVHcrUpSnHDpNzucB7"
ZPUB_C = "zpub6rFR7y4Q2AijHxf5H8YD9SZ1S1hrLi3PmbR9iJeVVZSJmK8R86EPCwBhyTaycoeXEVqLigViktQUy2tt3yLnvcZ7BcXz9QxHrLjaTeJn3xL"

# Real Sparrow-format descriptor; checksum is whatever (we strip it).
DESC_2OF3_WSH = (
    f"wsh(sortedmulti(2,"
    f"[12345678/84h/0h/0h]{ZPUB_A}/0/*,"
    f"[abcdef01/84h/0h/0h]{ZPUB_B}/0/*,"
    f"[deadbeef/84h/0h/0h]{ZPUB_C}/0/*"
    f"))#abcdefgh"
)


# -------- parser --------

def test_parses_p2wsh_sortedmulti_with_origin_and_checksum():
    ms = parse_descriptor(DESC_2OF3_WSH)
    assert ms.script_type == "p2wsh"
    assert ms.threshold == 2
    assert ms.n == 3
    assert ms.sorted_keys is True
    assert ms.cosigners[0].xpub == ZPUB_A
    # Sparrow shows the receive-only /0/* in its UI; we promote that to
    # multipath so one paste covers both receive and change.
    assert ms.cosigners[0].deriv_suffix == "/<0;1>/*"


def test_single_path_one_promotion_only():
    """`/1/*` (Sparrow's change descriptor) also promotes to multipath."""
    desc = f"wsh(sortedmulti(2,{ZPUB_A}/1/*,{ZPUB_B}/1/*,{ZPUB_C}/1/*))"
    ms = parse_descriptor(desc)
    assert ms.cosigners[0].deriv_suffix == "/<0;1>/*"


def test_custom_path_passes_through():
    """A non-/0/*-or-/1/* suffix is left alone (used as-is for both chains)."""
    desc = f"wsh(sortedmulti(2,{ZPUB_A}/2/*,{ZPUB_B}/2/*,{ZPUB_C}/2/*))"
    ms = parse_descriptor(desc)
    assert ms.cosigners[0].deriv_suffix == "/2/*"


def test_parses_sh_wsh_wrapper():
    desc = f"sh(wsh(sortedmulti(2,{ZPUB_A}/0/*,{ZPUB_B}/0/*,{ZPUB_C}/0/*)))"
    assert parse_descriptor(desc).script_type == "p2sh-p2wsh"


def test_parses_legacy_sh():
    desc = f"sh(multi(2,{ZPUB_A}/0/*,{ZPUB_B}/0/*,{ZPUB_C}/0/*))"
    ms = parse_descriptor(desc)
    assert ms.script_type == "p2sh"
    assert ms.sorted_keys is False


def test_parses_multipath_bip389():
    desc = f"wsh(sortedmulti(2,{ZPUB_A}/<0;1>/*,{ZPUB_B}/<0;1>/*,{ZPUB_C}/<0;1>/*))"
    ms = parse_descriptor(desc)
    assert ms.cosigners[0].deriv_suffix == "/<0;1>/*"
    # Multipath is resolved at derivation time, not parse time.
    recv = derive_multisig_address(ms, "receive", 0)
    chg = derive_multisig_address(ms, "change", 0)
    assert recv != chg


def test_rejects_unknown_wrapper():
    with pytest.raises(ValueError, match="Unsupported descriptor wrapper"):
        parse_descriptor(f"pkh({ZPUB_A}/0/*)")


def test_rejects_threshold_out_of_range():
    with pytest.raises(ValueError, match="Threshold"):
        parse_descriptor(f"wsh(sortedmulti(4,{ZPUB_A}/0/*,{ZPUB_B}/0/*))")


# -------- script construction --------

def test_redeem_script_opcodes():
    # OP_2 <33-byte pk> <33-byte pk> OP_2 OP_CHECKMULTISIG
    pk1 = b"\x02" + b"\x11" * 32
    pk2 = b"\x03" + b"\x22" * 32
    script = build_multisig_script(2, [pk1, pk2])
    assert script[0] == 0x52  # OP_2 (M)
    assert script[1] == 0x21  # push 33
    assert script[2:35] == pk1
    assert script[35] == 0x21  # push 33
    assert script[36:69] == pk2
    assert script[69] == 0x52  # OP_2 (N)
    assert script[70] == 0xAE  # OP_CHECKMULTISIG
    assert len(script) == 71


def test_build_script_rejects_invalid_threshold():
    pk = b"\x02" + b"\x11" * 32
    with pytest.raises(ValueError):
        build_multisig_script(0, [pk])
    with pytest.raises(ValueError):
        build_multisig_script(2, [pk])  # M > N
    with pytest.raises(ValueError):
        build_multisig_script(1, [pk] * 17)  # N > 16


# -------- derivation --------

def test_p2wsh_address_format_and_determinism():
    ms = parse_descriptor(DESC_2OF3_WSH)
    a = derive_multisig_address(ms, "receive", 0)
    b = derive_multisig_address(ms, "receive", 0)
    assert a == b
    # P2WSH bech32 address: hrp 'bc', witness v0, 32-byte program → 62 chars total
    assert a.startswith("bc1q")
    assert len(a) == 62


def test_receive_chain_differs_from_change():
    ms = parse_descriptor(DESC_2OF3_WSH)
    assert (
        derive_multisig_address(ms, "receive", 0)
        != derive_multisig_address(ms, "change", 0)
    )


def test_indices_differ():
    ms = parse_descriptor(DESC_2OF3_WSH)
    assert (
        derive_multisig_address(ms, "receive", 0)
        != derive_multisig_address(ms, "receive", 1)
    )


def test_sortedmulti_independent_of_input_order():
    """sortedmulti(M, A, B, C) and sortedmulti(M, C, B, A) must yield the same address."""
    desc_forward = f"wsh(sortedmulti(2,{ZPUB_A}/0/*,{ZPUB_B}/0/*,{ZPUB_C}/0/*))"
    desc_reverse = f"wsh(sortedmulti(2,{ZPUB_C}/0/*,{ZPUB_B}/0/*,{ZPUB_A}/0/*))"
    ms_fwd = parse_descriptor(desc_forward)
    ms_rev = parse_descriptor(desc_reverse)
    assert derive_multisig_address(ms_fwd, "receive", 0) == derive_multisig_address(
        ms_rev, "receive", 0
    )


def test_unsorted_multi_depends_on_input_order():
    """Plain multi(...) is order-sensitive. Reversing the keys must change the address."""
    desc_forward = f"wsh(multi(2,{ZPUB_A}/0/*,{ZPUB_B}/0/*,{ZPUB_C}/0/*))"
    desc_reverse = f"wsh(multi(2,{ZPUB_C}/0/*,{ZPUB_B}/0/*,{ZPUB_A}/0/*))"
    a = derive_multisig_address(parse_descriptor(desc_forward), "receive", 0)
    b = derive_multisig_address(parse_descriptor(desc_reverse), "receive", 0)
    assert a != b


def test_p2sh_address_format():
    desc = f"sh(sortedmulti(2,{ZPUB_A}/0/*,{ZPUB_B}/0/*,{ZPUB_C}/0/*))"
    ms = parse_descriptor(desc)
    addr = derive_multisig_address(ms, "receive", 0)
    assert addr.startswith("3")
    # Validate base58check
    raw = Base58Decoder.CheckDecode(addr)
    assert raw[0] == 0x05  # mainnet P2SH version byte
    assert len(raw) == 21  # version + 20-byte hash160


def test_p2sh_p2wsh_address_format():
    desc = f"sh(wsh(sortedmulti(2,{ZPUB_A}/0/*,{ZPUB_B}/0/*,{ZPUB_C}/0/*)))"
    ms = parse_descriptor(desc)
    addr = derive_multisig_address(ms, "receive", 0)
    assert addr.startswith("3")
    raw = Base58Decoder.CheckDecode(addr)
    assert raw[0] == 0x05
    assert len(raw) == 21
    # Differs from plain sh(...) because the redeem script is OP_0 + sha256(witness_script)
    plain_sh_desc = f"sh(sortedmulti(2,{ZPUB_A}/0/*,{ZPUB_B}/0/*,{ZPUB_C}/0/*))"
    plain = derive_multisig_address(parse_descriptor(plain_sh_desc), "receive", 0)
    assert addr != plain
