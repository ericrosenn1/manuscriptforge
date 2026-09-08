from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from manuscriptforge.models.citation import CitationRecord


class ManuscriptSection(BaseModel):
    section_name: str
    section_type: str
    content: str
    style_mode: str | None = None
    claim_ids: list[str] = Field(default_factory=list)
    citation_ids: list[str] = Field(default_factory=list)
    style_chunk_ids: list[str] = Field(default_factory=list)
    unresolved_flags: list[str] = Field(default_factory=list)
    revision_notes: list[str] = Field(default_factory=list)


class Manuscript(BaseModel):
    title: str
    title_candidates: list[str] = Field(default_factory=list)
    abstract: str = ""
    sections: list[ManuscriptSection] = Field(default_factory=list)
    references: list[CitationRecord] = Field(default_factory=list)
    figure_legends: list[str] = Field(default_factory=list)
    tables: list[dict[str, Any]] = Field(default_factory=list)
    ai_disclosure: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
