from __future__ import annotations

from typing import Any, TypeVar

from pydantic import BaseModel

from manuscriptforge.llm.base import LLMProvider
from manuscriptforge.utils.hashing import sha256_text

T = TypeVar("T", bound=BaseModel)


class MockLLMProvider(LLMProvider):
    name = "mock"

    def generate_text(self, prompt: str, system: str | None = None) -> str:
        del system
        digest = sha256_text(prompt)[:8]
        return (
            "Mock provider response. This deterministic text is intended for offline "
            f"workflow tests and should be reviewed by a human author. prompt_id={digest}"
        )

    def generate_json(
        self,
        prompt: str,
        schema: type[T] | None = None,
        system: str | None = None,
    ) -> dict[str, Any]:
        del system
        if schema is None:
            return {"provider": self.name, "prompt_id": sha256_text(prompt)[:8], "items": []}
        defaults: dict[str, Any] = {}
        for name, field in schema.model_fields.items():
            if field.default is not None:
                defaults[name] = field.default
        return defaults

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        embeddings: list[list[float]] = []
        for text in texts:
            digest = sha256_text(text)
            embeddings.append([int(digest[i : i + 2], 16) / 255 for i in range(0, 16, 2)])
        return embeddings
