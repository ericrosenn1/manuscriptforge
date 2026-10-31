from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from manuscriptforge.evaluation.models import EvaluationDataset
from manuscriptforge.evaluation.protocol import validate_dataset
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


def test_generator_reports_actual_file_bytes(tmp_path, monkeypatch, capsys):
    output = tmp_path / "revised.json"
    monkeypatch.setattr("sys.argv", ["generator", "--output", str(output)])
    main()
    reported = json.loads(capsys.readouterr().out)
    assert reported["sha256"] == hashlib.sha256(output.read_bytes()).hexdigest()
    assert b"\r\n" not in output.read_bytes()
