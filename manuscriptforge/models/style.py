from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field


class StyleMode(StrEnum):
    academic_manuscript = "academic_manuscript"
    coursework_explanatory = "coursework_explanatory"
    response_to_reviewers = "response_to_reviewers"
    journal_cover_letter = "journal_cover_letter"
    grant_or_proposal = "grant_or_proposal"
    review_article = "review_article"
    journal_adapted_academic = "journal_adapted_academic"
    unknown = "unknown"


STYLE_MODE_VALUES = tuple(mode.value for mode in StyleMode)
DEFAULT_ALLOWED_STYLE_MODES = [
    StyleMode.academic_manuscript.value,
    StyleMode.coursework_explanatory.value,
    StyleMode.response_to_reviewers.value,
    StyleMode.journal_cover_letter.value,
    StyleMode.grant_or_proposal.value,
    StyleMode.review_article.value,
    StyleMode.journal_adapted_academic.value,
]


def normalize_style_mode(value: str | None, fallback: str = StyleMode.unknown.value) -> str:
    """Return a supported style mode, falling back for empty or unknown values."""
    cleaned = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    return cleaned if cleaned in STYLE_MODE_VALUES else fallback


class StyleCorpusItem(BaseModel):
    source_path: str
    text: str = ""
    content_hash: str = ""
    style_mode: str = StyleMode.unknown.value
    document_type: str = "unknown"
    approved_for: list[str] = Field(default_factory=list)
    style_chunk_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class StyleExemplar(BaseModel):
    chunk_id: str
    source_file: str
    section_type: str
    style_mode: str = StyleMode.unknown.value
    text: str
    score: float | None = None
    why_selected: str = ""
    content_hash: str = ""


class StyleMemory(BaseModel):
    style_mode: str = StyleMode.unknown.value
    updated_utc: str | None = None
    preference_records: int = 0
    answered_preferences: int = 0
    data: dict[str, Any] = Field(default_factory=dict)


class StyleProfile(BaseModel):
    style_mode: str = StyleMode.unknown.value
    corpus_files: list[str] = Field(default_factory=list)
    global_features: dict[str, Any] = Field(default_factory=dict)
    section_features: dict[str, Any] = Field(default_factory=dict)
    preferred_phrases: list[str] = Field(default_factory=list)
    avoided_phrases: list[str] = Field(default_factory=list)
    hedging_profile: dict[str, Any] = Field(default_factory=dict)
    transition_profile: dict[str, Any] = Field(default_factory=dict)
    citation_style: dict[str, Any] = Field(default_factory=dict)
    sentence_length_distribution: dict[str, Any] = Field(default_factory=dict)
    paragraph_length_distribution: dict[str, Any] = Field(default_factory=dict)
    examples_by_section: dict[str, list[str]] = Field(default_factory=dict)
    preference_training_examples: list[dict[str, Any]] = Field(default_factory=list)


ExtractionStatus = Literal[
    "extracted",
    "partial",
    "empty",
    "failed",
    "unsupported",
    "likely_scanned_or_unextractable",
]


class StyleDocumentExtraction(BaseModel):
    asset_id: str
    source_path: str
    relative_path: str
    filename: str
    extension: str
    style_mode: str = StyleMode.unknown.value
    extraction_status: ExtractionStatus
    extracted_text_path: str | None = None
    extracted_char_count: int = 0
    extracted_word_count: int = 0
    page_count: int | None = None
    paragraph_count: int | None = None
    warnings: list[str] = Field(default_factory=list)
    content_hash: str
    extracted_at: str


class StyleChunk(BaseModel):
    chunk_id: str
    source_file: str
    section_type: str
    style_mode: str = StyleMode.unknown.value
    source_extension: str = ""
    source_asset_id: str = ""
    extracted_text_path: str | None = None
    extraction_status: str | None = None
    text: str
    token_estimate: int
    sentence_count: int
    paragraph_count: int
    detected_features: dict[str, Any] = Field(default_factory=dict)
    content_hash: str


ChunkSectionConfidence = Literal["high", "medium", "low", "unknown"]
ChunkApprovalStatus = Literal["new", "approved", "excluded", "needs_review"]
ChunkApprovedUse = Literal["style_profile", "exemplar_retrieval", "benchmark", "style_eval"]


class StyleChunkRegistryRow(BaseModel):
    chunk_id: str
    source_asset_id: str = ""
    source_file: str
    source_extension: str = ""
    extracted_text_path: str | None = None
    style_mode: str = StyleMode.unknown.value
    section_type: str = "unknown"
    chunk_index: int = 0
    word_count: int = 0
    sentence_count: int = 0
    paragraph_count: int = 0
    content_hash: str
    extraction_status: str | None = None
    section_confidence: ChunkSectionConfidence = "unknown"
    approval_status: ChunkApprovalStatus = "new"
    approved_for: list[ChunkApprovedUse] = Field(default_factory=list)
    warning_flags: list[str] = Field(default_factory=list)
    notes: str = ""
    missing: bool = False


class StyleEvalResult(BaseModel):
    eval_id: str
    eval_type: str
    section_type: str
    score: float
    passed: bool
    explanation: str
    feature_target: str
    source_chunk_id: str | None = None


class StyleBenchmarkMetric(BaseModel):
    metric_id: str
    metric_name: str
    active_style_mode: str | None = None
    section_type: str | None = None
    score: float | None = None
    max_score: float = 1.0
    normalized_score: float | None = None
    status: Literal["scored", "insufficient_data", "warning", "failed"] = "scored"
    explanation: str
    evidence: dict[str, Any] = Field(default_factory=dict)
    suggested_fix: str | None = None


class StyleBenchmarkReport(BaseModel):
    run_id: str
    compared_run_id: str | None = None
    active_style_mode: str = StyleMode.unknown.value
    overall_score: float
    section_scores: dict[str, Any] = Field(default_factory=dict)
    metric_scores: list[StyleBenchmarkMetric] = Field(default_factory=list)
    eval_results: list[StyleEvalResult] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    generated_at: str
    input_hashes: dict[str, str] = Field(default_factory=dict)
    config_hash: str
    comparison: dict[str, Any] = Field(default_factory=dict)
