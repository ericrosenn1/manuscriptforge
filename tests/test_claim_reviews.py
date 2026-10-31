"""Explicit author decisions over original claims; no model calls."""

from __future__ import annotations

import json
from concurrent.futures import ProcessPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from manuscriptforge.models.claim import EvidenceItem, ScientificClaim
from manuscriptforge.research.claim_reviews import (
    ClaimReview,
    ClaimReviewLedgerError,
    append_claim_review,
    apply_claim_reviews,
    claim_review_fingerprints,
    load_claim_reviews,
)


def claim(identity="claim-a"):
    evidence = EvidenceItem(
        evidence_id="evidence-a",
        evidence_type="interpretation_note",
        source_path="inputs/interpretation_notes.md",
        source_label="Author interpretation",
        text="This synthetic result may justify further work.",
        metadata={"author_supplied": True},
        content_hash="original-source-digest",
    )
    return ScientificClaim(
        claim_id=identity,
        claim_text=evidence.text,
        normalized_claim=evidence.text.casefold(),
        section_target="Discussion",
        evidence_items=[evidence],
        evidence_type="interpretation_note",
        support_strength="needs_review",
        citation_ids=["citation-a"],
    )


def approve(path, item=None, **changes):
    params = dict(
        decision="approve",
        support_strength="indirect",
        reviewer="Author",
        reason="Compared claim with the original evidence.",
    )
    return append_claim_review(path, item or claim(), **(params | changes))


def test_append_roundtrip_unicode_and_preserve_prior_byte_prefix(tmp_path):
    path = tmp_path / "claim_reviews.jsonl"
    first = approve(path, reviewer="Renée", reason="µ-unit checked.\u2028Second sentence.")
    original = path.read_bytes()
    second = approve(path, reason="A further explicit review.")
    assert path.read_bytes().startswith(original)
    assert load_claim_reviews(path) == [first, second]
    assert first.reviewed_at.tzinfo is UTC
    assert path.with_name(path.name + ".lock").is_file()


def test_missing_ledger_read_is_read_only(tmp_path):
    path = tmp_path / "absent/claim_reviews.jsonl"
    assert load_claim_reviews(path) == []
    assert not path.parent.exists()


def test_applies_approved_support_route_and_language_only_to_copies(tmp_path):
    item = claim()
    original = item.model_dump(mode="json")
    review = approve(tmp_path / "reviews.jsonl", item, section_target="Introduction")
    output, report = apply_claim_reviews(
        [item], [review], allowed_sections={"Introduction", "Discussion"}
    )
    assert item.model_dump(mode="json") == original
    assert output[0] is not item and output[0].evidence_items[0] is not item.evidence_items[0]
    assert output[0].claim_text == item.claim_text
    assert output[0].support_strength == "indirect"
    assert output[0].section_target == "Introduction"
    assert not output[0].needs_human_review
    assert output[0].allowed_language
    assert report.approved == 1 and report.unreviewed == 0
    assert report.entries[0].review_id == review.review_id
    assert report.entries[0].reviewer == "Author"


def test_fingerprints_cover_exact_claim_and_source_but_ignore_evidence_order():
    original = claim()
    original.evidence_items.append(
        original.evidence_items[0].model_copy(update={"evidence_id": "evidence-b"})
    )
    reordered = original.model_copy(deep=True)
    reordered.evidence_items.reverse()
    assert claim_review_fingerprints(original) == claim_review_fingerprints(reordered)
    changed = original.model_copy(deep=True)
    changed.evidence_items[0].metadata["author_supplied"] = False
    assert (
        claim_review_fingerprints(original).evidence_sha256
        != claim_review_fingerprints(changed).evidence_sha256
    )
    assert (
        claim_review_fingerprints(original).claim_sha256
        == claim_review_fingerprints(changed).claim_sha256
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("claim_text", "Changed claim"),
        ("section_target", "Results"),
        ("support_strength", "direct"),
        ("citation_ids", ["citation-b"]),
        ("notes", "New original note"),
    ],
)
def test_changed_raw_claim_stales_approval(tmp_path, field, value):
    original = claim()
    review = approve(tmp_path / "reviews.jsonl", original)
    changed = original.model_copy(update={field: value}, deep=True)
    output, report = apply_claim_reviews([changed], [review])
    assert output[0] == changed
    assert report.stale == 1 and report.unreviewed == 1 and report.approved == 0


@pytest.mark.parametrize(
    "field,value",
    [
        ("text", "Changed source"),
        ("content_hash", "changed-hash"),
        ("source_path", "other.md"),
        ("metadata", {"row": {"n": 12}}),
    ],
)
def test_changed_evidence_stales_approval(tmp_path, field, value):
    original = claim()
    review = approve(tmp_path / "reviews.jsonl", original)
    changed = original.model_copy(deep=True)
    setattr(changed.evidence_items[0], field, value)
    output, report = apply_claim_reviews([changed], [review])
    assert output[0].support_strength == "needs_review"
    assert report.stale == 1


def test_latest_append_wins_even_with_earlier_timestamp(tmp_path):
    item = claim()
    first = approve(tmp_path / "reviews.jsonl", item)
    rejected = first.model_copy(
        update={
            "review_id": "b" * 32,
            "reviewed_at": first.reviewed_at - timedelta(days=1),
            "decision": "reject",
            "support_strength": "unsupported",
            "reason": "Unsupported interpretation",
        }
    )
    output, report = apply_claim_reviews([item], [first, rejected])
    assert output[0].support_strength == "unsupported"
    assert report.rejected == 1 and report.approved == 0


def test_stale_latest_does_not_resurrect_older_matching_approval(tmp_path):
    item = claim()
    first = approve(tmp_path / "reviews.jsonl", item)
    other = item.model_copy(update={"claim_text": "A changed claim"}, deep=True)
    latest = approve(tmp_path / "reviews.jsonl", other)
    output, report = apply_claim_reviews([item], [first, latest])
    assert output[0] == item and report.stale == 1 and report.approved == 0


def test_routes_must_match_exact_allowed_sections(tmp_path):
    item = claim()
    review = approve(tmp_path / "reviews.jsonl", item, section_target="Introduction")
    output, report = apply_claim_reviews([item], [review], allowed_sections={"introduction"})
    assert output[0] == item
    assert report.invalid_routes == 1 and report.unreviewed == 1


def test_orphan_reviews_and_unreviewed_claims_are_reported(tmp_path):
    review = approve(tmp_path / "reviews.jsonl")
    output, report = apply_claim_reviews([claim("claim-b")], [review])
    assert report.orphaned == 1 and report.orphan_review_ids == [review.review_id]
    assert report.unreviewed == 1
    assert output[0].needs_human_review


@pytest.mark.parametrize(
    "changes",
    [
        {"reviewer": " "},
        {"reason": "\n"},
        {"decision": "auto"},
        {"support_strength": "weak"},
        {"support_strength": "unsupported"},
        {"section_target": "\x00"},
    ],
)
def test_invalid_explicit_decision_does_not_create_ledger(tmp_path, changes):
    path = tmp_path / "reviews.jsonl"
    with pytest.raises(ValidationError):
        approve(path, **changes)
    assert not path.exists()


def test_reject_requires_unsupported_and_no_route(tmp_path):
    path = tmp_path / "reviews.jsonl"
    with pytest.raises(ValidationError):
        approve(path, decision="reject")
    with pytest.raises(ValidationError):
        approve(path, decision="reject", support_strength="unsupported", section_target="Results")


@pytest.mark.parametrize("damage", [b'{"broken":\n', b"{}\n", b"{}", b"\xff\n", b"\n"])
def test_malformed_or_truncated_ledgers_fail_closed_without_repair(tmp_path, damage):
    path = tmp_path / "reviews.jsonl"
    path.write_bytes(damage)
    with pytest.raises(ClaimReviewLedgerError):
        load_claim_reviews(path)
    with pytest.raises(ClaimReviewLedgerError):
        approve(path)
    assert path.read_bytes() == damage


def test_duplicate_json_keys_and_review_ids_fail_closed(tmp_path):
    path = tmp_path / "reviews.jsonl"
    approve(path)
    raw = path.read_bytes()
    path.write_bytes(raw + raw)
    with pytest.raises(ClaimReviewLedgerError):
        load_claim_reviews(path)
    path.write_bytes(raw.replace(b'"schema_version":1', b'"schema_version":1,"schema_version":1'))
    with pytest.raises(ClaimReviewLedgerError):
        load_claim_reviews(path)


@pytest.mark.parametrize("version", [2, True, "1"])
def test_unknown_or_noninteger_schema_rejected(tmp_path, version):
    path = tmp_path / "reviews.jsonl"
    review = approve(path).model_dump(mode="json")
    review["schema_version"] = version
    path.write_text(json.dumps(review) + "\n", encoding="utf-8")
    with pytest.raises(ClaimReviewLedgerError):
        load_claim_reviews(path)


def test_approval_cannot_create_missing_evidence(tmp_path):
    item = claim()
    item.evidence_items = []
    with pytest.raises(ClaimReviewLedgerError, match="evidence"):
        approve(tmp_path / "reviews.jsonl", item)


def test_imported_approval_without_evidence_is_not_applied(tmp_path):
    review = approve(tmp_path / "reviews.jsonl")
    empty = claim().model_copy(update={"evidence_items": []}, deep=True)
    review = review.model_copy(update=claim_review_fingerprints(empty).model_dump())
    output, report = apply_claim_reviews([empty], [review])
    assert output[0] == empty
    assert report.invalid_reviews == 1 and report.approved == 0 and report.unreviewed == 1


def test_atomic_publication_failure_preserves_prior_ledger(tmp_path, monkeypatch):
    path = tmp_path / "reviews.jsonl"
    approve(path)
    original = path.read_bytes()

    def fail(*args, **kwargs):
        raise OSError("simulated publication failure")

    monkeypatch.setattr("manuscriptforge.research.claim_reviews.write_text", fail)
    with pytest.raises(OSError):
        approve(path)
    assert path.read_bytes() == original


def _concurrent_review(args):
    path, index = args
    return approve(Path(path), reviewer=f"Reviewer {index}").review_id


def test_concurrent_process_appends_have_no_lost_or_duplicate_records(tmp_path):
    path = tmp_path / "reviews.jsonl"
    with ProcessPoolExecutor(max_workers=3) as pool:
        ids = list(pool.map(_concurrent_review, [(str(path), i) for i in range(9)]))
    records = load_claim_reviews(path)
    assert len(records) == 9
    assert {r.review_id for r in records} == set(ids)


def test_application_revalidates_mutated_records_and_duplicate_claim_ids(tmp_path):
    item = claim()
    review = approve(tmp_path / "reviews.jsonl", item)
    invalid = review.model_copy(update={"support_strength": "weak"})
    with pytest.raises(ClaimReviewLedgerError):
        apply_claim_reviews([item], [invalid])
    with pytest.raises(ClaimReviewLedgerError):
        apply_claim_reviews([item, item], [review])
    with pytest.raises(ClaimReviewLedgerError):
        apply_claim_reviews([item], [review, review])


def test_repeat_application_against_raw_claims_is_deterministic(tmp_path):
    original = claim()
    review = approve(tmp_path / "reviews.jsonl", original)
    first = apply_claim_reviews([original], [review])
    second = apply_claim_reviews([original], [review])
    assert first == second


def test_timestamp_requires_explicit_timezone(tmp_path):
    review = approve(tmp_path / "reviews.jsonl").model_dump()
    review["reviewed_at"] = datetime(2026, 10, 31)
    with pytest.raises(ValidationError):
        ClaimReview.model_validate(review)
