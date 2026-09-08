from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class AuditFinding(BaseModel):
    finding_id: str
    severity: Literal["info", "warning", "serious"] = "info"
    category: str
    message: str
    location: str | None = None
    suggested_fix: str | None = None
    related_claim_ids: list[str] = Field(default_factory=list)
    related_citation_ids: list[str] = Field(default_factory=list)
