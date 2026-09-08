from __future__ import annotations

from manuscriptforge.models.claim import EvidenceItem
from manuscriptforge.utils.text import split_sentences


def evidence_snippets(evidence: list[EvidenceItem], max_sentences: int = 3) -> dict[str, list[str]]:
    snippets: dict[str, list[str]] = {}
    for item in evidence:
        snippets[item.evidence_id] = split_sentences(item.text)[:max_sentences]
    return snippets
