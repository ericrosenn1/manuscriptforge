from __future__ import annotations

import hashlib
import json
import re
from types import SimpleNamespace

import pytest

from manuscriptforge.evaluation.extraction_runner import (
    evaluate_source_only,
    run_extraction_comparison,
)
from manuscriptforge.evaluation.synthetic import build_synthetic_dataset
from manuscriptforge.llm.base import LLMProviderError, LLMResponseError
from manuscriptforge.llm.extraction import FactExtractionResponse
from manuscriptforge.llm.extraction_adapter import FactExtractionCandidate
from manuscriptforge.llm.openai_provider import OpenAIProvider
from manuscriptforge.llm.response_capture import ResponseCapture


class Client:
    def __init__(self, mode="valid"):
        self.responses = self
        self.mode = mode
        self.returned = []

    def create(self, **body):
        if self.mode == "schema":
            payload = {"facts": "bad type"}
        else:
            request = json.loads(body["input"][-1]["content"])["input_data"]
            source = request["sources"][0]
            matched = re.search(r"(collection-\d+) has (\d+) records\.", source["text"])
            if matched:
                payload = {
                    "facts": [
                        {
                            "source_id": source["source_id"],
                            "subject": matched[1],
                            "predicate": "count",
                            "value": matched[2],
                            "unit": "records",
                            "quote": matched[0],
                        }
                    ]
                }
                if self.mode == "guard":
                    payload["facts"][0]["value"] = "999999"
            else:
                payload = {"facts": [], "unresolved_questions": ["Not available."]}
        raw = json.dumps(payload, ensure_ascii=False)
        self.returned.append(raw)
        return SimpleNamespace(
            status="completed",
            output_text=raw,
            output=[],
            id="response-fixture",
            model="offline-fixture",
            usage={},
        )


def make_candidate(mode="valid"):
    client = Client(mode)
    provider = OpenAIProvider(client=client, max_retries=0)
    return FactExtractionCandidate(provider), client


@pytest.mark.parametrize(
    "mode,code", [("schema", "schema_mismatch"), ("guard", "extraction_grounding_failed")]
)
def test_failures_retain_raw_json_without_prompts_and_clear_after_each_call(mode, code):
    dataset = build_synthetic_dataset(families=5)
    dataset.cases[0].query += " PRIVATE_REQUEST_SENTINEL"
    candidate, client = make_candidate(mode)
    row = evaluate_source_only(
        dataset, dataset.cases[0].case_id, candidate, capture_policy="failures"
    )
    capture = row["response_capture"]
    assert capture["status"] == "captured"
    record = capture["attempts"][0]
    assert record["output_text"] == client.returned[0]
    assert record["output_sha256"] == hashlib.sha256(client.returned[0].encode()).hexdigest()
    assert record["output_bytes"] == len(client.returned[0].encode())
    assert record["schema_name"] == "FactExtractionResponse"
    assert row["error_details"]["code"] == code
    assert "PRIVATE_REQUEST_SENTINEL" not in json.dumps(capture)
    assert candidate.provider.response_capture.snapshot() == []
    assert not candidate.provider.response_capture.enabled


def test_capture_is_off_by_default_and_failures_only_omits_success():
    dataset = build_synthetic_dataset(families=5)
    candidate, _ = make_candidate()
    row = evaluate_source_only(dataset, dataset.cases[0].case_id, candidate)
    assert row["complete_case_correct"]
    assert row["response_capture"] == {"policy": "off", "status": "disabled", "attempts": []}
    row = evaluate_source_only(
        dataset, dataset.cases[0].case_id, candidate, capture_policy="failures"
    )
    assert row["response_capture"]["status"] == "not_selected"
    assert row["response_capture"]["attempts"] == []


def test_local_capture_sidecars_are_included_in_manifest(tmp_path):
    dataset = build_synthetic_dataset(families=5)
    baseline, _ = make_candidate("schema")
    candidate, _ = make_candidate()
    run = run_extraction_comparison(dataset, baseline, candidate, tmp_path, capture_policy="all")
    protocol = json.loads((run / "protocol.json").read_text())
    manifest = json.loads((run / "manifest.json").read_text())
    rows = [json.loads(line) for line in (run / "baseline.jsonl").read_text().splitlines()]
    assert protocol["capture_policy"] == manifest["capture_policy"] == "all"
    assert protocol["configured_extraction_guards"] == {"baseline": "2.1", "candidate": "2.1"}
    for row in rows:
        captured = row["response_capture"]
        path = run / captured["path"]
        assert captured["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
        assert captured["path"] in {item["path"] for item in manifest["files"]}
        assert "attempts" not in captured


def test_capture_distinguishes_missing_empty_and_truncated_utf8():
    capture = ResponseCapture(enabled=True, max_bytes=5)
    metadata = dict(
        attempt=1,
        response_id=None,
        model=None,
        status="error",
        schema_name=None,
        schema_sha256=None,
    )
    capture.record(None, **metadata)
    capture.record("", **metadata)
    capture.record("λλλλ", **metadata)
    missing, empty, truncated = capture.snapshot()
    assert not missing["available"] and missing["output_text"] is None
    assert empty["available"] and empty["output_text"] == "" and empty["output_bytes"] == 0
    assert truncated["output_text"] == "λλ" and truncated["truncated"]
    assert truncated["output_bytes"] == 8 and truncated["stored_bytes"] == 4
    assert truncated["output_sha256"] == hashlib.sha256("λλλλ".encode()).hexdigest()


def test_preflight_error_cannot_reuse_previous_response():
    candidate, _ = make_candidate("schema")
    provider = candidate.provider
    provider.response_capture.enabled = True
    with pytest.raises(LLMResponseError):
        provider.generate_json("{}", FactExtractionResponse)
    assert provider.response_capture.snapshot()
    # A later local validation failure must not expose earlier response data.
    provider.max_input_bytes = 1
    with pytest.raises(LLMProviderError, match="size limit"):
        provider.generate_json("later input")
    assert provider.response_capture.snapshot() == []
    assert provider.last_record is None


@pytest.mark.parametrize("kind", ["malformed_json", "refused", "incomplete"])
def test_capture_preserves_unusable_provider_responses(kind):
    raw = '{"facts":'
    response = SimpleNamespace(
        status="completed",
        output_text=raw,
        output=[],
        id="failed-response",
        model="fixture",
        usage={},
    )
    if kind == "refused":
        response.output_text = None
        response.output = [{"content": [{"type": "refusal", "refusal": "Fixture refusal."}]}]
    elif kind == "incomplete":
        response.status = "incomplete"
    client = SimpleNamespace(responses=SimpleNamespace(create=lambda **kwargs: response))
    provider = OpenAIProvider(client=client, capture_responses=True)
    with pytest.raises(LLMResponseError):
        provider.generate_json("SECRET PROMPT", FactExtractionResponse)
    record = provider.response_capture.snapshot()[0]
    if kind == "refused":
        assert record["output_text"] is None and not record["available"]
        assert record["refused"] and record["refusal_text"] == "Fixture refusal."
        assert record["refusal_sha256"] == hashlib.sha256(b"Fixture refusal.").hexdigest()
    else:
        assert record["output_text"] == raw
    assert record["incomplete"] == (kind == "incomplete")
    assert "SECRET PROMPT" not in json.dumps(record)
