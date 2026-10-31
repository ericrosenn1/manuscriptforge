from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class LLMProviderError(RuntimeError):
    """Raised when an LLM provider cannot satisfy a request."""

    def __init__(
        self, message: str, *, code: str = "provider_error", retryable: bool = False
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class LLMResponseError(LLMProviderError):
    """A completed provider operation returned unusable or refused content."""


class LLMProvider(ABC):
    name: str = "base"

    @abstractmethod
    def generate_text(self, prompt: str, system: str | None = None) -> str:
        raise NotImplementedError

    @abstractmethod
    def generate_json(
        self,
        prompt: str,
        schema: type[T] | None = None,
        system: str | None = None,
    ) -> dict[str, Any]:
        raise NotImplementedError

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        raise LLMProviderError(f"{self.name} does not implement embeddings")

    def rank_options(self, prompt: str, options: list[str]) -> list[str]:
        del prompt
        return list(options)
