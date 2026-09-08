from __future__ import annotations

from pathlib import Path
from typing import Any

from manuscriptforge.models.audit import AuditFinding
from manuscriptforge.models.manuscript import Manuscript
from manuscriptforge.models.project import ProjectConfig
from manuscriptforge.resources import read_config_resource
from manuscriptforge.utils.ids import stable_id
from manuscriptforge.utils.io import read_yaml, write_text
from manuscriptforge.utils.text import word_tokens

STATEMENT_TERMS = {
    "data_availability": ["data availability", "data are available", "data will be made"],
    "code_availability": ["code availability", "source code", "repository", "github"],
    "ethics_statement": ["ethics", "institutional review", "irb", "consent"],
    "funding": ["funding", "grant", "supported by"],
    "conflicts_of_interest": ["conflict of interest", "competing interest"],
    "author_contributions": ["author contribution", "contributed"],
    "ai_disclosure": ["ai-use disclosure", "artificial intelligence", "manuscriptforge"],
}

CHECKLIST_TERMS = {
    "methods_reproducibility": ["reproducibility", "workflow", "versioned"],
    "statistics": ["statistical", "p value", "adjusted", "test"],
    "software_versions": ["software", "version", "package"],
    "sample_size": ["sample size", "cohort", "n="],
    "validation": ["validation", "independent cohort", "held-out"],
    "limitations": ["limitation", "limited"],
    "data_code_availability": ["data availability", "code availability", "repository"],
}


def load_journal_profiles(config_path: Path | None = None) -> dict[str, Any]:
    return read_yaml(config_path) if config_path is not None else read_config_resource("journal_profiles.yaml")


def select_profile(config: ProjectConfig, profiles: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    candidates = [config.target_journal, config.article_type, "generic_biomedical"]
    for candidate in candidates:
        if candidate and candidate in profiles:
            return candidate, profiles[candidate]
    return "generic_biomedical", profiles.get("generic_biomedical", {})


def _full_text(manuscript: Manuscript) -> str:
    return "\n".join(
        [manuscript.title, manuscript.abstract]
        + [section.section_name + "\n" + section.content for section in manuscript.sections]
        + [manuscript.ai_disclosure]
    )


def audit_journal_profile(
    manuscript: Manuscript,
    config: ProjectConfig,
    profiles: dict[str, Any] | None = None,
) -> tuple[list[AuditFinding], str]:
    profiles = profiles or load_journal_profiles()
    profile_name, profile = select_profile(config, profiles)
    findings: list[AuditFinding] = []
    section_names = {section.section_name for section in manuscript.sections} | {"Abstract", "References"}
    for section in profile.get("required_sections", []):
        if section not in section_names:
            findings.append(
                AuditFinding(
                    finding_id=stable_id("aud", profile_name + "missing_section" + section),
                    severity="warning",
                    category="journal_compliance",
                    message=f"Required section missing for {profile_name}: {section}.",
                    location=section,
                    suggested_fix=f"Add or explicitly justify omission of the {section} section.",
                )
            )
    abstract_limit = profile.get("abstract_word_limit")
    abstract_words = len(word_tokens(manuscript.abstract))
    if abstract_limit and abstract_words > abstract_limit:
        findings.append(
            AuditFinding(
                finding_id=stable_id("aud", profile_name + "abstract_length"),
                severity="warning",
                category="journal_compliance",
                message=f"Abstract has {abstract_words} words, above the {abstract_limit}-word profile limit.",
                location="Abstract",
                suggested_fix="Shorten the abstract or select a profile with a different limit.",
            )
        )
    text = _full_text(manuscript).lower()
    total_words = len(word_tokens(text))
    manuscript_limit = profile.get("manuscript_word_limit")
    if manuscript_limit and total_words > manuscript_limit:
        findings.append(
            AuditFinding(
                finding_id=stable_id("aud", profile_name + "manuscript_length"),
                severity="warning",
                category="journal_compliance",
                message=f"Manuscript has approximately {total_words} words, above the {manuscript_limit}-word profile limit.",
                location="manuscript",
                suggested_fix="Condense sections or select a different profile.",
            )
        )
    for statement in profile.get("required_statements", []):
        terms = STATEMENT_TERMS.get(statement, [statement.replace("_", " ")])
        if not any(term in text for term in terms):
            findings.append(
                AuditFinding(
                    finding_id=stable_id("aud", profile_name + "statement" + statement),
                    severity="warning",
                    category="journal_compliance",
                    message=f"Required statement may be missing: {statement.replace('_', ' ')}.",
                    location="statements",
                    suggested_fix=f"Add a {statement.replace('_', ' ')} statement if required by the journal.",
                )
            )
    for checklist in profile.get("reporting_checklist", []):
        terms = CHECKLIST_TERMS.get(checklist, [checklist.replace("_", " ")])
        if not any(term in text for term in terms):
            findings.append(
                AuditFinding(
                    finding_id=stable_id("aud", profile_name + "checklist" + checklist),
                    severity="warning",
                    category="journal_compliance",
                    message=f"Reporting checklist item may be incomplete: {checklist.replace('_', ' ')}.",
                    location="manuscript",
                    suggested_fix=f"Add explicit reporting for {checklist.replace('_', ' ')} where applicable.",
                )
            )
    report_lines = [
        "# Journal/Profile Audit",
        "",
        f"- Profile: {profile_name}",
        f"- Abstract words: {abstract_words}",
        f"- Manuscript words: {total_words}",
        f"- Findings: {len(findings)}",
        "",
    ]
    for finding in findings:
        report_lines.append(f"- {finding.severity}: {finding.message}")
    return findings, "\n".join(report_lines).strip() + "\n"


def write_journal_audit_report(path: Path, report: str) -> None:
    write_text(path, report)
