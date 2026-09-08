from __future__ import annotations

import json
from pathlib import Path
from shutil import copytree

from typer.testing import CliRunner

from manuscriptforge.audit.style_audit import audit_style_details
from manuscriptforge.cli import app
from manuscriptforge.ingest.style_ingest import build_style_chunks
from manuscriptforge.models.manuscript import Manuscript, ManuscriptSection
from manuscriptforge.style.memory import update_style_memory
from manuscriptforge.style.preference_trainer import generate_style_questions
from manuscriptforge.style.profiler import build_style_profile
from manuscriptforge.style.retrieval import get_style_exemplars


def _style_project(tmp_path: Path, text: str | None = None) -> Path:
    project_dir = tmp_path / "style_project"
    (project_dir / "style_corpus").mkdir(parents=True)
    (project_dir / "outputs").mkdir()
    if text is not None:
        (project_dir / "style_corpus" / "multi_section.md").write_text(text, encoding="utf-8")
    return project_dir


MULTI_SECTION_STYLE = """A cautious translational manuscript title

Abstract

The study may indicate an association that warrants careful follow-up.

Methods

We used versioned scripts, transparent tables, and reproducible intermediate artifacts.

Results

Across evaluated markers, the strongest differences were observed in inflammatory transcripts.

Discussion

Together, these observations suggest a focused signal that may warrant follow-up.

Limitations

Important limitations include sample size and the need for independent validation.
"""


def test_style_chunk_creation_and_heading_detection(tmp_path: Path) -> None:
    project_dir = _style_project(tmp_path, MULTI_SECTION_STYLE)
    chunks = build_style_chunks(project_dir)
    sections = {chunk.section_type for chunk in chunks}
    assert {"title", "abstract", "methods", "results", "discussion", "limitations"} <= sections
    assert all(chunk.chunk_id.startswith("sty_") for chunk in chunks)
    assert all(chunk.content_hash for chunk in chunks)


def test_empty_style_corpus_writes_empty_artifacts(tmp_path: Path) -> None:
    project_dir = _style_project(tmp_path)
    run_dir = tmp_path / "run"
    profile = build_style_profile(project_dir, run_dir)
    assert profile.global_features["style_chunk_index"]["chunk_count"] == 0
    assert profile.sentence_length_distribution["count"] == 0
    assert (run_dir / "style_chunks.jsonl").exists()
    assert (run_dir / "abstract_style_profile.json").exists()


def test_section_specific_profiles_and_exemplar_retrieval(tmp_path: Path) -> None:
    project_dir = _style_project(tmp_path, MULTI_SECTION_STYLE)
    profile = build_style_profile(project_dir)
    assert profile.section_features["results"]["chunk_count"] == 1
    exemplars = get_style_exemplars(profile, "Results", ["markers", "inflammatory"], max_examples=2)
    assert exemplars
    assert exemplars[0]["chunk_id"].startswith("sty_")
    assert exemplars[0]["style_example_label"].startswith("style exemplar")


def test_preference_questions_and_style_memory_update(tmp_path: Path) -> None:
    project_dir = _style_project(tmp_path, MULTI_SECTION_STYLE)
    profile = build_style_profile(project_dir)
    questions = generate_style_questions(profile)
    assert {question["question_type"] for question in questions} >= {
        "pairwise_preference",
        "rank_3_options",
        "rewrite_this_sentence",
        "choose_hedge_strength",
        "choose_best_transition",
        "choose_least_acceptable_option",
        "choose_citation_integration_style",
        "choose_limitation_phrasing",
    }
    assert all("why_this_question_was_asked" in question for question in questions)
    preference_path = project_dir / "style_preferences.jsonl"
    preference_path.write_text(
        json.dumps({"question": questions[3], "answer": "may indicate"}) + "\n",
        encoding="utf-8",
    )
    memory = update_style_memory(project_dir, profile)
    assert memory["answered_preferences"] == 1
    assert memory["preferred_hedge_level"] == "may indicate"
    assert (project_dir / "style_memory.json").exists()


def test_style_summary_and_build_style_eval_commands(tmp_path: Path) -> None:
    src = Path("tests/fixtures/synthetic_project")
    project_dir = tmp_path / "project_minimal"
    copytree(src, project_dir)
    runner = CliRunner()
    summary = runner.invoke(app, ["style-summary", str(project_dir)])
    assert summary.exit_code == 0, summary.output
    assert (project_dir / "outputs" / "latest" / "style_summary.md").exists()
    style_eval = runner.invoke(app, ["build-style-eval", str(project_dir)])
    assert style_eval.exit_code == 0, style_eval.output
    latest = project_dir / "outputs" / "latest"
    assert (latest / "style_eval_pairs.jsonl").exists()
    assert (latest / "style_eval_rewrite_prompts.jsonl").exists()
    assert (latest / "style_eval_rank_questions.jsonl").exists()


def test_style_audit_flags_generic_phrase(tmp_path: Path) -> None:
    project_dir = _style_project(tmp_path, MULTI_SECTION_STYLE)
    profile = build_style_profile(project_dir)
    manuscript = Manuscript(
        title="Demo",
        abstract="It is important to note that the result is interesting.",
        sections=[
            ManuscriptSection(
                section_name="Methods",
                section_type="methods",
                content="The analysis used standard methods.",
            ),
            ManuscriptSection(
                section_name="Results",
                section_type="results",
                content="The results clearly demonstrate a robust effect.",
            ),
            ManuscriptSection(
                section_name="Discussion",
                section_type="discussion",
                content="These findings prove clinical utility.",
            ),
            ManuscriptSection(
                section_name="Limitations",
                section_type="limitations",
                content="Important limitations include sample size.",
            ),
        ],
    )
    findings, _report, data = audit_style_details(manuscript, profile)
    messages = [finding.message for finding in findings]
    assert any("Generic LLM-like phrase" in message for message in messages)
    assert data["summary"]["finding_count"] == len(findings)
