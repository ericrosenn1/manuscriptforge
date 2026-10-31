"""Explicit operational settings, separate from scientific project settings."""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ResearchSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
    provider: Literal["mock", "openai"] = "mock"
    model: str = "gpt-6-luna"
    reasoning_effort: Literal["low", "medium", "high"] = "low"
    max_workers: int = Field(default=2, ge=1, le=8)
    max_words_per_section: int = Field(default=600, ge=50, le=5000)
    max_output_tokens: int = Field(default=4096, ge=256, le=16000)
    request_timeout_seconds: float = Field(default=90, gt=0, le=600)
    node_timeout_seconds: float = Field(default=300, gt=0, le=3600)
    max_attempts: int = Field(default=2, ge=1, le=3)
    use_cache: bool = True
    allow_network: bool = False
    budget_limit_usd: Decimal | None = None
    budget_ledger_path: str | None = None

    @model_validator(mode="after")
    def explicit_network_budget(self) -> ResearchSettings:
        if self.provider == "openai":
            if not self.allow_network:
                raise ValueError("OpenAI requires explicit allow_network=True")
            if (
                self.budget_limit_usd is None
                or not self.budget_limit_usd.is_finite()
                or self.budget_limit_usd <= 0
            ):
                raise ValueError("OpenAI requires an explicit positive budget_limit_usd")
            if not self.budget_ledger_path:
                raise ValueError("OpenAI requires a shared budget_ledger_path")
        return self
