import json

from manuscriptforge.models.style import StyleProfile
from manuscriptforge.style.benchmark import (
    DEFAULT_WEIGHTS,
    _category_scores,
    _global_metrics,
    _latest_file,
    _overall_score,
    _score_style_eval,
    _section_metric,
)


def test_empty_manuscript_has_no_overall_style_score():
    metrics = _global_metrics("", {}, StyleProfile(), {}, {})
    assert _overall_score(_category_scores(metrics, []), DEFAULT_WEIGHTS) is None


def test_missing_numeric_targets_cannot_yield_perfect_section_similarity():
    profile = StyleProfile(section_features={"methods": {"chunk_count": 1}})
    result = _section_metric("methods", "A generic paragraph.", profile)
    assert result.score is None
    assert result.status == "insufficient_data"


def test_eval_ties_and_unrelated_section_text_are_inconclusive(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    pair = {
        "example_id": "pair",
        "section_type": "methods",
        "positive": "Alpha beta",
        "negative": "Gamma delta",
    }
    (run / "style_eval_pairs.jsonl").write_text(json.dumps(pair))
    rank = {
        "example_id": "classify",
        "task": "section_classification",
        "section_type": "methods",
        "text": "Alpha beta",
    }
    (run / "style_eval_rank_questions.jsonl").write_text(json.dumps(rank))
    for text in ("", "Unrelated words."):
        scores = _score_style_eval(tmp_path, run, {"methods": text}, text)
        assert all(not item.passed and item.status == "inconclusive" for item in scores)


def test_a_future_run_cannot_supply_an_old_runs_evaluation(tmp_path):
    old = tmp_path / "outputs" / "runs" / "old"
    future = tmp_path / "outputs" / "runs" / "future"
    old.mkdir(parents=True)
    future.mkdir()
    (future / "style_eval_pairs.jsonl").write_text('{"future":true}')
    assert _latest_file(tmp_path, "style_eval_pairs.jsonl", old) is None
