"""Independent research review cases; temporary sentinels, no API calls."""

import json
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from pathlib import Path

import pytest

from manuscriptforge.config import load_project_config, write_project_config
from manuscriptforge.orchestration import RunLockedError, RunRecord, WorkerContext
from manuscriptforge.research.demo import prepare_research_demo
from manuscriptforge.research.handlers import ingest_node
from manuscriptforge.research.pipeline import run_research, selected_inputs
from manuscriptforge.research.settings import ResearchSettings
from manuscriptforge.style.document_extractors import extract_style_corpus


def test_active_credential_file_is_not_selected_as_corpus(synthetic_project, monkeypatch):
    credential = synthetic_project / "inputs" / "connection-key.txt"
    credential.write_text("REVIEW_SENTINEL_NOT_A_REAL_CREDENTIAL", encoding="utf-8")
    monkeypatch.setenv("MANUSCRIPTFORGE_KEY_FILE", str(credential))
    assert credential.relative_to(synthetic_project).as_posix() not in selected_inputs(
        synthetic_project
    )


def test_environment_variants_are_not_selected_as_corpus(synthetic_project):
    credential = synthetic_project / "inputs" / ".env.development"
    credential.write_text("REVIEW_SENTINEL_NOT_A_REAL_CREDENTIAL", encoding="utf-8")
    assert credential.relative_to(synthetic_project).as_posix() not in selected_inputs(
        synthetic_project
    )


def test_ingest_cannot_write_external_metadata_cache(synthetic_project, tmp_path):
    outside = tmp_path / "external-cache"
    config = load_project_config(synthetic_project)
    config.sources.enrich_doi = True
    config.sources.metadata_cache_dir = str(outside)
    write_project_config(synthetic_project, config)
    work = tmp_path / "attempt" / "artifacts"
    work.mkdir(parents=True)
    context = WorkerContext(
        project_dir=synthetic_project,
        work_dir=work,
        inputs={},
        dependencies={},
        parameters={},
        node_id="ingest",
        run_id="review",
        attempt=1,
    )
    with suppress(ValueError):
        ingest_node(context)
    assert not outside.exists(), "Ingest wrote outside the snapshot before rejecting the path"


def test_changed_extracted_text_is_not_accepted_under_original_source_hash(synthetic_project):
    first = extract_style_corpus(synthetic_project)
    record = first.extractions[0]
    cache = synthetic_project / str(record.extracted_text_path)
    original = first.texts_by_relative_path[record.relative_path]
    altered = "A changed cache passage that does not appear in the original source document."
    cache.write_text(altered, encoding="utf-8")
    second = extract_style_corpus(synthetic_project)
    assert second.texts_by_relative_path[record.relative_path] == original


def test_valid_extracted_cache_is_reused_with_relative_provenance(synthetic_project):
    first = extract_style_corpus(synthetic_project)
    second = extract_style_corpus(synthetic_project)
    assert first.extractions[0].extracted_at == second.extractions[0].extracted_at
    assert first.extractions[0].extracted_text_hash
    assert second.extractions[0].source_path == second.extractions[0].relative_path


def test_demo_checks_nested_outputs_named_source_directories(tmp_path: Path):
    project = prepare_research_demo(tmp_path / "demo")
    additional = project / "style_corpus" / "outputs" / "added.md"
    additional.parent.mkdir()
    additional.write_text("## Methods\n\nA newly added, unreviewed source.", encoding="utf-8")
    try:
        prepare_research_demo(project)
    except ValueError:
        return
    raise AssertionError(
        "An added source beneath a nested outputs directory escaped demo verification"
    )


class RecordingEngine:
    def __init__(self, state_dir: Path):
        self.state_dir = state_dir
        self.calls = []

    def run(self, plan, *, run_id, resume):
        self.calls.append((plan, run_id, resume))
        time.sleep(0.05)
        return RunRecord(
            run_id=run_id,
            pipeline_id=plan.id,
            status="succeeded",
            run_dir=str(self.state_dir),
            created_at="test",
            updated_at="test",
            snapshot_fingerprint="test",
            spec_fingerprint="test",
        )


def test_resume_keeps_original_request_and_reuses_latest_explicit_settings(
    synthetic_project, tmp_path, monkeypatch
):
    engine = RecordingEngine(tmp_path / "engine")
    monkeypatch.setattr("manuscriptforge.research.pipeline.research_engine", lambda *_args: engine)
    run_research(
        synthetic_project, ResearchSettings(max_words_per_section=600), run_id="request-test"
    )
    original = engine.state_dir / "requests/request-test.json"
    first_bytes = original.read_bytes()
    run_research(
        synthetic_project,
        ResearchSettings(max_words_per_section=200),
        run_id="request-test",
        resume=True,
    )
    run_research(synthetic_project, run_id="request-test", resume=True)
    section = next(node for node in engine.calls[-1][0].nodes if node.handler == "section")
    assert section.parameters["max_words_per_section"] == 200
    assert original.read_bytes() == first_bytes
    assert len(list((engine.state_dir / "requests/request-test.revisions").glob("*.json"))) == 2


def test_request_creation_is_exclusive_for_the_same_run(synthetic_project, tmp_path, monkeypatch):
    engine = RecordingEngine(tmp_path / "engine")
    monkeypatch.setattr("manuscriptforge.research.pipeline.research_engine", lambda *_args: engine)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(
                run_research,
                synthetic_project,
                ResearchSettings(max_words_per_section=count),
                run_id="same-request",
            )
            for count in (100, 200)
        ]
        completed = []
        rejected = []
        for future in futures:
            try:
                completed.append(future.result())
            except (RunLockedError, FileExistsError) as exc:
                rejected.append(exc)
    assert len(completed) == len(rejected) == len(engine.calls) == 1


def test_changed_request_revision_is_rejected(synthetic_project, tmp_path, monkeypatch):
    engine = RecordingEngine(tmp_path / "engine")
    monkeypatch.setattr("manuscriptforge.research.pipeline.research_engine", lambda *_args: engine)
    run_research(synthetic_project, run_id="altered-request")
    active = json.loads((engine.state_dir / "requests/altered-request.active.json").read_text())
    recorded = engine.state_dir / "requests" / active["request_path"]
    recorded.write_text(recorded.read_text() + " ")
    with pytest.raises(ValueError, match="changed"):
        run_research(synthetic_project, run_id="altered-request", resume=True)


def test_resume_does_not_infer_settings_when_original_request_is_missing(
    synthetic_project, tmp_path, monkeypatch
):
    engine = RecordingEngine(tmp_path / "engine")
    monkeypatch.setattr("manuscriptforge.research.pipeline.research_engine", lambda *_args: engine)
    with pytest.raises(ValueError, match="request is missing"):
        run_research(synthetic_project, run_id="unknown-request", resume=True)
    assert not engine.calls
