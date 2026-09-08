from __future__ import annotations

import json
from pathlib import Path
from shutil import copytree, rmtree

from typer.testing import CliRunner

from manuscriptforge.cli import app
from manuscriptforge.models.review import FeedbackAnswer, ReviewItem
from manuscriptforge.pipeline.workflow import run_audit, run_draft, run_style_benchmark
from manuscriptforge.review.workflow import (
    apply_feedback,
    build_feedback_summary,
    build_review_plan,
    capture_feedback,
    generate_variants,
    generate_variants_for_item,
)
from manuscriptforge.utils.dates import utc_iso
from manuscriptforge.utils.io import read_json, write_json


def _copy_clean_example(tmp_path: Path) -> Path:
    src = Path("tests/fixtures/synthetic_project")
    project_dir = tmp_path / "project_minimal"
    copytree(src, project_dir)
    rmtree(project_dir / "outputs", ignore_errors=True)
    (project_dir / "outputs").mkdir()
    for name in ["style_feedback.jsonl", "accepted_rewrites.jsonl", "style_memory.json"]:
        path = project_dir / name
        if path.exists():
            path.unlink()
    return project_dir


def _drafted_project(tmp_path: Path) -> tuple[Path, Path]:
    project_dir = _copy_clean_example(tmp_path)
    run_dir = run_draft(project_dir)
    run_audit(project_dir)
    run_style_benchmark(project_dir)
    return project_dir, run_dir


def _write_feedback(path: Path, answers: list[FeedbackAnswer]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(answer.model_dump(mode="json"), sort_keys=True) for answer in answers) + "\n",
        encoding="utf-8",
    )


def test_review_plan_generation_and_deduplication(tmp_path: Path) -> None:
    project_dir, run_dir = _drafted_project(tmp_path)
    findings = read_json(run_dir / "audit_findings.json")
    findings.append(findings[0])
    write_json(run_dir / "audit_findings.json", findings)
    plan = build_review_plan(project_dir, run_dir, max_items=80)
    ids = [item.review_item_id for item in plan.review_items]
    assert len(ids) == len(set(ids))
    assert (run_dir / "review_plan.json").exists()
    assert (run_dir / "review_plan.md").exists()
    assert (run_dir / "review_plan.xlsx").exists()


def test_variant_generation_for_generic_and_overclaiming_phrases() -> None:
    item = ReviewItem(
        review_item_id="rvi_test",
        run_id="run",
        section_type="discussion",
        original_text="It is important to note that these findings prove clinical utility.",
        issue_type="overclaiming",
        severity="warning",
        priority_score=100,
        why_it_matters="Overclaiming.",
        suggested_review_action="Soften.",
        suggested_answer_format="Select.",
        created_at=utc_iso(),
    )
    variants = generate_variants_for_item(item, max_variants=5)
    texts = [variant.variant_text for variant in variants]
    assert any("is consistent with" in text or "may" in text for text in texts)
    assert not any("It is important to note" in text for text in texts[1:])
    assert all(not variant.introduces_new_numbers for variant in variants)


def test_capture_feedback_is_append_only_and_updates_memory(tmp_path: Path) -> None:
    project_dir, run_dir = _drafted_project(tmp_path)
    plan = build_review_plan(project_dir, run_dir, max_items=5)
    variants = generate_variants(run_dir, max_variants=3)
    selected = next(variant for variant in variants if variant.variant_text != plan.review_items[0].original_text)
    feedback_path = tmp_path / "feedback.jsonl"
    answer = FeedbackAnswer(
        feedback_id="fb_one",
        review_item_id=selected.review_item_id,
        selected_variant_id=selected.variant_id,
        accepted_text=selected.variant_text,
        rejected_variant_ids=[],
        style_tags=["hedging"],
        confidence="high",
        created_at=utc_iso(),
    )
    _write_feedback(feedback_path, [answer])
    capture_feedback(project_dir, run_dir, from_file=feedback_path)
    capture_feedback(project_dir, run_dir, from_file=feedback_path)
    lines = (project_dir / "style_feedback.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    memory = read_json(project_dir / "style_memory.json")
    assert "fb_one" in memory["feedback_provenance"]


def test_apply_feedback_creates_reviewed_copy_without_overwriting(tmp_path: Path) -> None:
    project_dir, run_dir = _drafted_project(tmp_path)
    plan = build_review_plan(project_dir, run_dir, max_items=10)
    variants = generate_variants(run_dir, max_variants=5)
    variant_by_item = {}
    for variant in variants:
        item = next(item for item in plan.review_items if item.review_item_id == variant.review_item_id)
        if variant.variant_text != item.original_text and not variant.introduces_new_numbers and not variant.introduces_new_citations:
            variant_by_item[item.review_item_id] = variant
            break
    assert variant_by_item
    variant = next(iter(variant_by_item.values()))
    original_manuscript = (run_dir / "manuscript.md").read_text(encoding="utf-8")
    feedback_path = tmp_path / "feedback.jsonl"
    _write_feedback(
        feedback_path,
        [
            FeedbackAnswer(
                feedback_id="fb_apply",
                review_item_id=variant.review_item_id,
                selected_variant_id=variant.variant_id,
                accepted_text=variant.variant_text,
                rejected_variant_ids=[],
                style_tags=["causal_language"],
                confidence="high",
                created_at=utc_iso(),
            )
        ],
    )
    capture_feedback(project_dir, run_dir, from_file=feedback_path)
    result = apply_feedback(project_dir, run_dir, feedback_path=feedback_path, mode="reviewed-copy")
    assert result["applied"]
    assert (run_dir / "manuscript_reviewed.md").exists()
    assert (run_dir / "manuscript_reviewed.docx").exists()
    assert (run_dir / "manuscript_reviewed_source_map.json").exists()
    assert (run_dir / "accepted_rewrites.jsonl").exists()
    assert (run_dir / "manuscript.md").read_text(encoding="utf-8") == original_manuscript


def test_apply_feedback_rejects_unknown_citation_and_new_number(tmp_path: Path) -> None:
    project_dir, run_dir = _drafted_project(tmp_path)
    plan = build_review_plan(project_dir, run_dir, max_items=5)
    generate_variants(run_dir, max_variants=2)
    item = plan.review_items[0]
    feedback_path = tmp_path / "unsafe_feedback.jsonl"
    _write_feedback(
        feedback_path,
        [
            FeedbackAnswer(
                feedback_id="fb_unsafe",
                review_item_id=item.review_item_id,
                user_rewrite=item.original_text + " This adds 9999 [cit_unknown].",
                confidence="high",
                created_at=utc_iso(),
            )
        ],
    )
    result = apply_feedback(project_dir, run_dir, feedback_path=feedback_path)
    assert not result["applied"]
    assert "unknown citation" in (run_dir / "feedback_application_report.md").read_text(encoding="utf-8")


def test_feedback_summary_outputs_and_empty_feedback(tmp_path: Path) -> None:
    project_dir, run_dir = _drafted_project(tmp_path)
    build_review_plan(project_dir, run_dir, max_items=5)
    generate_variants(run_dir, max_variants=2)
    summary = build_feedback_summary(project_dir, run_dir)
    assert summary.total_feedback_answers == 0
    assert (run_dir / "feedback_summary.md").exists()
    assert (run_dir / "feedback_summary.json").exists()
    assert (run_dir / "feedback_summary.xlsx").exists()


def test_review_commands_work_with_missing_benchmark_artifacts(tmp_path: Path) -> None:
    project_dir = _copy_clean_example(tmp_path)
    run_draft(project_dir)
    runner = CliRunner()
    result = runner.invoke(app, ["review-plan", str(project_dir), "--max-items", "5"])
    assert result.exit_code == 0, result.output
    result = runner.invoke(app, ["generate-variants", str(project_dir)])
    assert result.exit_code == 0, result.output
    result = runner.invoke(app, ["feedback-summary", str(project_dir)])
    assert result.exit_code == 0, result.output
