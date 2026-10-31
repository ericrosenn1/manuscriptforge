"""Bounded Responses API adapter. No SDK retries or source prose in usage records."""

from __future__ import annotations

import copy
import json
import math
import os
import time
from collections.abc import Callable
from typing import Any, TypeVar

from pydantic import BaseModel

from manuscriptforge.llm.base import LLMProvider, LLMProviderError, LLMResponseError
from manuscriptforge.llm.response_capture import ResponseCapture
from manuscriptforge.utils.hashing import sha256_text

T = TypeVar("T", bound=BaseModel)
BeforeRequest = Callable[[dict[str, Any]], str]
OnResult = Callable[[dict[str, Any]], None]


def _field(value: Any, name: str, default: Any = None) -> Any:
    return value.get(name, default) if isinstance(value, dict) else getattr(value, name, default)


def _strict_schema(schema: type[BaseModel]) -> dict[str, Any]:
    """Make Pydantic optional/default fields explicit for strict structured output."""
    result = copy.deepcopy(schema.model_json_schema())

    def visit(node: Any) -> None:
        if isinstance(node, dict):
            node.pop("default", None)
            if node.get("type") == "object" or "properties" in node:
                if isinstance(node.get("additionalProperties"), dict):
                    raise LLMProviderError(
                        "Open-ended mapping fields are unsupported in strict output schemas.",
                        code="invalid_schema",
                    )
                node["additionalProperties"] = False
                node["required"] = list(node.get("properties", {}))
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)

    visit(result)
    if result.get("type") != "object":
        raise LLMProviderError(
            "Structured responses require an object schema.", code="invalid_schema"
        )
    return result


def _token_count(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _retryable(error: Exception) -> bool:
    status = getattr(error, "status_code", None)
    return (
        status in {408, 409, 429}
        or isinstance(status, int)
        and 500 <= status <= 599
        or isinstance(error, (TimeoutError, ConnectionError))
        or type(error).__name__ in {"APIConnectionError", "APITimeoutError"}
    )


class OpenAIProvider(LLMProvider):
    name = "openai"

    def __init__(
        self,
        model: str | None = None,
        temperature: float | None = 0.2,
        local_only: bool = False,
        max_retries: int = 2,
        *,
        timeout_seconds: float = 60.0,
        max_output_tokens: int = 4096,
        max_input_bytes: int = 1_000_000,
        reasoning_effort: str | None = None,
        client: Any = None,
        before_request: BeforeRequest | None = None,
        on_result: OnResult | None = None,
        capture_responses: bool = False,
        capture_max_bytes: int = 1_000_000,
    ) -> None:
        if local_only:
            raise LLMProviderError("OpenAIProvider is disabled because privacy.local_only is true")
        if max_retries < 0 or max_retries > 5:
            raise ValueError("max_retries must be between 0 and 5")
        if timeout_seconds <= 0 or max_output_tokens <= 0 or max_input_bytes <= 0:
            raise ValueError("Request limits must be positive")
        if (
            not math.isfinite(timeout_seconds)
            or temperature is not None
            and not math.isfinite(temperature)
        ):
            raise ValueError("Request parameters must be finite")
        if client is None:
            api_key = os.getenv("OPENAI_API_KEY")
            if not api_key:
                raise LLMProviderError("OPENAI_API_KEY is not set", code="missing_credentials")
            try:
                from openai import OpenAI
            except ImportError as exc:
                raise LLMProviderError("Install the openai extra to use OpenAIProvider") from exc
            client = OpenAI(api_key=api_key, timeout=timeout_seconds, max_retries=0)
        if not callable(getattr(getattr(client, "responses", None), "create", None)):
            raise LLMProviderError(
                "The client must support the Responses API.", code="unsupported_client"
            )
        self.client = client
        self.model = model or os.getenv("OPENAI_MODEL") or "gpt-4.1-mini"
        self.temperature = temperature
        self.max_retries = max_retries
        self.max_output_tokens = max_output_tokens
        self.max_input_bytes = max_input_bytes
        self.reasoning_effort = reasoning_effort
        self.before_request = before_request
        self.on_result = on_result
        self.last_record: dict[str, Any] | None = None
        self.response_capture = ResponseCapture(
            enabled=capture_responses, max_bytes=capture_max_bytes
        )

    def _request(
        self, prompt: str, system: str | None, schema: type[BaseModel] | None, *, json_output: bool
    ) -> str:
        self.last_record = None
        self.response_capture.clear()
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        body: dict[str, Any] = {
            "model": self.model,
            "input": messages,
            "max_output_tokens": self.max_output_tokens,
            "store": False,
            "service_tier": "default",
        }
        if self.temperature is not None:
            body["temperature"] = self.temperature
        if self.reasoning_effort is not None:
            body["reasoning"] = {"effort": self.reasoning_effort}
        if json_output:
            if schema is not None:
                body["text"] = {
                    "format": {
                        "type": "json_schema",
                        "name": schema.__name__,
                        "strict": True,
                        "schema": _strict_schema(schema),
                    }
                }
            else:
                body["text"] = {"format": {"type": "json_object"}}
        serialized = json.dumps(
            body, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
        input_bytes = len(serialized.encode("utf-8"))
        if input_bytes > self.max_input_bytes:
            raise LLMProviderError(
                "Request exceeds the configured input size limit.", code="input_limit"
            )
        request_hash = sha256_text(serialized)
        schema_hash = (
            sha256_text(json.dumps(schema.model_json_schema(), sort_keys=True)) if schema else None
        )
        for attempt in range(self.max_retries + 1):
            request_record = {
                "model": self.model,
                "input_bytes_upper_bound": input_bytes,
                "max_output_tokens": self.max_output_tokens,
                "request_hash": request_hash,
                "attempt": attempt + 1,
            }
            reservation_id = (
                self.before_request(dict(request_record)) if self.before_request else None
            )
            started = time.monotonic()
            response: Any = None
            error: Exception | None = None
            try:
                response = self.client.responses.create(**body)
            except Exception as exc:
                error = exc
            record: dict[str, Any] = {
                **request_record,
                "reservation_id": reservation_id,
                "response_model": None,
                "response_id": None,
                "input_tokens": None,
                "output_tokens": None,
                "cached_input_tokens": None,
                "reasoning_tokens": None,
                "status": "error",
                "error_class": type(error).__name__ if error else None,
                "elapsed_seconds": round(time.monotonic() - started, 6),
            }
            output = ""
            capture_output = None
            refusal_texts = []
            refusal = False
            status = None
            if response is not None:
                usage = _field(response, "usage", {})
                record.update(
                    {
                        "response_model": _field(response, "model"),
                        "response_id": _field(response, "id"),
                        "input_tokens": _token_count(_field(usage, "input_tokens")),
                        "output_tokens": _token_count(_field(usage, "output_tokens")),
                        "cached_input_tokens": _token_count(
                            _field(_field(usage, "input_tokens_details", {}), "cached_tokens")
                        ),
                        "reasoning_tokens": _token_count(
                            _field(_field(usage, "output_tokens_details", {}), "reasoning_tokens")
                        ),
                    }
                )
                status = _field(response, "status")
                texts = []
                for item in _field(response, "output", []) or []:
                    for content in _field(item, "content", []) or []:
                        refusal = refusal or _field(content, "type") == "refusal"
                        if _field(content, "type") == "refusal":
                            refusal_texts.append(str(_field(content, "refusal", "")))
                        if _field(content, "type") == "output_text":
                            texts.append(str(_field(content, "text", "")))
                output = _field(response, "output_text", None) or "".join(texts)
                direct_output = _field(response, "output_text", None)
                capture_output = (
                    direct_output
                    if isinstance(direct_output, str)
                    else ("".join(texts) if texts else None)
                )
                if refusal:
                    record.update(status="refused", error_class="ModelRefusal")
                elif status == "incomplete":
                    record.update(status="incomplete", error_class="IncompleteResponse")
                elif status != "completed":
                    record.update(status="error", error_class="UnexpectedResponseStatus")
                elif not isinstance(output, str) or not output.strip():
                    record.update(status="error", error_class="EmptyResponse")
                else:
                    record["status"] = "completed"
            self.response_capture.record(
                capture_output,
                attempt=attempt + 1,
                response_id=record["response_id"],
                model=record["response_model"],
                status=record["status"],
                schema_name=schema.__name__ if schema else None,
                schema_sha256=schema_hash,
                refused=refusal,
                incomplete=status == "incomplete",
                refusal_text="\n".join(refusal_texts) if refusal_texts else None,
                error_class=record["error_class"],
                error_code="request_failed" if error else record["error_class"],
            )
            self.last_record = record
            if self.on_result is not None:
                # A ledger failure must not trigger another paid request.
                self.on_result(dict(record))
            if error is not None:
                if _retryable(error) and attempt < self.max_retries:
                    time.sleep(min(0.5 * (2**attempt), 4.0))
                    continue
                raise LLMProviderError(
                    f"OpenAI request failed ({type(error).__name__}).",
                    code="request_failed",
                    retryable=_retryable(error),
                ) from error
            if record["status"] != "completed":
                raise LLMResponseError(
                    f"OpenAI response could not be used ({record['status']}).",
                    code=str(record["error_class"]),
                )
            return output
        raise LLMProviderError("OpenAI request attempts were exhausted.")

    def generate_text(self, prompt: str, system: str | None = None) -> str:
        return self._request(prompt, system, None, json_output=False)

    def generate_json(
        self, prompt: str, schema: type[T] | None = None, system: str | None = None
    ) -> dict[str, Any]:
        json_system = (
            system + "\n" if system else ""
        ) + "Return one JSON object matching the supplied schema."
        raw = self._request(prompt, json_system, schema, json_output=True)
        try:

            def reject_constant(value: str) -> None:
                raise ValueError("Non-finite JSON number")

            def object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
                parsed: dict[str, Any] = {}
                for key, value in pairs:
                    if key in parsed:
                        raise ValueError("Duplicate JSON object key")
                    parsed[key] = value
                return parsed

            payload = json.loads(
                raw, parse_constant=reject_constant, object_pairs_hook=object_pairs
            )
            if not isinstance(payload, dict):
                raise TypeError("Expected a JSON object")
            return (
                schema.model_validate(payload, strict=True).model_dump(mode="json")
                if schema
                else payload
            )
        except (ValueError, TypeError) as exc:
            self.response_capture.mark_error(
                error_class=type(exc).__name__, error_code="schema_mismatch"
            )
            raise LLMResponseError(
                "Response did not satisfy the requested JSON schema.", code="schema_mismatch"
            ) from exc
