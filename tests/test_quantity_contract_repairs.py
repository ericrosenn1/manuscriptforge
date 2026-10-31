"""Synthetic regressions from the DEV output-contract audit; no held-out data."""

import random

import pytest
from test_quantity_guard_regressions import extraction_valid, grounded_valid

from manuscriptforge.evidence.quantities import (
    quantity_mentions,
    quantity_proposal_supported,
    unsupported_quantities,
)


@pytest.mark.parametrize("opening,closing", [("[", "]"), ("(", ")"), ("[", ")"), ("(", "]")])
@pytest.mark.parametrize("separator", [",", ", "])
def test_interval_delimiters_and_trailing_unit_are_one_quantity(opening, closing, separator):
    value = f"{opening}-0.125{separator}2.75{closing}"
    source = f"Gauge A reads {value} mV."
    mentions = quantity_mentions(source)
    assert len(mentions) == 1
    assert mentions[0].values == ("-0.125", "2.75")
    assert mentions[0].unit == "mV"
    assert mentions[0].relation == f"interval:{opening}{closing}"
    assert quantity_proposal_supported(value, "mV", source)
    assert extraction_valid(source, value, "mV")
    assert not quantity_proposal_supported(value, "V", source)
    assert not quantity_proposal_supported(value, None, source)
    assert not quantity_proposal_supported("-0.125", "mV", source)
    assert not quantity_proposal_supported(f"{opening}2.75, -0.125{closing}", "mV", source)
    assert not grounded_valid(source, f"Gauge A reads {value} V.")


def test_closed_open_and_plain_ranges_are_not_silently_equivalent():
    source = "Gauge A reads [1.25, 3.75] kg."
    for changed in ["(1.25, 3.75)", "[1.25, 3.75)", "(1.25, 3.75]", "1.25 to 3.75", "3.75"]:
        assert not quantity_proposal_supported(changed, "kg", source)
    assert not extraction_valid(source, "[1.25, 3.75]", "g")
    assert not grounded_valid(source, "Gauge A reads (1.25, 3.75) kg.")


def test_interval_sign_scientific_notation_and_approximation_remain_bound():
    source = "Gauge A reads approximately [− 1.25e-2, +2.50E-2] mW."
    assert quantity_proposal_supported("approximately [-0.0125, 0.025]", "mW", source)
    assert not quantity_proposal_supported("[-0.0125, 0.025]", "mW", source)
    assert not quantity_proposal_supported("approximately [0.0125, 0.025]", "mW", source)
    assert not quantity_proposal_supported("approximately [-0.0125, 0.025]", "MW", source)


def test_interval_with_grouped_thousands_and_nearby_scalar_keeps_source_order():
    source = "Gauge A reads 4 mV and [1,250, 2,500] mV, then 7 mV."
    mentions = quantity_mentions(source)
    assert [item.values for item in mentions] == [("4",), ("1.25E+3", "2.5E+3"), ("7",)]
    assert all(item.unit == "mV" for item in mentions)


def test_grounded_prose_counts_can_name_records_and_missing_labels():
    source = "record_count=10; missing_labels=1"
    candidate = "There were 10 records and 1 missing label."
    assert grounded_valid(source, candidate)
    assert not unsupported_quantities(candidate, source, narrative_counts=True)
    assert unsupported_quantities(candidate, source)  # Explicit unit comparison stays strict.
    assert not grounded_valid(source, "There were 11 records and 1 missing label.")
    assert not grounded_valid(source, "There were -10 records and 1 missing label.")


@pytest.mark.parametrize(
    "source,candidate",
    [
        ("reading=10", "The reading was 10 mg."),
        ("Gauge A reads 10 mg.", "Gauge A reads 10 records."),
        ("Gauge A reads 10 mW.", "Gauge A reads 10 MW."),
        ("Gauge A reads 10 mg/kg.", "Gauge A reads 10 mg."),
        ("reading=10", "The reading was 10 widgets."),
    ],
)
def test_count_prose_exception_does_not_disable_unit_checks(source, candidate):
    assert not grounded_valid(source, candidate)


def test_count_nouns_in_explicit_extraction_unit_field_remain_literal():
    source = "Gauge A records 10 entries."
    assert extraction_valid(source, "10", "entries")
    assert not extraction_valid(source, "10", "records")


def test_fixed_seed_interval_mutations_preserve_bounds_and_units():
    rng = random.Random(43107)
    for index in range(120):
        lower = f"-{rng.randrange(1, 900)}.{rng.randrange(1, 99):02d}"
        upper = f"{rng.randrange(1, 900)}.{rng.randrange(1, 99):02d}"
        unit = ["mV", "mg/kg", "MiB/s", "m^2", "N m"][index % 5]
        opening, closing = [("[", "]"), ("(", ")"), ("[", ")"), ("(", "]")][index % 4]
        value = f"{opening}{lower}, {upper}{closing}"
        source = f"Gauge A reads {value} {unit}."
        assert quantity_proposal_supported(value, unit, source)
        assert not quantity_proposal_supported(value, "seconds", source)
        assert not quantity_proposal_supported(value, None, source)
        assert not quantity_proposal_supported(lower, unit, source)
        assert not quantity_proposal_supported(f"{opening}{upper}, {lower}{closing}", unit, source)
        assert not quantity_proposal_supported(
            value.replace(lower, lower.lstrip("-")), unit, source
        )
