from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

MetadataStatus = Literal["supplied", "parsed", "enriched", "failed", "offline_skipped", "needs_review"]
MetadataConfidence = Literal["exact_identifier", "title_match", "weak_match", "unknown"]
SourceKind = Literal[
    "bibtex",
    "ris",
    "json",
    "manual_list",
    "pdf",
    "plain_text",
    "doi_list",
    "pmid_list",
]


class CitationRecord(BaseModel):
    citation_id: str
    title: str
    authors: list[str] = []
    year: str | None = None
    doi: str | None = None
    pmid: str | None = None
    pmcid: str | None = None
    arxiv_id: str | None = None
    url: str | None = None
    journal: str | None = None
    raw_bibtex: str | None = None
    source_path: str | None = None
    abstract: str | None = None
    metadata_status: MetadataStatus = "supplied"
    metadata_confidence: MetadataConfidence = "unknown"
    retrieval_notes: str = ""
    source_kind: SourceKind = "manual_list"
    notes: str = ""
