from __future__ import annotations

import csv
import json
from pathlib import Path

from typer.testing import CliRunner

from manuscriptforge.cli import app
from manuscriptforge.config import validate_project
from manuscriptforge.utils.io import read_json


def _prep_project(tmp_path: Path) -> Path:
    project_dir = tmp_path / "pilot_project"
    result = CliRunner().invoke(app, ["prep-pilot", str(project_dir)])
    assert result.exit_code == 0, result.output
    return project_dir


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _write_style_sample(project_dir: Path, mode: str, filename: str, text: str) -> Path:
    path = project_dir / "style_corpus" / mode / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_intake_scan_creates_data_assets_jsonl(tmp_path: Path) -> None:
    project_dir = _prep_project(tmp_path)
    result = CliRunner().invoke(app, ["intake-scan", str(project_dir), "--write"])
    assert result.exit_code == 0, result.output
    assets_path = project_dir / "planning" / "intake" / "data_assets.jsonl"
    assert assets_path.exists()
    assert _jsonl(assets_path)


def test_intake_scan_does_not_count_style_readme_as_writing_sample(tmp_path: Path) -> None:
    project_dir = _prep_project(tmp_path)
    result = CliRunner().invoke(app, ["intake-scan", str(project_dir), "--write"])
    assert result.exit_code == 0, result.output
    summary = read_json(project_dir / "planning" / "intake" / "intake_summary.json")
    assert summary["writing_sample_count"] == 0
    assets = _jsonl(project_dir / "planning" / "intake" / "data_assets.jsonl")
    assert not any(asset["asset_type"] == "writing_sample" and "README_style_corpus.md" in asset["relative_path"] for asset in assets)


def test_nested_academic_style_folder_assigns_academic_mode(tmp_path: Path) -> None:
    project_dir = _prep_project(tmp_path)
    _write_style_sample(
        project_dir,
        "academic_manuscript",
        "methods.md",
        "Methods\n\nWe used versioned scripts and reproducible tables for this analysis.\n",
    )
    result = CliRunner().invoke(app, ["intake-scan", str(project_dir), "--write"])
    assert result.exit_code == 0, result.output
    rows = _csv_rows(project_dir / "planning" / "intake" / "writing_samples_registry.csv")
    assert rows[0]["style_mode"] == "academic_manuscript"


def test_nested_coursework_style_folder_assigns_coursework_mode(tmp_path: Path) -> None:
    project_dir = _prep_project(tmp_path)
    _write_style_sample(
        project_dir,
        "coursework_explanatory",
        "homework.md",
        "This explanation walks through the concept step by step for a course assignment.\n",
    )
    result = CliRunner().invoke(app, ["intake-scan", str(project_dir), "--write"])
    assert result.exit_code == 0, result.output
    rows = _csv_rows(project_dir / "planning" / "intake" / "writing_samples_registry.csv")
    assert rows[0]["style_mode"] == "coursework_explanatory"


def test_intake_report_identifies_missing_result_tables(tmp_path: Path) -> None:
    project_dir = _prep_project(tmp_path)
    result = CliRunner().invoke(app, ["intake-report", str(project_dir)])
    assert result.exit_code == 0, result.output
    report = read_json(project_dir / "planning" / "intake" / "intake_report.json")
    assert report["result_table_count"] == 0
    assert any("No result tables" in warning for warning in report["warnings"])


def test_style_mode_summary_detects_no_academic_samples(tmp_path: Path) -> None:
    project_dir = _prep_project(tmp_path)
    result = CliRunner().invoke(app, ["style-mode-summary", str(project_dir)])
    assert result.exit_code == 0, result.output
    summary = read_json(project_dir / "planning" / "intake" / "style_mode_summary.json")
    assert summary["selected_mode"] == "academic_manuscript"
    assert summary["selected_sample_count"] == 0
    assert any("No `academic_manuscript`" in warning for warning in summary["warnings"])


def test_style_mode_summary_reports_toy_academic_sample(tmp_path: Path) -> None:
    project_dir = _prep_project(tmp_path)
    _write_style_sample(
        project_dir,
        "academic_manuscript",
        "discussion.md",
        "Discussion\n\nTogether, these toy observations suggest a cautious interpretation.\n",
    )
    result = CliRunner().invoke(app, ["style-mode-summary", str(project_dir)])
    assert result.exit_code == 0, result.output
    summary = read_json(project_dir / "planning" / "intake" / "style_mode_summary.json")
    assert summary["selected_sample_count"] == 1
    assert summary["mode_counts"]["academic_manuscript"] == 1


def test_validate_warns_for_scaffold_source_list_and_data_dictionary_only(tmp_path: Path) -> None:
    project_dir = _prep_project(tmp_path)
    result = validate_project(project_dir)
    assert result.ok
    assert any("source_list.md appears to be scaffold" in warning for warning in result.warnings)
    assert any("only contains data_dictionary.md" in warning for warning in result.warnings)


def test_profile_style_uses_active_mode_when_registry_exists(tmp_path: Path) -> None:
    project_dir = _prep_project(tmp_path)
    _write_style_sample(
        project_dir,
        "academic_manuscript",
        "methods.md",
        "Methods\n\nWe used versioned scripts and reproducible tables for this analysis.\n",
    )
    _write_style_sample(
        project_dir,
        "coursework_explanatory",
        "homework.md",
        "I will explain every concept step by step for this assignment.\n",
    )
    runner = CliRunner()
    assert runner.invoke(app, ["intake-scan", str(project_dir), "--write"]).exit_code == 0
    result = runner.invoke(app, ["profile-style", str(project_dir)])
    assert result.exit_code == 0, result.output
    profile = read_json(project_dir / "outputs" / "latest" / "style_profile.json")
    assert "style_corpus/academic_manuscript/methods.md" in profile["corpus_files"]
    assert "style_corpus/coursework_explanatory/homework.md" not in profile["corpus_files"]
    assert profile["global_features"]["style_mode_distribution"] == {"academic_manuscript": 1}


def test_homework_samples_do_not_affect_academic_mode_when_excluded(tmp_path: Path) -> None:
    project_dir = _prep_project(tmp_path)
    _write_style_sample(
        project_dir,
        "coursework_explanatory",
        "homework.md",
        "I will explain every concept step by step for this assignment.\n",
    )
    runner = CliRunner()
    assert runner.invoke(app, ["intake-scan", str(project_dir), "--write"]).exit_code == 0
    result = runner.invoke(app, ["profile-style", str(project_dir)])
    assert result.exit_code == 0, result.output
    profile = read_json(project_dir / "outputs" / "latest" / "style_profile.json")
    assert profile["corpus_files"] == []
    assert profile["global_features"]["sample_count"] == 0


def test_intake_approve_updates_registry_and_writes_decision(tmp_path: Path) -> None:
    project_dir = _prep_project(tmp_path)
    sample = _write_style_sample(
        project_dir,
        "academic_manuscript",
        "abstract.md",
        "Abstract\n\nThis toy prior abstract is cautious and locally supplied.\n",
    )
    runner = CliRunner()
    assert runner.invoke(app, ["intake-scan", str(project_dir), "--write"]).exit_code == 0
    assets = _jsonl(project_dir / "planning" / "intake" / "data_assets.jsonl")
    asset = next(item for item in assets if item["relative_path"] == sample.relative_to(project_dir).as_posix())
    result = runner.invoke(
        app,
        [
            "intake-approve",
            str(project_dir),
            "--asset-id",
            asset["asset_id"],
            "--approve-for",
            "style_profile",
            "--for-style-mode",
            "academic_manuscript",
            "--note",
            "toy approval",
        ],
    )
    assert result.exit_code == 0, result.output
    updated = _jsonl(project_dir / "planning" / "intake" / "data_assets.jsonl")
    approved = next(item for item in updated if item["asset_id"] == asset["asset_id"])
    assert approved["status"] == "approved"
    assert "style_profile" in approved["approved_for"]
    assert (project_dir / "planning" / "intake" / "intake_decisions.jsonl").exists()
