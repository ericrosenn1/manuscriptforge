from __future__ import annotations

import re
from typing import Any, Literal

from manuscriptforge.models.audit import AuditFinding
from manuscriptforge.models.manuscript import Manuscript, ManuscriptSection
from manuscriptforge.models.style import StyleProfile
from manuscriptforge.style.features import normalize_section_type
from manuscriptforge.style.style_scoring import score_style_deviation
from manuscriptforge.utils.ids import stable_id
from manuscriptforge.utils.io import write_text
from manuscriptforge.utils.text import HEDGING_WORDS, split_sentences, word_tokens

GENERIC_LLM_PHRASES = [
    "it is important to note",
    "in today's",
    "delves into",
    "underscores the importance",
    "robust framework",
    "tapestry",
    "transformative",
]

HYPE_TERMS = [
    "groundbreaking",
    "game-changing",
    "definitive",
    "proves",
    "clearly demonstrates",
    "unprecedented",
]


def _finding(
    key: str,
    severity: Literal["info", "warning", "serious"],
    message: str,
    location: str,
    suggested_fix: str,
) -> AuditFinding:
    return AuditFinding(
        finding_id=stable_id("aud", f"style:{key}:{location}:{message}"),
        severity=severity,
        category="style",
        message=message,
        location=location,
        suggested_fix=suggested_fix,
    )


def _section_text(manuscript: Manuscript, section_type: str) -> str:
    for section in manuscript.sections:
        if normalize_section_type(section.section_type or section.section_name) == section_type:
            return section.content
    return ""


def _sentence_mean(text: str) -> float | None:
    lengths = [len(word_tokens(sentence)) for sentence in split_sentences(text)]
    if not lengths:
        return None
    return round(sum(lengths) / len(lengths), 3)


def _hedges_per_1000(text: str) -> float:
    tokens = word_tokens(text)
    hedge_count = sum(1 for token in tokens if token in HEDGING_WORDS)
    return round(1000 * hedge_count / max(len(tokens), 1), 3)


def _citation_style(text: str) -> str:
    numeric = len(re.findall(r"\[\d+(?:,\s*\d+)*\]", text))
    author_year = len(re.findall(r"\([A-Z][A-Za-z]+ et al\.,? \d{4}\)", text))
    if not numeric and not author_year:
        return "unknown"
    return "numeric" if numeric >= author_year else "author-year"


def _section_length_findings(
    section: ManuscriptSection,
    profile: StyleProfile,
    metrics: list[dict[str, Any]],
) -> list[AuditFinding]:
    section_type = normalize_section_type(section.section_type or section.section_name)
    section_profile = profile.section_features.get(section_type, {})
    target = section_profile.get("sentence_length_distribution", {}).get("mean")
    observed = _sentence_mean(section.content)
    metrics.append(
        {
            "section": section.section_name,
            "metric": "sentence_mean",
            "observed": observed,
            "target": target,
        }
    )
    if not isinstance(target, (int, float)) or observed is None:
        return []
    delta = abs(observed - float(target))
    if delta >= max(8.0, float(target) * 0.45):
        return [
            _finding(
                "section_sentence_length",
                "warning",
                f"{section.section_name} sentence length differs from the section profile by {round(delta, 2)} words.",
                section.section_name,
                "Revise pacing against section-specific exemplars rather than the global style alone.",
            )
        ]
    return []


def audit_style_details(
    manuscript: Manuscript,
    profile: StyleProfile,
    style_memory: dict[str, Any] | None = None,
) -> tuple[list[AuditFinding], str, dict[str, Any]]:
    style_memory = style_memory or {}
    text = manuscript.abstract + "\n\n" + "\n\n".join(section.content for section in manuscript.sections)
    score = score_style_deviation(text, profile)
    findings: list[AuditFinding] = []
    metrics: list[dict[str, Any]] = [
        {
            "section": "manuscript",
            "metric": "mean_sentence_delta",
            "observed": score["mean_sentence_delta"],
            "target": profile.sentence_length_distribution.get("mean"),
        }
    ]
    if score["severity"] == "warning":
        findings.append(
            _finding(
                "style_sentence_delta",
                "warning",
                f"Mean sentence length differs from the style corpus by {score['mean_sentence_delta']} words.",
                "manuscript",
                "Review sentence length and paragraph pacing against the style guide.",
            )
        )
    for section in manuscript.sections:
        findings.extend(_section_length_findings(section, profile, metrics))

    lower_text = text.lower()
    for phrase in GENERIC_LLM_PHRASES:
        if phrase in lower_text:
            findings.append(
                _finding(
                    "generic_phrase",
                    "warning",
                    f"Generic LLM-like phrase detected: '{phrase}'.",
                    "manuscript",
                    "Replace with a concrete manuscript-specific sentence or remove the phrase.",
                )
            )
    for phrase in HYPE_TERMS:
        if phrase in lower_text:
            findings.append(
                _finding(
                    "hype",
                    "warning",
                    f"Potential hype or over-strong style phrase detected: '{phrase}'.",
                    "manuscript",
                    "Use cautious language aligned with the style memory and claim support.",
                )
            )
    for phrase in style_memory.get("disliked_phrases", []):
        if phrase and str(phrase).lower() in lower_text:
            findings.append(
                _finding(
                    "memory_disliked_phrase",
                    "warning",
                    f"Phrase appears in draft but is disliked in style memory: '{phrase}'.",
                    "manuscript",
                    "Replace with an accepted rewrite or a corpus-supported alternative.",
                )
            )

    target_hedges = profile.hedging_profile.get("hedging_terms_per_1000_words", 0) or 0
    observed_hedges = _hedges_per_1000(text)
    metrics.append(
        {
            "section": "manuscript",
            "metric": "hedges_per_1000_words",
            "observed": observed_hedges,
            "target": target_hedges,
        }
    )
    if isinstance(target_hedges, (int, float)) and target_hedges >= 5 and observed_hedges < target_hedges * 0.4:
        findings.append(
            _finding(
                "low_hedging",
                "warning",
                "The draft uses substantially fewer hedge terms than the style corpus.",
                "manuscript",
                "Add cautious qualifiers where evidence is indirect or not causal.",
            )
        )

    observed_citation_style = _citation_style(text)
    target_citation_style = profile.citation_style.get("likely_style", "unknown")
    metrics.append(
        {
            "section": "manuscript",
            "metric": "citation_style",
            "observed": observed_citation_style,
            "target": target_citation_style,
        }
    )
    if (
        observed_citation_style != "unknown"
        and target_citation_style != "unknown"
        and observed_citation_style != target_citation_style
    ):
        findings.append(
            _finding(
                "citation_style_mismatch",
                "warning",
                f"Citation style appears to be {observed_citation_style}, but the corpus looks like {target_citation_style}.",
                "manuscript",
                "Adjust citation integration to match the target journal and style corpus.",
            )
        )

    methods = _section_text(manuscript, "methods").lower()
    if methods and (
        "standard methods" in methods
        or "should be checked" in methods
        or not any(term in methods for term in ["version", "software", "statistical", "preprocessing"])
    ):
        findings.append(
            _finding(
                "methods_vague",
                "warning",
                "Methods prose is too vague for the user's section-specific style.",
                "Methods",
                "Add concrete software, version, preprocessing, sample, and statistical details from supplied inputs.",
            )
        )

    results = _section_text(manuscript, "results").lower()
    if results and any(term in results for term in ["causality", "causal", "warrant follow-up", "interprets"]):
        findings.append(
            _finding(
                "results_interpretive",
                "warning",
                "Results prose may be more interpretive than the user's Results style.",
                "Results",
                "Keep Results descriptive and move interpretation to Discussion.",
            )
        )

    discussion = _section_text(manuscript, "discussion").lower()
    if discussion:
        if not any(term in discussion for term in ["together", "however", "important limitations", "may", "suggest"]):
            findings.append(
                _finding(
                    "discussion_flat",
                    "warning",
                    "Discussion prose lacks the transitions or cautious interpretation patterns seen in the corpus.",
                    "Discussion",
                    "Use a corpus-supported transition and state interpretation with calibrated uncertainty.",
                )
            )
        if any(term in discussion for term in ["definitive", "proves", "guarantees"]):
            findings.append(
                _finding(
                    "discussion_speculative",
                    "warning",
                    "Discussion prose appears too speculative or certain for the style profile.",
                    "Discussion",
                    "Soften the claim or tie it to direct evidence.",
                )
            )

    limitations = _section_text(manuscript, "limitations").lower()
    limitation_profile = profile.section_features.get("limitations", {})
    if not limitations:
        findings.append(
            _finding(
                "missing_limitations",
                "warning",
                "No Limitations section was found for comparison with the style corpus.",
                "Limitations",
                "Add a limitations section or confirm that the target article type omits it.",
            )
        )
    elif limitation_profile.get("chunk_count") and "limitation" not in limitations and "limited" not in limitations:
        findings.append(
            _finding(
                "limitations_unlike_profile",
                "warning",
                "Limitations section does not use limitation phrasing patterns detected in the corpus.",
                "Limitations",
                "Use a limitation pattern from the style guide or style memory.",
            )
        )

    report_data = {
        "summary": {
            "finding_count": len(findings),
            "mean_sentence_delta": score["mean_sentence_delta"],
            "observed_hedges_per_1000_words": observed_hedges,
            "target_hedges_per_1000_words": target_hedges,
            "observed_citation_style": observed_citation_style,
            "target_citation_style": target_citation_style,
            "style_memory_loaded": bool(style_memory),
        },
        "metrics": metrics,
        "findings": [finding.model_dump(mode="json") for finding in findings],
    }
    report = render_style_report(report_data)
    return findings, report, report_data


def audit_style(manuscript: Manuscript, profile: StyleProfile) -> tuple[list[AuditFinding], str]:
    findings, report, _data = audit_style_details(manuscript, profile)
    return findings, report


def render_style_report(report_data: dict[str, Any]) -> str:
    summary = report_data["summary"]
    lines = [
        "# Style Deviation Report",
        "",
        f"- Findings: {summary['finding_count']}",
        f"- Mean sentence delta: {summary['mean_sentence_delta']}",
        f"- Observed hedges per 1000 words: {summary['observed_hedges_per_1000_words']}",
        f"- Target hedges per 1000 words: {summary['target_hedges_per_1000_words']}",
        f"- Observed citation style: {summary['observed_citation_style']}",
        f"- Target citation style: {summary['target_citation_style']}",
        f"- Style memory loaded: {summary['style_memory_loaded']}",
        "",
        "## Findings",
    ]
    findings = report_data.get("findings", [])
    if findings:
        for finding in findings:
            lines.append(f"- {finding['severity']}: {finding['message']} ({finding['location']})")
    else:
        lines.append("- No style audit findings were detected.")
    lines.extend(["", "## Metrics"])
    for metric in report_data.get("metrics", []):
        lines.append(
            f"- {metric.get('section')}: {metric.get('metric')} observed={metric.get('observed')} target={metric.get('target')}"
        )
    return "\n".join(lines).strip() + "\n"


def write_style_report(path, report: str) -> None:
    write_text(path, report)
