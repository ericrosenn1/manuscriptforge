"""Synthetic numerical boundary regressions; no empirical scientific data."""

import pytest

from manuscriptforge.llm.extraction import (
    ExtractionSource,
    FactExtractionRequest,
    FactExtractionResponse,
    FactProposal,
    validate_fact_proposals,
)
from manuscriptforge.llm.grounded import (
    GroundedCitation,
    GroundedClaim,
    GroundedEvidence,
    GroundedParagraph,
    GroundedSectionRequest,
    GroundedSectionResponse,
    validate_grounded_response,
)
from manuscriptforge.utils.hashing import sha256_text


def extraction_valid(text, value, unit):
    request = FactExtractionRequest(
        sources=[ExtractionSource(source_id="source-a", text=text, content_hash=sha256_text(text))]
    )
    response = FactExtractionResponse(
        facts=[
            FactProposal(
                source_id="source-a",
                subject="Gauge A",
                predicate="reading",
                value=value,
                unit=unit,
                quote=text,
            )
        ]
    )
    return validate_fact_proposals(request, response).validation.valid


def grounded_valid(text, candidate, *, cite=False):
    request = GroundedSectionRequest(
        section_name="Results",
        claims=[
            GroundedClaim(
                claim_id="claim-a",
                claim_text=text,
                evidence_ids=["source-a"],
                citation_ids=["cite-a"] if cite else [],
                support_strength="direct",
            )
        ],
        evidence=[
            GroundedEvidence(
                evidence_id="source-a",
                text=text,
                source_path="input.txt",
                content_hash=sha256_text(text),
            )
        ],
        citations=[
            GroundedCitation(
                citation_id="cite-a",
                title="Background reference",
                authors=["Alex Smith"],
                year="2020",
            )
        ]
        if cite
        else [],
    )
    response = GroundedSectionResponse(
        section_name="Results",
        paragraphs=[
            GroundedParagraph(
                text=candidate, claim_ids=["claim-a"], citation_ids=["cite-a"] if cite else []
            )
        ],
    )
    return validate_grounded_response(request, response).valid


@pytest.mark.parametrize(
    "text,value,unit",
    [
        ("Gauge A reads 10-20 volts.", "10", None),
        ("Gauge A reads less than 10 volts.", "10", "volts"),
        ("Gauge A reads − 1.25 volts.", "1.25", "volts"),
        ("Gauge A reads 12 mg/kg.", "12", "mg"),
        ("Gauge A reads 12 m^2.", "12", "m"),
        ("Gauge A reads 1,2345 volts.", "2345", "volts"),
    ],
)
def test_source_only_guard_rejects_quantity_changes(text, value, unit):
    assert not extraction_valid(text, value, unit)


@pytest.mark.parametrize(
    "source,candidate",
    [
        ("Gauge A reads 1.23mg.", "Gauge A reads 1 mg."),
        ("Gauge A reads −1.25 volts.", "Gauge A reads 1.25 volts."),
        ("Gauge A reads 12 mg.", "Gauge A reads 12 kg."),
        ("Gauge A reads 10-20 volts.", "Gauge A reads 20 volts."),
    ],
)
def test_grounded_guard_rejects_quantity_changes(source, candidate):
    assert not grounded_valid(source, candidate)


def test_citation_year_cannot_license_a_new_measurement():
    assert not grounded_valid(
        "Archive A was inspected.", "Archive A contains 2020 records.", cite=True
    )


def test_known_author_year_and_existing_section_references_remain_valid():
    assert grounded_valid(
        "Archive A was inspected.", "Smith (2020) describes the archive.", cite=True
    )
    assert grounded_valid(
        "See Section 2 for the archive description.", "See Section 2 for the archive description."
    )


@pytest.mark.parametrize(
    "source", ["Gauge A reads 1.23mg.", "Gauge A reads 12 mg/kg.", "Gauge A reads 10-20 volts."]
)
def test_unchanged_quantitative_prose_is_preserved(source):
    assert grounded_valid(source, source)


def test_legacy_quantity_protocol_is_explicit_and_does_not_replace_new_default():
    text = "Gauge A reads 12 mg/kg."
    request = FactExtractionRequest(
        sources=[ExtractionSource(source_id="source-a", text=text, content_hash=sha256_text(text))]
    )
    response = FactExtractionResponse(
        facts=[
            FactProposal(
                source_id="source-a",
                subject="Gauge A",
                predicate="reading",
                value="12",
                unit="mg",
                quote=text,
            )
        ]
    )
    current = validate_fact_proposals(request, response)
    historical = validate_fact_proposals(request, response, quantity_protocol="2.0")
    assert not current.validation.valid
    assert current.validation.quantity_protocol == "2.1"
    assert historical.validation.valid
    assert historical.validation.quantity_protocol == "2.0"
