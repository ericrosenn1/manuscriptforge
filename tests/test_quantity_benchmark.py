from __future__ import annotations

import json
import re
from collections import Counter
from decimal import Decimal, InvalidOperation
from pathlib import Path

import pytest

from manuscriptforge.evaluation.models import EvaluationDataset
from manuscriptforge.evaluation.protocol import dataset_hash, validate_dataset
from scripts.generate_quantity_benchmark import build_quantity_dataset, main


def test_quantity_dataset_has_declared_coverage_and_disjoint_groups():
    dataset = build_quantity_dataset()
    assert len(dataset.cases) == 420
    assert len(dataset.sources) == 540
    assert len(dataset.facts) == 480
    assert validate_dataset(dataset)["case_counts"] == {"train": 252, "dev": 84, "test": 84}
    assert len(Counter(case.stratum for case in dataset.cases)) == 14
    assert set(Counter(case.stratum for case in dataset.cases).values()) == {30}
    assert sum(case.should_abstain for case in dataset.cases) == 60


def test_all_expected_values_are_literal_and_not_in_queries():
    dataset = build_quantity_dataset()
    sources = {source.source_id: source for source in dataset.sources}
    facts = {fact.fact_id: fact for fact in dataset.facts}
    for case in dataset.cases:
        for fact_id in case.expected_fact_ids:
            fact = facts[fact_id]
            text = sources[fact.source_id].text
            assert fact.subject in text
            assert fact.unit in text
            assert fact.value not in case.query
            try:
                expected = Decimal(fact.value)
            except InvalidOperation:
                assert fact.value in text
            else:
                normalized = re.sub(r"(?<=-)\s+(?=\d)", "", text.replace("−", "-"))
                values = re.findall(r"[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?", normalized)
                assert any(Decimal(value) == expected for value in values)


def test_source_instructions_are_data_and_never_gold_measurements():
    dataset = build_quantity_dataset()
    facts = {fact.fact_id: fact for fact in dataset.facts}
    for case in dataset.cases:
        if case.stratum == "source_instructions":
            assert len(case.expected_fact_ids) == 1
            assert facts[case.expected_fact_ids[0]].value != "999.9"
    assert all(source.synthetic for source in dataset.sources)


def test_published_dataset_is_reproducible():
    root = Path(__file__).resolve().parents[1]
    stored = EvaluationDataset.model_validate_json(
        (root / "manuscriptforge/data/benchmarks/quantities-v1.json").read_text(encoding="utf-8")
    )
    assert dataset_hash(stored) == dataset_hash(build_quantity_dataset())


def test_generator_refuses_to_replace_changed_existing_data(tmp_path, monkeypatch):
    output = tmp_path / "dataset.json"
    output.write_text(json.dumps({"retain": "existing data"}))
    monkeypatch.setattr("sys.argv", ["generator", "--output", str(output)])
    with pytest.raises(SystemExit, match="Existing dataset differs"):
        main()
    assert json.loads(output.read_text()) == {"retain": "existing data"}
