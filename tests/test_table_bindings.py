"""Metadata-backed row association; no provider calls or inferred field aliases."""

from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from manuscriptforge.evidence.table_bindings import (
    TableRowBinding,
    check_table_bindings,
    derive_table_bindings,
)
from manuscriptforge.ingest.table_ingest import ingest_result_tables
from manuscriptforge.models.claim import EvidenceItem


def evidence(label="A", count=10, missing=1, **metadata):
    return EvidenceItem(
        evidence_id=f"row-{label}",
        evidence_type="table",
        source_path="inputs/results_tables/counts.csv",
        source_label="counts",
        text="Deliberately unrelated text: Z had 999 records.",
        content_hash="recorded-hash",
        metadata={
            "table_id": "table-counts",
            "row_index": 1,
            "row": {"collection": label, "record_count": count, "missing_labels": missing},
            **metadata,
        },
    )


@pytest.fixture
def bindings():
    return derive_table_bindings([evidence(), evidence("B", 12, 0), evidence("C", 8, 2)])


def test_derive_uses_only_explicit_metadata():
    rows = derive_table_bindings([evidence()])
    assert rows[0].entity_labels == {"collection": "A"}
    assert rows[0].numeric_values == {"record_count": "1E+1", "missing_labels": "1"}
    assert rows[0].evidence_id == "row-A"
    assert "Z" not in str(rows)
    restored = TableRowBinding.model_validate_json(rows[0].model_dump_json())
    assert restored == rows[0]


def test_ingested_real_row_metadata_is_supported(tmp_path):
    source = tmp_path / "inputs/results_tables/counts.csv"
    source.parent.mkdir(parents=True)
    source.write_text("collection,record_count,missing_labels\nA,10,1\nB,12,0\nC,8,2\n")
    _, items = ingest_result_tables(tmp_path)
    result = derive_table_bindings(items)
    assert len(result) == 3
    assert [r.row_index for r in result] == [1, 2, 3]
    assert result[0].numeric_values["record_count"] == "1E+1"


@pytest.mark.parametrize(
    "metadata",
    [
        {},
        {"row_index": 0},
        {"row_index": True},
        {"table_id": ""},
        {"row": {"collection": "A", "count": float("nan")}},
        {"row": {"collection": "A", "count": float("inf")}},
        {"row": {"collection": "A", "count": "10"}},
        {"row": {"collection": "A", "count": True}},
        {"row": {"count": 10, "missing": 1}},
        {"row": {"collection": "001", "count": 10}},
    ],
)
def test_missing_or_ambiguous_metadata_not_invented(metadata):
    item = evidence(**metadata)
    if not metadata:
        item.metadata = {}
    assert derive_table_bindings([item]) == []


def test_non_table_and_duplicate_evidence_ignored():
    row = evidence()
    assert len(derive_table_bindings([row, row])) == 1
    assert derive_table_bindings([row.model_copy(update={"evidence_type": "manual"})]) == []


def test_large_signed_and_decimal_values_are_finite_without_float_coercion():
    rows = derive_table_bindings([evidence(count=10**500, missing=Decimal("-0.125"))])
    assert rows[0].numeric_values == {"record_count": "1E+500", "missing_labels": "-0.125"}
    assert derive_table_bindings([evidence(count=Decimal("NaN"))]) == []


def test_nullable_cells_do_not_become_zero():
    rows = derive_table_bindings([evidence(missing=None)])
    assert rows[0].numeric_values == {"record_count": "1E+1"}
    assert check_table_bindings("A had 0 records.", rows).status == "inconsistent"


@pytest.mark.parametrize("bad", [{"count": "NaN"}, {"count": "Infinity"}, {}])
def test_imported_binding_rejects_nonfinite_or_empty(bad):
    with pytest.raises(ValidationError):
        TableRowBinding(
            evidence_id="row-A",
            table_id="t",
            row_index=1,
            entity_labels={"collection": "A"},
            numeric_values=bad,
        )


def test_correct_natural_prose_preserves_review_for_unknown_field_aliases(bindings):
    report = check_table_bindings(
        "Collection A had 10 records and 1 missing label; "
        "collection B had 12 records and 0 missing labels; "
        "collection C had 8 records and 2 missing labels.",
        bindings,
    )
    assert report.issues == []
    assert report.evaluated_mentions == 6
    assert report.status == "not_evaluated"
    assert any("field association" in reason for reason in report.review_reasons)


def test_natural_A_B_count_swap_detected(bindings):
    report = check_table_bindings(
        "Collection A had 12 records and 1 missing label; "
        "collection B had 10 records and 0 missing labels; "
        "collection C had 8 records and 2 missing labels.",
        bindings,
    )
    assert report.status == "inconsistent"
    assert len(report.issues) == 2
    assert "row-A" in report.issues[0] and "row-B" in report.issues[1]


def test_exact_source_field_forms_and_concrete_swapped_probe(bindings):
    correct = (
        "In counts, collection A had record_count was 10, missing_labels was 1. "
        "In counts, collection B had record_count was 12, missing_labels was 0. "
        "In counts, collection C had record_count was 8, missing_labels was 2."
    )
    report = check_table_bindings(correct, bindings)
    assert report.status == "consistent" and not report.review_reasons
    assert report.evaluated_mentions == 6
    swapped = (
        correct.replace("was 10,", "was SWAP,")
        .replace("was 12,", "was 10,")
        .replace("was SWAP,", "was 12,")
    )
    bad = check_table_bindings(swapped, bindings)
    assert bad.status == "inconsistent" and len(bad.issues) == 2


def test_exact_field_names_detect_within_row_swap(bindings):
    bad = check_table_bindings("collection=A; record_count=1; missing_labels=10", bindings)
    assert len(bad.issues) == 2
    assert all("field" in issue for issue in bad.issues)


def test_numeric_membership_does_not_invent_field_semantics(bindings):
    report = check_table_bindings("A had 1 record and 10 missing labels.", bindings)
    assert report.issues == []
    assert report.status == "not_evaluated"
    assert report.review_reasons


@pytest.mark.parametrize(
    "text",
    [
        "10 records in A, 12 in B, and 8 in C.",
        "A and B had 10 records.",
        "A had 10 records and they had 12 records overall.",
        "A had 10 out of 30 records.",
        "They had 12 records.",
        "A had ten records.",
        "Collection D had 99 records.",
    ],
)
def test_unresolved_grammar_remains_review_not_false_validation(bindings, text):
    report = check_table_bindings(text, bindings)
    assert report.status == "not_evaluated"
    assert not report.issues
    assert report.review_reasons


def test_unrecognized_second_owner_never_absorbed_into_first_row(bindings):
    report = check_table_bindings("A had 10 records, B 12 records.", bindings)
    assert report.issues == []
    assert report.status == "not_evaluated"
    assert report.evaluated_mentions == 1


def test_nonunique_labels_across_tables_remain_ambiguous(bindings):
    other = bindings[0].model_copy(update={"table_id": "other", "evidence_id": "other-A"})
    report = check_table_bindings("A had 12 records.", [*bindings, other])
    assert report.status == "not_evaluated" and report.evaluated_mentions == 0
    assert not report.issues


def test_labels_do_not_match_substrings_or_case_aliases(bindings):
    for text in ("AA had 12 records.", "a had 12 records."):
        report = check_table_bindings(text, bindings)
        assert report.status == "not_evaluated" and not report.issues


def test_empty_bindings_and_text_are_explicitly_not_evaluated(bindings):
    assert check_table_bindings("A had 12", []).status == "not_evaluated"
    assert check_table_bindings("", bindings).status == "not_evaluated"


def test_decimal_points_and_negative_values_do_not_split_sentences():
    rows = derive_table_bindings([evidence(count=1.25, missing=-2)])
    report = check_table_bindings("collection=A; record_count=1.25; missing_labels=-2.", rows)
    assert report.status == "consistent"
    assert report.evaluated_mentions == 2


def test_numbers_outside_row_clause_are_not_silently_validated(bindings):
    report = check_table_bindings("There were 99 tables. A had 10 records.", bindings)
    assert report.status == "not_evaluated" and not report.issues
    assert report.evaluated_mentions == 1
    assert any("Some numeric mentions" in reason for reason in report.review_reasons)


@pytest.mark.parametrize(
    "text",
    [
        "A had 10 records meeting the <= 0.05 threshold.",
        "A had 10 records using a threshold of 0.05.",
        "A had 10 records at a cutoff: 0.05.",
        "A had 10 records using 0.05 as the threshold.",
    ],
)
def test_explicit_threshold_is_not_assumed_to_be_observed_row_value(bindings, text):
    report = check_table_bindings(text, bindings)
    assert not report.issues
    assert report.status == "not_evaluated"
    assert any("threshold context" in reason for reason in report.review_reasons)


def test_threshold_context_does_not_hide_wrong_observed_count(bindings):
    report = check_table_bindings("A had 12 records meeting the 0.05 threshold.", bindings)
    assert report.status == "inconsistent"
    assert len(report.issues) == 1
    assert "12 records" in report.issues[0]


def test_threshold_named_numeric_row_field_still_checked():
    rows = derive_table_bindings([evidence(row={"collection": "A", "threshold": 0.05})])
    good = check_table_bindings("collection=A; threshold=0.05", rows)
    bad = check_table_bindings("collection=A; threshold=0.06", rows)
    assert good.status == "consistent"
    assert bad.status == "inconsistent"


def test_ordinary_unbound_number_is_not_a_threshold_exception(bindings):
    report = check_table_bindings("A had 0.05 records.", bindings)
    assert report.status == "inconsistent"


@pytest.mark.parametrize(
    "value,threshold,valid",
    [("0.04", "0.05", True), ("0.04", "0.06", False), ("0.05", "0.05", False)],
)
def test_threshold_review_preserves_global_value_guard_and_exact_row_binding(
    value, threshold, valid
):
    from manuscriptforge.llm.grounded import (
        GroundedClaim,
        GroundedEvidence,
        GroundedParagraph,
        GroundedSectionRequest,
        GroundedSectionResponse,
        validate_grounded_response,
    )

    text = "Collection A had p_value=0.04, meeting the 0.05 threshold."
    item = GroundedSectionRequest(
        section_name="Results",
        claims=[
            GroundedClaim(
                claim_id="claim-a",
                claim_text=text,
                evidence_ids=["row-A"],
                support_strength="direct",
            )
        ],
        evidence=[
            GroundedEvidence(
                evidence_id="row-A", text=text, source_path="table.csv", content_hash="original"
            )
        ],
        table_bindings=derive_table_bindings([evidence(row={"collection": "A", "p_value": 0.04})]),
    )
    response = GroundedSectionResponse(
        section_name="Results",
        paragraphs=[
            GroundedParagraph(
                text=f"Collection A had p_value={value}, meeting the {threshold} threshold.",
                claim_ids=["claim-a"],
            )
        ],
    )
    report = validate_grounded_response(item, response)
    assert report.valid is valid
    if valid:
        assert report.table_binding_checks[0].report.status == "not_evaluated"
    elif threshold == "0.06":
        assert any("numeric literal" in issue for issue in report.issues)
    else:
        assert any("table row binding" in issue for issue in report.issues)
