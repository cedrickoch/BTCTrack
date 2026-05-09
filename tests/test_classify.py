from __future__ import annotations

from btctrack.ledger.classify import IO, classify


def test_internal_transfer():
    """All inputs and outputs owned → internal."""
    inputs = [IO("addrA", 100_000, owned=True)]
    outputs = [
        IO("addrB", 60_000, owned=True),
        IO("addrA_change", 39_000, owned=True),
    ]
    res = classify(inputs, outputs)
    assert res.classification == "internal"


def test_external_in_receive_from_exchange():
    """Some external input, net positive → external_in."""
    inputs = [IO("exchange_addr", 200_000, owned=False)]
    outputs = [
        IO("our_recv", 195_000, owned=True),
        IO("exchange_change", 4_000, owned=False),
    ]
    res = classify(inputs, outputs)
    assert res.classification == "external_in"
    assert res.net_owned_sats == 195_000


def test_external_out_payment_to_third_party():
    """All inputs owned, some output not owned → external_out."""
    inputs = [IO("our_addr", 100_000, owned=True)]
    outputs = [
        IO("merchant", 70_000, owned=False),
        IO("our_change", 28_000, owned=True),
    ]
    res = classify(inputs, outputs)
    assert res.classification == "external_out"


def test_pure_receive_no_owned_inputs():
    """No inputs owned, output owned → external_in (e.g. our first deposit)."""
    inputs = [IO("someone", 50_000, owned=False)]
    outputs = [IO("ours", 49_000, owned=True), IO("theirs_change", 500, owned=False)]
    res = classify(inputs, outputs)
    assert res.classification == "external_in"


def test_unknown_coinjoin_like():
    """Mixed owned/unowned both sides with net zero or odd flow → unknown."""
    inputs = [
        IO("our1", 100_000, owned=True),
        IO("their1", 100_000, owned=False),
    ]
    outputs = [
        IO("our2", 100_000, owned=True),
        IO("their2", 100_000, owned=False),
    ]
    res = classify(inputs, outputs)
    assert res.classification == "unknown"


def test_external_in_with_some_owned_inputs():
    """If we contribute some inputs but receive net positive, still external_in."""
    inputs = [
        IO("our_existing", 10_000, owned=True),
        IO("payer", 100_000, owned=False),
    ]
    outputs = [IO("our_new", 109_000, owned=True)]
    res = classify(inputs, outputs)
    assert res.classification == "external_in"
    assert res.net_owned_sats == 99_000
