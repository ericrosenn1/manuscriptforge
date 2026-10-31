from manuscriptforge.audit.citation_audit import audit_citations
from manuscriptforge.audit.no_invention_audit import audit_no_invention
from manuscriptforge.evidence.citation_mapper import map_citations_to_claims
from manuscriptforge.models.citation import CitationRecord
from manuscriptforge.models.claim import ScientificClaim
from manuscriptforge.models.manuscript import Manuscript, ManuscriptSection


def manuscript(text):
    return Manuscript(
        title="Synthetic test",
        sections=[ManuscriptSection(section_name="Results", section_type="results", content=text)],
    )


def test_citation_stopwords_do_not_establish_a_candidate_mapping():
    claim = ScientificClaim(
        claim_id="clm_test",
        claim_text="The device was calibrated.",
        normalized_claim="the device was calibrated",
        section_target="Introduction",
        claim_type="background",
        needs_citation=True,
    )
    unrelated = CitationRecord(
        citation_id="cit_unrelated", title="The survey was anonymous", year="2024"
    )
    assert map_citations_to_claims([claim], [unrelated])[0].citation_ids == []


def test_lexical_candidate_does_not_become_verified_support():
    claim = ScientificClaim(
        claim_id="clm_test",
        claim_text="Sensor calibration reduces drift.",
        normalized_claim="sensor calibration reduces drift",
        section_target="Introduction",
        claim_type="background",
        needs_citation=True,
    )
    source = CitationRecord(
        citation_id="cit_candidate", title="Sensor calibration and drift", year="2024"
    )
    mapped = map_citations_to_claims([claim], [source])[0]
    assert mapped.citation_ids == ["cit_candidate"]
    assert mapped.needs_citation and mapped.needs_human_review
    assert any(
        "unverified" in item.message
        for item in audit_citations(manuscript("Text."), [mapped], [source])
    )


def test_numeric_citation_without_explicit_mapping_is_flagged():
    findings = audit_citations(manuscript("The protocol is described in [99]."), [], [])
    assert any("no explicit numeric" in item.message for item in findings)


def test_numeric_context_catches_swaps_percent_loss_and_invented_year():
    cases = [
        (
            "Group A had 12 records and group B had 24 records.",
            "Group A had 24 records and group B had 12 records.",
        ),
        ("The proportion was 5%.", "The proportion was 5."),
        ("The procedure is described here.", "The procedure was validated in 2020."),
    ]
    for original, changed in cases:
        findings, _ = audit_no_invention(
            manuscript(changed), [], [], {"text_inputs": {"source": {"text": original}}}
        )
        assert findings, changed


def test_numeric_equivalence_preserves_sign_units_and_group():
    findings, _ = audit_no_invention(
        manuscript("Group A had 1.0e1 records."),
        [],
        [],
        {"text_inputs": {"source": {"text": "Group A had 10 records."}}},
    )
    assert not findings
