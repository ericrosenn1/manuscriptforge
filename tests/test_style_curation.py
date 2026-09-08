from __future__ import annotations

import csv
import json
from pathlib import Path

from typer.testing import CliRunner

from manuscriptforge.cli import app
from manuscriptforge.style.curation import coverage_strength
from manuscriptforge.utils.io import read_json, read_yaml, write_yaml


def _prep_project(tmp_path: Path) -> Path:
    project_dir = tmp_path / "pilot_project"
    result = CliRunner().invoke(app, ["prep-pilot", str(project_dir)])
    assert result.exit_code == 0, result.output
    return project_dir


def _write_style_file(project_dir: Path, mode: str, filename: str, text: str) -> Path:
    path = project_dir / "style_corpus" / mode / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _first_chunk_id(project_dir: Path) -> str:
    rows = _jsonl(project_dir / "planning" / "intake" / "style_chunks" / "style_chunks_registry.jsonl")
    assert rows
    return str(rows[0]["chunk_id"])


def _toy_methods_text(sentence: str = "We used versioned scripts and cautious reporting.") -> str:
    return (
        "Methods\n\n"
        f"{sentence} "
        "The analysis retained intermediate artifacts, documented software versions, "
        "and summarized quality checks before interpretation.\n"
    )


def test_build_style_chunk_registry_creates_registry_files(tmp_path: Path) -> None:
    project_dir = _prep_project(tmp_path)
    _write_style_file(project_dir, "academic_manuscript", "methods.md", _toy_methods_text())

    result = CliRunner().invoke(
        app,
        ["build-style-chunk-registry", str(project_dir), "--mode", "academic_manuscript"],
    )

    assert result.exit_code == 0, result.output
    folder = project_dir / "planning" / "intake" / "style_chunks"
    assert (folder / "style_chunks_registry.csv").exists()
    assert (folder / "style_chunks_registry.jsonl").exists()
    assert (folder / "style_chunk_decisions.jsonl").exists()
    assert (folder / "style_chunk_review.md").exists()
    assert (folder / "style_chunk_review.xlsx").exists()
    assert (folder / "style_chunk_warnings.md").exists()
    assert _jsonl(folder / "style_chunks_registry.jsonl")


def test_chunk_registry_preserves_approval_decisions_after_rebuild(tmp_path: Path) -> None:
    project_dir = _prep_project(tmp_path)
    _write_style_file(project_dir, "academic_manuscript", "methods.md", _toy_methods_text())
    runner = CliRunner()
    assert runner.invoke(app, ["build-style-chunk-registry", str(project_dir)]).exit_code == 0
    chunk_id = _first_chunk_id(project_dir)
    result = runner.invoke(
        app,
        [
            "style-chunk-approve",
            str(project_dir),
            "--chunk-id",
            chunk_id,
            "--approve",
            "--approve-for",
            "style_profile",
            "--note",
            "keep this toy chunk",
        ],
    )
    assert result.exit_code == 0, result.output

    result = runner.invoke(app, ["build-style-chunk-registry", str(project_dir)])

    assert result.exit_code == 0, result.output
    rows = _jsonl(project_dir / "planning" / "intake" / "style_chunks" / "style_chunks_registry.jsonl")
    row = next(item for item in rows if item["chunk_id"] == chunk_id)
    assert row["approval_status"] == "approved"
    assert "style_profile" in row["approved_for"]
    assert "keep this toy chunk" in row["notes"]


def test_style_chunk_report_flags_short_chunks(tmp_path: Path) -> None:
    project_dir = _prep_project(tmp_path)
    _write_style_file(project_dir, "academic_manuscript", "abstract.md", "Abstract\n\nBrief toy note.\n")
    runner = CliRunner()
    assert runner.invoke(app, ["build-style-chunk-registry", str(project_dir)]).exit_code == 0
    result = runner.invoke(app, ["style-chunk-report", str(project_dir)])

    assert result.exit_code == 0, result.output
    report = read_json(project_dir / "planning" / "intake" / "style_chunks" / "style_chunk_report.json")
    assert report["findings"]["very_short_chunks"]


def test_style_chunk_approve_can_approve_one_chunk(tmp_path: Path) -> None:
    project_dir = _prep_project(tmp_path)
    _write_style_file(project_dir, "academic_manuscript", "methods.md", _toy_methods_text())
    runner = CliRunner()
    assert runner.invoke(app, ["build-style-chunk-registry", str(project_dir)]).exit_code == 0
    chunk_id = _first_chunk_id(project_dir)

    result = runner.invoke(app, ["style-chunk-approve", str(project_dir), "--chunk-id", chunk_id, "--approve"])

    assert result.exit_code == 0, result.output
    rows = _jsonl(project_dir / "planning" / "intake" / "style_chunks" / "style_chunks_registry.jsonl")
    assert next(item for item in rows if item["chunk_id"] == chunk_id)["approval_status"] == "approved"
    decisions = _jsonl(project_dir / "planning" / "intake" / "style_chunks" / "style_chunk_decisions.jsonl")
    assert decisions[-1]["status"] == "approved"


def test_style_chunk_approve_can_exclude_all_chunks_from_file(tmp_path: Path) -> None:
    project_dir = _prep_project(tmp_path)
    source = _write_style_file(project_dir, "academic_manuscript", "methods.md", _toy_methods_text())
    runner = CliRunner()
    assert runner.invoke(app, ["build-style-chunk-registry", str(project_dir)]).exit_code == 0
    source_file = source.relative_to(project_dir).as_posix()

    result = runner.invoke(app, ["style-chunk-approve", str(project_dir), "--source-file", source_file, "--exclude"])

    assert result.exit_code == 0, result.output
    rows = _jsonl(project_dir / "planning" / "intake" / "style_chunks" / "style_chunks_registry.jsonl")
    assert rows
    assert all(row["approval_status"] == "excluded" for row in rows if row["source_file"] == source_file)


def test_profile_style_excludes_explicitly_excluded_chunks(tmp_path: Path) -> None:
    project_dir = _prep_project(tmp_path)
    keep = _write_style_file(project_dir, "academic_manuscript", "keep.md", _toy_methods_text("We documented each step."))
    drop = _write_style_file(project_dir, "academic_manuscript", "drop.md", _toy_methods_text("This older style should be excluded."))
    runner = CliRunner()
    assert runner.invoke(app, ["build-style-chunk-registry", str(project_dir)]).exit_code == 0
    result = runner.invoke(
        app,
        ["style-chunk-approve", str(project_dir), "--source-file", drop.relative_to(project_dir).as_posix(), "--exclude"],
    )
    assert result.exit_code == 0, result.output

    result = runner.invoke(app, ["profile-style", str(project_dir)])

    assert result.exit_code == 0, result.output
    profile = read_json(project_dir / "outputs" / "latest" / "style_profile.json")
    assert keep.relative_to(project_dir).as_posix() in profile["corpus_files"]
    assert drop.relative_to(project_dir).as_posix() not in profile["corpus_files"]


def test_profile_style_uses_only_approved_chunks_when_required(tmp_path: Path) -> None:
    project_dir = _prep_project(tmp_path)
    keep = _write_style_file(project_dir, "academic_manuscript", "keep.md", _toy_methods_text("We documented each step."))
    drop = _write_style_file(project_dir, "academic_manuscript", "drop.md", _toy_methods_text("This unapproved file remains out."))
    config = read_yaml(project_dir / "project.yaml")
    config.setdefault("style", {}).setdefault("curation", {})["require_chunk_approval"] = True
    write_yaml(project_dir / "project.yaml", config)
    runner = CliRunner()
    assert runner.invoke(app, ["build-style-chunk-registry", str(project_dir)]).exit_code == 0
    result = runner.invoke(
        app,
        [
            "style-chunk-approve",
            str(project_dir),
            "--source-file",
            keep.relative_to(project_dir).as_posix(),
            "--approve",
            "--approve-for",
            "style_profile",
        ],
    )
    assert result.exit_code == 0, result.output

    result = runner.invoke(app, ["profile-style", str(project_dir)])

    assert result.exit_code == 0, result.output
    profile = read_json(project_dir / "outputs" / "latest" / "style_profile.json")
    assert keep.relative_to(project_dir).as_posix() in profile["corpus_files"]
    assert drop.relative_to(project_dir).as_posix() not in profile["corpus_files"]


def test_style_coverage_report_marks_absent_sparse_usable_strong(tmp_path: Path) -> None:
    assert coverage_strength(0) == "absent"
    assert coverage_strength(1) == "sparse"
    assert coverage_strength(3) == "usable"
    assert coverage_strength(8) == "strong"
    project_dir = _prep_project(tmp_path)
    _write_style_file(project_dir, "academic_manuscript", "methods.md", _toy_methods_text())
    runner = CliRunner()
    assert runner.invoke(app, ["build-style-chunk-registry", str(project_dir)]).exit_code == 0
    chunk_id = _first_chunk_id(project_dir)
    assert runner.invoke(app, ["style-chunk-approve", str(project_dir), "--chunk-id", chunk_id, "--approve"]).exit_code == 0

    result = runner.invoke(app, ["style-coverage-report", str(project_dir), "--mode", "academic_manuscript"])

    assert result.exit_code == 0, result.output
    data = read_json(project_dir / "planning" / "intake" / "style_coverage_report.json")
    coverage = {row["section_type"]: row["coverage_strength"] for row in data["coverage"]}
    assert coverage["methods"] == "sparse"
    assert coverage["abstract"] == "absent"
    assert (project_dir / "planning" / "intake" / "style_coverage_matrix.csv").exists()


def test_build_style_cards_creates_cards_and_short_examples(tmp_path: Path) -> None:
    project_dir = _prep_project(tmp_path)
    long_body = (
        "This deliberately long private-style example sentence is repeated for truncation checks. " * 10
    ).strip()
    _write_style_file(project_dir, "academic_manuscript", "methods.md", f"Methods\n\n{long_body}\n")

    result = CliRunner().invoke(app, ["build-style-cards", str(project_dir), "--mode", "academic_manuscript"])

    assert result.exit_code == 0, result.output
    cards_dir = project_dir / "planning" / "intake" / "style_cards"
    assert (cards_dir / "academic_manuscript__methods.md").exists()
    assert (cards_dir / "academic_manuscript__overall.md").exists()
    manifest = read_json(cards_dir / "style_cards_manifest.json")
    assert len(manifest["cards"]) == 8
    methods_card = (cards_dir / "academic_manuscript__methods.md").read_text(encoding="utf-8")
    assert long_body not in methods_card
    assert "..." in methods_card


def test_prep_style_modes_creates_folder_structure(tmp_path: Path) -> None:
    project_dir = _prep_project(tmp_path)
    runner = CliRunner()
    result = runner.invoke(app, ["prep-style-modes", str(project_dir)])

    assert result.exit_code == 0, result.output
    for mode in [
        "academic_manuscript",
        "coursework_explanatory",
        "response_to_reviewers",
        "journal_cover_letter",
        "grant_or_proposal",
        "review_article",
        "journal_adapted_academic",
    ]:
        assert (project_dir / "style_corpus" / mode / "README.md").exists()
    result = runner.invoke(app, ["intake-scan", str(project_dir), "--write"])
    assert result.exit_code == 0, result.output
    summary = read_json(project_dir / "planning" / "intake" / "intake_summary.json")
    assert summary["writing_sample_count"] == 0


def test_prep_journal_adapter_creates_empty_scaffold_without_web(tmp_path: Path) -> None:
    project_dir = _prep_project(tmp_path)
    result = CliRunner().invoke(app, ["prep-journal-adapter", str(project_dir), "--journal", "Example Journal"])

    assert result.exit_code == 0, result.output
    root = project_dir / "planning" / "journal_adapters"
    adapter = root / "example-journal"
    assert (root / "journal_adapter_inventory.csv").exists()
    assert (adapter / "instructions_for_authors.md").exists()
    assert (adapter / "adapter_config.yaml").exists()


def test_coursework_chunks_remain_isolated_from_academic_mode(tmp_path: Path) -> None:
    project_dir = _prep_project(tmp_path)
    _write_style_file(project_dir, "academic_manuscript", "methods.md", _toy_methods_text())
    _write_style_file(
        project_dir,
        "coursework_explanatory",
        "homework.md",
        "Methods\n\nI will explain each concept step by step for this assignment.\n",
    )

    result = CliRunner().invoke(
        app,
        ["build-style-chunk-registry", str(project_dir), "--mode", "academic_manuscript"],
    )

    assert result.exit_code == 0, result.output
    rows = _jsonl(project_dir / "planning" / "intake" / "style_chunks" / "style_chunks_registry.jsonl")
    assert rows
    assert {row["style_mode"] for row in rows} == {"academic_manuscript"}
    assert not any("coursework_explanatory" in row["source_file"] for row in rows)
