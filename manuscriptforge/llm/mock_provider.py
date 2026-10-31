from __future__ import annotations

import json
from collections import deque
from collections.abc import Callable, Iterable
from typing import Any, TypeVar

from pydantic import BaseModel

from manuscriptforge.llm.base import LLMProvider, LLMProviderError, LLMResponseError
from manuscriptforge.utils.hashing import sha256_text

T = TypeVar("T", bound=BaseModel)


class MockLLMProvider(LLMProvider):
    """Offline deterministic provider; schema output requires valid defaults or a fixture.

    Required scientific fields are never synthesized from their type. Supply json_responses
    (a replay queue) or json_factory for structured workflow tests.
    """

    name = "mock"

    def __init__(
        self,
        *,
        json_responses: Iterable[dict[str, Any]] | None = None,
        text_responses: Iterable[str] | None = None,
        json_factory: Callable[[str, type[BaseModel] | None], dict[str, Any]] | None = None,
    ) -> None:
        self._json_responses = deque(json_responses or ())
        self._text_responses = deque(text_responses or ())
        self._replay_json = json_responses is not None
        self._replay_text = text_responses is not None
        self._json_factory = json_factory

    def generate_text(self, prompt: str, system: str | None = None) -> str:
        if self._text_responses:
            return self._text_responses.popleft()
        if self._replay_text:
            raise LLMProviderError("The text replay fixture is exhausted.", code="replay_exhausted")
        digest = sha256_text((system + "\n" if system else "") + prompt)[:8]
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
        if self._json_responses:
            payload = self._json_responses.popleft()
        elif self._replay_json:
            raise LLMProviderError("The JSON replay fixture is exhausted.", code="replay_exhausted")
        elif self._json_factory is not None:
            payload = self._json_factory(prompt, schema)
        elif schema is None:
            payload = {
                "provider": self.name,
                "prompt_id": sha256_text((system + "\n" if system else "") + prompt)[:8],
                "items": [],
            }
        else:
            try:
                return schema().model_dump(mode="json")
            except Exception as exc:
                raise LLMProviderError(
                    "Mock structured generation requires a fixture for required schema fields.",
                    code="mock_fixture_required",
                ) from exc
        if not isinstance(payload, dict):
            raise LLMResponseError("Mock fixture must be a JSON object.", code="invalid_fixture")
        if schema is None:
            try:
                return json.loads(json.dumps(payload, allow_nan=False))
            except (TypeError, ValueError) as exc:
                raise LLMResponseError("Mock fixture is not valid JSON.", code="invalid_fixture") from exc
        try:
            result = schema.model_validate(payload, strict=True).model_dump(mode="json")
            json.dumps(result, allow_nan=False)
            return result
        except Exception as exc:
            raise LLMResponseError(
                "Mock fixture does not satisfy the requested schema.", code="schema_mismatch"
            ) from exc

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Return deterministic test vectors, not semantic embeddings."""
        embeddings: list[list[float]] = []
        for text in texts:
            digest = sha256_text(text)
            embeddings.append([int(digest[i : i + 2], 16) / 255 for i in range(0, 16, 2)])
        return embeddings
