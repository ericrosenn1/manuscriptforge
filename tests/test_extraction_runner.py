from __future__ import annotations

import json
import re

import pytest

from manuscriptforge.evaluation.extraction_runner import (
    evaluate_source_only,
    run_extraction_comparison,
)
from manuscriptforge.evaluation.synthetic import build_synthetic_dataset
from manuscriptforge.llm.extraction_adapter import FactExtractionCandidate
from manuscriptforge.llm.mock_provider import MockLLMProvider


def candidate():
    def fixture(prompt, schema):
        request = json.loads(prompt)["input_data"]
        assert set(request) == {"sources", "query", "max_facts"}
        first = request["sources"][0]
        matched = re.search(r"(collection-\d+) has (\d+) records\.", first["text"])
        if matched is None:
            return {"facts": [], "unresolved_questions": ["No directly parsed record count."]}
        return {
            "facts": [
                {
                    "source_id": first["source_id"],
                    "subject": matched[1],
                    "predicate": "count",
                    "value": matched[2],
                    "unit": "records",
                    "quote": matched[0],
                }
            ],
            "unresolved_questions": [],
        }

    return FactExtractionCandidate(MockLLMProvider(json_factory=fixture))


def test_runner_omits_gold_and_aligns_actual_proposals_to_private_facts():
    dataset = build_synthetic_dataset(families=5)
    inner = candidate()

    class Spy:
        candidate = inner

        def __call__(self, task):
            assert task.facts == []
            assert "predicate 'count'" in task.query
            return inner(task)

    row = evaluate_source_only(dataset, dataset.cases[0].case_id, Spy())
    assert row["complete_case_correct"]
    assert row["source_quote_grounding"]["source_reverified"]
    assert row["prediction"]["assertions"][0]["fact_id"].startswith("proposal_")
    assert row["model_input_contains_gold_facts"] is False


def test_stale_result_is_cleared_on_error():
    dataset = build_synthetic_dataset(families=5)
    inner = candidate()

    class SometimesFails:
        candidate = inner
        calls = 0

        def __call__(self, task):
            self.calls += 1
            if self.calls == 2:
                raise RuntimeError("Synthetic execution failure")
            return inner(task)

    wrapped = SometimesFails()
    first = evaluate_source_only(dataset, dataset.cases[0].case_id, wrapped)
    second = evaluate_source_only(dataset, dataset.cases[0].case_id, wrapped)
    assert first["extraction_result"] is not None
    assert second["extraction_result"] is None
    assert inner.last_result is None
    assert second["execution_error"] and not second["complete_case_correct"]


def test_quote_offsets_are_rechecked_independently_of_provider_validation():
    dataset = build_synthetic_dataset(families=5)
    inner = candidate()

    class Tamper:
        candidate = inner

        def __call__(self, task):
            prediction = inner(task)
            inner.last_result.facts[0].quote_start += 1
            return prediction

    row = evaluate_source_only(dataset, dataset.cases[0].case_id, Tamper())
    assert row["source_quote_grounding"]["status"] == "fail"
    assert not row["complete_case_correct"]


def test_progress_and_rows_survive_interruption(tmp_path):
    dataset = build_synthetic_dataset(families=5)
    inner = candidate()

    class Interrupt:
        candidate = inner
        calls = 0

        def __call__(self, task):
            self.calls += 1
            if self.calls == 3:
                raise KeyboardInterrupt
            return inner(task)

    shared = Interrupt()
    with pytest.raises(KeyboardInterrupt):
        run_extraction_comparison(dataset, shared, shared, tmp_path)
    run = next(tmp_path.iterdir())
    progress = json.loads((run / "progress.json").read_text())
    assert progress["status"] == "interrupted"
    assert progress["completed_calls"] == 2
    lines = sum(len(path.read_text().splitlines()) for path in run.glob("*.jsonl"))
    assert lines == 2


def test_completed_run_has_incremental_journals_and_verified_manifest(tmp_path):
    dataset = build_synthetic_dataset(families=5)
    run = run_extraction_comparison(dataset, candidate(), candidate(), tmp_path)
    progress = json.loads((run / "progress.json").read_text())
    assert progress["status"] == "completed" and progress["completed_calls"] == 16
    summary = json.loads((run / "summary.json").read_text())
    assert summary["model_input_contains_gold_facts"] is False
    assert summary["scientific_validation"] == "NOT_EVALUATED"
    assert summary["resources"]["baseline"]["cost_usd"]["known_total"] is None
    assert summary["source_files_changed_during_run"] == []
