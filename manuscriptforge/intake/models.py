from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

AssetType = Literal[
    "writing_sample",
    "result_table",
    "source_list",
    "bibtex",
    "source_pdf",
    "manuscript_note",
    "feedback",
    "other",
]
AssetStatus = Literal[
    "new",
    "scanned",
    "needs_metadata",
    "needs_privacy_review",
    "approved",
    "excluded",
    "archived",
]
ApprovedUse = Literal[
    "drafting",
    "style_profile",
    "citation_mapping",
    "claim_generation",
    "benchmark",
    "feedback_training",
]


class DataAsset(BaseModel):
    asset_id: str
    asset_type: AssetType
    path: str
    relative_path: str
    filename: str
    extension: str
    content_hash: str
    size_bytes: int
    created_or_detected_at: str
    modified_at: str | None = None
    status: AssetStatus = "scanned"
    approved_for: list[ApprovedUse] = Field(default_factory=list)
    privacy_flags: list[str] = Field(default_factory=list)
    notes: str = ""
    missing: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class WritingSampleAsset(BaseModel):
    asset_id: str
    style_mode: str = "unknown"
    document_type: Literal[
        "full_manuscript",
        "abstract",
        "introduction",
        "methods",
        "results",
        "discussion",
        "limitations",
        "figure_legends",
        "response_to_reviewers",
        "cover_letter",
        "homework",
        "essay",
        "derivation",
        "concept_explanation",
        "grant_or_proposal",
        "unknown",
    ] = "unknown"
    section_types: list[str] = Field(default_factory=list)
    author_share: Literal["all_me", "mostly_me", "mixed", "mostly_not_me", "unknown"] = "unknown"
    quality_for_style: Literal["high", "medium", "low", "exclude"] = "medium"
    current_style_match: Literal["current", "older_but_useful", "outdated", "unknown"] = "unknown"
    coauthor_edited: Literal["no", "lightly", "heavily", "unknown"] = "unknown"
    approved_for_modes: list[str] = Field(default_factory=list)
    exclude_from_modes: list[str] = Field(default_factory=list)
    contains_sensitive_text: bool = False
    privacy_reviewed: bool = False
    notes: str = ""


class ResultTableAsset(BaseModel):
    asset_id: str
    table_role: Literal[
        "primary_results",
        "secondary_results",
        "exploratory_results",
        "validation_results",
        "supplementary",
        "unknown",
    ] = "unknown"
    analysis_type: str = ""
    primary_comparison: str = ""
    row_unit: str = ""
    key_columns: list[str] = Field(default_factory=list)
    detected_column_roles: dict[str, str] = Field(default_factory=dict)
    schema_review_status: Literal["not_inferred", "inferred", "needs_review", "approved"] = "not_inferred"
    claim_generation_status: Literal["disabled", "ready", "needs_review"] = "needs_review"
    contains_sensitive_data: bool = False
    deidentified: bool = False
    notes: str = ""


class SourceAsset(BaseModel):
    asset_id: str
    source_type: Literal["source_list", "bibtex", "doi_list", "pmid_list", "pdf", "manual"]
    metadata_status: Literal["scaffold", "incomplete", "parsed", "enriched", "needs_review", "approved"] = "needs_review"
    citation_count: int = 0
    incomplete_metadata_count: int = 0
    approved_for_citation_mapping: bool = False
    notes: str = ""


ImportKind = Literal[
    "writing_sample",
    "result_table",
    "source_pdf",
    "source_list",
    "bibtex",
    "feedback_jsonl",
    "manuscript_note",
    "miscellaneous",
    "unknown",
]
DuplicateStatus = Literal[
    "new",
    "duplicate_same_hash",
    "possible_duplicate_name",
    "possible_duplicate_content",
    "unknown",
]
PrivacyStatus = Literal["unknown", "needs_review", "reviewed"]
PrivacyScanStatus = Literal[
    "not_scanned_binary",
    "scanned_text_snippet",
    "skipped_large_file",
    "scan_failed",
    "not_applicable",
]
ImportStatus = Literal["staged", "needs_metadata", "ready", "imported", "rejected", "archived"]
ImportAction = Literal[
    "approve",
    "reject",
    "archive",
    "update_metadata",
    "import_copy",
    "import_move",
]


class ImportCandidate(BaseModel):
    import_id: str
    source_path: str
    relative_source_path: str
    filename: str
    extension: str
    size_bytes: int
    content_hash: str
    detected_kind: ImportKind = "unknown"
    suggested_destination: str = ""
    suggested_style_mode: str = "unknown"
    suggested_document_type: str = "unknown"
    suggested_asset_type: str = "other"
    duplicate_status: DuplicateStatus = "unknown"
    privacy_status: PrivacyStatus = "unknown"
    privacy_scan_status: PrivacyScanStatus = "not_applicable"
    privacy_hits: list[str] = Field(default_factory=list)
    import_status: ImportStatus = "staged"
    warnings: list[str] = Field(default_factory=list)
    notes: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class ImportDecision(BaseModel):
    decision_id: str
    import_id: str
    action: ImportAction
    destination_path: str = ""
    style_mode: str = ""
    document_type: str = ""
    approved_for: list[str] = Field(default_factory=list)
    note: str = ""
    decided_at: str
