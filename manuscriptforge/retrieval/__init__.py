"""Deterministic lexical retrieval; no embeddings or network dependencies."""

from manuscriptforge.retrieval.lexical import (
    LexicalIndex,
    RetrievalFilter,
    RetrievalRecord,
    SearchHit,
    text_sha256,
    tokenize,
)

__all__ = [
    "LexicalIndex",
    "RetrievalFilter",
    "RetrievalRecord",
    "SearchHit",
    "text_sha256",
    "tokenize",
]
