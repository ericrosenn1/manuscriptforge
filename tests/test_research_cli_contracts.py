"""CLI label conventions reach the evaluator without shell-empty-string tricks."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from manuscriptforge.cli import app
from manuscriptforge.evaluation.extraction_runner import LABEL_HINTS


@pytest.mark.parametrize(
    ("flags", "expected"),
    [
        ([], LABEL_HINTS),
        (["--no-label-hints"], ""),
        (["--label-hints", "Use named fields."], "Use named fields."),
    ],
)
def test_comparison_label_hints_are_explicit(monkeypatch, tmp_path, flags, expected):
    received = {}

    def comparison(*args, **kwargs):
        received.update(kwargs)
        return Path(args[3]) / "fixture"

    monkeypatch.setattr("manuscriptforge.research.cli.load_configured_key", lambda path: True)
    monkeypatch.setattr("manuscriptforge.research.comparison.compare_models", comparison)
    result = CliRunner().invoke(
        app,
        [
            "research",
            "compare-models",
            str(tmp_path / "output"),
            str(tmp_path / "budget.sqlite"),
            "1",
            "--allow-network",
            *flags,
        ],
    )
    assert result.exit_code == 0, result.output
    assert received["label_hints"] == expected


def test_conflicting_label_flags_fail_before_credentials(monkeypatch, tmp_path):
    def forbidden(path):
        pytest.fail("Credential access must follow argument validation")

    monkeypatch.setattr("manuscriptforge.research.cli.load_configured_key", forbidden)
    result = CliRunner().invoke(
        app,
        [
            "research",
            "compare-models",
            str(tmp_path / "output"),
            str(tmp_path / "budget.sqlite"),
            "1",
            "--label-hints",
            "custom",
            "--no-label-hints",
        ],
    )
    assert result.exit_code != 0
    assert "Choose either" in result.output
