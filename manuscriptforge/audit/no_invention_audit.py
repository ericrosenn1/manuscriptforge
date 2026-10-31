from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from manuscriptforge.audit.citation_audit import PLACEHOLDER_RE
from manuscriptforge.audit.numeric_consistency import numeric_mentions
from manuscriptforge.models.audit import AuditFinding
from manuscriptforge.models.citation import CitationRecord
from manuscriptforge.models.claim import ScientificClaim
from manuscriptforge.models.manuscript import Manuscript
from manuscriptforge.utils.ids import stable_id
from manuscriptforge.utils.io import write_json, write_text
from manuscriptforge.utils.text import split_paragraphs

NUMBER_RE = re.compile(r"(?<![A-Za-z0-9_])[-+]?\d+(?:\.\d+)?(?:e[-+]?\d+)?%?(?![A-Za-z0-9_])", re.I)
DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+\b", re.I)
PMID_RE = re.compile(r"\bPMID[:\s]*(\d{5,9})\b", re.I)
CITATION_ID_RE = re.compile(r"\b(?:cit|clm)_[A-Za-z0-9_]+\b")


def build_source_map(manuscript: Manuscript, provider_name: str = "unknown") -> dict[str, Any]:
    sections = []
    for section in manuscript.sections:
        paragraphs = []
        for index, paragraph in enumerate(split_paragraphs(section.content) or [section.content], start=1):
            paragraphs.append(
                {
                    "paragraph_index": index,
                    "text": paragraph,
                    "claim_ids": section.claim_ids,
                    "citation_ids": section.citation_ids,
                    "style_mode": section.style_mode or manuscript.metadata.get("active_style_mode", "unknown"),
                    "style_chunk_ids": section.style_chunk_ids,
                    "unresolved_flags": section.unresolved_flags,
                    "generated_by": provider_name,
                    "style_examples_used": [
                        note for note in section.revision_notes if "Style" in note or "style" in note
                    ],
                }
            )
        sections.append(
            {
                "section_name": section.section_name,
                "section_type": section.section_type,
                "claim_ids": section.claim_ids,
                "citation_ids": section.citation_ids,
                "style_mode": section.style_mode or manuscript.metadata.get("active_style_mode", "unknown"),
                "style_chunk_ids": section.style_chunk_ids,
                "unresolved_flags": section.unresolved_flags,
                "paragraphs": paragraphs,
            }
        )
    return {"title": manuscript.title, "generated_by": provider_name, "sections": sections}


def _allowed_numbers(
    claims: list[ScientificClaim], citations: list[CitationRecord], ingested: dict[str, Any] | None
) -> set[str]:
    text_parts: list[str] = []
    text_parts.extend(claim.claim_text for claim in claims)
    for citation in citations:
        text_parts.extend([citation.year or "", citation.doi or "", citation.pmid or "", citation.title])
    for item in (ingested or {}).get("text_inputs", {}).values():
        text_parts.append(item.get("text", ""))
    for table in (ingested or {}).get("tables", []):
        for row in table.get("rows", []):
            text_parts.extend(str(value) for value in row.values() if value is not None)
    return {f"{item.value}|{item.unit}" for item in numeric_mentions("\n".join(text_parts))}


def _manuscript_text(manuscript: Manuscript) -> str:
    return "\n".join([manuscript.title, manuscript.abstract] + [s.content for s in manuscript.sections])


def _audit_numeric_context(manuscript: Manuscript, claims: list[ScientificClaim],
                           ingested: dict[str, Any] | None) -> list[AuditFinding]:
    registry = {claim.claim_id: claim for claim in claims}
    fallback = [claim.claim_text for claim in claims]
    fallback.extend(item.get("text", "") for item in (ingested or {}).get("text_inputs", {}).values())
    for table in (ingested or {}).get("tables", []):
        fallback.extend(" ".join(f"{key} {value}" for key, value in row.items()) for row in table.get("rows", []))
    findings: list[AuditFinding] = []
    for section in manuscript.sections:
        linked = [registry[key] for key in section.claim_ids if key in registry]
        evidence_text = [item.text for claim in linked for item in claim.evidence_items]
        evidence_text.extend(claim.claim_text for claim in linked)
        source_numbers = numeric_mentions("\n".join(evidence_text or fallback))
        for mention in numeric_mentions(section.content):
            equal_values = [item for item in source_numbers if item.value == mention.value]
            matching_units = [item for item in equal_values if item.unit == mention.unit]
            reason = None
            if linked and not equal_values:
                reason = "not found in the section's linked claims or evidence"
            elif equal_values and not matching_units:
                reason = "unit differs from the supplied numeric context"
            elif mention.group and matching_units:
                known_groups = {item.group for item in matching_units if item.group}
                if known_groups and mention.group not in known_groups:
                    reason = "explicit group label differs from the supplied numeric context"
            if reason:
                findings.append(AuditFinding(
                    finding_id=stable_id("aud", f"numeric_context:{section.section_name}:{mention}:{reason}"),
                    severity="warning", category="numeric_context",
                    message=f"Numeric context needs review: {mention.raw} ({reason}).",
                    location=section.section_name,
                    suggested_fix="Check subject, value, unit, and source association; a shared number alone is not factual support.",
                    related_claim_ids=[claim.claim_id for claim in linked],
                ))
    return findings


def audit_no_invention(
    manuscript: Manuscript,
    claims: list[ScientificClaim],
    citations: list[CitationRecord],
    ingested: dict[str, Any] | None = None,
) -> tuple[list[AuditFinding], str]:
    findings: list[AuditFinding] = []
    claim_ids = {claim.claim_id for claim in claims}
    citation_ids = {citation.citation_id for citation in citations}
    text = _manuscript_text(manuscript)

    for section in manuscript.sections:
        for claim_id in section.claim_ids:
            if claim_id not in claim_ids:
                findings.append(
                    AuditFinding(
                        finding_id=stable_id("aud", "unknown_claim" + claim_id),
                        severity="serious",
                        category="no_invention",
                        message=f"Manuscript section references claim ID not found in registry: {claim_id}.",
                        location=section.section_name,
                        suggested_fix="Regenerate the section or repair claim_registry.json.",
                        related_claim_ids=[claim_id],
                    )
                )
        for citation_id in section.citation_ids:
            if citation_id not in citation_ids:
                findings.append(
                    AuditFinding(
                        finding_id=stable_id("aud", "unknown_citation" + citation_id),
                        severity="serious",
                        category="no_invention",
                        message=f"Manuscript section references citation ID not found in registry: {citation_id}.",
                        location=section.section_name,
                        suggested_fix="Use only supplied/enriched citation IDs.",
                        related_citation_ids=[citation_id],
                    )
                )

    for match in PLACEHOLDER_RE.finditer(text):
        findings.append(
            AuditFinding(
                finding_id=stable_id("aud", "placeholder" + match.group(0) + str(match.start())),
                severity="serious",
                category="no_invention",
                message=f"Placeholder citation or unresolved marker remains: {match.group(0)}.",
                location=f"character {match.start()}",
                suggested_fix="Replace with a supplied citation ID or remove the unsupported claim.",
            )
        )

    registered_identifiers = {
        value.lower()
        for citation in citations
        for value in [citation.doi, citation.pmid]
        if value
    }
    for doi in DOI_RE.findall(text):
        if doi.lower().rstrip(".,;") not in registered_identifiers:
            findings.append(
                AuditFinding(
                    finding_id=stable_id("aud", "unknown_doi" + doi),
                    severity="warning",
                    category="no_invention",
                    message=f"DOI appears in manuscript but is not in the citation registry: {doi}.",
                    location="manuscript",
                    suggested_fix="Add the source to citation inputs or remove the DOI.",
                )
            )
    for pmid in PMID_RE.findall(text):
        if pmid.lower() not in registered_identifiers:
            findings.append(
                AuditFinding(
                    finding_id=stable_id("aud", "unknown_pmid" + pmid),
                    severity="warning",
                    category="no_invention",
                    message=f"PMID appears in manuscript but is not in the citation registry: {pmid}.",
                    location="manuscript",
                    suggested_fix="Add the source to citation inputs or remove the PMID.",
                )
            )

    allowed_numbers = _allowed_numbers(claims, citations, ingested)
    for number in set(numeric_mentions(text)):
        if f"{number.value}|{number.unit}" not in allowed_numbers:
            findings.append(
                AuditFinding(
                    finding_id=stable_id("aud", "number" + number.raw),
                    severity="warning",
                    category="no_invention",
                    message=f"Numeric value or unit may not be traceable to supplied inputs: {number.raw} {number.unit}.",
                    location="manuscript",
                    suggested_fix="Verify the number against tables, notes, or citations; otherwise remove it.",
                )
            )

    findings.extend(_audit_numeric_context(manuscript, claims, ingested))
    report_lines = ["# No-Invention Report", "", f"- Findings: {len(findings)}",
                    "- These are syntactic consistency checks, not semantic or scientific validation.", ""]
    for finding in findings:
        report_lines.append(f"- {finding.severity}: {finding.message}")
    return findings, "\n".join(report_lines).strip() + "\n"


def write_source_map(path: Path, source_map: dict[str, Any]) -> None:
    write_json(path, source_map)


def write_no_invention_report(path: Path, report: str) -> None:
    write_text(path, report)
