from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

EvidenceType = Literal[
    "table",
    "figure",
    "method_note",
    "interpretation_note",
    "source_pdf",
    "bibtex",
    "manual",
]

SupportStrength = Literal["direct", "indirect", "weak", "unsupported", "needs_review"]
ClaimType = Literal["result", "method", "background", "interpretation", "limitation", "future_work"]


class EvidenceItem(BaseModel):
    evidence_id: str
    evidence_type: EvidenceType
    source_path: str
    source_label: str
    locator: str | None = None
    text: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    content_hash: str


class ScientificClaim(BaseModel):
    claim_id: str
    claim_text: str
    normalized_claim: str
    section_target: str
    evidence_items: list[EvidenceItem] = Field(default_factory=list)
    evidence_type: EvidenceType | None = None
    support_strength: SupportStrength = "needs_review"
    claim_type: ClaimType = "interpretation"
    allowed_language: list[str] = Field(default_factory=list)
    forbidden_language: list[str] = Field(default_factory=list)
    citation_ids: list[str] = Field(default_factory=list)
    needs_citation: bool = False
    needs_human_review: bool = True
    notes: str = ""
