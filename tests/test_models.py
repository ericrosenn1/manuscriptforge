from manuscriptforge.models.claim import EvidenceItem, ScientificClaim
from manuscriptforge.models.project import ProjectConfig


def test_project_config_defaults() -> None:
    config = ProjectConfig()
    assert config.llm.provider == "mock"
    assert "Results" in config.manuscript.include_sections


def test_claim_model_accepts_required_fields() -> None:
    evidence = EvidenceItem(
        evidence_id="ev_1",
        evidence_type="table",
        source_path="inputs/results_tables/a.csv",
        source_label="a",
        text="A table row.",
        metadata={},
        content_hash="abc",
    )
    claim = ScientificClaim(
        claim_id="clm_1",
        claim_text="A result was reported.",
        normalized_claim="a result was reported.",
        section_target="Results",
        evidence_items=[evidence],
        support_strength="direct",
        claim_type="result",
    )
    assert claim.evidence_items[0].evidence_type == "table"
