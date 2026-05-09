"""Classify a transaction relative to the user's owned address set.

Categories:
- `internal`     all inputs owned AND all outputs owned (wallet→wallet move).
- `external_in`  some inputs not owned, net flow > 0 (someone paid us).
- `external_out` all inputs owned, some outputs not owned (we paid someone).
- `unknown`      mixed (e.g. coinjoin, payjoin) — surfaced for manual review.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Literal

Classification = Literal["internal", "external_in", "external_out", "unknown"]


@dataclass(frozen=True)
class IO:
    address: str | None
    amount_sats: int
    owned: bool


@dataclass(frozen=True)
class ClassifiedTx:
    classification: Classification
    net_owned_sats: int  # +ve = received, -ve = sent (excluding fee adjustments)
    owned_in_sats: int
    owned_out_sats: int


def classify(inputs: Iterable[IO], outputs: Iterable[IO]) -> ClassifiedTx:
    inputs = list(inputs)
    outputs = list(outputs)

    owned_in = sum(i.amount_sats for i in inputs if i.owned)
    owned_out = sum(o.amount_sats for o in outputs if o.owned)
    any_unowned_in = any(not i.owned for i in inputs)
    any_unowned_out = any(not o.owned for o in outputs)
    all_in_owned = bool(inputs) and not any_unowned_in
    all_out_owned = bool(outputs) and not any_unowned_out

    net = owned_out - owned_in

    if all_in_owned and all_out_owned:
        cls: Classification = "internal"
    elif any_unowned_in and not all_in_owned and net > 0:
        # We're receiving something from at least one external input
        cls = "external_in"
    elif all_in_owned and any_unowned_out:
        cls = "external_out"
    elif owned_in == 0 and owned_out > 0:
        # Pure receive (no inputs owned at all)
        cls = "external_in"
    else:
        cls = "unknown"

    return ClassifiedTx(
        classification=cls,
        net_owned_sats=net,
        owned_in_sats=owned_in,
        owned_out_sats=owned_out,
    )
