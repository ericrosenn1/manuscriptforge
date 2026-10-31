import json
from types import SimpleNamespace

import pytest

from manuscriptforge.models.claim import EvidenceItem
from manuscriptforge.orchestration.models import ArtifactRecord
from manuscriptforge.orchestration.storage import hash_file, verify_artifacts
from manuscriptforge.research.search import INDEX_ARTIFACT, build_evidence_index, search_run
from manuscriptforge.retrieval import LexicalIndex, text_sha256


def evidence(id="ev-original", text="Archive A contains 12 records.", path="inputs/archive.md"):
    return EvidenceItem(
        evidence_id=id,
        evidence_type="manual",
        source_path=path,
        source_label="Original archive",
        locator="line 4",
        text=text,
        metadata={"input_key": "rationale"},
        content_hash="original-source-file-hash",
    )


def prepared(tmp_path, monkeypatch):
    state = tmp_path / "state"
    directory = state / "attempt" / "work"
    directory.mkdir(parents=True)
    metadata = build_evidence_index({"evidence_items": [evidence()]}, directory / INDEX_ARTIFACT)
    artifact = ArtifactRecord(
        path=INDEX_ARTIFACT,
        sha256=hash_file(directory / INDEX_ARTIFACT),
        bytes=(directory / INDEX_ARTIFACT).stat().st_size,
    )
    node = SimpleNamespace(
        status="succeeded", artifact_dir=str(directory), artifacts=[artifact], metadata=metadata
    )
    run = SimpleNamespace(run_id="run-1", status="succeeded", nodes={"ingest": node})
    calls = []

    def verify(run_id):
        calls.append(run_id)
        verify_artifacts(directory, node.artifacts)
        return SimpleNamespace(valid=True, errors=[])

    engine = SimpleNamespace(state_dir=state, status=lambda run_id: run, verify_run=verify)
    monkeypatch.setattr("manuscriptforge.research.search.research_engine", lambda _: engine)
    return directory / INDEX_ARTIFACT, node, run, engine, calls


def test_builder_preserves_original_evidence_ids_text_and_separate_source_hash(tmp_path):
    item = evidence(text="Archive A contains 12 records.\n")
    style = evidence(id="ev-style", path="style_corpus/academic_manuscript/private.md")
    payload = {
        "evidence_items": [item, style.model_dump(), evidence(id="blank", text="")],
        "style_corpus": [{"text": "Never indexed as evidence."}],
    }
    path = tmp_path / INDEX_ARTIFACT
    metadata = build_evidence_index(payload, path)
    index = LexicalIndex.load(path)
    assert metadata["strategy"] == "bm25"
    assert metadata["index_records"] == 1
    assert metadata["excluded_style_records"] == metadata["blank_evidence_records"] == 1
    hit = index.search("Archive A")[0]
    assert hit.record_id == hit.source_id == item.evidence_id
    assert hit.text == item.text
    assert hit.content_hash == text_sha256(item.text)
    assert hit.metadata["source_hash"] == item.content_hash
    assert hit.metadata["source_path"] == item.source_path
    assert hit.metadata["locator"] == "line 4"


def test_search_checks_run_and_returns_verified_provenance(tmp_path, monkeypatch):
    path, node, run, engine, calls = prepared(tmp_path, monkeypatch)
    before = path.read_bytes()
    result = search_run(tmp_path, "run-1", "archive")
    assert calls == ["run-1"]
    assert result["run_id"] == "run-1" and result["strategy"] == "bm25"
    assert result["index_hash"] == node.metadata["index_hash"]
    assert result["hits"][0]["id"] == result["hits"][0]["source_id"] == "ev-original"
    assert result["hits"][0]["metadata"]["source_hash"] == "original-source-file-hash"
    assert path.read_bytes() == before
    assert search_run(tmp_path, "run-1", "unmatched")["hits"] == []


def test_unfinished_or_invalid_runs_cannot_be_searched(tmp_path, monkeypatch):
    path, node, run, engine, calls = prepared(tmp_path, monkeypatch)
    run.status = "running"
    with pytest.raises(ValueError, match="completed"):
        search_run(tmp_path, "run-1", "archive")
    assert not calls
    run.status = "succeeded"
    engine.verify_run = lambda _: SimpleNamespace(valid=False, errors=["corrupt source snapshot"])
    with pytest.raises(ValueError, match="verification failed"):
        search_run(tmp_path, "run-1", "archive")


def test_changed_index_fails_artifact_and_internal_checks(tmp_path, monkeypatch):
    path, node, run, engine, calls = prepared(tmp_path, monkeypatch)
    payload = json.loads(path.read_text())
    payload["document_lengths"] = [999]
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="manifest"):
        search_run(tmp_path, "run-1", "archive")
    node.artifacts = [
        ArtifactRecord(path=INDEX_ARTIFACT, sha256=hash_file(path), bytes=path.stat().st_size)
    ]
    with pytest.raises(ValueError, match="checksum"):
        search_run(tmp_path, "run-1", "archive")


def test_duplicate_ids_or_accidental_index_overwrite_are_rejected(tmp_path):
    path = tmp_path / INDEX_ARTIFACT
    with pytest.raises(ValueError, match="unique"):
        build_evidence_index({"evidence_items": [evidence(), evidence()]}, path)
    assert not path.exists()
    build_evidence_index({"evidence_items": [evidence()]}, path)
    before = path.read_bytes()
    with pytest.raises(FileExistsError):
        build_evidence_index({"evidence_items": []}, path)
    assert path.read_bytes() == before


def test_search_rejects_unsafe_run_ids_before_engine_access(tmp_path, monkeypatch):
    def forbidden(_):
        raise AssertionError("Engine should not be accessed")

    monkeypatch.setattr("manuscriptforge.research.search.research_engine", forbidden)
    with pytest.raises(ValueError, match="IDs"):
        search_run(tmp_path, "../outside", "archive")
