from pathlib import Path
from shutil import copytree

from typer.testing import CliRunner

from manuscriptforge.cli import app


def test_cli_init_and_validate(tmp_path) -> None:
    runner = CliRunner()
    project_dir = tmp_path / "demo"
    result = runner.invoke(app, ["init", str(project_dir)])
    assert result.exit_code == 0, result.output
    assert (project_dir / "project.yaml").exists()
    assert (project_dir / "inputs" / "results_tables").exists()

    result = runner.invoke(app, ["validate", str(project_dir)])
    assert result.exit_code == 0, result.output

    (project_dir / "inputs" / "abstract.md").unlink()
    result = runner.invoke(app, ["validate", str(project_dir)])
    assert result.exit_code == 1


def test_cli_validate_example() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["validate", str(Path("tests/fixtures/synthetic_project"))])
    assert result.exit_code == 0, result.output


def test_cli_non_ui_commands_run_on_example_copy(tmp_path) -> None:
    src = Path("tests/fixtures/synthetic_project")
    project_dir = tmp_path / "project_minimal"
    copytree(src, project_dir)
    runner = CliRunner()
    for args in [
        ["ingest", str(project_dir)],
        ["profile-style", str(project_dir)],
        ["build-claims", str(project_dir)],
        ["draft", str(project_dir)],
        ["audit", str(project_dir)],
        ["reviewer-sim", str(project_dir)],
        ["ask-style", str(project_dir)],
        ["style-summary", str(project_dir)],
        ["build-style-eval", str(project_dir)],
        ["style-benchmark", str(project_dir)],
        ["review-plan", str(project_dir), "--max-items", "5"],
        ["generate-variants", str(project_dir), "--max-variants", "2"],
        [
            "capture-feedback",
            str(project_dir),
            "--from-file",
            str(project_dir / "feedback" / "example_feedback.jsonl"),
        ],
        ["apply-feedback", str(project_dir), "--mode", "reviewed-copy"],
        ["feedback-summary", str(project_dir)],
        ["export", str(project_dir)],
    ]:
        result = runner.invoke(app, args)
        assert result.exit_code == 0, f"{args}: {result.output}"
