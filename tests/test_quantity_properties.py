"""Fixed-seed bounded property checks; no stochastic oracle or new dependency."""

import random
from decimal import Decimal

from manuscriptforge.evidence.quantities import (
    canonical_number,
    quantity_proposal_supported,
    unsupported_quantities,
)

UNITS = [
    "mg",
    "kg",
    "mg/kg",
    "mg/(kg min)",
    "m^2",
    "m2",
    "m/s",
    "m/s^2",
    "µg",
    "mW",
    "MW",
    "kg m^-2",
    "records",
    "%",
    "% w/v",
    "N m",
]


def test_fixed_seed_quantity_preservation_and_mutations():
    rng = random.Random(20261031)
    assertions = 0
    for index in range(320):
        integer = rng.randrange(2, 9000)
        fraction = rng.randrange(1, 999)
        formats = [
            str(integer),
            f"{integer}.{fraction:03d}",
            f".{fraction:03d}",
            f"{integer}e-3",
            f"{integer:,}.50",
        ]
        literal = formats[index % len(formats)]
        literal = ["", "+", "-", "−", "- ", "− "][index % 6] + literal
        value = canonical_number(literal)
        unit = UNITS[index % len(UNITS)]
        source = f"Gauge A reads {literal}{'' if index % 3 == 0 else ' '}{unit}."
        context = (source, value, unit)
        assert quantity_proposal_supported(value, unit, source), context
        assert not unsupported_quantities(source, source), context
        assert not quantity_proposal_supported(str(-Decimal(value)), unit, source), context
        assert not quantity_proposal_supported(value, "seconds", source), context
        plain = format(abs(Decimal(value)), "f")
        prefix = plain.split(".")[0]
        if Decimal(prefix) == Decimal(value):
            prefix = prefix[:-1] or "0"
        suffix = plain.replace(".", "")[-2:]
        if Decimal(suffix) == Decimal(value):
            suffix = "0"
        assert not quantity_proposal_supported(prefix, unit, source), context
        assert not quantity_proposal_supported(suffix, unit, source), context
        bounded = f"Gauge A reads at most {literal} {unit}."
        assert not quantity_proposal_supported(value, unit, bounded), context
        assert quantity_proposal_supported(f"at most {value}", unit, bounded), context
        other = str(Decimal(value) + Decimal("1.75"))
        interval = f"Gauge A reads {literal} to {other} {unit}."
        assert not quantity_proposal_supported(value, unit, interval), context
        assert quantity_proposal_supported(f"{value} to {other}", unit, interval), context
        assertions += 10
    assert assertions == 3200


def test_truncated_compound_units_and_identifier_suffixes_are_rejected():
    rng = random.Random(107)
    pairs = [
        ("mg/kg", "mg"),
        ("mg/(kg min)", "mg"),
        ("m^2", "m"),
        ("m2", "m"),
        ("m/s", "m"),
        ("% w/v", "%"),
        ("N m", "N"),
    ]
    for full, partial in pairs:
        for _ in range(15):
            value = str(rng.randrange(20, 900))
            assert not quantity_proposal_supported(value, partial, f"Gauge A reads {value} {full}.")
            assert not quantity_proposal_supported(
                value, None, f"record-{value} contains 3 entries."
            )
