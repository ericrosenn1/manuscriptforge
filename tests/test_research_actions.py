"""No-network tests for proposal diagnostics and comparison preflight."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from manuscriptforge.config import write_project_config
from manuscriptforge.evaluation.synthetic import build_synthetic_dataset
from manuscriptforge.llm.budget import BudgetError, BudgetLedger
from manuscriptforge.models.project import ProjectConfig
from manuscriptforge.orchestration import ArtifactRecord, NodeRecord
from manuscriptforge.research import comparison, proposals
from manuscriptforge.research.settings import ResearchSettings
from manuscriptforge.utils.hashing import sha256_file, sha256_text
from manuscriptforge.utils.io import read_json, write_json


def proposal_fixture(tmp_path, monkeypatch, *, hits=True):
    project = tmp_path / "project"
    project.mkdir()
    config = ProjectConfig()
    config.privacy.local_only = False
    write_project_config(project, config)
    state = tmp_path / "engine"
    directory = state / "ingest"
    directory.mkdir(parents=True)
    path = directory / "project_config.json"
    write_json(path, config)
    artifact = ArtifactRecord(
        path="project_config.json", bytes=path.stat().st_size, sha256=sha256_file(path)
    )
    node = NodeRecord(
        id="ingest", status="succeeded", artifact_dir=str(directory), artifacts=[artifact]
    )
    run = SimpleNamespace(status="succeeded", nodes={"ingest": node})
    engine = SimpleNamespace(state_dir=state, status=lambda _run_id: run)
    monkeypatch.setattr(proposals, "research_engine", lambda _project: engine)
    text = "Sensor A has 12 records."
    rows = (
        [{"source_id": "source-one", "text": text, "content_hash": sha256_text(text)}]
        if hits
        else []
    )
    monkeypatch.setattr(
        proposals, "search_run", lambda *_args: {"hits": rows, "index_hash": "fixture"}
    )
    return project, path


def paid_settings(path: Path, **overrides) -> ResearchSettings:
    return ResearchSettings(
        provider="openai",
        allow_network=True,
        budget_limit_usd="1",
        budget_ledger_path=str(path),
        **overrides,
    )


def forbidden_provider(*_args, **_kwargs):
    raise AssertionError("This test must not construct or call a paid provider")


def test_empty_retrieval_does_not_construct_provider_or_load_credentials(tmp_path, monkeypatch):
    project, _ = proposal_fixture(tmp_path, monkeypatch, hits=False)
    monkeypatch.setattr(proposals, "research_provider", forbidden_provider)
    monkeypatch.setattr(proposals, "load_configured_key", forbidden_provider)
    ledger = tmp_path / "unused.sqlite"
    output = proposals.propose_facts(
        project, "run", "no match", tmp_path / "out", paid_settings(ledger)
    )
    assert not ledger.exists()
    assert read_json(output / "proposals.json")["facts"] == []
    provenance = read_json(output / "provenance.json")
    assert provenance["status"] == "no_sources"
    assert provenance["provider"] is None and not provenance["provider_invoked"]


def test_proposals_recheck_configuration_hash_after_retrieval(tmp_path, monkeypatch):
    project, config = proposal_fixture(tmp_path, monkeypatch)
    config.write_text("{}", encoding="utf-8")
    with pytest.raises(proposals.ProposalRunError) as captured:
        proposals.propose_facts(project, "run", "records", tmp_path / "out", ResearchSettings())
    output = captured.value.output_path
    assert str(output) in str(captured.value)
    assert read_json(output / "provenance.json")["status"] == "failed"
    assert "hash" in read_json(output / "failure.json")["message"]
    assert (output / "manifest.json").is_file()


def test_configuration_changed_during_read_is_rejected(tmp_path, monkeypatch):
    project, config = proposal_fixture(tmp_path, monkeypatch)
    original = proposals.read_json

    def changed(path):
        value = original(path)
        if path == config:
            config.write_text("{}", encoding="utf-8")
        return value

    monkeypatch.setattr(proposals, "read_json", changed)
    with pytest.raises(proposals.ProposalRunError, match="changed while"):
        proposals.propose_facts(project, "run", "records", tmp_path / "out", ResearchSettings())


def test_proposal_failure_preserves_diagnostics_and_redacts_known_credentials(
    tmp_path, monkeypatch
):
    project, _ = proposal_fixture(tmp_path, monkeypatch)
    secret = "SENTINEL_CREDENTIAL_NOT_A_REAL_API_KEY"
    monkeypatch.setenv("OPENAI_API_KEY", secret)
    monkeypatch.setattr(proposals, "load_configured_key", lambda _project: True)

    class FailedProvider:
        def generate_json(self, *_args, **_kwargs):
            raise RuntimeError(f"fixture failure {secret}")

    monkeypatch.setattr(proposals, "research_provider", lambda _settings: FailedProvider())
    with pytest.raises(proposals.ProposalRunError) as captured:
        proposals.propose_facts(
            project, "run", "records", tmp_path / "out", paid_settings(tmp_path / "ledger.sqlite")
        )
    output = captured.value.output_path
    assert secret not in str(captured.value)
    assert secret not in (output / "failure.json").read_text()
    assert (output / "request.json").is_file() and (output / "retrieval.json").is_file()
    assert not (output / "proposals.json").exists()
    assert read_json(output / "manifest.json")["status"] == "failed"


def test_mock_proposals_remain_explicitly_unpromoted(tmp_path, monkeypatch):
    project, _ = proposal_fixture(tmp_path, monkeypatch)
    output = proposals.propose_facts(
        project, "run", "records", tmp_path / "out", ResearchSettings()
    )
    result = read_json(output / "proposals.json")
    assert not result["facts"] and result["needs_human_review"]
    assert "Offline fixture" in result["unresolved_questions"][0]
    assert read_json(output / "provenance.json")["status"] == "completed"
    manifest = read_json(output / "manifest.json")
    for item in manifest["files"]:
        assert sha256_file(output / item["path"]) == item["sha256"]


@pytest.fixture
def comparison_setup(tmp_path, monkeypatch):
    dataset = build_synthetic_dataset(families=10)
    settings = paid_settings(tmp_path / "ledger.sqlite")
    monkeypatch.setattr(
        comparison, "research_provider", lambda _settings: SimpleNamespace(name="never-called")
    )
    monkeypatch.setattr(comparison, "_source_hashes", lambda: {"stable.py": "a"})
    return dataset, settings


def fake_results(dataset, _baseline, _candidate, output_root, *, split, **_kwargs):
    cases = [case for case in dataset.cases if case.split == split]
    run = output_root / "fixture"
    run.mkdir(parents=True)
    summary = {
        name: {"cases": len(cases), "execution_failures": 0} for name in ("baseline", "candidate")
    }
    summary["functional_suite_status"] = "PASS"
    write_json(run / "summary.json", summary)
    write_json(
        run / "progress.json",
        {"status": "completed", "planned_calls": len(cases) * 2, "completed_calls": len(cases) * 2},
    )
    write_json(run / "protocol.json", {"fixture": True})
    write_json(run / "dataset.json", dataset)
    (run / "REPORT.md").write_text("A synthetic test fixture, not a model result.")
    for name in ("baseline", "candidate"):
        (run / f"{name}.jsonl").write_text(
            "".join(
                json.dumps({"case_id": case.case_id, "execution_error": None}) + "\n"
                for case in cases
            )
        )
    refresh_manifest(run)
    return run


def refresh_manifest(run: Path) -> None:
    write_json(
        run / "manifest.json",
        {
            "files": [
                {"path": path.name, "bytes": path.stat().st_size, "sha256": sha256_file(path)}
                for path in sorted(run.iterdir())
                if path.name != "manifest.json"
            ]
        },
    )


def test_comparison_rejects_mock_before_creating_ledger_or_campaign(tmp_path):
    dataset = build_synthetic_dataset(families=10)
    output = tmp_path / "output"
    with pytest.raises(ValueError, match="OpenAI provider"):
        comparison.compare_models(dataset, ResearchSettings(), ResearchSettings(), output)
    assert not output.exists()


@pytest.mark.parametrize("change", ["path", "cap", "model"])
def test_comparison_preflight_rejects_inconsistent_policy(tmp_path, comparison_setup, change):
    dataset, settings = comparison_setup
    other = settings.model_copy(
        update={
            "path": {"budget_ledger_path": str(tmp_path / "other.sqlite")},
            "cap": {"budget_limit_usd": settings.budget_limit_usd * 2},
            "model": {"model": "unpriced-model"},
        }[change]
    )
    output = tmp_path / "output"
    with pytest.raises(ValueError, match="ledger and cap|price policy"):
        comparison.compare_models(dataset, settings, other, output)
    assert not output.exists()
    assert not (tmp_path / "ledger.sqlite").exists()


def test_existing_ledger_policy_is_not_replaced(tmp_path, comparison_setup):
    dataset, settings = comparison_setup
    ledger = BudgetLedger(tmp_path / "ledger.sqlite", "2")
    with pytest.raises(BudgetError, match="differs"):
        comparison.compare_models(dataset, settings, settings, tmp_path / "output")
    assert not (tmp_path / "output").exists()
    assert ledger.snapshot()["limit_usd"] == "2"


def test_comparison_verifies_artifacts_and_preserves_nested_manifest(
    tmp_path, comparison_setup, monkeypatch
):
    dataset, settings = comparison_setup
    monkeypatch.setattr(comparison, "run_extraction_comparison", fake_results)
    output = comparison.compare_models(dataset, settings, settings, tmp_path / "output")
    metadata = read_json(output / "campaign.json")
    assert metadata["status"] == "completed"
    assert metadata["budget_after"]["attempt_count"] == 0
    entries = read_json(output / "manifest.json")["files"]
    assert "results/fixture/manifest.json" in {item["path"] for item in entries}
    assert all(sha256_file(output / item["path"]) == item["sha256"] for item in entries)


def test_comparison_failure_reports_diagnostic_path_and_preserves_partial_files(
    tmp_path, comparison_setup, monkeypatch
):
    dataset, settings = comparison_setup

    def failure(_dataset, _baseline, _candidate, root, **_kwargs):
        root.mkdir(parents=True)
        (root / "partial.txt").write_text("A retained partial result")
        raise RuntimeError("fixture execution failure")

    monkeypatch.setattr(comparison, "run_extraction_comparison", failure)
    with pytest.raises(comparison.ComparisonRunError) as captured:
        comparison.compare_models(dataset, settings, settings, tmp_path / "output")
    output = captured.value.output_path
    assert str(output) in str(captured.value)
    assert (output / "results/partial.txt").is_file()
    assert read_json(output / "campaign.json")["status"] == "failed"
    assert (output / "manifest.json").is_file()


def test_completed_case_failures_are_not_labeled_plain_completed(
    tmp_path, comparison_setup, monkeypatch
):
    dataset, settings = comparison_setup

    def failed_cases(*args, **kwargs):
        run = fake_results(*args, **kwargs)
        rows = [json.loads(line) for line in (run / "candidate.jsonl").read_text().splitlines()]
        rows[0]["execution_error"] = "A recorded candidate failure"
        (run / "candidate.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
        summary = read_json(run / "summary.json")
        summary["candidate"]["execution_failures"] = 1
        summary["functional_suite_status"] = "FAIL"
        write_json(run / "summary.json", summary)
        refresh_manifest(run)
        return run

    monkeypatch.setattr(comparison, "run_extraction_comparison", failed_cases)
    output = comparison.compare_models(dataset, settings, settings, tmp_path / "output")
    metadata = read_json(output / "campaign.json")
    assert metadata["status"] == "completed_with_failures"
    assert metadata["execution_failures"]["candidate"] == 1
    assert metadata["functional_suite_status"] == "FAIL"


def test_source_drift_invalidates_comparison_but_retains_results(
    tmp_path, comparison_setup, monkeypatch
):
    dataset, settings = comparison_setup
    hashes = iter([{"code.py": "before"}, {"code.py": "after"}])
    monkeypatch.setattr(comparison, "_source_hashes", lambda: next(hashes))
    monkeypatch.setattr(comparison, "run_extraction_comparison", fake_results)
    with pytest.raises(comparison.ComparisonRunError) as captured:
        comparison.compare_models(dataset, settings, settings, tmp_path / "output")
    output = captured.value.output_path
    metadata = read_json(output / "campaign.json")
    assert metadata["status"] == "source_changed"
    assert metadata["source_files_changed"] == ["code.py"]
    assert (output / "results/fixture/summary.json").is_file()


def test_comparison_rejects_corrupt_result_manifest(tmp_path, comparison_setup, monkeypatch):
    dataset, settings = comparison_setup

    def corrupt(*args, **kwargs):
        run = fake_results(*args, **kwargs)
        (run / "summary.json").write_text("{}")
        return run

    monkeypatch.setattr(comparison, "run_extraction_comparison", corrupt)
    with pytest.raises(comparison.ComparisonRunError, match="artifact integrity") as captured:
        comparison.compare_models(dataset, settings, settings, tmp_path / "output")
    assert read_json(captured.value.output_path / "campaign.json")["status"] == "failed"


def test_comparison_interruption_keeps_status_and_diagnostic_location(
    tmp_path, comparison_setup, monkeypatch
):
    dataset, settings = comparison_setup

    def interrupt(*_args, **_kwargs):
        raise KeyboardInterrupt()

    monkeypatch.setattr(comparison, "run_extraction_comparison", interrupt)
    with pytest.raises(KeyboardInterrupt) as captured:
        comparison.compare_models(dataset, settings, settings, tmp_path / "output")
    outputs = list((tmp_path / "output").iterdir())
    assert len(outputs) == 1
    assert read_json(outputs[0] / "campaign.json")["status"] == "interrupted"
    assert any(str(outputs[0]) in note for note in captured.value.__notes__)


def test_candidate_refuses_a_different_ledger_before_provider_creation(
    tmp_path, comparison_setup, monkeypatch
):
    _, settings = comparison_setup
    ledger = BudgetLedger(tmp_path / "different.sqlite", "1")
    monkeypatch.setattr(comparison, "research_provider", forbidden_provider)
    with pytest.raises(ValueError, match="do not match"):
        comparison.BudgetedExtractionCandidate(settings, ledger)


def test_protocols_are_forwarded_and_recorded(tmp_path, comparison_setup, monkeypatch):
    dataset, settings = comparison_setup
    received = {}

    def capture(dataset, baseline, candidate, root, **kwargs):
        received.update(kwargs)
        received["baseline_quantity_protocol"] = baseline.candidate.quantity_protocol
        received["candidate_quantity_protocol"] = candidate.candidate.quantity_protocol
        return fake_results(dataset, baseline, candidate, root, **kwargs)

    monkeypatch.setattr(comparison, "run_extraction_comparison", capture)
    output = comparison.compare_models(
        dataset,
        settings,
        settings,
        tmp_path / "output",
        scoring_version="2.1-quantities",
        capture_policy="failures",
        baseline_quantity_protocol="2.0",
        candidate_quantity_protocol="2.1",
    )
    assert received["scoring_version"] == "2.1-quantities"
    assert received["capture_policy"] == "failures"
    assert received["baseline_quantity_protocol"] == "2.0"
    assert received["candidate_quantity_protocol"] == "2.1"
    metadata = read_json(output / "campaign.json")
    assert metadata["quantity_protocols"] == {"baseline": "2.0", "candidate": "2.1"}


def test_actual_comparison_runner_with_offline_provider_makes_no_paid_calls(
    tmp_path, comparison_setup, monkeypatch
):
    dataset, settings = comparison_setup

    class OfflineProvider:
        name = "offline-test"

        def generate_json(self, *_args, **_kwargs):
            return {
                "facts": [],
                "needs_human_review": True,
                "unresolved_questions": ["Offline test provider abstained."],
            }

    monkeypatch.setattr(comparison, "research_provider", lambda _settings: OfflineProvider())
    output = comparison.compare_models(dataset, settings, settings, tmp_path / "output")
    metadata = read_json(output / "campaign.json")
    assert metadata["status"] == "completed"
    assert metadata["execution_failures"] == {"baseline": 0, "candidate": 0}
    assert metadata["functional_suite_status"] == "FAIL"
    assert metadata["budget_after"]["attempt_count"] == 0
