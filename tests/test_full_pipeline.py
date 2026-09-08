from pathlib import Path
from shutil import copytree

from manuscriptforge.pipeline.workflow import run_ask_style, run_audit, run_draft, run_reviewer_sim
from manuscriptforge.utils.io import read_json


def test_full_pipeline_runs_on_example_copy(tmp_path) -> None:
    src = Path("tests/fixtures/synthetic_project")
    project_dir = tmp_path / "project_minimal"
    copytree(src, project_dir)
    run_dir = run_draft(project_dir)
    expected = [
        "manuscript.md",
        "manuscript.docx",
        "manuscript.tex",
        "claim_registry.xlsx",
        "citation_audit.xlsx",
        "style_profile.json",
        "style_guide.md",
        "style_report.md",
        "style_report.json",
        "style_report.xlsx",
        "reviewer_critique.md",
        "revision_questions.md",
        "ai_disclosure.md",
        "run_manifest.json",
    ]
    for name in expected:
        assert (run_dir / name).exists(), name
    source_map = read_json(run_dir / "source_map.json")
    assert any(section.get("style_chunk_ids") for section in source_map["sections"])
    assert run_audit(project_dir) == run_dir
    assert run_reviewer_sim(project_dir) == run_dir
    style_run = run_ask_style(project_dir)
    assert (style_run / "style_questions.md").exists()
