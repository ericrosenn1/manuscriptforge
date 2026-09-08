from __future__ import annotations

from pathlib import Path
from shutil import copytree

import yaml

from manuscriptforge.audit.citation_audit import audit_citations
from manuscriptforge.config import initialize_project, load_project_config, validate_project
from manuscriptforge.ingest.bibtex_ingest import ingest_bibtex
from manuscriptforge.ingest.pdf_ingest import ingest_pdfs
from manuscriptforge.models.manuscript import Manuscript
from manuscriptforge.pipeline.workflow import provider_from_config, run_draft
from manuscriptforge.style.profiler import build_style_profile
from manuscriptforge.utils.io import read_json


def _copy_example(tmp_path: Path) -> Path:
    project_dir = tmp_path / "project_minimal"
    copytree(Path("tests/fixtures/synthetic_project"), project_dir)
    return project_dir


def _write_config(project_dir: Path, data: dict) -> None:
    (project_dir / "project.yaml").write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def _read_config(project_dir: Path) -> dict:
    return yaml.safe_load((project_dir / "project.yaml").read_text(encoding="utf-8"))


def test_empty_style_corpus_warns_and_profiles(tmp_path) -> None:
    project_dir = tmp_path / "empty_style"
    initialize_project(project_dir)
    result = validate_project(project_dir)
    assert any("No supported style corpus files" in warning for warning in result.warnings)

    profile = build_style_profile(project_dir, tmp_path / "run")
    assert profile.global_features["sample_count"] == 0
    assert profile.sentence_length_distribution["count"] == 0


def test_missing_references_warns_without_blocking(tmp_path) -> None:
    project_dir = _copy_example(tmp_path)
    (project_dir / "inputs" / "references.bib").unlink()
    result = validate_project(project_dir)
    assert result.ok
    assert any("No BibTeX or JSON citation source" in warning for warning in result.warnings)


def test_malformed_csv_is_validation_error(tmp_path) -> None:
    project_dir = _copy_example(tmp_path)
    bad_table = project_dir / "inputs" / "results_tables" / "malformed.csv"
    bad_table.write_text('marker,value\n"IL6,1.2\nCXCL10,2.0\n', encoding="utf-8")
    result = validate_project(project_dir)
    assert not result.ok
    assert any("Could not read result table" in error for error in result.errors)


def test_interpretation_notes_with_unsupported_causal_claims_are_reported(tmp_path) -> None:
    project_dir = _copy_example(tmp_path)
    (project_dir / "inputs" / "interpretation_notes.md").write_text(
        "The marker causes response and proves clinical utility.\n",
        encoding="utf-8",
    )
    run_dir = run_draft(project_dir)
    claims = read_json(run_dir / "claim_registry.json")
    assert any(claim["support_strength"] == "unsupported" for claim in claims)
    report = (run_dir / "unsupported_claims.md").read_text(encoding="utf-8")
    assert "Remove, rewrite as a limitation, or add direct evidence" in report


def test_missing_methods_file_is_validation_error(tmp_path) -> None:
    project_dir = _copy_example(tmp_path)
    (project_dir / "inputs" / "methods.md").unlink()
    result = validate_project(project_dir)
    assert not result.ok
    assert any("inputs/methods.md" in error for error in result.errors)


def test_no_llm_provider_falls_back_to_mock(tmp_path) -> None:
    project_dir = _copy_example(tmp_path)
    config = _read_config(project_dir)
    config["llm"]["provider"] = None
    _write_config(project_dir, config)
    result = validate_project(project_dir)
    assert result.ok
    assert any("No LLM provider configured" in warning for warning in result.warnings)
    run_dir = run_draft(project_dir)
    manifest = read_json(run_dir / "run_manifest.json")
    assert manifest["config"]["llm"]["provider"] is None


def test_local_only_openai_provider_uses_mock_without_api_key(tmp_path) -> None:
    project_dir = _copy_example(tmp_path)
    config = _read_config(project_dir)
    config["privacy"]["local_only"] = True
    config["llm"]["provider"] = "openai"
    _write_config(project_dir, config)
    result = validate_project(project_dir)
    assert result.ok
    assert any("privacy.local_only is true" in warning for warning in result.warnings)
    provider = provider_from_config(load_project_config(project_dir))
    assert provider.name == "mock"
    run_dir = run_draft(project_dir)
    assert (run_dir / "manuscript.md").exists()


def test_unsupported_citation_placeholders_are_detected() -> None:
    manuscript = Manuscript(title="Test", abstract="A claim needs [CITATION] and {citation}.")
    findings = audit_citations(manuscript, [], [])
    assert len([finding for finding in findings if finding.category == "citation_placeholder"]) == 2


def test_json_citation_and_invalid_pdf_ingest_do_not_crash(tmp_path) -> None:
    project_dir = _copy_example(tmp_path)
    (project_dir / "inputs" / "citations_manual.json").write_text(
        '[{"title": "Manual source", "authors": ["Ada Author"], "year": "2024", "journal": "Demo"}]',
        encoding="utf-8",
    )
    (project_dir / "inputs" / "source_pdfs" / "not_a_pdf.pdf").write_text("not a pdf", encoding="utf-8")
    citations = ingest_bibtex(project_dir)
    assert any(citation.title == "Manual source" for citation in citations)
    pdf_evidence = ingest_pdfs(project_dir)
    assert pdf_evidence
    assert pdf_evidence[0].metadata["extraction_status"] == "failed"


def test_manifest_contains_hashes_and_software_versions(tmp_path) -> None:
    project_dir = _copy_example(tmp_path)
    run_dir = run_draft(project_dir)
    manifest = read_json(run_dir / "run_manifest.json")
    assert manifest["config_hash"]
    assert manifest["input_hashes"]
    assert manifest["output_hashes"]
    assert manifest["software_versions"]["python"]
    assert manifest["completed_utc"]
