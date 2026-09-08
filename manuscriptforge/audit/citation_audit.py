from __future__ import annotations

import re

from manuscriptforge.models.audit import AuditFinding
from manuscriptforge.models.citation import CitationRecord
from manuscriptforge.models.claim import ScientificClaim
from manuscriptforge.models.manuscript import Manuscript
from manuscriptforge.utils.ids import stable_id

PLACEHOLDER_RE = re.compile(
    r"\[(REF|TODO|citation needed|citation|insert citation|add citation|\?)\]"
    r"|\{(?:REF|citation needed|citation)\}"
    r"|needed citation|citation needed",
    re.I,
)
BRACKET_RE = re.compile(r"\[([^\[\]]{1,160})\]")


def _split_citation_tokens(raw: str) -> list[str]:
    tokens = []
    for token in re.split(r"[;,]", raw):
        cleaned = token.strip()
        if cleaned:
            tokens.append(cleaned)
    return tokens


def _looks_like_citation_token(token: str, citation_ids: set[str]) -> bool:
    if token in citation_ids:
        return True
    if token.startswith("cit_"):
        return True
    return bool(re.fullmatch(r"\d+(?:[-–]\d+)?", token))


def audit_citations(
    manuscript: Manuscript,
    claims: list[ScientificClaim],
    citations: list[CitationRecord],
) -> list[AuditFinding]:
    findings: list[AuditFinding] = []
    citation_ids = {citation.citation_id for citation in citations}
    full_text = manuscript.abstract + "\n" + "\n".join(section.content for section in manuscript.sections)
    for match in PLACEHOLDER_RE.finditer(full_text):
        findings.append(
            AuditFinding(
                finding_id=stable_id("aud", match.group(0) + str(match.start())),
                severity="serious",
                category="citation_placeholder",
                message=f"Citation placeholder detected: {match.group(0)}",
                location=f"character {match.start()}",
                suggested_fix="Replace the placeholder with a supplied citation record or remove the claim.",
            )
        )
    cited_in_text: set[str] = set()
    for match in BRACKET_RE.finditer(full_text):
        raw = match.group(1)
        if PLACEHOLDER_RE.search(match.group(0)):
            continue
        for token in _split_citation_tokens(raw):
            if not _looks_like_citation_token(token, citation_ids):
                continue
            if token in citation_ids:
                cited_in_text.add(token)
            elif token.startswith("cit_"):
                findings.append(
                    AuditFinding(
                        finding_id=stable_id("aud", token + str(match.start())),
                        severity="serious",
                        category="citation_mapping",
                        message=f"Manuscript cites unknown citation ID {token}.",
                        location=f"character {match.start()}",
                        suggested_fix="Use only citation IDs from citation_registry.json or remove the bracketed citation.",
                        related_citation_ids=[token],
                    )
                )
    for claim in claims:
        if claim.needs_citation and not claim.citation_ids:
            findings.append(
                AuditFinding(
                    finding_id=stable_id("aud", claim.claim_id + "missing_citation"),
                    severity="warning",
                    category="citation_mapping",
                    message="Claim is marked as needing citation but has no citation IDs.",
                    location=claim.claim_id,
                    suggested_fix="Map the claim to an existing citation record or mark it unsupported.",
                    related_claim_ids=[claim.claim_id],
                )
            )
        for citation_id in claim.citation_ids:
            if citation_id not in citation_ids:
                findings.append(
                    AuditFinding(
                        finding_id=stable_id("aud", claim.claim_id + citation_id),
                        severity="serious",
                        category="citation_mapping",
                        message=f"Claim cites unknown citation ID {citation_id}.",
                        location=claim.claim_id,
                        suggested_fix="Use only citation IDs from the citation registry.",
                        related_claim_ids=[claim.claim_id],
                        related_citation_ids=[citation_id],
                    )
                )
            else:
                cited_in_text.add(citation_id)
    for citation in citations:
        if citation.title.strip().lower() in {"", "untitled source"} or not citation.year:
            findings.append(
                AuditFinding(
                    finding_id=stable_id("aud", citation.citation_id + "metadata_quality"),
                    severity="warning",
                    category="citation_metadata",
                    message=f"Citation metadata may be incomplete for {citation.citation_id}.",
                    location=citation.citation_id,
                    suggested_fix="Confirm title, authors, year, DOI/PMID, and journal before submission.",
                    related_citation_ids=[citation.citation_id],
                )
            )
        if citation.citation_id not in cited_in_text:
            findings.append(
                AuditFinding(
                    finding_id=stable_id("aud", citation.citation_id + "unused"),
                    severity="info",
                    category="citation_usage",
                    message=f"Supplied citation is not currently mapped to a manuscript claim: {citation.title}",
                    location=citation.citation_id,
                    suggested_fix="Map it to a supported background claim if relevant; otherwise leave it out of the final reference list.",
                    related_citation_ids=[citation.citation_id],
                )
            )
    return findings
