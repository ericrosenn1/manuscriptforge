from __future__ import annotations

from collections import OrderedDict
from pathlib import Path
from typing import Any

from manuscriptforge.models.audit import AuditFinding
from manuscriptforge.models.claim import ScientificClaim
from manuscriptforge.utils.ids import stable_id
from manuscriptforge.utils.io import write_json, write_text


def _short_claim(claim: ScientificClaim, max_length: int = 130) -> str:
    text = claim.claim_text.strip()
    return text if len(text) <= max_length else text[: max_length - 3] + "..."


def _record(
    *,
    category: str,
    prompt: str,
    priority: str = "medium",
    finding_ids: list[str] | None = None,
    claim_ids: list[str] | None = None,
    suggested_answer_format: str = "Short answer with exact manuscript wording or evidence source.",
    why_it_matters: str = "This helps the author resolve audit findings before submission.",
) -> dict[str, Any]:
    return {
        "question_id": stable_id("revq", category + prompt, length=8),
        "category": category,
        "priority": priority,
        "question": prompt,
        "linked_finding_ids": finding_ids or [],
        "linked_claim_ids": claim_ids or [],
        "suggested_answer_format": suggested_answer_format,
        "why_it_matters": why_it_matters,
    }


def generate_revision_question_records(
    claims: list[ScientificClaim], findings: list[AuditFinding] | None = None
) -> list[dict[str, Any]]:
    questions: OrderedDict[str, dict[str, Any]] = OrderedDict()
    for claim in claims:
        if claim.needs_human_review:
            if claim.support_strength == "unsupported":
                prompt = (
                    f"Claim {claim.claim_id} is unsupported: \"{_short_claim(claim)}\". "
                    "Should it be removed, rewritten as a limitation, or supported with direct evidence?"
                )
                questions[prompt] = _record(
                    category="Claim strength",
                    prompt=prompt,
                    priority="high",
                    claim_ids=[claim.claim_id],
                    suggested_answer_format="Remove / rewrite as limitation / add evidence source + revised sentence.",
                    why_it_matters="Unsupported interpretation is a high-risk authorship and review issue.",
                )
            else:
                prompt = (
                    f"Claim {claim.claim_id} needs review: \"{_short_claim(claim)}\". "
                    "What exact evidence scope and hedge level should be used?"
                )
                questions[prompt] = _record(
                    category="Claim strength",
                    prompt=prompt,
                    priority="medium",
                    claim_ids=[claim.claim_id],
                    suggested_answer_format="Evidence scope + preferred hedge phrase + revised claim.",
                    why_it_matters="Claim wording should match the strength of supplied evidence.",
                )
        if claim.needs_citation:
            prompt = (
                f"Which supplied citation, if any, supports claim {claim.claim_id}; "
                "if none, should the claim be removed?"
            )
            questions[prompt] = _record(
                category="Citation needed",
                prompt=prompt,
                priority="high",
                claim_ids=[claim.claim_id],
                suggested_answer_format="Citation ID or 'remove claim'.",
                why_it_matters="Background and interpretation claims need traceable sources.",
            )
    for finding in findings or []:
        if finding.severity in {"warning", "serious"}:
            if finding.category == "overclaiming":
                prompt = f"How should the wording be softened at {finding.location}: {finding.message}"
                questions[prompt] = _record(
                    category="Claim strength",
                    prompt=prompt,
                    priority="high",
                    finding_ids=[finding.finding_id],
                    claim_ids=finding.related_claim_ids,
                    suggested_answer_format="Replacement sentence using cautious language.",
                    why_it_matters="Overclaiming can misrepresent evidence and trigger reviewer objections.",
                )
            elif finding.category == "reproducibility":
                prompt = f"What reproducibility detail should be added for {finding.location}: {finding.message}"
                questions[prompt] = _record(
                    category="Code/software reproducibility",
                    prompt=prompt,
                    priority="medium",
                    finding_ids=[finding.finding_id],
                    suggested_answer_format="Exact method detail, software/package version, parameter, or availability statement.",
                    why_it_matters="Reproducibility details support review and reuse.",
                )
            elif finding.category.startswith("citation"):
                prompt = f"How should this citation issue be resolved: {finding.message}"
                questions[prompt] = _record(
                    category="Citation needed",
                    prompt=prompt,
                    priority="high" if finding.severity == "serious" else "medium",
                    finding_ids=[finding.finding_id],
                    claim_ids=finding.related_claim_ids,
                    suggested_answer_format="Citation ID, corrected metadata, or removal decision.",
                    why_it_matters="Citation traceability prevents invented or mismapped sources.",
                )
            elif finding.category == "journal_compliance":
                prompt = f"How should this journal/profile issue be resolved: {finding.message}"
                questions[prompt] = _record(
                    category="Journal compliance",
                    prompt=prompt,
                    priority="medium",
                    finding_ids=[finding.finding_id],
                    suggested_answer_format="Statement text, section change, or profile override.",
                    why_it_matters="Journal-specific requirements can block submission.",
                )
            elif finding.category == "no_invention":
                prompt = f"How should this provenance issue be resolved: {finding.message}"
                questions[prompt] = _record(
                    category="Unsupported interpretation",
                    prompt=prompt,
                    priority="high",
                    finding_ids=[finding.finding_id],
                    claim_ids=finding.related_claim_ids,
                    suggested_answer_format="Evidence source, revised wording, or removal decision.",
                    why_it_matters="Every factual value and citation should trace to supplied inputs.",
                )
            else:
                prompt = f"How should the author address {finding.category}: {finding.message}"
                questions[prompt] = _record(
                    category=finding.category.replace("_", " ").title(),
                    prompt=prompt,
                    priority="medium",
                    finding_ids=[finding.finding_id],
                    claim_ids=finding.related_claim_ids,
                )
    if not questions:
        prompt = "Are there journal-specific formatting or reporting requirements not captured in project.yaml?"
        questions[prompt] = _record(
            category="Journal compliance",
            prompt=prompt,
            priority="low",
            suggested_answer_format="List of required changes or 'none'.",
            why_it_matters="Some journal requirements are not inferable from manuscript text.",
        )
    return list(questions.values())[:30]


def generate_revision_questions(claims: list[ScientificClaim], findings: list[AuditFinding] | None = None) -> list[str]:
    return [record["question"] for record in generate_revision_question_records(claims, findings)]


def write_revision_questions(path, questions: list[str]) -> None:
    text = "# Targeted Revision Questions\n\n" + "\n".join(
        f"{index}. {question}" for index, question in enumerate(questions, start=1)
    )
    write_text(path, text + "\n")


def write_revision_question_artifacts(run_dir: Path, records: list[dict[str, Any]]) -> None:
    write_revision_questions(run_dir / "revision_questions.md", [record["question"] for record in records])
    write_json(run_dir / "revision_questions.json", records)
    from manuscriptforge.export.excel_reporter import write_rows_xlsx

    write_rows_xlsx(run_dir / "revision_questions.xlsx", records, "Revision Questions")
