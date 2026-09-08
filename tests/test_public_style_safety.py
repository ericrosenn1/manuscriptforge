from __future__ import annotations

import csv
import json
import os
import subprocess
from pathlib import Path

import pytest
import yaml
from docx import Document

from manuscriptforge.ingest.style_ingest import build_style_chunks, ingest_style_corpus
from manuscriptforge.intake.models import DataAsset
from manuscriptforge.intake.registry import (
    data_assets_path,
    intake_dir,
    load_eligible_writing_samples,
)
from manuscriptforge.style import document_extractors
from manuscriptforge.style.curation import (
    apply_chunk_curation_to_profile_chunks,
    build_style_chunk_registry,
    chunk_registry_jsonl_path,
    update_style_chunk_approval,
)
from manuscriptforge.style.document_extractors import (
    discover_style_documents,
    extract_style_corpus,
    extract_style_document,
)
from manuscriptforge.style.profiler import build_style_profile
from manuscriptforge.utils.io import read_yaml, write_jsonl, write_yaml

PASSAGE = (
    "Methods\n\n"
    "We simulated measurements from a fixed sequence of calibration targets. "
    "Each target was measured at three scheduled positions and retained its original identifier. "
    "The inspection script compared the recorded positions with the expected sequence before "
    "calculating descriptive summaries. Missing measurements remained missing throughout the "
    "workflow, and the report described the simulated setting without claiming performance "
    "on observations from a physical instrument.\n"
)


def _project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    (project / "style_corpus" / "academic_manuscript").mkdir(parents=True)
    write_yaml(project / "project.yaml", {"project_name": "Synthetic calibration"})
    return project


def _source(project: Path, name: str = "methods.md", mode: str = "academic_manuscript") -> Path:
    path = project / "style_corpus" / mode / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(PASSAGE, encoding="utf-8")
    return path


def _style_config(project: Path, **settings: object) -> None:
    data = read_yaml(project / "project.yaml")
    data.setdefault("style", {}).update(settings)
    write_yaml(project / "project.yaml", data)


@pytest.mark.parametrize("malformed", ["style: [", "style: {extraction: {enabled: nonsense}}", "- not a mapping"])
@pytest.mark.parametrize("operation", [ingest_style_corpus, extract_style_corpus, build_style_chunk_registry, build_style_profile])
def test_existing_invalid_config_is_not_replaced_by_defaults(tmp_path: Path, malformed: str, operation) -> None:
    project = _project(tmp_path)
    _source(project)
    (project / "project.yaml").write_text(malformed, encoding="utf-8")
    with pytest.raises((ValueError, yaml.YAMLError)):
        operation(project)
    assert not (project / "planning").exists()


def test_config_errors_propagate_when_samples_are_supplied(tmp_path: Path) -> None:
    project = _project(tmp_path)
    (project / "project.yaml").write_text("style: [", encoding="utf-8")
    with pytest.raises(yaml.YAMLError):
        build_style_chunks(project, [{"source_path": "sample.md", "text": PASSAGE}])
    with pytest.raises(yaml.YAMLError):
        apply_chunk_curation_to_profile_chunks(project, [])


def test_bare_corpus_without_config_keeps_text_support(tmp_path: Path) -> None:
    project = tmp_path / "project"
    _source(project, mode="")
    assert len(ingest_style_corpus(project)) == 1


def test_configured_modes_apply_without_an_intake_registry(tmp_path: Path) -> None:
    project = _project(tmp_path)
    academic = _source(project)
    _source(project, mode="coursework_explanatory")
    _source(project, mode="response_to_reviewers")
    _source(project, mode="")
    _style_config(
        project,
        include_modes=["academic_manuscript", "coursework_explanatory"],
        exclude_modes=["coursework_explanatory"],
    )
    assert [item["path"] for item in discover_style_documents(project)] == [academic]
    assert {sample["style_mode"] for sample in ingest_style_corpus(project)} == {"academic_manuscript"}
    assert discover_style_documents(project, mode="coursework_explanatory") == []


def test_disabling_extraction_cannot_reuse_cached_or_direct_text(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _source(project)
    assert extract_style_corpus(project).summary["files_extracted"] == 1
    _style_config(project, extraction={"enabled": False})
    result = extract_style_corpus(project)
    assert result.summary["files_extracted"] == 0
    assert result.texts_by_relative_path == {}
    assert ingest_style_corpus(project) == []
    assert build_style_profile(project).corpus_files == []


def test_docx_cache_respects_format_policy_and_reenabling(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _project(tmp_path)
    path = project / "style_corpus" / "academic_manuscript" / "note.docx"
    document = Document()
    document.add_paragraph(PASSAGE)
    document.save(path)
    calls = []
    original = document_extractors._extract_docx_text

    def counted(source: Path):
        calls.append(source)
        return original(source)

    monkeypatch.setattr(document_extractors, "_extract_docx_text", counted)
    first = extract_style_corpus(project)
    second = extract_style_corpus(project)
    assert first.texts_by_relative_path == second.texts_by_relative_path
    assert len(calls) == 1
    _style_config(project, extraction={"include_docx": False})
    assert extract_style_corpus(project).summary["unsupported"] == 1
    assert ingest_style_corpus(project) == []
    _style_config(project, extraction={"include_docx": True})
    assert extract_style_corpus(project).summary["files_extracted"] == 1
    assert len(calls) == 2


def test_cache_disabled_still_returns_text_on_every_run(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _source(project)
    first = extract_style_corpus(project)
    assert first.extractions[0].extracted_text_path
    _style_config(project, extraction={"cache_extracted_text": False})
    for _ in range(2):
        result = extract_style_corpus(project)
        assert result.texts_by_relative_path == first.texts_by_relative_path
        assert result.extractions[0].extracted_text_path is None
        assert len(ingest_style_corpus(project)) == 1


def test_changed_extraction_settings_invalidate_cache_identity(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _source(project)
    first = extract_style_corpus(project)
    _style_config(project, extraction={"max_chunk_words": 100})
    second = extract_style_corpus(project)
    assert first.extractions[0].extracted_text_path != second.extractions[0].extracted_text_path
    assert first.texts_by_relative_path == second.texts_by_relative_path


@pytest.mark.parametrize("registry_enabled", [True, False])
def test_strict_approval_fails_closed_without_usable_registry(tmp_path: Path, registry_enabled: bool) -> None:
    project = _project(tmp_path)
    _source(project)
    _style_config(project, curation={"require_chunk_approval": True, "use_chunk_registry": registry_enabled})
    profile = build_style_profile(project)
    assert profile.corpus_files == []
    assert profile.global_features["style_chunk_curation"]["excluded_chunks"] > 0
    assert profile.global_features["style_chunk_curation"]["warning"]


def test_new_duplicate_does_not_inherit_prior_approval(tmp_path: Path) -> None:
    project = _project(tmp_path)
    source = _source(project)
    original = build_style_chunk_registry(project).rows[0]
    update_style_chunk_approval(project, chunk_id=original.chunk_id, approve=True, note="Reviewed original source")
    duplicate = _source(project, "duplicate.md")
    rows = [row for row in build_style_chunk_registry(project).rows if not row.missing]
    retained = next(row for row in rows if row.source_file == source.relative_to(project).as_posix())
    added = next(row for row in rows if row.source_file == duplicate.relative_to(project).as_posix())
    assert retained.approval_status == "approved"
    assert added.content_hash == retained.content_hash
    assert added.chunk_id != retained.chunk_id
    assert added.approval_status != "approved"
    assert added.notes == ""
    _style_config(project, curation={"require_chunk_approval": True})
    assert build_style_profile(project).corpus_files == [source.relative_to(project).as_posix()]


def test_strict_approval_reads_existing_csv_registry(tmp_path: Path) -> None:
    project = _project(tmp_path)
    source = _source(project)
    chunk = build_style_chunk_registry(project).rows[0]
    update_style_chunk_approval(project, chunk_id=chunk.chunk_id, approve=True)
    chunk_registry_jsonl_path(project).unlink()
    _style_config(project, curation={"require_chunk_approval": True})
    assert build_style_profile(project).corpus_files == [source.relative_to(project).as_posix()]


def test_direct_extraction_rejects_source_outside_project(tmp_path: Path) -> None:
    project = _project(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text(PASSAGE, encoding="utf-8")
    with pytest.raises(ValueError, match="inside the project"):
        extract_style_document(project, outside)
    assert not (project / "planning").exists()


def test_registry_escape_is_rejected_before_scaffold_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _project(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text(PASSAGE, encoding="utf-8")
    asset = DataAsset(
        asset_id="synthetic_asset", asset_type="writing_sample", path=str(outside),
        relative_path="style_corpus/../../outside.txt", filename=outside.name, extension=".txt",
        content_hash="synthetic", size_bytes=outside.stat().st_size,
        created_or_detected_at="2026-01-01T00:00:00Z", status="approved", approved_for=["style_profile"],
    )
    write_jsonl(data_assets_path(project), [asset])
    with (intake_dir(project) / "writing_samples_registry.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["asset_id", "style_mode"])
        writer.writeheader()
        writer.writerow({"asset_id": asset.asset_id, "style_mode": "academic_manuscript"})

    def forbidden_read(path: Path):
        pytest.fail(f"An escaping registry path reached scaffold inspection: {path}")

    monkeypatch.setattr("manuscriptforge.intake.registry.is_scaffold_file", forbidden_read)
    with pytest.raises(ValueError, match="inside style_corpus"):
        load_eligible_writing_samples(project, active_mode="academic_manuscript", include_modes=["academic_manuscript"], exclude_modes=[])


def test_external_cached_text_is_not_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _project(tmp_path)
    _source(project)
    first = extract_style_corpus(project)
    outside = tmp_path / "outside.txt"
    outside.write_text("This content must not enter the profile.", encoding="utf-8")
    row = first.extractions[0].model_dump(mode="json")
    row["extracted_text_path"] = str(outside)
    first.manifest_path.write_text(json.dumps(row) + "\n", encoding="utf-8")
    original = Path.read_text

    def guarded_read(path: Path, *args, **kwargs):
        assert path != outside, "The untrusted cache path was read"
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", guarded_read)
    assert extract_style_corpus(project).texts_by_relative_path == first.texts_by_relative_path


def test_linked_corpus_directory_is_not_traversed(tmp_path: Path) -> None:
    project = _project(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "note.txt").write_text(PASSAGE, encoding="utf-8")
    link = project / "style_corpus" / "academic_manuscript" / "linked"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        if os.name != "nt":
            raise
        # Directory junctions exercise Windows traversal without symlink privileges.
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(outside)],
            capture_output=True, text=True, check=False,
        )
        assert result.returncode == 0, result.stderr
    with pytest.raises(ValueError, match="inside the project|Linked style"):
        discover_style_documents(project)
