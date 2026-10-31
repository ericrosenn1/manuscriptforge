from __future__ import annotations

from manuscriptforge.models.citation import CitationRecord
from manuscriptforge.models.claim import ScientificClaim
from manuscriptforge.utils.text import word_tokens

MIN_CITATION_OVERLAP = 2
_FUNCTION_WORDS = set(["a", "an", "the", "and", "or", "but", "of", "in", "on", "at", "for", "from", "to", "with", "by", "is", "are", "was", "were", "be", "been", "being", "this", "that", "these", "those", "as", "it", "its", "we", "our", "their", "they", "not", "no", "can", "may", "could", "would", "should", "has", "have", "had"])


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
    claim_tokens = set(word_tokens(claim.claim_text)) - _FUNCTION_WORDS
    scored: list[tuple[int, CitationRecord]] = []
    for citation in citations:
        citation_tokens = set(word_tokens(_citation_text(citation))) - _FUNCTION_WORDS
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
                claim.needs_citation = True
                claim.needs_human_review = True
                claim.notes = (
                    claim.notes
                    + " Candidate citation IDs ranked by informative keyword overlap; evidence support remains unverified and requires human review."
                ).strip()
            else:
                claim.needs_citation = True
                claim.needs_human_review = True
                claim.notes = (
                    claim.notes
                    + " No supplied citation had enough keyword overlap; human mapping required."
                ).strip()
    return claims
