from __future__ import annotations

from pathlib import Path
from shutil import copytree

from typer.testing import CliRunner

from manuscriptforge.cli import app
from manuscriptforge.utils.io import read_json


def test_prep_pilot_creates_expected_folders_and_files(tmp_path: Path) -> None:
    runner = CliRunner()
    project_dir = tmp_path / "pilot_project"
    result = runner.invoke(
        app,
        ["prep-pilot", str(project_dir), "--template", "bioinformatics_methods"],
    )
    assert result.exit_code == 0, result.output

    for rel_path in [
        "project.yaml",
        "inputs/abstract.md",
        "inputs/rationale.md",
        "inputs/methods.md",
        "inputs/interpretation_notes.md",
        "inputs/figure_legends.md",
        "inputs/source_list.md",
        "inputs/results_tables/data_dictionary.md",
        "inputs/source_pdfs",
        "style_corpus/README_style_corpus.md",
        "feedback/README_feedback.md",
        "outputs",
        "planning/pilot_project_data_plan.md",
        "planning/pilot_input_checklist.csv",
        "planning/redaction_and_privacy_checklist.md",
        "planning/table_schema_guide.md",
        "planning/source_list_guide.md",
        "planning/style_corpus_guide.md",
    ]:
        assert (project_dir / rel_path).exists(), rel_path
    assert "Clearly labeled toy example" not in (project_dir / "inputs" / "abstract.md").read_text(encoding="utf-8")


def test_prep_pilot_with_examples_adds_clearly_labeled_toy_content(tmp_path: Path) -> None:
    runner = CliRunner()
    project_dir = tmp_path / "pilot_project"
    result = runner.invoke(app, ["prep-pilot", str(project_dir), "--with-examples"])
    assert result.exit_code == 0, result.output
    abstract = (project_dir / "inputs" / "abstract.md").read_text(encoding="utf-8")
    dictionary = (project_dir / "inputs" / "results_tables" / "data_dictionary.md").read_text(
        encoding="utf-8"
    )
    assert "Clearly labeled toy example" in abstract
    assert "not scientific evidence" in abstract
    assert "MF_DEMO_1" in dictionary


def test_prep_pilot_does_not_overwrite_without_flag_and_overwrites_with_flag(tmp_path: Path) -> None:
    runner = CliRunner()
    project_dir = tmp_path / "pilot_project"
    assert runner.invoke(app, ["prep-pilot", str(project_dir)]).exit_code == 0
    abstract_path = project_dir / "inputs" / "abstract.md"
    abstract_path.write_text("author supplied abstract\n", encoding="utf-8")

    result = runner.invoke(app, ["prep-pilot", str(project_dir)])
    assert result.exit_code == 0, result.output
    assert abstract_path.read_text(encoding="utf-8") == "author supplied abstract\n"

    result = runner.invoke(app, ["prep-pilot", str(project_dir), "--overwrite"])
    assert result.exit_code == 0, result.output
    assert "author supplied abstract" not in abstract_path.read_text(encoding="utf-8")
    assert "Abstract Drafting Template" in abstract_path.read_text(encoding="utf-8")


def test_inspect_pilot_detects_missing_inputs(tmp_path: Path) -> None:
    runner = CliRunner()
    project_dir = tmp_path / "not_initialized"
    result = runner.invoke(app, ["inspect-pilot", str(project_dir), "--json"])
    assert result.exit_code == 0, result.output
    data = read_json(project_dir / "planning" / "pilot_inspection_report.json")
    assert data["status"] == "not initialized"
    assert "inputs/abstract.md" in data["missing_required"]


def test_inspect_pilot_detects_ready_example_project(tmp_path: Path) -> None:
    runner = CliRunner()
    project_dir = tmp_path / "project_minimal"
    copytree(Path("tests/fixtures/synthetic_project"), project_dir)
    result = runner.invoke(app, ["inspect-pilot", str(project_dir), "--tree"])
    assert result.exit_code == 0, result.output
    data = read_json(project_dir / "planning" / "pilot_inspection_report.json")
    assert data["status"] == "example-only"
    assert data["counts"]["result_tables"] == 1
    assert data["counts"]["citation_source_files"] >= 1


def test_project_map_writes_tree_and_diagrams(tmp_path: Path) -> None:
    runner = CliRunner()
    project_dir = tmp_path / "pilot_project"
    assert runner.invoke(app, ["prep-pilot", str(project_dir)]).exit_code == 0
    result = runner.invoke(app, ["project-map", str(project_dir)])
    assert result.exit_code == 0, result.output
    assert "Pilot Project Architecture" in (project_dir / "planning" / "project_architecture.md").read_text(
        encoding="utf-8"
    )
    assert "flowchart TD" in (project_dir / "planning" / "workflow_diagram.md").read_text(encoding="utf-8")
    assert "pilot_project/" in (project_dir / "planning" / "project_tree.md").read_text(encoding="utf-8")


def test_infer_table_schema_creates_suggestions_for_toy_csv(tmp_path: Path) -> None:
    runner = CliRunner()
    project_dir = tmp_path / "pilot_project"
    assert runner.invoke(app, ["prep-pilot", str(project_dir)]).exit_code == 0
    table_path = project_dir / "inputs" / "results_tables" / "toy_results.csv"
    table_path.write_text(
        "gene,comparison,log2_fold_change,adjusted_p_value,n,notes\n"
        "GENE1,A_vs_B,1.2,0.01,10,toy row\n",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["infer-table-schema", str(project_dir)])
    assert result.exit_code == 0, result.output
    data = read_json(project_dir / "planning" / "table_schema_suggestions.json")
    roles = data["tables"][0]["column_roles"]
    assert roles["gene"] == "feature"
    assert roles["comparison"] == "comparison"
    assert roles["log2_fold_change"] == "effect_size"
    assert roles["adjusted_p_value"] == "adjusted_p_value"


def test_infer_table_schema_handles_malformed_csv_gracefully(tmp_path: Path) -> None:
    runner = CliRunner()
    project_dir = tmp_path / "pilot_project"
    assert runner.invoke(app, ["prep-pilot", str(project_dir)]).exit_code == 0
    bad_csv = project_dir / "inputs" / "results_tables" / "bad.csv"
    bad_csv.write_text('feature,value\n"unterminated,1\nnext,2\n', encoding="utf-8")

    result = runner.invoke(app, ["infer-table-schema", str(project_dir)])
    assert result.exit_code == 0, result.output
    data = read_json(project_dir / "planning" / "table_schema_suggestions.json")
    assert data["tables"][0]["warnings"]
    assert "could not read table" in data["tables"][0]["warnings"][0]


def test_make_source_list_creates_blank_template_when_no_references_exist(tmp_path: Path) -> None:
    runner = CliRunner()
    project_dir = tmp_path / "pilot_project"
    (project_dir / "inputs").mkdir(parents=True)
    result = runner.invoke(app, ["make-source-list", str(project_dir)])
    assert result.exit_code == 0, result.output
    source_list = (project_dir / "inputs" / "source_list.md").read_text(encoding="utf-8")
    assert "## Background" in source_list
    assert "Toy" not in source_list


def test_make_source_list_does_not_invent_citation_metadata(tmp_path: Path) -> None:
    runner = CliRunner()
    project_dir = tmp_path / "pilot_project"
    (project_dir / "inputs").mkdir(parents=True)
    (project_dir / "inputs" / "references.bib").write_text(
        "@article{KnownKey,\n  title={Supplied Title Only}\n}\n",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["make-source-list", str(project_dir)])
    assert result.exit_code == 0, result.output
    source_list = (project_dir / "inputs" / "source_list.md").read_text(encoding="utf-8")
    assert "KnownKey" in source_list
    assert "Supplied Title Only" in source_list
    assert "10." not in source_list
    assert "2024" not in source_list


def test_pilot_prep_commands_require_no_api_key_or_internet(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    runner = CliRunner()
    project_dir = tmp_path / "pilot_project"
    commands = [
        ["prep-pilot", str(project_dir)],
        ["inspect-pilot", str(project_dir)],
        ["project-map", str(project_dir)],
        ["infer-table-schema", str(project_dir)],
        ["make-source-list", str(project_dir)],
    ]
    for command in commands:
        result = runner.invoke(app, command)
        assert result.exit_code == 0, f"{command}: {result.output}"
