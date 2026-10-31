"""Integration of bounded, explicit row metadata into grounded validation."""

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from manuscriptforge.evidence.table_bindings import TableRowBinding
from manuscriptforge.llm.grounded import (
    GroundedClaim,
    GroundedEvidence,
    GroundedParagraph,
    GroundedSectionRequest,
    GroundedSectionResponse,
    request_from_context,
    validate_grounded_response,
)
from manuscriptforge.models.claim import EvidenceItem, ScientificClaim


def request():
    rows = [("A", 10, 1), ("B", 12, 0), ("C", 8, 2)]
    return GroundedSectionRequest(
        section_name="Results",
        claims=[
            GroundedClaim(
                claim_id=f"claim-{label}",
                claim_text=f"collection={label}; record_count={count}; missing_labels={missing}",
                evidence_ids=[f"row-{label}"],
                support_strength="direct",
            )
            for label, count, missing in rows
        ],
        evidence=[
            GroundedEvidence(
                evidence_id=f"row-{label}",
                text=f"collection={label}; record_count={count}; missing_labels={missing}",
                source_path="counts.csv",
                content_hash=f"hash-{label}",
            )
            for label, count, missing in rows
        ],
        table_bindings=[
            TableRowBinding(
                evidence_id=f"row-{label}",
                table_id="counts",
                row_index=index,
                entity_labels={"collection": label},
                numeric_values={"record_count": str(count), "missing_labels": str(missing)},
            )
            for index, (label, count, missing) in enumerate(rows, 1)
        ],
    )


def validate(text, claim_ids=None):
    item = request()
    response = GroundedSectionResponse(
        section_name="Results",
        paragraphs=[
            GroundedParagraph(
                text=text,
                claim_ids=claim_ids or [claim.claim_id for claim in item.claims],
            )
        ],
    )
    return validate_grounded_response(item, response)


def test_correct_natural_counts_are_valid_but_field_semantics_require_review():
    report = validate(
        "Collection A had 10 records and 1 missing label; collection B had 12 records."
    )
    assert report.valid
    check = report.table_binding_checks[0]
    assert check.paragraph_index == 1
    assert check.report.status == "not_evaluated"
    assert check.report.evaluated_mentions == 3
    assert check.report.review_reasons
    assert report.requires_human_review


def test_cross_row_count_swap_is_rejected_even_when_all_numbers_exist_in_sources():
    report = validate("Collection A had 12 records; collection B had 10 records.")
    assert not report.valid
    assert report.table_binding_checks[0].report.status == "inconsistent"
    assert any("table row binding" in issue for issue in report.issues)


def test_exact_field_binding_is_checked_without_alias_inference():
    correct = validate("collection=A; record_count=10; missing_labels=1")
    assert correct.valid
    assert correct.table_binding_checks[0].report.status == "consistent"
    wrong = validate("collection=A; record_count=1; missing_labels=10")
    assert not wrong.valid


def test_only_cited_claim_rows_are_available_to_each_paragraph():
    report = validate("Collection B had 12 records.", claim_ids=["claim-A"])
    assert not report.valid
    assert report.table_binding_checks[0].report.status == "not_evaluated"


def test_unknown_and_duplicate_binding_evidence_ids_are_rejected():
    item = request().model_dump()
    item["table_bindings"][0]["evidence_id"] = "unknown"
    with pytest.raises(ValidationError, match="not present"):
        GroundedSectionRequest.model_validate(item)
    item = request().model_dump()
    item["table_bindings"].append(item["table_bindings"][0])
    with pytest.raises(ValidationError, match="Duplicate table binding"):
        GroundedSectionRequest.model_validate(item)


def test_context_adapter_preserves_metadata_backed_bindings(tmp_path, monkeypatch):
    monkeypatch.setattr("manuscriptforge.style.retrieval.get_style_exemplars", lambda *a, **k: [])
    evidence = EvidenceItem(
        evidence_id="table-row",
        evidence_type="table",
        source_path="counts.csv",
        source_label="counts",
        text="collection=A; record_count=10",
        content_hash="original-row-hash",
        metadata={
            "table_id": "counts",
            "row_index": 1,
            "row": {"collection": "A", "record_count": 10},
        },
    )
    claim = ScientificClaim(
        claim_id="claim-a",
        claim_text=evidence.text,
        normalized_claim=evidence.text,
        section_target="Results",
        evidence_items=[evidence],
        support_strength="direct",
    )
    context = SimpleNamespace(
        claims=[claim],
        citations=[],
        project_dir=tmp_path,
        style_profile=None,
        config=SimpleNamespace(style=SimpleNamespace(max_style_examples_per_section=0)),
    )
    result = request_from_context(context, "Results")
    assert result.evidence[0].content_hash == "original-row-hash"
    assert result.table_bindings[0].evidence_id == "table-row"
    assert result.table_bindings[0].entity_labels == {"collection": "A"}
    assert result.table_bindings[0].numeric_values == {"record_count": "1E+1"}
