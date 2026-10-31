from __future__ import annotations

import hashlib
import json

import pytest

from manuscriptforge.evaluation.models import Assertion, CandidatePrediction
from manuscriptforge.evaluation.protocol import public_task, text_hash, validate_dataset
from manuscriptforge.evaluation.runner import run_comparison
from manuscriptforge.evaluation.scoring import paired_comparison, score_prediction, summarize
from manuscriptforge.evaluation.synthetic import build_synthetic_dataset


def test_grouped_splits_are_deterministic_and_labels_withheld():
    dataset = build_synthetic_dataset(families=5)
    assert dataset == build_synthetic_dataset(families=5)
    assert validate_dataset(dataset)["case_counts"] == {"train": 24, "dev": 8, "test": 8}
    task = public_task(dataset, dataset.cases[0].case_id).model_dump()
    assert set(task) == {"case_id", "task_kind", "query", "sources", "facts"}
    assert not {"expected_fact_ids", "relevant_source_ids", "should_abstain", "split"} & task.keys()


def test_leakage_hashes_and_unknown_fact_links_fail_closed():
    dataset = build_synthetic_dataset(families=5)
    tampered = dataset.model_copy(deep=True)
    tampered.sources[0].text += " Changed."
    with pytest.raises(ValueError, match="hash mismatch"):
        validate_dataset(tampered)
    tampered = dataset.model_copy(deep=True)
    tampered.cases[0].split = "test" if tampered.cases[1].split != "test" else "train"
    with pytest.raises(ValueError, match="leakage"):
        validate_dataset(tampered)
    tampered = dataset.model_copy(deep=True)
    tampered.facts[0].source_id = "missing"
    with pytest.raises(ValueError, match="Unknown fact source"):
        validate_dataset(tampered)


def test_normalized_duplicate_or_same_author_cannot_cross_partitions():
    dataset = build_synthetic_dataset(families=5)
    case_a = dataset.cases[0]
    case_b = next(case for case in dataset.cases if case.split != case_a.split)
    a = next(source for source in dataset.sources if source.source_id == case_a.source_ids[0])
    b = next(source for source in dataset.sources if source.source_id == case_b.source_ids[0])
    old_author = b.author_id
    b.author_id = a.author_id
    with pytest.raises(ValueError, match="leakage"):
        validate_dataset(dataset)
    b.author_id = old_author
    b.text = "  " + a.text.upper() + "  "
    b.sha256 = text_hash(b.text)
    with pytest.raises(ValueError, match="leakage"):
        validate_dataset(dataset)


def test_numeric_units_source_ids_and_rankings_are_independently_checked():
    dataset = build_synthetic_dataset(families=5)
    case = dataset.cases[0]
    fact = next(fact for fact in dataset.facts if fact.fact_id == case.expected_fact_ids[0])
    assertion = Assertion(**fact.model_dump(exclude={"locator"}))
    prediction = CandidatePrediction(
        case_id=case.case_id, assertions=[assertion], ranked_source_ids=[fact.source_id]
    )
    facts = {fact.fact_id: fact for fact in dataset.facts}
    assert score_prediction(case, prediction, facts)["complete_case_correct"]
    for changes in (
        {"value": "9999"},
        {"unit": "hours"},
        {"source_id": "unknown"},
        {"subject": "wrong-group"},
    ):
        changed = prediction.model_copy(deep=True)
        changed.assertions[0] = assertion.model_copy(update=changes)
        score = score_prediction(case, changed, facts)
        assert score["unsupported_assertions"] == 1
        assert not score["complete_case_correct"]
    changed = prediction.model_copy(update={"ranked_source_ids": list(reversed(case.source_ids))})
    assert score_prediction(case, changed, facts)["reciprocal_rank"] == 0.5
    duplicate = prediction.model_copy(
        update={"ranked_source_ids": [fact.source_id, fact.source_id]}
    )
    assert score_prediction(case, duplicate, facts)["reciprocal_rank"] is None


def test_empty_output_is_not_a_success_and_missing_costs_stay_missing():
    dataset = build_synthetic_dataset(families=5)
    case = dataset.cases[0]
    facts = {fact.fact_id: fact for fact in dataset.facts}
    row = score_prediction(case, CandidatePrediction(case_id=case.case_id), facts)
    assert not row["complete_case_correct"]
    summary = summarize([row])
    assert summary["factual_precision_micro"] is None
    assert summary["fact_recall_micro"] == 0
    assert summary["cost_usd"]["known_total"] is None
    assert summary["cost_usd"]["missing_cases"] == 1
    null_case = next(case for case in dataset.cases if case.should_abstain)
    abstain = CandidatePrediction(case_id=null_case.case_id, abstained=True)
    assert score_prediction(null_case, abstain, facts)["complete_case_correct"]


def test_calibration_and_paired_outcomes_include_denominators():
    dataset = build_synthetic_dataset(families=5)
    case = dataset.cases[0]
    row = score_prediction(case, CandidatePrediction(case_id=case.case_id, confidence=0.8), {})
    assert summarize([row])["brier_score"]["mean"] == pytest.approx(0.64)
    improved = dict(row, complete_case_correct=True)
    comparison = paired_comparison([row], [improved], repetitions=100)
    assert comparison["improved"] == 1
    assert comparison["delta"] == 1
    assert comparison["paired_cases"] == 1
    with pytest.raises(ValueError, match="same unique case IDs"):
        paired_comparison([row], [])


def test_run_manifest_is_versioned_complete_and_hash_verified(tmp_path):
    dataset = build_synthetic_dataset(families=5)
    seen = []

    def empty(task):
        seen.append(set(task.model_dump()))
        return CandidatePrediction(case_id=task.case_id)

    first = run_comparison(dataset, empty, empty, tmp_path)
    second = run_comparison(dataset, empty, empty, tmp_path)
    assert first != second
    assert all(keys == {"case_id", "task_kind", "query", "sources", "facts"} for keys in seen)
    summary = json.loads((first / "summary.json").read_text())
    assert summary["scientific_validation"] == "NOT_EVALUATED"
    assert summary["functional_suite_status"] == "FAIL"
    assert summary["paired"]["paired_cases"] == 8
    manifest = json.loads((first / "manifest.json").read_text())
    for row in manifest["files"]:
        data = (first / row["path"]).read_bytes()
        assert len(data) == row["bytes"]
        assert hashlib.sha256(data).hexdigest() == row["sha256"]
