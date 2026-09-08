from __future__ import annotations

from manuscriptforge.models.citation import CitationRecord
from manuscriptforge.models.claim import ScientificClaim
from manuscriptforge.utils.text import word_tokens

MIN_CITATION_OVERLAP = 2


def _citation_text(citation: CitationRecord) -> str:
    return " ".join(
        part
        for part in [
            citation.title,
            citation.journal or "",
            citation.abstract or "",
            " ".join(citation.authors),
            citation.notes,
        ]
        if part
    )


def _rank_citations(claim: ScientificClaim, citations: list[CitationRecord]) -> list[CitationRecord]:
    claim_tokens = set(word_tokens(claim.claim_text))
    scored: list[tuple[int, CitationRecord]] = []
    for citation in citations:
        citation_tokens = set(word_tokens(_citation_text(citation)))
        overlap = len(claim_tokens & citation_tokens)
        if overlap >= MIN_CITATION_OVERLAP:
            scored.append((overlap, citation))
    return [citation for _, citation in sorted(scored, key=lambda item: item[0], reverse=True)]


def map_citations_to_claims(
    claims: list[ScientificClaim], citations: list[CitationRecord]
) -> list[ScientificClaim]:
    if not citations:
        return claims
    for claim in claims:
        if claim.claim_type == "background" or claim.needs_citation:
            ranked = _rank_citations(claim, citations)
            if ranked:
                claim.citation_ids = [citation.citation_id for citation in ranked[:2]]
                claim.needs_citation = False
                claim.notes = (
                    claim.notes
                    + " Citation IDs mapped by keyword overlap with supplied source metadata."
                ).strip()
            else:
                claim.needs_citation = True
                claim.needs_human_review = True
                claim.notes = (
                    claim.notes
                    + " No supplied citation had enough keyword overlap; human mapping required."
                ).strip()
    return claims
