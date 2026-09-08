from __future__ import annotations

import json
from pathlib import Path

import pytest
from docx import Document

from manuscriptforge.demo import DUPLICATE_SOURCE, PASSAGES, run_demo
from manuscriptforge.utils.hashing import sha256_file


def test_demo_runs_real_extraction_curation_and_profile(tmp_path: Path) -> None:
    output = tmp_path / "demo"
    summary = run_demo(output)
    assert summary["validation_status"] == "PASS"
    assert summary["input_formats"] == [".docx", ".md", ".txt"]
    assert summary["source_documents"] == 3
    assert summary["extracted_chunks"] == 7
    assert summary["approved_chunks"] == 6
    assert summary["excluded_duplicate_chunks"] == 1
    assert summary["section_counts"] == dict.fromkeys(PASSAGES, 1)
    expected_path = Path(__file__).resolve().parents[1] / "examples/demo_expected_summary.json"
    expected = json.loads(expected_path.read_text(encoding="utf-8"))
    assert {key: summary[key] for key in expected} == expected
    profile = json.loads((output / summary["reports"]["profile"]).read_text(encoding="utf-8"))
    assert DUPLICATE_SOURCE not in profile["corpus_files"]
    assert len(profile["global_features"]["chunk_ids"]) == 6
    decisions_path = output / "planning/intake/style_chunks/style_chunk_decisions.jsonl"
    decisions = [json.loads(line) for line in decisions_path.read_text(encoding="utf-8").splitlines()]
    assert [decision["status"] for decision in decisions].count("excluded") == 1
    document = Document(str(output / DUPLICATE_SOURCE))
    assert document.paragraphs[1].text == PASSAGES["methods"]
    assert document.core_properties.author == "ManuscriptForge synthetic example"


def test_demo_rerun_preserves_all_completed_files(tmp_path: Path) -> None:
    output = tmp_path / "demo"
    first = run_demo(output)
    before = {path.relative_to(output): (sha256_file(path), path.stat().st_mtime_ns)
              for path in output.rglob("*") if path.is_file()}
    assert run_demo(output) == first
    after = {path.relative_to(output): (sha256_file(path), path.stat().st_mtime_ns)
             for path in output.rglob("*") if path.is_file()}
    assert after == before


@pytest.mark.parametrize("change", ["source", "report", "added_file"])
def test_demo_rejects_changed_outputs_without_overwriting(tmp_path: Path, change: str) -> None:
    output = tmp_path / "demo"
    run_demo(output)
    target = {
        "source": output / "style_corpus/academic_manuscript/protocol.md",
        "report": output / "outputs/profile/style_profile.json",
        "added_file": output / "personal_note.txt",
    }[change]
    target.write_text("A user edit must be preserved.", encoding="utf-8")
    with pytest.raises(ValueError, match="files changed"):
        run_demo(output)
    assert target.read_text(encoding="utf-8") == "A user edit must be preserved."


def test_demo_rejects_unrelated_directory_and_file(tmp_path: Path) -> None:
    existing = tmp_path / "existing.txt"
    existing.write_text("Keep this.", encoding="utf-8")
    with pytest.raises(ValueError, match="not a completed demo"):
        run_demo(tmp_path)
    with pytest.raises(ValueError, match="must be a directory"):
        run_demo(existing)
    assert existing.read_text(encoding="utf-8") == "Keep this."


def test_demo_summary_and_source_documents_are_portable(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    assert run_demo(first) == run_demo(second)
    assert (first / "demo_summary.json").read_bytes() == (second / "demo_summary.json").read_bytes()
    assert (first / DUPLICATE_SOURCE).read_bytes() == (second / DUPLICATE_SOURCE).read_bytes()
    assert str(tmp_path) not in (first / "demo_summary.json").read_text(encoding="utf-8")
