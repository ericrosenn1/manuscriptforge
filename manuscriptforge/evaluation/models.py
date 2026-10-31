from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Split = Literal["train", "dev", "test"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EvaluationSource(StrictModel):
    source_id: str
    document_family: str
    author_id: str
    text: str
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    provenance: str
    synthetic: bool = True


class EvaluationFact(StrictModel):
    fact_id: str
    subject: str
    predicate: str
    value: str
    unit: str | None = None
    source_id: str
    locator: str


class PublicTask(StrictModel):
    """Only these fields may be sent to a candidate; labels stay withheld."""

    case_id: str
    task_kind: Literal["grounded_response", "source_ranking"] = "grounded_response"
    query: str
    sources: list[EvaluationSource]
    facts: list[EvaluationFact]


class EvaluationCase(StrictModel):
    case_id: str
    task_kind: Literal["grounded_response", "source_ranking"] = "grounded_response"
    query: str
    source_ids: list[str]
    split: Split
    stratum: str
    expected_fact_ids: list[str]
    relevant_source_ids: list[str]
    should_abstain: bool = False
    related_case_id: str | None = None


class EvaluationDataset(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    dataset_id: str
    revision: str
    description: str
    synthetic: bool
    split_seed: int
    sources: list[EvaluationSource]
    facts: list[EvaluationFact]
    cases: list[EvaluationCase]


class Assertion(StrictModel):
    fact_id: str
    source_id: str
    subject: str
    predicate: str
    value: str
    unit: str | None = None


class CandidatePrediction(StrictModel):
    case_id: str
    assertions: list[Assertion] = Field(default_factory=list)
    ranked_source_ids: list[str] = Field(default_factory=list)
    abstained: bool = False
    confidence: float | None = Field(default=None, ge=0, le=1)
    text: str = ""
    provider: str = "deterministic"
    model: str = "unspecified"
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    cost_usd: float | None = Field(default=None, ge=0)
    usage_source: Literal["provider_reported", "unavailable"] = "unavailable"
    pricing_source: str | None = None

    @model_validator(mode="after")
    def honest_usage(self) -> CandidatePrediction:
        if self.usage_source == "unavailable" and (
            self.input_tokens is not None or self.output_tokens is not None
        ):
            raise ValueError("Token counts require provider-reported usage")
        if self.cost_usd is not None and not self.pricing_source:
            raise ValueError("Cost requires a pricing provenance source")
        return self
