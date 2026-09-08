from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

PDFExtractionStatus = Literal["extracted", "partial", "empty", "failed", "likely_scanned"]


class PDFSourceSummary(BaseModel):
    path: str
    page_count: int | None = None
    extracted_char_count: int = 0
    extraction_status: PDFExtractionStatus = "empty"
    needs_ocr: bool = False
    warnings: list[str] = Field(default_factory=list)
    first_text_snippet: str = ""
    content_hash: str
