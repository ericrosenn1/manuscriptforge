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


def _stage_file(project_dir: Path, rel: str, text: str = "Methods\n\nWe used careful local procedures.\n") -> Path:
    path = project_dir / "planning" / "import_staging" / "incoming" / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _stage_binary(project_dir: Path, rel: str, data: bytes = b"%PDF-1.4\nDEMO BINARY\n") -> Path:
    path = project_dir / "planning" / "import_staging" / "incoming" / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _candidates(project_dir: Path) -> list[dict]:
    return _jsonl(project_dir / "planning" / "import_staging" / "manifests" / "import_candidates.jsonl")


def _first_import_id(project_dir: Path) -> str:
    rows = _candidates(project_dir)
    assert rows
    return str(rows[0]["import_id"])


def _write_metadata_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = sorted({key for row in rows for key in row})
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def test_prep_import_staging_creates_folders_and_templates(tmp_path: Path) -> None:
    project_dir = _prep_project(tmp_path)
    result = CliRunner().invoke(app, ["prep-import-staging", str(project_dir)])

    assert result.exit_code == 0, result.output
    root = project_dir / "planning" / "import_staging"
    for rel in [
        "README_import_staging.md",
        "incoming/writing_samples",
        "incoming/result_tables",
        "incoming/sources",
        "incoming/source_pdfs",
        "incoming/feedback",
        "incoming/miscellaneous",
        "manifests",
        "reports",
        "accepted",
        "rejected",
        "archive",
        "manifests/writing_sample_import_template.csv",
        "manifests/result_table_import_template.csv",
        "manifests/source_import_template.csv",
    ]:
        assert (root / rel).exists()


def test_import_scan_detects_writing_samples(tmp_path: Path) -> None:
    project_dir = _prep_project(tmp_path)
    _stage_file(project_dir, "writing_samples/academic_manuscript/methods.md")

    result = CliRunner().invoke(app, ["import-scan", str(project_dir), "--write"])

    assert result.exit_code == 0, result.output
    rows = _candidates(project_dir)
    assert rows[0]["detected_kind"] == "writing_sample"
    assert rows[0]["suggested_style_mode"] == "academic_manuscript"
    assert rows[0]["suggested_destination"] == "style_corpus/academic_manuscript/methods.md"


def test_import_scan_detects_result_tables(tmp_path: Path) -> None:
    project_dir = _prep_project(tmp_path)
    _stage_file(project_dir, "result_tables/results.csv", "feature,comparison,log2_fold_change,adjusted_p_value\nA,x,1.2,0.01\n")

    result = CliRunner().invoke(app, ["import-scan", str(project_dir), "--write"])

    assert result.exit_code == 0, result.output
    row = _candidates(project_dir)[0]
    assert row["detected_kind"] == "result_table"
    assert row["suggested_destination"] == "inputs/results_tables/results.csv"


def test_import_scan_detects_bibtex_and_source_files(tmp_path: Path) -> None:
    project_dir = _prep_project(tmp_path)
    _stage_file(project_dir, "sources/references.bib", "@article{toy,title={Toy},year={2024}}\n")
    _stage_file(project_dir, "sources/source_list.md", "# Sources\n\n- Toy source\n")

    result = CliRunner().invoke(app, ["import-scan", str(project_dir), "--write"])

    assert result.exit_code == 0, result.output
    kinds = {row["filename"]: row["detected_kind"] for row in _candidates(project_dir)}
    assert kinds["references.bib"] == "bibtex"
    assert kinds["source_list.md"] == "source_list"


def test_import_scan_detects_duplicates_by_hash(tmp_path: Path) -> None:
    project_dir = _prep_project(tmp_path)
    active = project_dir / "style_corpus" / "academic_manuscript" / "methods.md"
    active.parent.mkdir(parents=True, exist_ok=True)
    active.write_text("Methods\n\nIdentical staged content.\n", encoding="utf-8")
    _stage_file(project_dir, "writing_samples/academic_manuscript/methods_copy.md", "Methods\n\nIdentical staged content.\n")

    result = CliRunner().invoke(app, ["import-scan", str(project_dir), "--write"])

    assert result.exit_code == 0, result.output
    assert _candidates(project_dir)[0]["duplicate_status"] == "duplicate_same_hash"


def test_import_plan_writes_markdown_json_and_csv(tmp_path: Path) -> None:
    project_dir = _prep_project(tmp_path)
    _stage_file(project_dir, "writing_samples/academic_manuscript/methods.md")
    runner = CliRunner()
    assert runner.invoke(app, ["import-scan", str(project_dir), "--write"]).exit_code == 0

    result = runner.invoke(app, ["import-plan", str(project_dir), "--kind", "writing_sample", "--mode", "academic_manuscript"])

    assert result.exit_code == 0, result.output
    root = project_dir / "planning" / "import_staging"
    assert (root / "reports" / "import_plan.md").exists()
    assert (root / "reports" / "import_plan.json").exists()
    assert (root / "manifests" / "import_plan.csv").exists()


def test_import_apply_dry_run_does_not_copy_files(tmp_path: Path) -> None:
    project_dir = _prep_project(tmp_path)
    _stage_file(project_dir, "writing_samples/academic_manuscript/methods.md")
    runner = CliRunner()
    assert runner.invoke(app, ["import-scan", str(project_dir), "--write"]).exit_code == 0
    import_id = _first_import_id(project_dir)

    result = runner.invoke(app, ["import-apply", str(project_dir), "--import-id", import_id, "--copy", "--dry-run"])

    assert result.exit_code == 0, result.output
    assert not (project_dir / "style_corpus" / "academic_manuscript" / "methods.md").exists()


def test_import_apply_copy_imports_file_to_correct_destination(tmp_path: Path) -> None:
    project_dir = _prep_project(tmp_path)
    _stage_file(project_dir, "writing_samples/academic_manuscript/methods.md")
    runner = CliRunner()
    assert runner.invoke(app, ["import-scan", str(project_dir), "--write"]).exit_code == 0
    import_id = _first_import_id(project_dir)

    result = runner.invoke(app, ["import-apply", str(project_dir), "--import-id", import_id, "--copy"])

    assert result.exit_code == 0, result.output
    assert (project_dir / "style_corpus" / "academic_manuscript" / "methods.md").exists()
    assert _candidates(project_dir)[0]["import_status"] == "imported"


def test_import_apply_does_not_overwrite_existing_file_silently(tmp_path: Path) -> None:
    project_dir = _prep_project(tmp_path)
    destination = project_dir / "style_corpus" / "academic_manuscript" / "methods.md"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text("Existing active file.\n", encoding="utf-8")
    _stage_file(project_dir, "writing_samples/academic_manuscript/methods.md", "New staged file.\n")
    runner = CliRunner()
    assert runner.invoke(app, ["import-scan", str(project_dir), "--write"]).exit_code == 0
    import_id = _first_import_id(project_dir)

    result = runner.invoke(app, ["import-apply", str(project_dir), "--import-id", import_id, "--copy"])

    assert result.exit_code == 0, result.output
    assert destination.read_text(encoding="utf-8") == "Existing active file.\n"
    assert (project_dir / "style_corpus" / "academic_manuscript" / "methods_2.md").exists()


def test_import_reject_marks_candidate_rejected_and_keeps_source(tmp_path: Path) -> None:
    project_dir = _prep_project(tmp_path)
    source = _stage_file(project_dir, "writing_samples/academic_manuscript/old.md")
    runner = CliRunner()
    assert runner.invoke(app, ["import-scan", str(project_dir), "--write"]).exit_code == 0
    import_id = _first_import_id(project_dir)

    result = runner.invoke(app, ["import-reject", str(project_dir), "--import-id", import_id, "--reason", "old style"])

    assert result.exit_code == 0, result.output
    assert source.exists()
    assert _candidates(project_dir)[0]["import_status"] == "rejected"


def test_import_update_metadata_updates_candidate_fields_from_csv(tmp_path: Path) -> None:
    project_dir = _prep_project(tmp_path)
    staged = _stage_file(project_dir, "writing_samples/misc/sample.md")
    runner = CliRunner()
    assert runner.invoke(app, ["import-scan", str(project_dir), "--write"]).exit_code == 0
    metadata_csv = project_dir / "planning" / "import_staging" / "manifests" / "filled_writing.csv"
    _write_metadata_csv(
        metadata_csv,
        [
            {
                "source_path": staged.relative_to(project_dir).as_posix(),
                "destination_filename": "approved_methods.md",
                "style_mode": "academic_manuscript",
                "document_type": "methods",
                "privacy_reviewed": "true",
                "use_in_pilot": "true",
                "notes": "approved local toy sample",
            }
        ],
    )

    result = runner.invoke(app, ["import-update-metadata", str(project_dir), "--from-csv", str(metadata_csv)])

    assert result.exit_code == 0, result.output
    row = _candidates(project_dir)[0]
    assert row["suggested_style_mode"] == "academic_manuscript"
    assert row["suggested_document_type"] == "methods"
    assert row["suggested_destination"] == "style_corpus/academic_manuscript/approved_methods.md"
    assert row["privacy_status"] == "reviewed"
    assert row["import_status"] == "ready"


def test_intake_report_mentions_pending_staged_files(tmp_path: Path) -> None:
    project_dir = _prep_project(tmp_path)
    _stage_file(project_dir, "writing_samples/academic_manuscript/methods.md")
    runner = CliRunner()
    assert runner.invoke(app, ["import-scan", str(project_dir), "--write"]).exit_code == 0

    result = runner.invoke(app, ["intake-report", str(project_dir)])

    assert result.exit_code == 0, result.output
    report = (project_dir / "planning" / "intake" / "intake_report.md").read_text(encoding="utf-8")
    assert "Import staging contains files not yet imported into active project folders." in report


def test_validate_warns_or_infos_about_staged_ready_files_without_failing(tmp_path: Path) -> None:
    project_dir = _prep_project(tmp_path)
    staged = _stage_file(project_dir, "writing_samples/misc/sample.md")
    runner = CliRunner()
    assert runner.invoke(app, ["import-scan", str(project_dir), "--write"]).exit_code == 0
    metadata_csv = project_dir / "planning" / "import_staging" / "manifests" / "filled_writing.csv"
    _write_metadata_csv(
        metadata_csv,
        [
            {
                "source_path": staged.relative_to(project_dir).as_posix(),
                "style_mode": "academic_manuscript",
                "privacy_reviewed": "true",
                "use_in_pilot": "true",
            }
        ],
    )
    assert runner.invoke(app, ["import-update-metadata", str(project_dir), "--from-csv", str(metadata_csv)]).exit_code == 0

    result = validate_project(project_dir)

    assert result.ok
    assert any("Import staging has 1 ready-but-not-imported" in warning for warning in result.warnings)
    assert any("Import staging contains" in info for info in result.infos)


def test_import_summary_writes_empty_but_actionable_report(tmp_path: Path) -> None:
    project_dir = _prep_project(tmp_path)
    runner = CliRunner()
    assert runner.invoke(app, ["prep-import-staging", str(project_dir)]).exit_code == 0

    result = runner.invoke(app, ["import-summary", str(project_dir)])

    assert result.exit_code == 0, result.output
    data = read_json(project_dir / "planning" / "import_staging" / "reports" / "import_summary.json")
    assert data["candidate_count"] == 0
    assert data["suggested_next_actions"]


def test_import_doctor_works_on_empty_staging(tmp_path: Path) -> None:
    project_dir = _prep_project(tmp_path)
    runner = CliRunner()
    assert runner.invoke(app, ["prep-import-staging", str(project_dir)]).exit_code == 0

    result = runner.invoke(app, ["import-doctor", str(project_dir)])

    assert result.exit_code == 0, result.output
    data = read_json(project_dir / "planning" / "import_staging" / "reports" / "import_doctor.json")
    assert data["candidate_count"] == 0
    assert data["metadata_templates"]["writing_sample_import_template.csv"]
    assert data["suggested_next_actions"]


def test_import_scan_report_includes_table_like_candidate_rows(tmp_path: Path) -> None:
    project_dir = _prep_project(tmp_path)
    _stage_file(project_dir, "writing_samples/academic_manuscript/methods.md")

    result = CliRunner().invoke(app, ["import-scan", str(project_dir), "--write"])

    assert result.exit_code == 0, result.output
    report = (project_dir / "planning" / "import_staging" / "reports" / "import_scan_report.md").read_text(encoding="utf-8")
    assert "| import_id | filename | detected_kind |" in report
    assert "methods.md" in report


def test_content_privacy_scanner_flags_terms_without_recording_snippets(tmp_path: Path) -> None:
    project_dir = _prep_project(tmp_path)
    _stage_file(
        project_dir,
        "writing_samples/academic_manuscript/privacy_note.md",
        "DEMO only. patient Jane Example can be reached at jane.private@example.test.\n",
    )

    result = CliRunner().invoke(app, ["import-scan", str(project_dir), "--write"])

    assert result.exit_code == 0, result.output
    row = _candidates(project_dir)[0]
    assert row["privacy_scan_status"] == "scanned_text_snippet"
    assert "patient" in row["privacy_hits"]
    assert "email_address" in row["privacy_hits"]
    serialized = json.dumps(row)
    assert "Jane Example" not in serialized


def test_binary_pdf_content_is_not_deeply_scanned_by_default(tmp_path: Path) -> None:
    project_dir = _prep_project(tmp_path)
    _stage_binary(project_dir, "source_pdfs/private_patient_source.pdf", b"%PDF-1.4 patient Jane Example\n")

    result = CliRunner().invoke(app, ["import-scan", str(project_dir), "--write"])

    assert result.exit_code == 0, result.output
    row = _candidates(project_dir)[0]
    assert row["detected_kind"] == "source_pdf"
    assert row["privacy_scan_status"] == "not_scanned_binary"
    assert row["privacy_hits"] == []


def test_source_pdf_can_be_ready_after_privacy_review_with_descriptive_filename(tmp_path: Path) -> None:
    project_dir = _prep_project(tmp_path)
    staged = _stage_binary(project_dir, "source_pdfs/smith_2024_methods_reference.pdf")
    runner = CliRunner()
    assert runner.invoke(app, ["import-scan", str(project_dir), "--write"]).exit_code == 0
    metadata_csv = project_dir / "planning" / "import_staging" / "manifests" / "filled_source.csv"
    _write_metadata_csv(
        metadata_csv,
        [
            {
                "source_path_or_key": staged.relative_to(project_dir).as_posix(),
                "privacy_reviewed": "true",
                "decision_note": "reviewed descriptive source pdf",
            }
        ],
    )

    result = runner.invoke(app, ["import-update-metadata", str(project_dir), "--from-csv", str(metadata_csv)])

    assert result.exit_code == 0, result.output
    row = _candidates(project_dir)[0]
    assert row["import_status"] == "ready"
    assert row["privacy_status"] == "reviewed"
    assert "citation mapping will be stronger" in " ".join(row["warnings"])


def test_import_update_metadata_supports_decision_note(tmp_path: Path) -> None:
    project_dir = _prep_project(tmp_path)
    staged = _stage_file(project_dir, "writing_samples/misc/sample.md")
    runner = CliRunner()
    assert runner.invoke(app, ["import-scan", str(project_dir), "--write"]).exit_code == 0
    metadata_csv = project_dir / "planning" / "import_staging" / "manifests" / "filled_writing.csv"
    _write_metadata_csv(
        metadata_csv,
        [
            {
                "source_path": staged.relative_to(project_dir).as_posix(),
                "style_mode": "academic_manuscript",
                "privacy_reviewed": "true",
                "use_in_pilot": "true",
                "decision_note": "approved for first-file rehearsal",
            }
        ],
    )

    result = runner.invoke(app, ["import-update-metadata", str(project_dir), "--from-csv", str(metadata_csv)])

    assert result.exit_code == 0, result.output
    assert _candidates(project_dir)[0]["metadata"]["decision_note"] == "approved for first-file rehearsal"
    decisions = _jsonl(project_dir / "planning" / "import_staging" / "manifests" / "import_decisions.jsonl")
    assert decisions[-1]["note"] == "approved for first-file rehearsal"


def test_import_preview_works_for_text_like_candidate(tmp_path: Path) -> None:
    project_dir = _prep_project(tmp_path)
    _stage_file(project_dir, "sources/references.bib", "@article{toy,title={Toy},year={2024}}\n")
    runner = CliRunner()
    assert runner.invoke(app, ["import-scan", str(project_dir), "--write"]).exit_code == 0
    import_id = _first_import_id(project_dir)

    result = runner.invoke(app, ["import-preview", str(project_dir), "--import-id", import_id, "--limit-chars", "80"])

    assert result.exit_code == 0, result.output
    assert "@article" in result.output
    assert (project_dir / "planning" / "import_staging" / "reports" / f"import_preview_{import_id}.md").exists()


def test_import_preview_suppresses_privacy_hit_snippets(tmp_path: Path) -> None:
    project_dir = _prep_project(tmp_path)
    _stage_file(project_dir, "sources/private_note.md", "patient Jane Example jane.private@example.test\n")
    runner = CliRunner()
    assert runner.invoke(app, ["import-scan", str(project_dir), "--write"]).exit_code == 0
    import_id = _first_import_id(project_dir)

    result = runner.invoke(app, ["import-preview", str(project_dir), "--import-id", import_id])

    assert result.exit_code == 0, result.output
    assert "Text preview suppressed" in result.output
    assert "Jane Example" not in result.output


def test_import_create_demo_files_creates_labeled_toy_files(tmp_path: Path) -> None:
    project_dir = _prep_project(tmp_path)

    result = CliRunner().invoke(app, ["import-create-demo-files", str(project_dir)])

    assert result.exit_code == 0, result.output
    root = project_dir / "planning" / "import_staging"
    expected = [
        "incoming/writing_samples/demo_academic_methods.md",
        "incoming/writing_samples/demo_coursework_explanation.md",
        "incoming/result_tables/demo_results.csv",
        "incoming/sources/demo_references.bib",
        "incoming/source_pdfs/README_no_demo_pdf.md",
        "incoming/feedback/demo_feedback.jsonl",
    ]
    for rel in expected:
        text = (root / rel).read_text(encoding="utf-8")
        assert "DEMO" in text
        assert "TOY" in text
        assert "NOT REAL DATA" in text


def test_import_rehearsal_runs_without_importing_real_files(tmp_path: Path) -> None:
    project_dir = _prep_project(tmp_path)

    result = CliRunner().invoke(app, ["import-rehearsal", str(project_dir), "--create-demo"])

    assert result.exit_code == 0, result.output
    data = read_json(project_dir / "planning" / "import_staging" / "reports" / "import_rehearsal_report.json")
    assert data["created_demo"]
    assert data["dry_run_import"]["attempted"]
    assert not (project_dir / "feedback" / "demo_feedback.jsonl").exists()


def test_duplicate_grouping_appears_in_import_summary(tmp_path: Path) -> None:
    project_dir = _prep_project(tmp_path)
    text = "Methods\n\nDEMO TOY duplicate text for local staging report only.\n"
    _stage_file(project_dir, "writing_samples/academic_manuscript/one.md", text)
    _stage_file(project_dir, "writing_samples/academic_manuscript/two.md", text)
    runner = CliRunner()
    assert runner.invoke(app, ["import-scan", str(project_dir), "--write"]).exit_code == 0

    result = runner.invoke(app, ["import-summary", str(project_dir)])

    assert result.exit_code == 0, result.output
    report = (project_dir / "planning" / "import_staging" / "reports" / "import_summary.md").read_text(encoding="utf-8")
    assert "## Duplicate Groups" in report
    assert "Exact hash duplicates" in report


def test_intake_report_includes_staging_counts(tmp_path: Path) -> None:
    project_dir = _prep_project(tmp_path)
    _stage_file(project_dir, "writing_samples/academic_manuscript/methods.md")
    runner = CliRunner()
    assert runner.invoke(app, ["import-scan", str(project_dir), "--write"]).exit_code == 0

    result = runner.invoke(app, ["intake-report", str(project_dir)])

    assert result.exit_code == 0, result.output
    report = (project_dir / "planning" / "intake" / "intake_report.md").read_text(encoding="utf-8")
    assert "Staged file count" in report
    assert "Last import scan" in report


def test_validate_mentions_staged_review_counts_without_failing(tmp_path: Path) -> None:
    project_dir = _prep_project(tmp_path)
    _stage_file(project_dir, "writing_samples/academic_manuscript/methods.md")
    runner = CliRunner()
    assert runner.invoke(app, ["import-scan", str(project_dir), "--write"]).exit_code == 0

    result = validate_project(project_dir)

    assert result.ok
    assert any("Import staging review counts" in info for info in result.infos)
