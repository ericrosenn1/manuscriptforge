from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from manuscriptforge.audit.style_audit import GENERIC_LLM_PHRASES, HYPE_TERMS
from manuscriptforge.export.docx_exporter import export_docx
from manuscriptforge.export.excel_reporter import write_workbook
from manuscriptforge.export.markdown_exporter import export_markdown, manuscript_to_markdown
from manuscriptforge.models.manuscript import Manuscript
from manuscriptforge.models.review import (
    AcceptedRewrite,
    FeedbackAnswer,
    FeedbackSummary,
    ReviewItem,
    ReviewPlan,
    StyleVariant,
)
from manuscriptforge.style.benchmark import resolve_benchmark_run
from manuscriptforge.style.features import normalize_section_type
from manuscriptforge.utils.dates import utc_iso
from manuscriptforge.utils.hashing import sha256_text
from manuscriptforge.utils.ids import stable_id
from manuscriptforge.utils.io import ensure_dir, read_json, write_json, write_jsonl, write_text
from manuscriptforge.utils.text import split_sentences, word_tokens

NUMBER_RE = re.compile(r"(?<![A-Za-z0-9_])[-+]?\d+(?:\.\d+)?(?:e[-+]?\d+)?%?(?![A-Za-z0-9_])", re.I)
CITATION_ID_RE = re.compile(r"\b(?:cit|ref|src)_[A-Za-z0-9_]+\b")

ISSUE_PRIORITY = {
    "unsupported_claim": 100,
    "numeric_traceability": 95,
    "overclaiming": 88,
    "citation_gap": 82,
    "weak_benchmark_score": 72,
    "generic_phrase": 68,
    "style_deviation": 62,
    "journal_compliance": 55,
    "methods_reproducibility": 55,
    "unclear_interpretation": 50,
    "user_preference_needed": 35,
}

PRIORITY_FILTERS = {
    "style": {"style_deviation", "generic_phrase", "weak_benchmark_score", "user_preference_needed"},
    "claims": {"unsupported_claim", "overclaiming", "unclear_interpretation"},
    "citations": {"citation_gap"},
    "journal": {"journal_compliance", "methods_reproducibility"},
    "no-invention": {"unsupported_claim", "numeric_traceability"},
    "benchmark": {"weak_benchmark_score"},
}


def _read_json_if_exists(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return read_json(path)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        loaded = json.loads(line)
        if isinstance(loaded, dict):
            rows.append(loaded)
    return rows


def _append_jsonl(path: Path, rows: list[Any]) -> None:
    ensure_dir(path.parent)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            if hasattr(row, "model_dump"):
                row = row.model_dump(mode="json")
            handle.write(json.dumps(row, sort_keys=True) + "\n")


def _run_id(run_dir: Path) -> str:
    return run_dir.name


def _manifest_artifacts(run_dir: Path, names: list[str]) -> list[str]:
    return [name for name in names if (run_dir / name).exists()]


def _source_map_entries(source_map: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for section in source_map.get("sections", []):
        for paragraph in section.get("paragraphs", []):
            rows.append(
                {
                    "section_name": section.get("section_name"),
                    "section_type": normalize_section_type(str(section.get("section_type") or section.get("section_name") or "")),
                    "paragraph_index": paragraph.get("paragraph_index"),
                    "text": paragraph.get("text", ""),
                    "claim_ids": paragraph.get("claim_ids", section.get("claim_ids", [])),
                    "citation_ids": paragraph.get("citation_ids", section.get("citation_ids", [])),
                    "style_chunk_ids": paragraph.get("style_chunk_ids", section.get("style_chunk_ids", [])),
                    "unresolved_flags": paragraph.get("unresolved_flags", []),
                    "generated_by": paragraph.get("generated_by"),
                }
            )
    return rows


def _section_entry(entries: list[dict[str, Any]], location: str | None, section_filter: str | None = None) -> dict[str, Any]:
    normalized_location = normalize_section_type(location or "")
    normalized_filter = normalize_section_type(section_filter) if section_filter else None
    for entry in entries:
        if normalized_filter and entry.get("section_type") != normalized_filter:
            continue
        if normalized_location != "unknown" and entry.get("section_type") == normalized_location:
            return entry
    for entry in entries:
        if not normalized_filter or entry.get("section_type") == normalized_filter:
            return entry
    return {}


def _first_sentence(text: str) -> tuple[str, int | None]:
    sentences = split_sentences(text)
    if not sentences:
        return text.strip()[:500], None
    return sentences[0], 1


def _issue_from_finding(finding: dict[str, Any]) -> str:
    category = str(finding.get("category", "")).lower()
    message = str(finding.get("message", "")).lower()
    if category == "citation" or "citation" in message:
        return "citation_gap"
    if category == "overclaiming" or any(term in message for term in ["causal", "overclaim", "strong language"]):
        return "overclaiming"
    if category == "no_invention":
        return "numeric_traceability" if "numeric" in message or "number" in message else "unsupported_claim"
    if category == "journal":
        return "journal_compliance"
    if category == "reproducibility":
        return "methods_reproducibility"
    if category == "claim":
        return "unsupported_claim"
    if category == "style":
        return "generic_phrase" if "generic" in message else "style_deviation"
    return "unclear_interpretation"


def _severity_priority(severity: str) -> int:
    return {"serious": 40, "warning": 20, "info": 5}.get(severity, 5)


def _review_item_from_finding(
    run_id: str,
    finding: dict[str, Any],
    entries: list[dict[str, Any]],
    section_filter: str | None,
) -> ReviewItem | None:
    issue_type = _issue_from_finding(finding)
    entry = _section_entry(entries, str(finding.get("location") or ""), section_filter)
    if section_filter and entry and entry.get("section_type") != normalize_section_type(section_filter):
        return None
    original_text, sentence_index = _first_sentence(str(entry.get("text") or finding.get("message") or ""))
    severity = str(finding.get("severity", "info"))
    priority = ISSUE_PRIORITY.get(issue_type, 40) + _severity_priority(severity)
    finding_id = str(finding.get("finding_id", stable_id("finding", json.dumps(finding, sort_keys=True))))
    return ReviewItem(
        review_item_id=stable_id("rvi", f"{run_id}:{finding_id}:{issue_type}:{original_text[:120]}"),
        run_id=run_id,
        section_name=entry.get("section_name") or str(finding.get("location") or "manuscript"),
        section_type=entry.get("section_type"),
        paragraph_index=entry.get("paragraph_index"),
        sentence_index=sentence_index,
        original_text=original_text,
        issue_type=issue_type,  # type: ignore[arg-type]
        severity=severity if severity in {"info", "warning", "serious"} else "info",  # type: ignore[arg-type]
        priority_score=float(priority),
        linked_claim_ids=list(finding.get("related_claim_ids", []) or entry.get("claim_ids", [])),
        linked_citation_ids=list(finding.get("related_citation_ids", []) or entry.get("citation_ids", [])),
        linked_finding_ids=[finding_id],
        source_map_entry=entry,
        why_it_matters=str(finding.get("message", "This issue needs human review.")),
        suggested_review_action=str(finding.get("suggested_fix") or "Choose or write an acceptable revision."),
        suggested_answer_format="Select a variant, write a replacement, reject all variants, or skip.",
        created_at=utc_iso(),
    )


def _review_items_from_benchmark(
    run_id: str,
    benchmark: dict[str, Any],
    entries: list[dict[str, Any]],
    section_filter: str | None,
) -> list[ReviewItem]:
    items: list[ReviewItem] = []
    for metric in benchmark.get("metric_scores", []):
        if not isinstance(metric, dict):
            continue
        metric_name = str(metric.get("metric_name", ""))
        score = metric.get("normalized_score")
        status = metric.get("status")
        section_type = normalize_section_type(str(metric.get("section_type") or ""))
        if section_filter and section_type != normalize_section_type(section_filter):
            continue
        if metric_name.endswith("_style_score") and (status == "warning" or (isinstance(score, (int, float)) and score < 0.7)):
            entry = _section_entry(entries, section_type, section_type)
            original_text, sentence_index = _first_sentence(str(entry.get("text") or metric.get("explanation") or ""))
            items.append(
                ReviewItem(
                    review_item_id=stable_id("rvi", f"{run_id}:{metric.get('metric_id')}:{original_text[:120]}"),
                    run_id=run_id,
                    section_name=entry.get("section_name") or section_type,
                    section_type=section_type,
                    paragraph_index=entry.get("paragraph_index"),
                    sentence_index=sentence_index,
                    original_text=original_text,
                    issue_type="weak_benchmark_score",
                    severity="warning",
                    priority_score=float(ISSUE_PRIORITY["weak_benchmark_score"] + 10),
                    linked_claim_ids=list(entry.get("claim_ids", [])),
                    linked_citation_ids=list(entry.get("citation_ids", [])),
                    linked_benchmark_metrics=[metric_name],
                    source_map_entry=entry,
                    why_it_matters=str(metric.get("explanation") or "This section scored weakly against the style benchmark."),
                    suggested_review_action=str(metric.get("suggested_fix") or "Compare this section to style exemplars and choose a preferred rewrite."),
                    suggested_answer_format="Select the closest style variant or provide a rewrite.",
                    created_at=utc_iso(),
                )
            )
    return items


def _review_items_from_revision_questions(
    run_id: str,
    questions: list[dict[str, Any]],
    entries: list[dict[str, Any]],
    section_filter: str | None,
) -> list[ReviewItem]:
    items = []
    for question in questions[:20]:
        section_type = normalize_section_type(str(question.get("section_type") or question.get("section") or ""))
        if section_filter and section_type != normalize_section_type(section_filter):
            continue
        entry = _section_entry(entries, section_type, section_type if section_type != "unknown" else section_filter)
        text = str(question.get("question") or question.get("prompt") or question.get("suggested_answer_format") or "")
        if not text:
            continue
        items.append(
            ReviewItem(
                review_item_id=stable_id("rvi", f"{run_id}:question:{question.get('question_id')}:{text[:120]}"),
                run_id=run_id,
                section_name=entry.get("section_name") or section_type,
                section_type=entry.get("section_type") or section_type,
                paragraph_index=entry.get("paragraph_index"),
                original_text=text[:500],
                issue_type="user_preference_needed",
                severity="info",
                priority_score=float(ISSUE_PRIORITY["user_preference_needed"]),
                linked_claim_ids=list(question.get("linked_claim_ids", []) or entry.get("claim_ids", [])),
                linked_citation_ids=list(entry.get("citation_ids", [])),
                linked_finding_ids=list(question.get("linked_finding_ids", [])),
                source_map_entry=entry,
                why_it_matters=str(question.get("why_it_matters") or "This question captures an author decision needed before revision."),
                suggested_review_action="Answer this preference or convert it into an accepted rewrite.",
                suggested_answer_format=str(question.get("suggested_answer_format") or "Short free-text answer."),
                created_at=utc_iso(),
            )
        )
    return items


def _dedupe_items(items: list[ReviewItem]) -> list[ReviewItem]:
    seen = set()
    deduped = []
    for item in sorted(items, key=lambda value: value.priority_score, reverse=True):
        key = (
            item.issue_type,
            item.section_type,
            item.paragraph_index,
            item.original_text[:140].lower(),
            tuple(item.linked_finding_ids),
            tuple(item.linked_benchmark_metrics),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _filter_items(items: list[ReviewItem], priority: str, section_filter: str | None) -> list[ReviewItem]:
    allowed = PRIORITY_FILTERS.get(priority)
    filtered = []
    normalized_section = normalize_section_type(section_filter) if section_filter else None
    for item in items:
        if allowed and item.issue_type not in allowed:
            continue
        if normalized_section and item.section_type != normalized_section:
            continue
        filtered.append(item)
    return filtered


def _summary_counts(items: list[ReviewItem]) -> dict[str, int]:
    counts: dict[str, int] = dict(Counter(str(item.issue_type) for item in items))
    for key, value in Counter(item.severity for item in items).items():
        counts[f"severity_{key}"] = value
    return counts


def render_review_plan_markdown(plan: ReviewPlan) -> str:
    groups = [
        ("Serious Scientific Issues", {"unsupported_claim", "numeric_traceability", "overclaiming", "unclear_interpretation"}),
        ("Citation And Traceability Issues", {"citation_gap"}),
        ("Style And Benchmark Issues", {"style_deviation", "generic_phrase", "weak_benchmark_score"}),
        ("Journal And Reproducibility Issues", {"journal_compliance", "methods_reproducibility"}),
        ("Optional Style Preference Questions", {"user_preference_needed"}),
    ]
    lines = [
        "# Review Plan",
        "",
        f"- Run: {plan.run_id}",
        f"- Items: {len(plan.review_items)}",
        f"- Strategy: {plan.selection_strategy}",
        "",
    ]
    for heading, issue_types in groups:
        lines.extend([f"## {heading}", ""])
        group_items = [item for item in plan.review_items if item.issue_type in issue_types]
        if not group_items:
            lines.append("No items selected.")
            lines.append("")
            continue
        for item in group_items:
            lines.append(f"### {item.review_item_id}")
            lines.append(f"- Severity: {item.severity}")
            lines.append(f"- Section: {item.section_name or item.section_type or 'manuscript'}")
            lines.append(f"- Issue: {item.issue_type}")
            lines.append(f"- Why it matters: {item.why_it_matters}")
            lines.append(f"- Suggested action: {item.suggested_review_action}")
            lines.append("")
            lines.append("> " + item.original_text.replace("\n", " ")[:700])
            lines.append("")
    return "\n".join(lines).strip() + "\n"


def write_review_plan(run_dir: Path, plan: ReviewPlan) -> None:
    write_json(run_dir / "review_plan.json", plan)
    write_text(run_dir / "review_plan.md", render_review_plan_markdown(plan))
    write_workbook(
        run_dir / "review_plan.xlsx",
        {
            "Review Items": [item.model_dump(mode="json") for item in plan.review_items],
            "Summary": [{"field": key, "value": value} for key, value in plan.summary_counts.items()],
        },
    )


def build_review_plan(
    project_dir: Path,
    run_dir: Path,
    *,
    max_items: int = 30,
    priority: str = "all",
    section_filter: str | None = None,
) -> ReviewPlan:
    source_map = _read_json_if_exists(run_dir / "source_map.json", {})
    entries = _source_map_entries(source_map if isinstance(source_map, dict) else {})
    findings = _read_json_if_exists(run_dir / "audit_findings.json", [])
    benchmark = _read_json_if_exists(run_dir / "style_benchmark_scores.json", {})
    questions = _read_json_if_exists(run_dir / "revision_questions.json", [])
    items: list[ReviewItem] = []
    if isinstance(findings, list):
        for finding in findings:
            if isinstance(finding, dict):
                item = _review_item_from_finding(_run_id(run_dir), finding, entries, section_filter)
                if item is not None:
                    items.append(item)
    if isinstance(benchmark, dict):
        items.extend(_review_items_from_benchmark(_run_id(run_dir), benchmark, entries, section_filter))
    if isinstance(questions, list):
        items.extend(_review_items_from_revision_questions(_run_id(run_dir), questions, entries, section_filter))
    items = _filter_items(_dedupe_items(items), priority, section_filter)
    if max_items > 0:
        items = items[:max_items]
    artifacts = _manifest_artifacts(
        run_dir,
        [
            "manuscript.md",
            "source_map.json",
            "style_report.json",
            "style_benchmark_scores.json",
            "no_invention_report.md",
            "claim_audit.xlsx",
            "citation_audit.xlsx",
            "overclaiming_report.md",
            "journal_audit.md",
            "revision_questions.json",
            "style_memory.json",
            "style_profile.json",
        ],
    )
    plan = ReviewPlan(
        run_id=_run_id(run_dir),
        project_path=str(project_dir),
        review_items=items,
        selection_strategy=f"priority={priority}; section={section_filter or 'all'}; max_items={max_items}",
        input_artifacts=artifacts,
        generated_at=utc_iso(),
        summary_counts=_summary_counts(items),
    )
    write_review_plan(run_dir, plan)
    return plan


def _numbers(text: str) -> set[str]:
    return {match.group(0).rstrip("%") for match in NUMBER_RE.finditer(text)}


def _citation_ids(text: str) -> set[str]:
    return set(CITATION_ID_RE.findall(text))


def _phrase_counts(text: str, phrases: list[str]) -> dict[str, int]:
    lower = text.lower()
    return {phrase: lower.count(phrase.lower()) for phrase in phrases if lower.count(phrase.lower())}


def _remove_generic(text: str) -> str:
    revised = text
    for phrase in GENERIC_LLM_PHRASES:
        revised = re.sub(re.escape(phrase), "", revised, flags=re.I)
    return re.sub(r"\s+", " ", revised).strip(" ,")


def _cautious(text: str) -> str:
    revised = text
    replacements = {
        "proves": "is consistent with",
        "prove": "may support",
        "clearly demonstrates": "is consistent with",
        "demonstrates": "suggests",
        "guarantees": "does not establish without additional evidence",
        "definitive": "preliminary",
        "causes": "is associated with",
        "cause": "be associated with",
    }
    for source, target in replacements.items():
        revised = re.sub(rf"\b{re.escape(source)}\b", target, revised, flags=re.I)
    if revised == text and not any(token in revised.lower() for token in ["may", "suggest", "consistent with"]):
        revised = "The supplied evidence may indicate that " + revised[:1].lower() + revised[1:]
    return revised


def _concise(text: str) -> str:
    revised = _remove_generic(text)
    revised = re.sub(r"\bvery\b|\bclearly\b|\bimportant(?:ly)?\b", "", revised, flags=re.I)
    return re.sub(r"\s+", " ", revised).strip()


def _style_closer(text: str, section_type: str | None, style_memory: dict[str, Any]) -> str:
    revised = _cautious(_remove_generic(text))
    transitions = [str(item) for item in style_memory.get("preferred_transition_patterns", []) if str(item).strip()]
    if section_type in {"discussion", "limitations"} and transitions and not revised.lower().startswith(transitions[0].lower()):
        revised = transitions[0].rstrip(",") + ", " + revised[:1].lower() + revised[1:]
    hedge = str(style_memory.get("preferred_hedge_level", ""))
    if hedge and hedge != "corpus_default" and hedge.lower() not in revised.lower():
        revised = revised.replace("suggests", hedge, 1) if "suggests" in revised.lower() else revised
    return revised


def _methods_precise(text: str) -> str:
    base = _concise(text)
    return (
        base
        + " [AUTHOR: add dataset identifiers, software names and versions, preprocessing steps, statistical tests, thresholds, and sample counts.]"
    )


def _results_restrained(text: str) -> str:
    return _cautious(text).replace("interprets", "describes")


def _discussion_synthetic(text: str) -> str:
    revised = _cautious(text)
    if not revised.lower().startswith("together"):
        revised = "Together, " + revised[:1].lower() + revised[1:]
    return revised


def _limitation_style(text: str) -> str:
    if "limitation" in text.lower() or "limited" in text.lower():
        return _cautious(text)
    return "Important limitations include author-confirmed constraints in the supplied evidence: " + _cautious(text)


def _variant(
    item: ReviewItem,
    strategy: str,
    text: str,
    rationale: str,
    risk: str = "low",
) -> StyleVariant:
    return StyleVariant(
        variant_id=stable_id("var", f"{item.review_item_id}:{strategy}:{text}"),
        review_item_id=item.review_item_id,
        strategy=strategy,  # type: ignore[arg-type]
        variant_text=text,
        rationale=rationale,
        predicted_style_effects={"issue_type": item.issue_type, "section_type": item.section_type},
        predicted_scientific_risk=risk,  # type: ignore[arg-type]
        preserves_claim_ids=item.linked_claim_ids,
        preserves_citation_ids=item.linked_citation_ids,
        introduces_new_numbers=bool(_numbers(text) - _numbers(item.original_text)),
        introduces_new_citations=bool(_citation_ids(text) - _citation_ids(item.original_text) - set(item.linked_citation_ids)),
        needs_audit=risk == "high",
        created_at=utc_iso(),
    )


def generate_variants_for_item(
    item: ReviewItem,
    style_memory: dict[str, Any] | None = None,
    max_variants: int = 4,
) -> list[StyleVariant]:
    style_memory = style_memory or {}
    candidates = [
        _variant(item, "minimal_change", item.original_text, "Preserves the original text for explicit author rejection or acceptance."),
    ]
    if item.issue_type in {"overclaiming", "unsupported_claim", "numeric_traceability", "unclear_interpretation", "generic_phrase"}:
        candidates.append(_variant(item, "more_cautious", _cautious(_remove_generic(item.original_text)), "Softens causal or over-strong wording without adding evidence."))
    if item.issue_type in {"style_deviation", "generic_phrase", "weak_benchmark_score", "user_preference_needed"}:
        candidates.append(_variant(item, "user_style_closer", _style_closer(item.original_text, item.section_type, style_memory), "Uses stored style memory and conservative corpus-derived style tendencies."))
    if item.section_type == "methods" or item.issue_type == "methods_reproducibility":
        candidates.append(_variant(item, "methods_more_precise", _methods_precise(item.original_text), "Adds author-facing placeholders for missing Methods detail.", "medium"))
    if item.section_type == "results":
        candidates.append(_variant(item, "results_more_restrained", _results_restrained(item.original_text), "Keeps Results descriptive and less interpretive."))
    if item.section_type == "discussion":
        candidates.append(_variant(item, "discussion_more_synthetic", _discussion_synthetic(item.original_text), "Adds cautious synthesis without inventing new claims."))
    if item.section_type == "limitations":
        candidates.append(_variant(item, "limitation_style", _limitation_style(item.original_text), "Frames the sentence as explicit limitation language."))
    if item.linked_citation_ids and not _citation_ids(item.original_text):
        citation_text = item.original_text.rstrip(".") + f" [{'; '.join(item.linked_citation_ids)}]."
        candidates.append(_variant(item, "citation_integrated", citation_text, "Integrates only citation IDs already linked to this review item."))
    candidates.append(_variant(item, "more_concise", _concise(item.original_text), "Removes generic filler while preserving the core sentence."))

    unique: list[StyleVariant] = []
    seen = set()
    for candidate in candidates:
        key = candidate.variant_text.strip()
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(candidate)
        if len(unique) >= max_variants:
            break
    return unique


def render_variants_markdown(items: list[ReviewItem], variants: list[StyleVariant]) -> str:
    by_item: dict[str, list[StyleVariant]] = defaultdict(list)
    item_map = {item.review_item_id: item for item in items}
    for variant in variants:
        by_item[variant.review_item_id].append(variant)
    lines = ["# Rewrite Variants", ""]
    for item_id, values in by_item.items():
        item = item_map.get(item_id)
        lines.append(f"## {item_id}")
        if item:
            lines.append(f"- Issue: {item.issue_type}")
            lines.append(f"- Section: {item.section_name or item.section_type or 'manuscript'}")
            lines.append("")
            lines.append("> " + item.original_text.replace("\n", " ")[:700])
            lines.append("")
        for variant in values:
            lines.append(f"### {variant.strategy} (`{variant.variant_id}`)")
            lines.append(variant.variant_text)
            lines.append("")
            lines.append(f"- Rationale: {variant.rationale}")
            lines.append(f"- Scientific risk: {variant.predicted_scientific_risk}")
            lines.append(f"- Needs audit: {variant.needs_audit}")
            lines.append("")
    return "\n".join(lines).strip() + "\n"


def generate_variants(
    run_dir: Path,
    *,
    review_plan_path: Path | None = None,
    max_variants: int = 4,
    section_filter: str | None = None,
) -> list[StyleVariant]:
    plan_path = review_plan_path or run_dir / "review_plan.json"
    if not plan_path.exists():
        raise FileNotFoundError("No review_plan.json found. Run review-plan first or pass --review-plan.")
    plan = ReviewPlan.model_validate(read_json(plan_path))
    style_memory = _read_json_if_exists(run_dir / "style_memory.json", {})
    variants: list[StyleVariant] = []
    items = [
        item
        for item in plan.review_items
        if not section_filter or item.section_type == normalize_section_type(section_filter)
    ]
    for item in items:
        variants.extend(generate_variants_for_item(item, style_memory if isinstance(style_memory, dict) else {}, max_variants))
    write_jsonl(run_dir / "style_variants.jsonl", variants)
    write_text(run_dir / "style_variants.md", render_variants_markdown(items, variants))
    write_workbook(
        run_dir / "style_variants.xlsx",
        {
            "Review Items": [item.model_dump(mode="json") for item in items],
            "Variants": [variant.model_dump(mode="json") for variant in variants],
        },
    )
    return variants


def _load_feedback_rows(path: Path) -> list[FeedbackAnswer]:
    return [FeedbackAnswer.model_validate(row) for row in _read_jsonl(path)]


def _load_feedback(project_dir: Path, run_dir: Path) -> list[FeedbackAnswer]:
    rows = []
    for path in [project_dir / "style_feedback.jsonl", run_dir / "style_feedback.jsonl"]:
        if path.exists():
            rows.extend(_load_feedback_rows(path))
    deduped: dict[str, FeedbackAnswer] = {}
    for row in rows:
        deduped[row.feedback_id] = row
    return list(deduped.values())


def _feedback_from_interactive(variants: list[StyleVariant]) -> list[FeedbackAnswer]:
    answers: list[FeedbackAnswer] = []
    by_item: dict[str, list[StyleVariant]] = defaultdict(list)
    for variant in variants:
        by_item[variant.review_item_id].append(variant)
    for item_id, item_variants in by_item.items():
        print(f"\nReview item: {item_id}")
        for index, variant in enumerate(item_variants, start=1):
            print(f"{index}. {variant.strategy}: {variant.variant_text}")
        choice = input("Choose variant number, r=write rewrite, s=skip, b=all bad: ").strip().lower()
        if choice == "s":
            continue
        selected = None
        rewrite = None
        rejected = []
        if choice == "b":
            rejected = [variant.variant_id for variant in item_variants]
        elif choice == "r":
            rewrite = input("Enter your rewrite: ").strip()
        elif choice.isdigit() and 1 <= int(choice) <= len(item_variants):
            selected = item_variants[int(choice) - 1]
        note = input("Optional note: ").strip()
        answers.append(
            FeedbackAnswer(
                feedback_id=stable_id("fb", f"{item_id}:{choice}:{rewrite or selected.variant_id if selected else ''}:{note}"),
                review_item_id=item_id,
                selected_variant_id=selected.variant_id if selected else None,
                accepted_text=selected.variant_text if selected else None,
                rejected_variant_ids=rejected,
                user_rewrite=rewrite,
                user_notes=note or None,
                confidence="medium",
                created_at=utc_iso(),
            )
        )
    return answers


def update_style_memory_from_feedback(project_dir: Path, feedback: list[FeedbackAnswer], accepted: list[AcceptedRewrite] | None = None) -> dict[str, Any]:
    memory_path = project_dir / "style_memory.json"
    memory = read_json(memory_path) if memory_path.exists() else {}
    if not isinstance(memory, dict):
        memory = {}
    accepted = accepted or []
    feedback_ids = [answer.feedback_id for answer in feedback]
    memory["last_updated"] = utc_iso()
    memory.setdefault("feedback_provenance", [])
    memory["feedback_provenance"] = sorted(set(memory["feedback_provenance"] + feedback_ids))
    memory.setdefault("accepted_rewrites", [])
    memory.setdefault("rejected_rewrites", [])
    for rewrite in accepted:
        memory["accepted_rewrites"].append(
            {
                "rewrite_id": rewrite.rewrite_id,
                "section_type": rewrite.section_type,
                "accepted_text": rewrite.accepted_text,
                "feedback_ids": feedback_ids,
            }
        )
    for answer in feedback:
        if answer.rejected_variant_ids:
            memory["rejected_rewrites"].extend(
                {"feedback_id": answer.feedback_id, "variant_id": variant_id}
                for variant_id in answer.rejected_variant_ids
            )
    section_counts = Counter(rewrite.section_type or "unknown" for rewrite in accepted)
    existing_sections = memory.get("section_preferences", {})
    if not isinstance(existing_sections, dict):
        existing_sections = {}
    for section, count in section_counts.items():
        existing_sections[section] = int(existing_sections.get(section, 0)) + count
    memory["section_preferences"] = existing_sections
    feature_counts = Counter(tag for answer in feedback for tag in answer.style_tags)
    if any(answer.claim_strength_preference for answer in feedback):
        choices = [answer.claim_strength_preference for answer in feedback if answer.claim_strength_preference]
        memory["preferred_causal_language"] = "cautious" if any("caut" in choice.lower() or "hedg" in choice.lower() for choice in choices) else choices[-1]
    if any(answer.citation_preference for answer in feedback):
        memory["preferred_citation_integration"] = [answer.citation_preference for answer in feedback if answer.citation_preference][-1]
    memory["confidence_by_feature"] = {
        feature: "high" if count >= 8 else "medium" if count >= 3 else "low"
        for feature, count in feature_counts.items()
    }
    if accepted:
        memory["preferred_sentence_density"] = memory.get("preferred_sentence_density")
        memory["preferred_paragraph_density"] = memory.get("preferred_paragraph_density")
    write_json(memory_path, memory)
    return memory


def capture_feedback(
    project_dir: Path,
    run_dir: Path,
    *,
    variants_path: Path | None = None,
    interactive: bool = False,
    from_file: Path | None = None,
) -> list[FeedbackAnswer]:
    variants_path = variants_path or run_dir / "style_variants.jsonl"
    variants = [StyleVariant.model_validate(row) for row in _read_jsonl(variants_path)] if variants_path.exists() else []
    answers: list[FeedbackAnswer] = []
    if from_file is not None:
        answers.extend(_load_feedback_rows(from_file))
    elif interactive:
        answers.extend(_feedback_from_interactive(variants))
    else:
        raise ValueError("capture-feedback requires --interactive or --from-file.")
    if not answers:
        return []
    _append_jsonl(project_dir / "style_feedback.jsonl", answers)
    _append_jsonl(run_dir / "style_feedback.jsonl", answers)
    update_style_memory_from_feedback(project_dir, answers)
    return answers


def _known_citations(manuscript: Manuscript, source_map: dict[str, Any]) -> set[str]:
    known = {citation.citation_id for citation in manuscript.references}
    for entry in _source_map_entries(source_map):
        known.update(str(item) for item in entry.get("citation_ids", []))
    return known


def _feedback_text(answer: FeedbackAnswer, variants: dict[str, StyleVariant]) -> str | None:
    if answer.user_rewrite and answer.user_rewrite.strip():
        return answer.user_rewrite.strip()
    if answer.accepted_text and answer.accepted_text.strip():
        return answer.accepted_text.strip()
    if answer.selected_variant_id and answer.selected_variant_id in variants:
        return variants[answer.selected_variant_id].variant_text
    return None


def _safe_rewrite(original: str, replacement: str, known_citations: set[str]) -> tuple[bool, list[str]]:
    reasons = []
    new_numbers = _numbers(replacement) - _numbers(original)
    if new_numbers:
        reasons.append("replacement introduces new numeric value(s): " + ", ".join(sorted(new_numbers)))
    new_citations = _citation_ids(replacement) - _citation_ids(original)
    unknown = new_citations - known_citations
    if unknown:
        reasons.append("replacement introduces unknown citation ID(s): " + ", ".join(sorted(unknown)))
    return not reasons, reasons


def _apply_to_manuscript(manuscript: Manuscript, item: ReviewItem, replacement: str) -> bool:
    original = item.original_text
    if item.section_type == "abstract" and original in manuscript.abstract:
        manuscript.abstract = manuscript.abstract.replace(original, replacement, 1)
        return True
    for section in manuscript.sections:
        if item.section_type and normalize_section_type(section.section_type or section.section_name) != item.section_type:
            continue
        if original in section.content:
            section.content = section.content.replace(original, replacement, 1)
            section.revision_notes.append(f"Author feedback applied for {item.review_item_id}.")
            return True
    if original in manuscript.abstract:
        manuscript.abstract = manuscript.abstract.replace(original, replacement, 1)
        return True
    for section in manuscript.sections:
        if original in section.content:
            section.content = section.content.replace(original, replacement, 1)
            section.revision_notes.append(f"Author feedback applied for {item.review_item_id}.")
            return True
    return False


def _accepted_rewrite(answer: FeedbackAnswer, item: ReviewItem, replacement: str, variants: dict[str, StyleVariant]) -> AcceptedRewrite:
    rejected_texts = [
        variants[variant_id].variant_text for variant_id in answer.rejected_variant_ids if variant_id in variants
    ]
    feature_targets = sorted(set(answer.style_tags + [item.issue_type]))
    selected_strategy = variants[answer.selected_variant_id].strategy if answer.selected_variant_id in variants else None
    if selected_strategy:
        feature_targets.append(str(selected_strategy))
    return AcceptedRewrite(
        rewrite_id=stable_id("rw", f"{answer.feedback_id}:{replacement}"),
        section_type=item.section_type,
        original_text=item.original_text,
        accepted_text=replacement,
        rejected_texts=rejected_texts,
        feature_targets=feature_targets,
        linked_style_profile=item.section_type,
        linked_style_memory_feature=item.issue_type,
        linked_benchmark_metrics=item.linked_benchmark_metrics,
        content_hash=sha256_text(replacement),
        created_at=utc_iso(),
    )


def _simple_feedback_benchmark(before: str, after: str) -> dict[str, Any]:
    generic_before = sum(_phrase_counts(before, GENERIC_LLM_PHRASES).values())
    generic_after = sum(_phrase_counts(after, GENERIC_LLM_PHRASES).values())
    hype_before = sum(_phrase_counts(before, HYPE_TERMS).values())
    hype_after = sum(_phrase_counts(after, HYPE_TERMS).values())
    before_sentence = len(word_tokens(before)) / max(len(split_sentences(before)), 1)
    after_sentence = len(word_tokens(after)) / max(len(split_sentences(after)), 1)
    return {
        "generic_phrase_count_before": generic_before,
        "generic_phrase_count_after": generic_after,
        "overclaiming_phrase_count_before": hype_before,
        "overclaiming_phrase_count_after": hype_after,
        "mean_sentence_length_before": round(before_sentence, 3),
        "mean_sentence_length_after": round(after_sentence, 3),
    }


def apply_feedback(
    project_dir: Path,
    run_dir: Path,
    *,
    feedback_path: Path | None = None,
    mode: str = "reviewed-copy",
    benchmark: bool = False,
) -> dict[str, Any]:
    feedback_path = feedback_path or project_dir / "style_feedback.jsonl"
    feedback = _load_feedback_rows(feedback_path) if feedback_path.exists() else []
    variants = {
        variant.variant_id: variant
        for variant in [StyleVariant.model_validate(row) for row in _read_jsonl(run_dir / "style_variants.jsonl")]
    }
    plan_path = run_dir / "review_plan.json"
    plan = ReviewPlan.model_validate(read_json(plan_path)) if plan_path.exists() else ReviewPlan(
        run_id=_run_id(run_dir),
        project_path=str(project_dir),
        generated_at=utc_iso(),
        selection_strategy="missing review plan",
    )
    items = {item.review_item_id: item for item in plan.review_items}
    manuscript = Manuscript.model_validate(read_json(run_dir / "manuscript.json"))
    source_map = _read_json_if_exists(run_dir / "source_map.json", {})
    known_citations = _known_citations(manuscript, source_map if isinstance(source_map, dict) else {})
    original_markdown = manuscript_to_markdown(manuscript)
    applied = []
    unapplied = []
    accepted_rewrites: list[AcceptedRewrite] = []
    for answer in feedback:
        item = items.get(answer.review_item_id)
        replacement = _feedback_text(answer, variants)
        if item is None or replacement is None:
            unapplied.append({"feedback_id": answer.feedback_id, "reason": "missing review item or accepted text"})
            continue
        is_safe, reasons = _safe_rewrite(item.original_text, replacement, known_citations)
        if not is_safe:
            unapplied.append({"feedback_id": answer.feedback_id, "reason": "; ".join(reasons)})
            continue
        rewrite = _accepted_rewrite(answer, item, replacement, variants)
        if mode == "reviewed-copy":
            if _apply_to_manuscript(manuscript, item, replacement):
                applied.append({"feedback_id": answer.feedback_id, "rewrite_id": rewrite.rewrite_id})
                accepted_rewrites.append(rewrite)
            else:
                unapplied.append({"feedback_id": answer.feedback_id, "reason": "original text not found in manuscript"})
        else:
            applied.append({"feedback_id": answer.feedback_id, "rewrite_id": rewrite.rewrite_id, "mode": "patch-plan"})
            accepted_rewrites.append(rewrite)

    report_lines = [
        "# Feedback Application Report",
        "",
        f"- Mode: {mode}",
        f"- Feedback records: {len(feedback)}",
        f"- Applied: {len(applied)}",
        f"- Unapplied: {len(unapplied)}",
        "",
    ]
    if mode == "reviewed-copy":
        export_markdown(run_dir / "manuscript_reviewed.md", manuscript)
        export_docx(run_dir / "manuscript_reviewed.docx", manuscript)
        reviewed_source_map = source_map if isinstance(source_map, dict) else {}
        reviewed_source_map["feedback_application"] = applied
        write_json(run_dir / "manuscript_reviewed_source_map.json", reviewed_source_map)
    else:
        lines = ["# Manuscript Patch Plan", ""]
        for rewrite in accepted_rewrites:
            lines.append(f"## {rewrite.rewrite_id}")
            lines.append("Original:")
            lines.append(rewrite.original_text)
            lines.append("")
            lines.append("Accepted replacement:")
            lines.append(rewrite.accepted_text)
            lines.append("")
        write_text(run_dir / "manuscript_patch_plan.md", "\n".join(lines).strip() + "\n")
    if accepted_rewrites:
        _append_jsonl(run_dir / "accepted_rewrites.jsonl", accepted_rewrites)
        _append_jsonl(project_dir / "accepted_rewrites.jsonl", accepted_rewrites)
    update_style_memory_from_feedback(project_dir, feedback, accepted_rewrites)
    if benchmark and mode == "reviewed-copy":
        after_markdown = manuscript_to_markdown(manuscript)
        delta = _simple_feedback_benchmark(original_markdown, after_markdown)
        write_text(
            run_dir / "feedback_benchmark_delta.md",
            "# Feedback Benchmark Delta\n\n"
            + "\n".join(f"- {key}: {value}" for key, value in delta.items())
            + "\n",
        )
    if unapplied:
        report_lines.extend(["## Unapplied Feedback", ""])
        report_lines.extend(f"- {row['feedback_id']}: {row['reason']}" for row in unapplied)
        report_lines.append("")
    write_text(run_dir / "feedback_application_report.md", "\n".join(report_lines).strip() + "\n")
    return {"applied": applied, "unapplied": unapplied, "accepted_rewrites": [rw.model_dump(mode="json") for rw in accepted_rewrites]}


def _feedback_readiness(accepted_count: int, rejected_count: int, section_counts: dict[str, int]) -> dict[str, Any]:
    section_coverage = len([section for section, count in section_counts.items() if count > 0])
    ready = accepted_count >= 100 and rejected_count >= 100 and section_coverage >= 4
    return {
        "ready_for_fine_tuning_export": ready,
        "minimum_accepted_rewrites_met": accepted_count >= 100,
        "minimum_rejected_variants_met": rejected_count >= 100,
        "section_coverage": section_coverage,
        "enough_for_train_validation_split": accepted_count >= 120 and rejected_count >= 120,
        "privacy_review_needed": True,
        "human_review_status": "needs_more_review" if not ready else "candidate_ready_for_private_export_review",
    }


def render_feedback_summary(summary: FeedbackSummary) -> str:
    details = summary.details
    lines = [
        "# Feedback Summary",
        "",
        f"- Run: {summary.run_id}",
        f"- Review items: {summary.total_review_items}",
        f"- Variants: {summary.total_variants}",
        f"- Feedback answers: {summary.total_feedback_answers}",
        f"- Accepted rewrites: {summary.accepted_rewrites}",
        f"- Rejected variants: {summary.rejected_variants}",
        f"- Unresolved items: {summary.unresolved_items}",
        "",
        "## Accepted Variants By Strategy",
    ]
    for key, value in details.get("accepted_by_strategy", {}).items():
        lines.append(f"- {key}: {value}")
    lines.extend(["", "## Rejected Variants By Strategy"])
    for key, value in details.get("rejected_by_strategy", {}).items():
        lines.append(f"- {key}: {value}")
    lines.extend(["", "## Sections With Most Feedback"])
    for key, value in details.get("sections_with_most_feedback", {}).items():
        lines.append(f"- {key}: {value}")
    lines.extend(["", "## Style Features With Strongest Signal"])
    for key, value in details.get("style_features_with_strongest_signal", {}).items():
        lines.append(f"- {key}: {value}")
    lines.extend(["", "## Recommended Next Feedback Questions"])
    lines.extend(f"- {item}" for item in details.get("recommended_next_feedback_questions", []))
    lines.extend(["", "## Fine-Tuning Readiness"])
    for key, value in details.get("fine_tuning_readiness", {}).items():
        lines.append(f"- {key}: {value}")
    return "\n".join(lines).strip() + "\n"


def build_feedback_summary(project_dir: Path, run_dir: Path) -> FeedbackSummary:
    plan = ReviewPlan.model_validate(read_json(run_dir / "review_plan.json")) if (run_dir / "review_plan.json").exists() else ReviewPlan(
        run_id=_run_id(run_dir),
        project_path=str(project_dir),
        generated_at=utc_iso(),
        selection_strategy="missing review plan",
    )
    variants = [StyleVariant.model_validate(row) for row in _read_jsonl(run_dir / "style_variants.jsonl")]
    feedback = _load_feedback(project_dir, run_dir)
    accepted = [AcceptedRewrite.model_validate(row) for row in _read_jsonl(run_dir / "accepted_rewrites.jsonl")]
    variant_map = {variant.variant_id: variant for variant in variants}
    accepted_by_strategy = Counter(
        variant_map[answer.selected_variant_id].strategy
        for answer in feedback
        if answer.selected_variant_id in variant_map
    )
    rejected_by_strategy = Counter(
        variant_map[variant_id].strategy
        for answer in feedback
        for variant_id in answer.rejected_variant_ids
        if variant_id in variant_map
    )
    section_counts = Counter(item.section_type or "unknown" for item in plan.review_items if any(answer.review_item_id == item.review_item_id for answer in feedback))
    feature_counts = Counter(tag for answer in feedback for tag in answer.style_tags)
    answered_items = {answer.review_item_id for answer in feedback}
    unresolved_high = [
        item.review_item_id
        for item in plan.review_items
        if item.priority_score >= 80 and item.review_item_id not in answered_items
    ]
    benchmark = _read_json_if_exists(run_dir / "style_benchmark_scores.json", {})
    readiness = _feedback_readiness(
        len(accepted),
        sum(len(answer.rejected_variant_ids) for answer in feedback),
        dict(section_counts),
    )
    next_questions = []
    if unresolved_high:
        next_questions.append("Resolve high-priority review items before expanding the preference dataset.")
    if len(accepted) < 20:
        next_questions.append("Collect at least 20 accepted rewrites across multiple sections as an initial calibration set.")
    if not feature_counts:
        next_questions.append("Tag feedback with style features such as hedging, citation_style, and results_restraint.")
    summary = FeedbackSummary(
        run_id=_run_id(run_dir),
        total_review_items=len(plan.review_items),
        total_variants=len(variants),
        total_feedback_answers=len(feedback),
        accepted_rewrites=len(accepted),
        rejected_variants=sum(len(answer.rejected_variant_ids) for answer in feedback),
        style_memory_updates=len(_read_json_if_exists(project_dir / "style_memory.json", {}).get("feedback_provenance", []))
        if isinstance(_read_json_if_exists(project_dir / "style_memory.json", {}), dict)
        else 0,
        unresolved_items=len(unresolved_high),
        benchmark_before=benchmark.get("overall_score") if isinstance(benchmark, dict) else None,
        generated_at=utc_iso(),
        details={
            "answered_items": len(answered_items),
            "skipped_items": max(len(plan.review_items) - len(answered_items), 0),
            "accepted_by_strategy": dict(accepted_by_strategy),
            "rejected_by_strategy": dict(rejected_by_strategy),
            "user_rewrites": [answer.user_rewrite for answer in feedback if answer.user_rewrite],
            "sections_with_most_feedback": dict(section_counts.most_common()),
            "style_features_with_strongest_signal": dict(feature_counts.most_common()),
            "recommended_next_feedback_questions": next_questions,
            "unresolved_high_priority_items": unresolved_high,
            "fine_tuning_readiness": readiness,
        },
    )
    write_json(run_dir / "feedback_summary.json", summary)
    write_text(run_dir / "feedback_summary.md", render_feedback_summary(summary))
    write_workbook(
        run_dir / "feedback_summary.xlsx",
        {
            "Review Items": [item.model_dump(mode="json") for item in plan.review_items],
            "Variants": [variant.model_dump(mode="json") for variant in variants],
            "Feedback": [answer.model_dump(mode="json") for answer in feedback],
            "Accepted Rewrites": [rewrite.model_dump(mode="json") for rewrite in accepted],
            "Style Memory Updates": [{"field": key, "value": value} for key, value in (_read_json_if_exists(project_dir / "style_memory.json", {}) or {}).items()],
            "Feedback Summary": [summary.model_dump(mode="json")],
        },
    )
    return summary


def run_review_plan(
    project_dir: Path,
    *,
    run_ref: str | None = None,
    max_items: int = 30,
    priority: str = "all",
    section_filter: str | None = None,
) -> Path:
    run_dir = resolve_benchmark_run(project_dir, run_ref)
    build_review_plan(project_dir, run_dir, max_items=max_items, priority=priority, section_filter=section_filter)
    return run_dir


def run_generate_variants(
    project_dir: Path,
    *,
    run_ref: str | None = None,
    review_plan_path: Path | None = None,
    max_variants: int = 4,
    section_filter: str | None = None,
) -> Path:
    run_dir = resolve_benchmark_run(project_dir, run_ref)
    generate_variants(run_dir, review_plan_path=review_plan_path, max_variants=max_variants, section_filter=section_filter)
    return run_dir


def run_capture_feedback(
    project_dir: Path,
    *,
    run_ref: str | None = None,
    variants_path: Path | None = None,
    interactive: bool = False,
    from_file: Path | None = None,
) -> Path:
    run_dir = resolve_benchmark_run(project_dir, run_ref)
    capture_feedback(project_dir, run_dir, variants_path=variants_path, interactive=interactive, from_file=from_file)
    return run_dir


def run_apply_feedback(
    project_dir: Path,
    *,
    run_ref: str | None = None,
    feedback_path: Path | None = None,
    mode: str = "reviewed-copy",
    benchmark: bool = False,
) -> Path:
    run_dir = resolve_benchmark_run(project_dir, run_ref)
    apply_feedback(project_dir, run_dir, feedback_path=feedback_path, mode=mode, benchmark=benchmark)
    return run_dir


def run_feedback_summary(project_dir: Path, *, run_ref: str | None = None) -> Path:
    run_dir = resolve_benchmark_run(project_dir, run_ref)
    build_feedback_summary(project_dir, run_dir)
    return run_dir
