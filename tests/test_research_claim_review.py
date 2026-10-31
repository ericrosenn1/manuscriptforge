"""Explicit author decisions reuse input stages and preserve raw claim records."""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from manuscriptforge.cli import app
from manuscriptforge.research.pipeline import run_research


def test_review_is_source_bound_and_invalidates_only_claim_descendants(synthetic_project):
    initial = run_research(synthetic_project)
    assert initial.status == "succeeded"
    raw_path = Path(initial.nodes["claims"].artifact_dir) / "claim_registry_unreviewed.json"
    raw_bytes = raw_path.read_bytes()
    claims = json.loads(raw_bytes)
    selected = next(
        (claim for claim in claims if claim["support_strength"] == "needs_review"), claims[0]
    )
    result = CliRunner().invoke(
        app,
        [
            "research",
            "review-claim",
            str(synthetic_project),
            initial.run_id,
            selected["claim_id"],
            "--decision",
            "approve",
            "--support",
            "direct",
            "--reviewer",
            "Synthetic test reviewer",
            "--reason",
            "Verified against original synthetic fixture evidence.",
        ],
    )
    assert result.exit_code == 0, result.output
    assert raw_path.read_bytes() == raw_bytes
    revised = run_research(synthetic_project)
    assert revised.status == "succeeded"
    for name in ("validate", "ingest", "profile"):
        assert revised.nodes[name].status == "cached", name
    assert revised.nodes["claims"].status == "succeeded"
    directory = Path(revised.nodes["claims"].artifact_dir)
    audit = json.loads((directory / "claim_review_audit.json").read_bytes())
    assert audit["approved"] == 1
    assert (directory / "claim_registry_unreviewed.json").read_bytes() == raw_bytes
    new_claims = json.loads((directory / "claim_registry.json").read_bytes())
    updated = next(claim for claim in new_claims if claim["claim_id"] == selected["claim_id"])
    assert updated["support_strength"] == "direct"
    assert updated["needs_human_review"] is False


@pytest.mark.parametrize("missing", ["decision", "support", "reviewer", "reason"])
def test_review_command_requires_explicit_decision_and_author(synthetic_project, missing):
    values = {
        "decision": "approve",
        "support": "direct",
        "reviewer": "Test",
        "reason": "Evidence reviewed.",
    }
    arguments = ["research", "review-claim", str(synthetic_project), "run", "claim"]
    for name, value in values.items():
        if name != missing:
            arguments.extend([f"--{name}", value])
    result = CliRunner().invoke(app, arguments, color=True)
    assert result.exit_code == 2  # Click rejects missing options before any command code.
    assert not (synthetic_project / "claim_reviews.jsonl").exists()
