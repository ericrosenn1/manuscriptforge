from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from manuscriptforge.evaluation.extraction_runner import (
    LABEL_HINTS,
    evaluate_source_only,
    run_extraction_comparison,
)
from manuscriptforge.evaluation.models import CandidatePrediction, EvaluationDataset
from manuscriptforge.evaluation.protocol import validate_dataset
from scripts.generate_quantity_benchmark import main as original_main
from scripts.generate_quantity_benchmark_v11 import main, revise_quantity_queries

ROOT = Path(__file__).resolve().parents[1]


def original():
    return EvaluationDataset.model_validate_json(
        (ROOT / "manuscriptforge/data/benchmarks/quantities-v1.json").read_text(encoding="utf-8")
    )


def test_query_revision_preserves_all_sources_facts_partitions_and_endpoints():
    before = original()
    snapshot = before.model_dump()
    after = revise_quantity_queries(before)
    assert before.model_dump() == snapshot
    assert after.sources == before.sources and after.facts == before.facts
    assert after.split_seed == before.split_seed
    assert after.dataset_id == "original-quantities-v1.1" and after.revision == "1.1"
    for old, new in zip(before.cases, after.cases, strict=True):
        assert old.query != new.query
        assert old.model_dump(exclude={"query"}) == new.model_dump(exclude={"query"})
    assert validate_dataset(after)["case_counts"] == {"train": 252, "dev": 84, "test": 84}


def test_revised_queries_separate_value_unit_and_require_numeric_abstention():
    dataset = revise_quantity_queries(original())
    facts = {fact.fact_id: fact for fact in dataset.facts}
    for case in dataset.cases:
        assert "Exclude measurement names, explanatory labels and units from value" in case.query
        assert "unit only in the separate unit field" in case.query
        assert "return facts=[]" in case.query
        assert "unresolved_questions" in case.query
        for identity in case.expected_fact_ids:
            fact = facts[identity]
            assert f"predicate exactly to '{fact.predicate}'" in case.query
            assert fact.value not in case.query
        if case.stratum == "source_attribution":
            assert "Use the primary log" in case.query


def test_revision_refuses_changed_original_query_and_overwrite(tmp_path, monkeypatch):
    dataset = original()
    dataset.cases[0].query += " Changed."
    with pytest.raises(ValueError, match="Unexpected original query"):
        revise_quantity_queries(dataset)
    output = tmp_path / "preserve.json"
    output.write_text("preserve", encoding="utf-8")
    monkeypatch.setattr("sys.argv", ["generator", "--output", str(output)])
    with pytest.raises(SystemExit, match="Existing dataset differs"):
        main()
    assert output.read_text() == "preserve"


@pytest.mark.parametrize("command", [main, original_main])
def test_generator_reports_actual_file_bytes(tmp_path, monkeypatch, capsys, command):
    output = tmp_path / "revised.json"
    monkeypatch.setattr("sys.argv", ["generator", "--output", str(output)])
    command()
    reported = json.loads(capsys.readouterr().out)
    assert reported["sha256"] == hashlib.sha256(output.read_bytes()).hexdigest()
    if command is main:
        assert b"\r\n" not in output.read_bytes()


def test_published_v11_matches_the_query_only_transformation():
    published = EvaluationDataset.model_validate_json(
        (ROOT / "manuscriptforge/data/benchmarks/quantities-v1.1.json").read_text(encoding="utf-8")
    )
    assert published == revise_quantity_queries(original())


@pytest.mark.parametrize(
    "scoring,hints", [("2.0-source-only", ""), ("2.1-quantities", LABEL_HINTS)]
)
def test_v11_rejects_conflicting_contract_before_any_candidate_call(tmp_path, scoring, hints):
    def forbidden(task):
        raise AssertionError("No candidate should be invoked")

    with pytest.raises(ValueError):
        run_extraction_comparison(
            revise_quantity_queries(original()),
            forbidden,
            forbidden,
            tmp_path,
            scoring_version=scoring,
            label_hints=hints,
        )
    assert not list(tmp_path.iterdir())


def test_v11_empty_hints_preserve_exact_query_and_are_recorded(tmp_path):
    dataset = revise_quantity_queries(original())
    queries = {case.case_id: case.query for case in dataset.cases}

    def spy(task):
        assert task.query == queries[task.case_id]
        assert task.facts == []
        return CandidatePrediction(case_id=task.case_id, abstained=True)

    run = run_extraction_comparison(
        dataset, spy, spy, tmp_path, scoring_version="2.1-quantities", label_hints=""
    )
    protocol = json.loads((run / "protocol.json").read_text())
    assert protocol["generic_label_hints"] == ""
    assert protocol["input_contract"]["dataset_revision"] == "1.1"
    assert protocol["input_contract"]["label_hints_sha256"] == hashlib.sha256(b"").hexdigest()
    assert protocol["runtime"]["python"] and protocol["runtime"]["pydantic"]
    row = json.loads((run / "baseline.jsonl").read_text().splitlines()[0])
    assert row["model_query_sha256"] == hashlib.sha256(queries[row["case_id"]].encode()).hexdigest()


def test_legacy_default_hints_still_append_without_changing_query(tmp_path):
    from manuscriptforge.evaluation.synthetic import build_synthetic_dataset

    dataset = build_synthetic_dataset(families=5)
    case = dataset.cases[0]

    def spy(task):
        assert task.query == case.query + LABEL_HINTS
        return CandidatePrediction(case_id=task.case_id, abstained=True)

    evaluate_source_only(dataset, case.case_id, spy)
