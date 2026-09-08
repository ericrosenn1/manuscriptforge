from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from manuscriptforge.audit.citation_audit import PLACEHOLDER_RE
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
    return {match.group(0).rstrip("%") for match in NUMBER_RE.finditer("\n".join(text_parts))}


def _manuscript_text(manuscript: Manuscript) -> str:
    return "\n".join([manuscript.title, manuscript.abstract] + [s.content for s in manuscript.sections])


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
    for number in {match.group(0).rstrip("%") for match in NUMBER_RE.finditer(text)}:
        if len(number) == 4 and 1800 <= int(float(number)) <= 2100:
            continue
        if number not in allowed_numbers:
            findings.append(
                AuditFinding(
                    finding_id=stable_id("aud", "number" + number),
                    severity="warning",
                    category="no_invention",
                    message=f"Numeric value may not be traceable to supplied inputs: {number}.",
                    location="manuscript",
                    suggested_fix="Verify the number against tables, notes, or citations; otherwise remove it.",
                )
            )

    report_lines = ["# No-Invention Report", "", f"- Findings: {len(findings)}", ""]
    for finding in findings:
        report_lines.append(f"- {finding.severity}: {finding.message}")
    return findings, "\n".join(report_lines).strip() + "\n"


def write_source_map(path: Path, source_map: dict[str, Any]) -> None:
    write_json(path, source_map)


def write_no_invention_report(path: Path, report: str) -> None:
    write_text(path, report)
