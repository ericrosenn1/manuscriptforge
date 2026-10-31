from __future__ import annotations

import hashlib
import json

import pytest

from manuscriptforge.evaluation.models import CandidatePrediction
from manuscriptforge.evaluation.protocol import dataset_hash, validate_dataset
from manuscriptforge.evaluation.rescore import rescore_saved_run
from manuscriptforge.evaluation.runner import run_comparison
from manuscriptforge.evaluation.synthetic import as_retrieval_dataset_v11, build_synthetic_dataset


def test_answer_facet_allows_known_conflict_provenance_without_overwriting_legacy(tmp_path):
    dataset = build_synthetic_dataset(families=5)

    def abstain(task):
        return CandidatePrediction(
            case_id=task.case_id,
            abstained=True,
            ranked_source_ids=[source.source_id for source in task.sources],
        )

    original = run_comparison(dataset, abstain, abstain, tmp_path / "originals")
    (original / "campaign_metadata.json").write_text('{"changed_during_run":[]}')
    before = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in original.iterdir()
        if path.is_file()
    }
    changed = rescore_saved_run(original, tmp_path / "rescores")
    summary = json.loads((changed / "summary.json").read_text())
    assert summary["protocol_version"] == "1.1-rescore"
    assert summary["legacy"]["baseline"]["complete_case_rate"] == 0
    assert summary["answer_facet"]["baseline"]["complete_case_rate"] == 0.25
    assert summary["paired_answer_facet"]["delta"] == 0
    assert summary["model_calls"] == 0
    assert summary["campaign_metadata"]["path"] == "original/campaign_metadata.json"
    assert (changed / "original" / "campaign_metadata.json").read_bytes() == (
        original / "campaign_metadata.json"
    ).read_bytes()
    after = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in original.iterdir()
        if path.is_file()
    }
    assert after == before
    rows = [json.loads(line) for line in (changed / "baseline.jsonl").read_text().splitlines()]
    conflict = next(row for row in rows if row["stratum"] == "conflicting_evidence")
    assert not conflict["legacy_complete_case_correct"]
    assert conflict["answer_case_correct"]
    assert conflict["ranking_gold_changed"] is False


def test_unknown_ranked_source_still_invalidates_answer_facet(tmp_path):
    dataset = build_synthetic_dataset(families=5)

    def invalid(task):
        return CandidatePrediction(
            case_id=task.case_id, abstained=True, ranked_source_ids=["invented"]
        )

    original = run_comparison(dataset, invalid, invalid, tmp_path / "originals")
    changed = rescore_saved_run(original, tmp_path / "rescores")
    summary = json.loads((changed / "summary.json").read_text())
    assert summary["answer_facet"]["baseline"]["complete_case_rate"] == 0


def test_rescore_rejects_tampered_original_manifest(tmp_path):
    dataset = build_synthetic_dataset(families=5)

    def empty(task):
        return CandidatePrediction(case_id=task.case_id)

    original = run_comparison(dataset, empty, empty, tmp_path / "originals")
    with (original / "baseline.jsonl").open("a") as handle:
        handle.write("\n")
    with pytest.raises(ValueError, match="manifest mismatch"):
        rescore_saved_run(original, tmp_path / "rescores")


def test_new_ranking_dataset_versions_changed_labels_explicitly():
    original = build_synthetic_dataset(families=5)
    fingerprint = dataset_hash(original)
    new = as_retrieval_dataset_v11(original)
    assert new.revision == "1.1"
    validate_dataset(new)
    assert dataset_hash(original) == fingerprint
    old_conflict = next(case for case in original.cases if case.stratum == "conflicting_evidence")
    new_conflict = next(case for case in new.cases if case.case_id == old_conflict.case_id)
    assert old_conflict.relevant_source_ids == [] and old_conflict.should_abstain
    assert (
        new_conflict.relevant_source_ids == new_conflict.source_ids
        and not new_conflict.should_abstain
    )
