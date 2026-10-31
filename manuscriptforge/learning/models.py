from __future__ import annotations

import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class StylePair(StrictModel):
    pair_id: str
    group_id: str
    document_family: str
    author_id: str
    left_text: str = Field(min_length=1, max_length=200000)
    right_text: str = Field(min_length=1, max_length=200000)
    preferred: Literal["left", "right"]
    source_hashes: list[str] = Field(min_length=1)
    provenance: str
    synthetic: bool

    @model_validator(mode="after")
    def valid_provenance(self) -> StylePair:
        if not all(
            (self.pair_id, self.group_id, self.document_family, self.author_id, self.provenance)
        ):
            raise ValueError("Pair IDs, grouping, author, and provenance must be explicit")
        if any(
            len(value) != 64 or any(c not in "0123456789abcdef" for c in value)
            for value in self.source_hashes
        ):
            raise ValueError("source_hashes must be lowercase SHA-256 digests")
        if self.left_text.strip() == self.right_text.strip():
            raise ValueError("Identical choices cannot encode a strict preference")
        return self


class PairDataset(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    dataset_id: str
    description: str
    pairs: list[StylePair]


class LearningConfig(StrictModel):
    seed: int = 20261031
    group_by_author: bool = False
    l2: float = Field(default=0.05, gt=0)
    max_iterations: int = Field(default=1500, ge=1, le=20000)
    learning_rate: float = Field(default=1.0, gt=0, le=10)
    patience: int = Field(default=60, ge=1)
    min_delta: float = Field(default=1e-7, ge=0)


class StyleRanker(StrictModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
    schema_version: Literal["1.0"] = "1.0"
    feature_version: Literal["stylometry-1"] = "stylometry-1"
    feature_names: tuple[str, ...]
    coefficients: tuple[float, ...]
    scales: tuple[float, ...]
    training_dataset_sha256: str
    training_pair_ids: tuple[str, ...]
    config: LearningConfig
    best_iteration: int
    synthetic_training: bool

    @model_validator(mode="after")
    def valid_dimensions(self) -> StyleRanker:
        if not len(self.feature_names) == len(self.coefficients) == len(self.scales):
            raise ValueError("Feature, coefficient, and scale dimensions differ")
        if not self.feature_names or len(set(self.feature_names)) != len(self.feature_names):
            raise ValueError("Feature names must be nonempty and unique")
        if any(not math.isfinite(x) for x in (*self.coefficients, *self.scales)) or any(
            x <= 0 for x in self.scales
        ):
            raise ValueError("Model contains nonfinite values or invalid scales")
        return self
