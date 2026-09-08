from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from manuscriptforge.cli import app
from manuscriptforge.ingest.style_ingest import build_style_chunks
from manuscriptforge.style import document_extractors
from manuscriptforge.style.document_extractors import extract_style_document
from manuscriptforge.utils.io import read_json


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


def _write_docx(project_dir: Path, mode: str, filename: str, paragraphs: list[str]) -> Path:
    docx = pytest.importorskip("docx")
    path = project_dir / "style_corpus" / mode / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    document = docx.Document()
    for paragraph in paragraphs:
        document.add_paragraph(paragraph)
    document.save(path)
    return path


def test_txt_style_extraction_writes_cached_text(tmp_path: Path) -> None:
    project_dir = _prep_project(tmp_path)
    path = _write_style_file(
        project_dir,
        "academic_manuscript",
        "methods.txt",
        "Methods\n\nWe used versioned scripts and cautious reporting.\n",
    )
    record = extract_style_document(project_dir, path, style_mode="academic_manuscript")
    assert record.extraction_status == "extracted"
    assert record.extension == ".txt"
    assert record.extracted_text_path
    assert (project_dir / record.extracted_text_path).exists()


def test_md_style_extraction_preserves_text(tmp_path: Path) -> None:
    project_dir = _prep_project(tmp_path)
    path = _write_style_file(
        project_dir,
        "academic_manuscript",
        "discussion.md",
        "Discussion\n\nTogether, these observations may warrant careful follow-up.\n",
    )
    record = extract_style_document(project_dir, path, style_mode="academic_manuscript")
    assert record.extraction_status == "extracted"
    assert record.extracted_word_count >= 6


def test_docx_style_extraction_uses_paragraph_text(tmp_path: Path) -> None:
    project_dir = _prep_project(tmp_path)
    path = _write_docx(
        project_dir,
        "academic_manuscript",
        "methods.docx",
        ["Methods", "We used versioned scripts and reproducible intermediate artifacts."],
    )
    record = extract_style_document(project_dir, path, style_mode="academic_manuscript")
    assert record.extraction_status == "extracted"
    assert record.extension == ".docx"
    assert record.paragraph_count == 2
    assert record.extracted_text_path
    cached = (project_dir / record.extracted_text_path).read_text(encoding="utf-8")
    assert "versioned scripts" in cached


def test_malformed_docx_fails_gracefully(tmp_path: Path) -> None:
    project_dir = _prep_project(tmp_path)
    path = project_dir / "style_corpus" / "academic_manuscript" / "broken.docx"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"not a valid docx package")
    record = extract_style_document(project_dir, path, style_mode="academic_manuscript")
    assert record.extraction_status == "failed"
    assert record.warnings


def test_likely_scanned_pdf_heuristic_uses_extracted_text_density(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_dir = _prep_project(tmp_path)
    path = project_dir / "style_corpus" / "academic_manuscript" / "scan.pdf"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"%PDF-1.4\n")
    monkeypatch.setattr(document_extractors, "extract_pdf_pages", lambda _path: ["Tiny"])
    monkeypatch.setattr(document_extractors, "pdf_page_count", lambda _path: 1)
    record = extract_style_document(project_dir, path, style_mode="academic_manuscript")
    assert record.extraction_status == "likely_scanned_or_unextractable"
    assert record.warnings


def test_profile_style_uses_docx_when_intake_registry_exists(tmp_path: Path) -> None:
    project_dir = _prep_project(tmp_path)
    _write_docx(
        project_dir,
        "academic_manuscript",
        "methods.docx",
        ["Methods", "We used transparent tables and versioned software for this analysis."],
    )
    runner = CliRunner()
    assert runner.invoke(app, ["intake-scan", str(project_dir), "--write"]).exit_code == 0
    result = runner.invoke(app, ["profile-style", str(project_dir)])
    assert result.exit_code == 0, result.output
    profile = read_json(project_dir / "outputs" / "latest" / "style_profile.json")
    assert "style_corpus/academic_manuscript/methods.docx" in profile["corpus_files"]
    assert profile["global_features"]["source_extension_distribution"] == {".docx": 1}


def test_coursework_docx_does_not_affect_academic_profile_when_excluded(tmp_path: Path) -> None:
    project_dir = _prep_project(tmp_path)
    _write_docx(
        project_dir,
        "academic_manuscript",
        "methods.docx",
        ["Methods", "We used reproducible scripts and cautious reporting."],
    )
    _write_docx(
        project_dir,
        "coursework_explanatory",
        "homework.docx",
        ["I will explain every concept step by step for this assignment."],
    )
    runner = CliRunner()
    assert runner.invoke(app, ["intake-scan", str(project_dir), "--write"]).exit_code == 0
    result = runner.invoke(app, ["profile-style", str(project_dir)])
    assert result.exit_code == 0, result.output
    profile = read_json(project_dir / "outputs" / "latest" / "style_profile.json")
    assert "style_corpus/academic_manuscript/methods.docx" in profile["corpus_files"]
    assert "style_corpus/coursework_explanatory/homework.docx" not in profile["corpus_files"]


def test_references_section_excluded_from_style_chunks_by_default(tmp_path: Path) -> None:
    project_dir = _prep_project(tmp_path)
    _write_style_file(
        project_dir,
        "academic_manuscript",
        "full.md",
        (
            "Methods\n\n"
            "We used reproducible scripts and transparent tables.\n\n"
            "References\n\n"
            "1. Example reference that should not become a style chunk.\n"
        ),
    )
    chunks = build_style_chunks(project_dir)
    assert chunks
    assert "references" not in {chunk.section_type for chunk in chunks}
    assert all("Example reference" not in chunk.text for chunk in chunks)


def test_extract_style_corpus_command_creates_manifest_and_report(tmp_path: Path) -> None:
    project_dir = _prep_project(tmp_path)
    _write_style_file(
        project_dir,
        "academic_manuscript",
        "abstract.txt",
        "Abstract\n\nThis toy prior abstract uses cautious language.\n",
    )
    result = CliRunner().invoke(
        app,
        ["extract-style-corpus", str(project_dir), "--mode", "academic_manuscript"],
    )
    assert result.exit_code == 0, result.output
    output_dir = project_dir / "planning" / "intake" / "extracted_style_text"
    assert (output_dir / "extraction_manifest.jsonl").exists()
    assert (output_dir / "extraction_report.md").exists()


def test_style_corpus_doctor_reports_no_real_samples_in_scaffold(tmp_path: Path) -> None:
    project_dir = _prep_project(tmp_path)
    result = CliRunner().invoke(
        app,
        ["style-corpus-doctor", str(project_dir), "--mode", "academic_manuscript"],
    )
    assert result.exit_code == 0, result.output
    data = read_json(project_dir / "planning" / "intake" / "style_corpus_doctor.json")
    assert data["files_considered"] == 0
    assert any("Add real writing samples" in item for item in data["recommendations"])
