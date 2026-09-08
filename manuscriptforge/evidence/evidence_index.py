from __future__ import annotations

from dataclasses import dataclass

from manuscriptforge.models.claim import EvidenceItem
from manuscriptforge.utils.text import word_tokens


@dataclass
class EvidenceIndex:
    items: list[EvidenceItem]

    def by_type(self, evidence_type: str) -> list[EvidenceItem]:
        return [item for item in self.items if item.evidence_type == evidence_type]

    def search(self, query: str, limit: int = 5) -> list[EvidenceItem]:
        query_tokens = set(word_tokens(query))
        scored = []
        for item in self.items:
            tokens = set(word_tokens(item.text))
            score = len(query_tokens & tokens) / max(len(query_tokens | tokens), 1)
            scored.append((score, item))
        return [item for score, item in sorted(scored, reverse=True)[:limit] if score > 0]
