from __future__ import annotations

import json
import os
import time
from typing import Any, TypeVar

from pydantic import BaseModel

from manuscriptforge.llm.base import LLMProvider, LLMProviderError

T = TypeVar("T", bound=BaseModel)


class OpenAIProvider(LLMProvider):
    name = "openai"

    def __init__(
        self,
        model: str | None = None,
        temperature: float = 0.2,
        local_only: bool = False,
        max_retries: int = 2,
    ) -> None:
        if local_only:
            raise LLMProviderError("OpenAIProvider is disabled because privacy.local_only is true")
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise LLMProviderError("OPENAI_API_KEY is not set")
        try:
            from openai import OpenAI
        except Exception as exc:  # pragma: no cover - depends on optional extra
            raise LLMProviderError("Install the openai extra to use OpenAIProvider") from exc
        self.client = OpenAI(api_key=api_key)
        self.model = model or os.getenv("OPENAI_MODEL") or "gpt-4.1-mini"
        self.temperature = temperature
        self.max_retries = max_retries

    def generate_text(self, prompt: str, system: str | None = None) -> str:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    temperature=self.temperature,
                )
                return response.choices[0].message.content or ""
            except Exception as exc:  # pragma: no cover - network provider
                last_error = exc
                time.sleep(0.5 * (attempt + 1))
        raise LLMProviderError(f"OpenAI text generation failed: {last_error}")

    def generate_json(
        self,
        prompt: str,
        schema: type[T] | None = None,
        system: str | None = None,
    ) -> dict[str, Any]:
        json_system = (
            (system + "\n" if system else "")
            + "Return valid JSON only. Do not include Markdown fences or commentary."
        )
        raw = self.generate_text(prompt, system=json_system)
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:  # pragma: no cover - network provider
            raise LLMProviderError(f"Provider did not return valid JSON: {raw[:200]}") from exc
        if schema is not None:
            return schema.model_validate(parsed).model_dump(mode="json")
        if not isinstance(parsed, dict):
            raise LLMProviderError("Expected a JSON object")
        return parsed
