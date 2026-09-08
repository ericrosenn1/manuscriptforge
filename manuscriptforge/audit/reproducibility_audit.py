from __future__ import annotations

from manuscriptforge.models.audit import AuditFinding
from manuscriptforge.models.manuscript import Manuscript
from manuscriptforge.utils.ids import stable_id

REQUIRED_METHOD_TERMS = {
    "data sources": ["data source", "dataset", "cohort", "table"],
    "sample sizes": ["sample size", "n=", "cohort"],
    "preprocessing": ["preprocessing", "normalized", "filter"],
    "statistical tests": ["statistical", "p value", "test", "adjusted"],
    "software versions": ["software", "version", "package"],
    "random seeds": ["seed", "random"],
    "code/data availability": ["code availability", "data availability", "repository"],
    "parameter settings": ["parameter", "threshold"],
}


def audit_reproducibility(manuscript: Manuscript, field: str = "") -> list[AuditFinding]:
    method_text = "\n".join(
        section.content for section in manuscript.sections if section.section_name.lower() == "methods"
    ).lower()
    findings: list[AuditFinding] = []
    for label, terms in REQUIRED_METHOD_TERMS.items():
        if not any(term in method_text for term in terms):
            findings.append(
                AuditFinding(
                    finding_id=stable_id("aud", "repro" + label),
                    severity="warning",
                    category="reproducibility",
                    message=f"Methods may be missing {label}.",
                    location="Methods",
                    suggested_fix=f"Add explicit {label} details if applicable.",
                )
            )
    if "bioinformatics" in field.lower() or "computational" in field.lower():
        findings.append(
            AuditFinding(
                finding_id=stable_id("aud", "nfcore_principles"),
                severity="info",
                category="reproducibility",
                message="For computational biology, consider nf-core-style principles: versioned outputs, parameter logs, environment capture, and validation summaries.",
                location="Methods",
                suggested_fix="Add workflow, package version, parameter, and validation-log details where relevant; Nextflow is not required unless configured by the project.",
            )
        )
    return findings
