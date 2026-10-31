import pytest

from manuscriptforge.evidence.quantities import (
    ambiguous_numeric_fragments,
    mask_known_citations,
    quantity_mentions,
    quantity_proposal_supported,
    unsupported_quantities,
)


@pytest.mark.parametrize(
    "text,value,unit",
    [
        ("Gauge A reads 12mg.", "12", "mg"),
        ("Gauge A reads .5 volts.", "0.5", "volts"),
        ("Gauge A reads − 1.25 volts.", "-1.25", "volts"),
        ("Gauge A reads 12 mg/kg.", "12", "mg/kg"),
        ("Gauge A reads 12 m^2.", "12", "m^2"),
        ("Gauge A reads 12 mg/(kg min).", "12", "mg/(kg min)"),
        ("Gauge A reads 12 mg per kg.", "12", "mg per kg"),
        ("Gauge A reads 10-20 volts.", "10-20", "volts"),
        ("Gauge A reads less than 10 volts.", "less than 10", "volts"),
    ],
)
def test_whole_quantities_preserve_values_units_and_bounds(text, value, unit):
    assert quantity_proposal_supported(value, unit, text)
    assert not ambiguous_numeric_fragments(text)


@pytest.mark.parametrize(
    "text,value,unit",
    [
        ("Gauge A reads 10-20 volts.", "10", None),
        ("Gauge A reads less than 10 volts.", "10", "volts"),
        ("Gauge A reads − 1.25 volts.", "1.25", "volts"),
        ("Gauge A reads 12 mg/kg.", "12", "mg"),
        ("Gauge A reads 12 m^2.", "12", "m"),
        ("Gauge A reads 12 mg/(kg min).", "12", "mg"),
        ("Gauge A reads 12 mg per kg.", "12", "mg"),
        ("Gauge A reads 1,2345 volts.", "2345", "volts"),
    ],
)
def test_changed_or_partial_quantities_are_unsupported(text, value, unit):
    assert not quantity_proposal_supported(value, unit, text)


def test_numeric_prefixes_identifiers_and_malformed_fragments():
    assert unsupported_quantities("Gauge A reads 1 mg.", "Gauge A reads 1.23mg.")
    assert quantity_mentions("record-12 and specimen22") == []
    assert ambiguous_numeric_fragments("Gauge A reads 1,2345 volts.") == ["1,2345"]
    assert ambiguous_numeric_fragments("Gauge A reads 1.2.3 volts.") == ["1.2.3"]
    assert ambiguous_numeric_fragments("A reading was 12.") == []


def test_sections_and_known_author_year_citations_are_not_measurements():
    assert quantity_mentions("See Sections 2–4 and Figure 5.") == []
    assert ambiguous_numeric_fragments("See Sections 2–4 and Figure 5.") == []
    citations = [{"citation_id": "cite-a", "authors": ["Alex Smith"], "year": "2020"}]
    masked = mask_known_citations("Smith (2020) describes 12 records.", citations)
    assert len(quantity_mentions(masked)) == 1
    assert quantity_mentions(masked)[0].unit == "records"
    assert "2020 records" in mask_known_citations("Archive A contains 2020 records.", citations)


def test_ranges_uncertainty_and_fractions_are_not_scalar_measurements():
    examples = [
        ("between 10 and 20 volts", "range"),
        ("10 ± 2 mg", "uncertainty"),
        ("1/2 volts", "fraction"),
    ]
    for text, relation in examples:
        values = quantity_mentions(text)
        assert len(values) == 1 and values[0].relation == relation
        assert not quantity_proposal_supported("10" if relation != "fraction" else "1", None, text)
