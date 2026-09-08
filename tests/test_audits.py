from manuscriptforge.audit.citation_audit import audit_citations
from manuscriptforge.audit.overclaiming_audit import audit_overclaiming
from manuscriptforge.models.citation import CitationRecord
from manuscriptforge.models.claim import EvidenceItem, ScientificClaim
from manuscriptforge.models.manuscript import Manuscript, ManuscriptSection


def _weak_claim() -> ScientificClaim:
    evidence = EvidenceItem(
        evidence_id="ev_1",
        evidence_type="interpretation_note",
        source_path="inputs/interpretation_notes.md",
        source_label="notes",
        text="The pattern may indicate a signal.",
        metadata={},
        content_hash="abc",
    )
    return ScientificClaim(
        claim_id="clm_1",
        claim_text="The pattern may indicate a signal.",
        normalized_claim="the pattern may indicate a signal.",
        section_target="Discussion",
        evidence_items=[evidence],
        support_strength="weak",
        claim_type="interpretation",
    )


def test_overclaiming_audit_flags_strong_language_on_weak_claim() -> None:
    manuscript = Manuscript(
        title="Test",
        sections=[
            ManuscriptSection(
                section_name="Discussion",
                section_type="discussion",
                content="These data prove the mechanism.",
                claim_ids=["clm_1"],
            )
        ],
    )
    findings = audit_overclaiming(manuscript, [_weak_claim()])
    assert findings
    assert findings[0].severity == "serious"


def test_citation_audit_detects_placeholders() -> None:
    manuscript = Manuscript(
        title="Test",
        abstract="A claim needs [REF].",
        sections=[],
    )
    findings = audit_citations(manuscript, [], [])
    assert any(finding.category == "citation_placeholder" for finding in findings)


def test_citation_audit_detects_unknown_citation_id() -> None:
    manuscript = Manuscript(
        title="Test",
        sections=[
            ManuscriptSection(
                section_name="Introduction",
                section_type="introduction",
                content="Prior work motivates this claim [cit_missing].",
            )
        ],
    )
    findings = audit_citations(
        manuscript,
        [],
        [CitationRecord(citation_id="cit_known", title="Known source", year="2024")],
    )
    assert any("unknown citation ID" in finding.message for finding in findings)


def test_overclaiming_audit_flags_causal_language() -> None:
    manuscript = Manuscript(
        title="Test",
        sections=[
            ManuscriptSection(
                section_name="Discussion",
                section_type="discussion",
                content="The pattern caused clinical response.",
                claim_ids=["clm_1"],
            )
        ],
    )
    findings = audit_overclaiming(manuscript, [_weak_claim()])
    assert any("causal" in finding.message.lower() for finding in findings)
