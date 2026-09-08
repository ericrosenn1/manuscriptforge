from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

IssueType = Literal[
    "style_deviation",
    "generic_phrase",
    "overclaiming",
    "unsupported_claim",
    "citation_gap",
    "numeric_traceability",
    "journal_compliance",
    "methods_reproducibility",
    "weak_benchmark_score",
    "unclear_interpretation",
    "user_preference_needed",
]

Severity = Literal["info", "warning", "serious"]

VariantStrategy = Literal[
    "user_style_closer",
    "more_cautious",
    "more_concise",
    "more_dense",
    "methods_more_precise",
    "results_more_restrained",
    "discussion_more_synthetic",
    "citation_integrated",
    "limitation_style",
    "minimal_change",
]

ScientificRisk = Literal["low", "medium", "high"]
Confidence = Literal["low", "medium", "high"]


class ReviewItem(BaseModel):
    review_item_id: str
    run_id: str
    section_name: str | None = None
    section_type: str | None = None
    paragraph_index: int | None = None
    sentence_index: int | None = None
    original_text: str
    issue_type: IssueType
    severity: Severity = "info"
    priority_score: float
    linked_claim_ids: list[str] = Field(default_factory=list)
    linked_citation_ids: list[str] = Field(default_factory=list)
    linked_finding_ids: list[str] = Field(default_factory=list)
    linked_benchmark_metrics: list[str] = Field(default_factory=list)
    source_map_entry: dict[str, Any] = Field(default_factory=dict)
    why_it_matters: str
    suggested_review_action: str
    suggested_answer_format: str
    created_at: str


class ReviewPlan(BaseModel):
    run_id: str
    project_path: str
    review_items: list[ReviewItem] = Field(default_factory=list)
    selection_strategy: str
    input_artifacts: list[str] = Field(default_factory=list)
    generated_at: str
    summary_counts: dict[str, int] = Field(default_factory=dict)


class StyleVariant(BaseModel):
    variant_id: str
    review_item_id: str
    strategy: VariantStrategy
    variant_text: str
    rationale: str
    predicted_style_effects: dict[str, Any] = Field(default_factory=dict)
    predicted_scientific_risk: ScientificRisk = "low"
    preserves_claim_ids: list[str] = Field(default_factory=list)
    preserves_citation_ids: list[str] = Field(default_factory=list)
    introduces_new_numbers: bool = False
    introduces_new_citations: bool = False
    needs_audit: bool = False
    created_at: str


class FeedbackAnswer(BaseModel):
    feedback_id: str
    review_item_id: str
    selected_variant_id: str | None = None
    accepted_text: str | None = None
    rejected_variant_ids: list[str] = Field(default_factory=list)
    user_rewrite: str | None = None
    user_notes: str | None = None
    reason_selected: str | None = None
    style_tags: list[str] = Field(default_factory=list)
    claim_strength_preference: str | None = None
    citation_preference: str | None = None
    confidence: Confidence = "medium"
    created_at: str


class AcceptedRewrite(BaseModel):
    rewrite_id: str
    section_type: str | None = None
    original_text: str
    accepted_text: str
    rejected_texts: list[str] = Field(default_factory=list)
    feature_targets: list[str] = Field(default_factory=list)
    linked_style_profile: str | None = None
    linked_style_memory_feature: str | None = None
    linked_benchmark_metrics: list[str] = Field(default_factory=list)
    content_hash: str
    created_at: str


class FeedbackSummary(BaseModel):
    run_id: str
    total_review_items: int = 0
    total_variants: int = 0
    total_feedback_answers: int = 0
    accepted_rewrites: int = 0
    rejected_variants: int = 0
    style_memory_updates: int = 0
    unresolved_items: int = 0
    benchmark_before: float | None = None
    benchmark_after: float | None = None
    generated_at: str
    details: dict[str, Any] = Field(default_factory=dict)
