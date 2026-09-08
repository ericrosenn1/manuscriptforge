from __future__ import annotations

from manuscriptforge.models.audit import AuditFinding
from manuscriptforge.models.manuscript import Manuscript
from manuscriptforge.utils.io import write_text

REVIEW_CATEGORIES = [
    "novelty",
    "rationale",
    "methods clarity",
    "statistical rigor",
    "data presentation",
    "interpretation",
    "overclaiming",
    "missing controls",
    "reproducibility",
    "journal fit",
]


def simulate_reviewer(manuscript: Manuscript, findings: list[AuditFinding] | None = None) -> str:
    findings = findings or []
    grouped: dict[str, list[AuditFinding]] = {}
    for finding in findings:
        grouped.setdefault(finding.category, []).append(finding)

    lines = ["# Reviewer-Style Critique", ""]
    for category in REVIEW_CATEGORIES:
        lines.append(f"## {category.title()}")
        related = grouped.get(category.replace(" ", "_"), []) + grouped.get(category, [])
        if related:
            for finding in related[:3]:
                lines.append(f"- Concern: {finding.message}")
                if finding.suggested_fix:
                    lines.append(f"  Suggested fix: {finding.suggested_fix}")
        else:
            if category == "novelty":
                lines.append("- The novelty claim is not yet explicit. Clarify what is new relative to the supplied literature.")
            elif category == "methods clarity":
                lines.append("- Ensure datasets, preprocessing, statistics, software versions, and validation steps are sufficiently detailed.")
            elif category == "overclaiming":
                lines.append("- The manuscript should continue to avoid causal or clinical utility language unless directly supported.")
            elif category == "journal fit":
                lines.append("- Align abstract structure, word count, citation style, and reporting checklists with the target journal.")
            else:
                lines.append("- No specific automated issue was detected, but human review remains required.")
        lines.append("")
    lines.append("## Overall Recommendation")
    lines.append(
        "Major revision before submission. The draft is useful as an evidence-linked starting point, but claims, citations, methods detail, and journal-specific requirements need expert author review."
    )
    return "\n".join(lines).strip() + "\n"


def write_reviewer_critique(path, critique: str) -> None:
    write_text(path, critique)
