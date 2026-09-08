from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from manuscriptforge.models.style import (
    DEFAULT_ALLOWED_STYLE_MODES,
    STYLE_MODE_VALUES,
    StyleMode,
    normalize_style_mode,
)


class PrivacySettings(BaseModel):
    local_only: bool = True


class LLMSettings(BaseModel):
    provider: str | None = "mock"
    model: str | None = None
    temperature: float = 0.2


class ManuscriptSettings(BaseModel):
    include_sections: list[str] = Field(
        default_factory=lambda: [
            "Abstract",
            "Introduction",
            "Methods",
            "Results",
            "Discussion",
            "Limitations",
            "Conclusion",
            "References",
        ]
    )
    structured_abstract: bool = True


class StyleExtractionSettings(BaseModel):
    enabled: bool = True
    cache_extracted_text: bool = True
    exclude_references_section: bool = True
    max_chunk_words: int = 900
    min_chunk_words: int = 40
    include_pdf: bool = True
    include_docx: bool = True
    fail_on_extraction_error: bool = False


class StyleCurationSettings(BaseModel):
    use_chunk_registry: bool = True
    require_chunk_approval: bool = False
    include_needs_review_chunks: bool = True
    exclude_warning_chunks_by_default: bool = False


class StyleSettings(BaseModel):
    use_style_corpus: bool = True
    max_style_examples_per_section: int = 5
    active_mode: str = StyleMode.academic_manuscript.value
    allowed_modes: list[str] = Field(default_factory=lambda: list(DEFAULT_ALLOWED_STYLE_MODES))
    include_modes: list[str] = Field(default_factory=lambda: [StyleMode.academic_manuscript.value])
    exclude_modes: list[str] = Field(default_factory=lambda: [StyleMode.coursework_explanatory.value])
    mode_fallback: str = StyleMode.unknown.value
    warn_on_mixed_modes: bool = True
    extraction: StyleExtractionSettings = Field(default_factory=StyleExtractionSettings)
    curation: StyleCurationSettings = Field(default_factory=StyleCurationSettings)
    section_controls: dict[str, dict[str, str]] = Field(default_factory=dict)

    @field_validator("active_mode", "mode_fallback")
    @classmethod
    def validate_style_mode(cls, value: str) -> str:
        return normalize_style_mode(value)

    @field_validator("allowed_modes", "include_modes", "exclude_modes")
    @classmethod
    def validate_style_modes(cls, values: list[str]) -> list[str]:
        normalized = [normalize_style_mode(value) for value in values]
        return [value for value in normalized if value in STYLE_MODE_VALUES]


class StyleBenchmarkSettings(BaseModel):
    enabled: bool = True
    weights: dict[str, float] = Field(
        default_factory=lambda: {
            "section_similarity": 0.35,
            "style_memory_alignment": 0.20,
            "generic_phrase_avoidance": 0.10,
            "causal_hedging_alignment": 0.15,
            "citation_integration": 0.10,
            "traceability": 0.10,
        }
    )


class EvidenceSettings(BaseModel):
    require_claim_registry: bool = True
    flag_unsupported_claims: bool = True


class SourcesSettings(BaseModel):
    allow_network_enrichment: bool = False
    metadata_cache_dir: str = ".manuscriptforge_cache/metadata"
    enrich_doi: bool = False
    enrich_pmid: bool = False
    fail_on_metadata_error: bool = False
    require_pdf_text: bool = False


class TableClaimGenerationSettings(BaseModel):
    generate_row_claims: bool = True
    generate_table_summary_claims: bool = True
    max_row_claims: int = 20


class TableSchema(BaseModel):
    description: str | None = None
    column_roles: dict[str, str] = Field(default_factory=dict)
    thresholds: dict[str, float] = Field(default_factory=dict)
    claim_generation: TableClaimGenerationSettings = Field(
        default_factory=TableClaimGenerationSettings
    )


class TableSettings(BaseModel):
    schemas: dict[str, TableSchema] = Field(default_factory=dict)


OutputFormat = Literal["markdown", "docx", "latex", "xlsx"]


def default_output_formats() -> list[OutputFormat]:
    return ["markdown", "docx", "latex", "xlsx"]


class OutputSettings(BaseModel):
    formats: list[OutputFormat] = Field(default_factory=default_output_formats)


class ProjectConfig(BaseModel):
    project_name: str = "Example Manuscript"
    article_type: str = "biomedical_imrad"
    target_journal: str | None = None
    target_audience: str = "biomedical researchers"
    field: str = "bioinformatics"
    corresponding_author_name: str | None = None
    citation_style: str = "numeric"
    privacy: PrivacySettings = Field(default_factory=PrivacySettings)
    llm: LLMSettings = Field(default_factory=LLMSettings)
    manuscript: ManuscriptSettings = Field(default_factory=ManuscriptSettings)
    style: StyleSettings = Field(default_factory=StyleSettings)
    style_benchmark: StyleBenchmarkSettings = Field(default_factory=StyleBenchmarkSettings)
    evidence: EvidenceSettings = Field(default_factory=EvidenceSettings)
    sources: SourcesSettings = Field(default_factory=SourcesSettings)
    tables: TableSettings = Field(default_factory=TableSettings)
    outputs: OutputSettings = Field(default_factory=OutputSettings)
