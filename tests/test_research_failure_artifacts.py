"""Rejected model output remains reviewable and cannot become a section."""

import json
from types import SimpleNamespace

import pytest

from manuscriptforge.llm.grounded import (
    GroundedSectionRequest,
    GroundedValidationReport,
    GroundingValidationError,
)
from manuscriptforge.llm.response_capture import ResponseCapture
from manuscriptforge.orchestration import WorkerContext
from manuscriptforge.research import handlers


def test_failed_generation_preserves_request_response_validation_and_usage(monkeypatch, tmp_path):
    project = tmp_path / "snapshot"
    project.mkdir()
    provider = SimpleNamespace(
        response_capture=ResponseCapture(), last_record={"status": "completed", "input_tokens": 10}
    )
    loaded = SimpleNamespace(
        provider=provider,
        config=SimpleNamespace(privacy=SimpleNamespace(local_only=True)),
        claims=[],
    )
    request = GroundedSectionRequest(section_name="Methods")
    monkeypatch.setattr(handlers, "_project_copy", lambda context: project)
    monkeypatch.setattr(handlers, "_loaded_context", lambda *args, **kwargs: loaded)
    monkeypatch.setattr(handlers, "request_from_context", lambda *args, **kwargs: request)

    def rejected(request, selected):
        selected.response_capture.record(
            '{"invalid":"candidate"}',
            attempt=1,
            response_id="fixture-response",
            model="fixture",
            status="completed",
            schema_name="fixture",
            schema_sha256=None,
        )
        raise GroundingValidationError(
            GroundedValidationReport(
                valid=False,
                request_hash="0" * 64,
                issues=["unsupported numeric value"],
            )
        )

    monkeypatch.setattr(handlers, "draft_grounded_section", rejected)
    output = tmp_path / "attempt" / "artifacts"
    output.mkdir(parents=True)
    context = WorkerContext(
        project_dir=project,
        work_dir=output,
        inputs={},
        dependencies={},
        parameters={"section_name": "Methods"},
        node_id="section-01",
        run_id="fixture",
        attempt=1,
    )
    with pytest.raises(GroundingValidationError):
        handlers.section_node(context)
    assert json.loads((output / "request.json").read_bytes())["section_name"] == "Methods"
    assert (
        json.loads((output / "generation_failure.json").read_bytes())["validation"]["valid"]
        is False
    )
    assert (
        json.loads((output / "failed_model_response.json").read_bytes())[0]["output_text"]
        == '{"invalid":"candidate"}'
    )
    assert json.loads((output / "provider_usage.json").read_bytes())["input_tokens"] == 10
    assert not (output / "section.json").exists()
