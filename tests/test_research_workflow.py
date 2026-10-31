"""End-to-end research DAG contracts using original synthetic data."""

import hashlib
import json
from pathlib import Path
from zipfile import ZipFile

import pytest
from typer.testing import CliRunner

from manuscriptforge.cli import app
from manuscriptforge.research.pipeline import (
    build_research_plan,
    research_engine,
    run_research,
    selected_inputs,
)
from manuscriptforge.research.proposals import propose_facts
from manuscriptforge.research.search import search_run
from manuscriptforge.research.settings import ResearchSettings


def _hashes(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in root.rglob("*")
        if path.is_file() and "outputs" not in path.relative_to(root).parts
    }


def test_plan_declares_sources_not_credentials_and_separates_scheduler_settings(synthetic_project):
    (synthetic_project / ".env").write_text(
        "OPENAI_API_KEY=never-snapshot-this\n", encoding="utf-8"
    )
    assert ".env" not in selected_inputs(synthetic_project)
    first = build_research_plan(synthetic_project, ResearchSettings(max_workers=1))
    second = build_research_plan(synthetic_project, ResearchSettings(max_workers=3))
    assert first.max_workers != second.max_workers
    assert [node.parameters for node in first.nodes] == [node.parameters for node in second.nodes]
    changed = build_research_plan(synthetic_project, ResearchSettings(max_words_per_section=300))
    for original, updated in zip(first.nodes[:4], changed.nodes[:4], strict=True):
        assert original.parameters == updated.parameters == {}
    assert first.nodes[4].parameters != changed.nodes[4].parameters
    assert len([node for node in first.nodes if node.handler == "section"]) == 7
    assert all(
        node.handler
        in {"validate", "ingest", "profile", "claims", "section", "assemble", "audit", "export"}
        for node in first.nodes
    )


def test_paid_project_gate_preserves_explicit_local_only(synthetic_project, tmp_path):
    settings = ResearchSettings(
        provider="openai",
        allow_network=True,
        budget_limit_usd="100",
        budget_ledger_path=str(tmp_path / "cost.sqlite"),
    )
    with pytest.raises(ValueError, match="local_only"):
        build_research_plan(synthetic_project, settings)


def test_research_pipeline_preserves_inputs_verifies_package_and_reuses_cache(synthetic_project):
    before = _hashes(synthetic_project)
    first = run_research(synthetic_project, ResearchSettings(max_workers=3))
    assert first.status == "succeeded"
    assert before == _hashes(synthetic_project)
    verified = research_engine(synthetic_project).verify_run(first.run_id)
    assert verified.valid and verified.checked_artifacts > 20
    search = search_run(synthetic_project, first.run_id, "methods", 3)
    assert search["hits"]
    assert all("style_corpus" not in item["metadata"]["source_path"] for item in search["hits"])
    proposals = propose_facts(
        synthetic_project,
        first.run_id,
        "methods",
        synthetic_project / "outputs/proposals",
        ResearchSettings(),
    )
    result = json.loads((proposals / "proposals.json").read_text(encoding="utf-8"))
    assert result["facts"] == [] and result["needs_human_review"] is True
    assert "Offline fixture" in result["unresolved_questions"][0]
    archive = Path(first.nodes["export"].artifact_dir) / "research_bundle.zip"
    with ZipFile(archive) as bundle:
        assert bundle.testzip() is None
        manifest = json.loads(bundle.read("manifest.json"))
        assert manifest["requires_human_review"] is True
        assert "project/project.yaml" in bundle.namelist()
        for item in manifest["files"]:
            payload = bundle.read(item["path"])
            assert len(payload) == item["bytes"]
            assert hashlib.sha256(payload).hexdigest() == item["sha256"]
    second = run_research(synthetic_project, ResearchSettings(max_workers=1))
    assert second.status == "succeeded"
    assert all(node.status == "cached" for node in second.nodes.values())
    assert before == _hashes(synthetic_project)
    assert research_engine(synthetic_project).verify_run(second.run_id).valid


def test_run_request_path_cannot_traverse(synthetic_project):
    with pytest.raises(ValueError, match="IDs"):
        run_research(synthetic_project, run_id="../../outside")


def test_research_cli_plan_and_missing_consent_fail_clearly(synthetic_project):
    runner = CliRunner()
    result = runner.invoke(app, ["research", "plan", str(synthetic_project)])
    assert result.exit_code == 0 and "research-manuscript-v1" in result.stdout
    denied = runner.invoke(app, ["research", "run", str(synthetic_project), "--provider", "openai"])
    assert denied.exit_code != 0
    assert "allow_network" in denied.stdout
    malformed = runner.invoke(
        app, ["research", "run", str(synthetic_project), "--budget-usd", "abc"]
    )
    assert malformed.exit_code != 0 and "Research run failed" in malformed.stdout
