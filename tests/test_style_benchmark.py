from __future__ import annotations

from pathlib import Path
from shutil import copytree, rmtree

from typer.testing import CliRunner

from manuscriptforge.cli import app
from manuscriptforge.config import initialize_project
from manuscriptforge.pipeline.workflow import run_build_style_eval, run_draft, run_style_benchmark
from manuscriptforge.utils.io import read_json, read_yaml, write_json, write_yaml


def _copy_clean_example(tmp_path: Path) -> Path:
    src = Path("tests/fixtures/synthetic_project")
    project_dir = tmp_path / "project_minimal"
    copytree(src, project_dir)
    rmtree(project_dir / "outputs", ignore_errors=True)
    (project_dir / "outputs").mkdir()
    for name in ["style_memory.json", "style_preferences.jsonl"]:
        path = project_dir / name
        if path.exists():
            path.unlink()
    return project_dir


def _metric(scores: dict, name: str) -> dict:
    for metric in scores["metric_scores"]:
        if metric["metric_name"] == name:
            return metric
    raise AssertionError(f"Missing metric {name}")


def test_style_benchmark_command_writes_outputs_on_example_copy(tmp_path: Path) -> None:
    project_dir = _copy_clean_example(tmp_path)
    run_build_style_eval(project_dir)
    run_draft(project_dir)

    runner = CliRunner()
    result = runner.invoke(app, ["style-benchmark", str(project_dir)])
    assert result.exit_code == 0, result.output
    runs = sorted((project_dir / "outputs" / "runs").iterdir())
    benchmarked = [run for run in runs if (run / "style_benchmark_scores.json").exists()]
    assert benchmarked
    run_dir = benchmarked[-1]
    assert (run_dir / "style_benchmark_report.md").exists()
    assert (run_dir / "style_benchmark_scores.json").exists()
    assert (run_dir / "style_benchmark_details.xlsx").exists()
    assert (run_dir / "style_benchmark_manifest.json").exists()


def test_style_benchmark_handles_missing_style_memory(tmp_path: Path) -> None:
    project_dir = _copy_clean_example(tmp_path)
    run_dir = run_draft(project_dir)
    run_style_benchmark(project_dir)
    scores = read_json(run_dir / "style_benchmark_scores.json")
    assert _metric(scores, "style_memory_alignment_score")["status"] == "insufficient_data"


def test_style_benchmark_sparse_profile_marks_section_insufficient(tmp_path: Path) -> None:
    project_dir = _copy_clean_example(tmp_path)
    run_dir = run_draft(project_dir)
    profile = read_json(run_dir / "style_profile.json")
    profile["section_features"]["methods"] = {"chunk_count": 0}
    write_json(run_dir / "style_profile.json", profile)
    run_style_benchmark(project_dir, section_filter="methods")
    scores = read_json(run_dir / "style_benchmark_scores.json")
    assert scores["section_scores"]["methods"]["status"] == "insufficient_data"


def test_style_benchmark_empty_style_corpus_reports_insufficient_data(tmp_path: Path) -> None:
    project_dir = _copy_clean_example(tmp_path)
    for path in (project_dir / "style_corpus").glob("*"):
        if path.is_file():
            path.unlink()
    run_dir = run_draft(project_dir)
    run_style_benchmark(project_dir)
    scores = read_json(run_dir / "style_benchmark_scores.json")
    assert any(
        metric["status"] == "insufficient_data"
        for metric in scores["metric_scores"]
        if metric["metric_name"].endswith("_style_score")
    )


def test_style_benchmark_generic_phrase_penalty_is_applied(tmp_path: Path) -> None:
    project_dir = _copy_clean_example(tmp_path)
    run_dir = run_draft(project_dir)
    manuscript = read_json(run_dir / "manuscript.json")
    manuscript["abstract"] += " It is important to note that this draft uses generic phrasing."
    write_json(run_dir / "manuscript.json", manuscript)
    manuscript_md = run_dir / "manuscript.md"
    manuscript_md.write_text(
        manuscript_md.read_text(encoding="utf-8")
        + "\n\nIt is important to note that this draft uses generic phrasing.\n",
        encoding="utf-8",
    )
    run_style_benchmark(project_dir)
    scores = read_json(run_dir / "style_benchmark_scores.json")
    generic = _metric(scores, "generic_phrase_penalty")
    assert generic["status"] == "warning"
    assert generic["normalized_score"] < 1


def test_style_benchmark_normalizes_weights(tmp_path: Path) -> None:
    project_dir = _copy_clean_example(tmp_path)
    config = read_yaml(project_dir / "project.yaml")
    config["style_benchmark"] = {
        "enabled": True,
        "weights": {
            "section_similarity": 1,
            "style_memory_alignment": 1,
            "generic_phrase_avoidance": 1,
            "causal_hedging_alignment": 1,
            "citation_integration": 1,
            "traceability": 1,
        },
    }
    write_yaml(project_dir / "project.yaml", config)
    run_dir = run_draft(project_dir)
    run_style_benchmark(project_dir)
    manifest = read_json(run_dir / "style_benchmark_manifest.json")
    assert any("normalized" in warning for warning in manifest["warnings"])
    assert abs(sum(manifest["normalized_weights"].values()) - 1) < 0.00001


def test_style_benchmark_previous_compare_detects_changes(tmp_path: Path) -> None:
    project_dir = _copy_clean_example(tmp_path)
    first = run_draft(project_dir)
    assert first.exists()
    second = run_draft(project_dir)
    manuscript = read_json(second / "manuscript.json")
    manuscript["abstract"] += " It is important to note that this added sentence is generic."
    write_json(second / "manuscript.json", manuscript)
    (second / "manuscript.md").write_text(
        (second / "manuscript.md").read_text(encoding="utf-8")
        + "\n\nIt is important to note that this added sentence is generic.\n",
        encoding="utf-8",
    )

    run_style_benchmark(project_dir, compare_run_ref="previous")
    scores = read_json(second / "style_benchmark_scores.json")
    assert scores["compared_run_id"] == first.name
    assert scores["comparison"]["generic_phrase_count_change"] > 0


def test_style_benchmark_initialized_empty_project_runs_without_api(tmp_path: Path) -> None:
    project_dir = tmp_path / "empty_style_project"
    initialize_project(project_dir)
    run_dir = run_draft(project_dir)
    run_style_benchmark(project_dir)
    scores = read_json(run_dir / "style_benchmark_scores.json")
    assert scores["overall_score"] >= 0
