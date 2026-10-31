from types import SimpleNamespace

import pytest
from pydantic import BaseModel, Field

from manuscriptforge.llm.base import LLMProviderError, LLMResponseError
from manuscriptforge.llm.mock_provider import MockLLMProvider
from manuscriptforge.llm.openai_provider import OpenAIProvider


class Payload(BaseModel):
    title: str
    items: list[str] = Field(default_factory=list)
    note: str | None = None


class Defaults(BaseModel):
    items: list[str] = Field(default_factory=list)
    note: str | None = None


def response(text='{"title":"Known title","items":[],"note":null}', **changes):
    values = dict(
        status="completed",
        output_text=text,
        output=[],
        id="response-1",
        model="snapshot-model",
        usage=dict(
            input_tokens=31,
            output_tokens=9,
            input_tokens_details=dict(cached_tokens=4),
            output_tokens_details=dict(reasoning_tokens=2),
        ),
    )
    values.update(changes)
    return SimpleNamespace(**values)


class FakeClient:
    def __init__(self, values):
        self.values = iter(values)
        self.calls = []
        self.responses = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        value = next(self.values)
        if isinstance(value, Exception):
            raise value
        return value


def test_mock_defaults_are_json_valid_and_required_fields_need_a_fixture():
    assert MockLLMProvider().generate_json("input", Defaults) == {"items": [], "note": None}
    with pytest.raises(LLMProviderError, match="requires a fixture"):
        MockLLMProvider().generate_json("input", Payload)
    provider = MockLLMProvider(json_responses=[{"title": "Known title"}])
    assert provider.generate_json("input", Payload)["title"] == "Known title"
    with pytest.raises(LLMResponseError):
        MockLLMProvider(json_responses=[{"title": 5}]).generate_json("input", Payload)


def test_replay_exhaustion_never_falls_back_to_synthetic_success():
    provider = MockLLMProvider(json_responses=[{}], text_responses=["known response"])
    assert provider.generate_json("input", Defaults) == {"items": [], "note": None}
    assert provider.generate_text("input") == "known response"
    with pytest.raises(LLMProviderError, match="exhausted"):
        provider.generate_json("input", Defaults)
    with pytest.raises(LLMProviderError, match="exhausted"):
        provider.generate_text("input")


def test_response_schema_and_usage_hooks_are_explicit_and_do_not_log_prose():
    client = FakeClient([response()])
    requests, outcomes = [], []

    def reserve(record):
        requests.append(record)
        return "reservation-1"

    provider = OpenAIProvider(
        client=client, temperature=None, before_request=reserve, on_result=outcomes.append
    )
    assert provider.generate_json("PRIVATE CORPUS", Payload)["title"] == "Known title"
    body = client.calls[0]
    assert body["text"]["format"]["type"] == "json_schema"
    schema = body["text"]["format"]["schema"]
    assert set(schema["required"]) == {"title", "items", "note"}
    assert schema["additionalProperties"] is False
    assert "temperature" not in body
    assert body["store"] is False and body["service_tier"] == "default"
    assert "tools" not in body
    assert requests[0]["input_bytes_upper_bound"] > len("PRIVATE CORPUS")
    assert outcomes[0]["reservation_id"] == "reservation-1"
    assert outcomes[0]["input_tokens"] == 31
    assert outcomes[0]["cached_input_tokens"] == 4
    assert outcomes[0]["reasoning_tokens"] == 2
    assert outcomes[0]["response_model"] == "snapshot-model"
    assert outcomes[0]["status"] == "completed"
    assert "PRIVATE CORPUS" not in str(requests + outcomes)


@pytest.mark.parametrize(
    "value,code",
    [
        (response(status="incomplete"), "IncompleteResponse"),
        (
            response(text="", output=[{"content": [{"type": "refusal", "refusal": "refused"}]}]),
            "ModelRefusal",
        ),
        (response(text=""), "EmptyResponse"),
        (response(status="failed"), "UnexpectedResponseStatus"),
    ],
)
def test_refusal_truncation_and_empty_results_fail_without_retry(value, code):
    client = FakeClient([value])
    records = []
    with pytest.raises(LLMResponseError) as raised:
        OpenAIProvider(client=client, on_result=records.append).generate_text("input")
    assert raised.value.code == code
    assert len(client.calls) == len(records) == 1


def test_transient_retry_reserves_each_attempt_but_authentication_does_not(monkeypatch):
    monkeypatch.setattr("manuscriptforge.llm.openai_provider.time.sleep", lambda _: None)
    transient = RuntimeError("private message must not be logged")
    transient.status_code = 429
    client = FakeClient([transient, response(text="Done")])
    requests, records = [], []
    provider = OpenAIProvider(
        client=client,
        before_request=lambda record: requests.append(record) or str(len(requests)),
        on_result=records.append,
    )
    assert provider.generate_text("input") == "Done"
    assert [record["reservation_id"] for record in records] == ["1", "2"]
    assert [record["status"] for record in records] == ["error", "completed"]
    assert "private message" not in str(records)
    denied = RuntimeError("denied")
    denied.status_code = 401
    client = FakeClient([denied])
    with pytest.raises(LLMProviderError):
        OpenAIProvider(client=client).generate_text("input")
    assert len(client.calls) == 1


def test_preflight_denial_and_input_limits_cannot_start_network_calls():
    client = FakeClient([])

    def deny(_):
        raise LLMProviderError("Budget exhausted")

    with pytest.raises(LLMProviderError, match="Budget exhausted"):
        OpenAIProvider(client=client, before_request=deny).generate_text("input")
    with pytest.raises(LLMProviderError, match="input size"):
        OpenAIProvider(client=client, max_input_bytes=10).generate_text("input")
    with pytest.raises(LLMProviderError, match="local_only"):
        OpenAIProvider(client=client, local_only=True)
    assert not client.calls


def test_schema_failure_remains_typed_and_does_not_retry():
    client = FakeClient([response(text='{"title":99}')])
    with pytest.raises(LLMResponseError) as error:
        OpenAIProvider(client=client).generate_json("input", Payload)
    assert error.value.code == "schema_mismatch"
    assert len(client.calls) == 1


def test_failed_usage_callback_does_not_repeat_paid_request():
    client = FakeClient([response(text="Done")])

    def failed_ledger(_):
        raise RuntimeError("ledger write failed")

    with pytest.raises(RuntimeError, match="ledger write failed"):
        OpenAIProvider(client=client, on_result=failed_ledger).generate_text("input")
    assert len(client.calls) == 1


@pytest.mark.parametrize("raw", ['{"value":NaN}', '{"title":"one","title":"two"}', "[]"])
def test_nonstandard_json_and_duplicate_keys_are_rejected(raw):
    client = FakeClient([response(text=raw)])
    with pytest.raises(LLMResponseError, match="JSON schema"):
        OpenAIProvider(client=client).generate_json("input")
    assert len(client.calls) == 1


def test_unbounded_mapping_schema_is_rejected_before_dispatch():
    class MappingPayload(BaseModel):
        values: dict[str, int]

    client = FakeClient([])
    with pytest.raises(LLMProviderError, match="mapping fields"):
        OpenAIProvider(client=client).generate_json("input", MappingPayload)
    assert not client.calls


def test_reasoning_parameter_and_input_schema_are_counted_in_reservation():
    client = FakeClient([response()])
    requests = []
    provider = OpenAIProvider(
        client=client,
        temperature=None,
        reasoning_effort="low",
        before_request=lambda record: requests.append(record) or "reservation",
    )
    provider.generate_json("input", Payload)
    assert client.calls[0]["reasoning"] == {"effort": "low"}
    assert requests[0]["input_bytes_upper_bound"] > 100
