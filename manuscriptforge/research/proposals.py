"""Human-reviewable fact proposals from verified research-run evidence."""

from __future__ import annotations

import os
import uuid
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from manuscriptforge.config import load_project_config
from manuscriptforge.llm.extraction import (
    ExtractionSource,
    FactExtractionRequest,
    FactExtractionResponse,
    extract_fact_proposals,
    validate_fact_proposals,
)
from manuscriptforge.llm.mock_provider import MockLLMProvider
from manuscriptforge.models.project import ProjectConfig
from manuscriptforge.orchestration.storage import contained_file, hash_file
from manuscriptforge.research.pipeline import research_engine
from manuscriptforge.research.providers import load_configured_key, research_provider
from manuscriptforge.research.search import search_run
from manuscriptforge.research.settings import ResearchSettings
from manuscriptforge.utils.io import read_json, write_json


class ProposalRunError(RuntimeError):
    """Proposal failure with a preserved, inspectable diagnostic directory."""

    def __init__(self, output_path: Path, message: str) -> None:
        self.output_path = output_path
        super().__init__(f"{message} Diagnostics: {output_path}")


def _error_message(error: BaseException) -> str:
    message = str(error)
    secret = os.environ.get("OPENAI_API_KEY")
    return message.replace(secret, "[redacted]") if secret else message


def _verified_config(project: Path, run_id: str) -> tuple[ProjectConfig, str]:
    engine = research_engine(project)
    run = engine.status(run_id)
    if run.status != "succeeded":
        raise ValueError("Fact proposals require a completed successful source run")
    node = run.nodes.get("ingest")
    if node is None or node.status not in {"succeeded", "cached"} or not node.artifact_dir:
        raise ValueError("The source run has no completed ingestion artifacts")
    artifact = next((item for item in node.artifacts if item.path == "project_config.json"), None)
    if artifact is None:
        raise ValueError("The source run has no verified project configuration")
    directory = Path(node.artifact_dir)
    if not directory.resolve().is_relative_to(engine.state_dir.resolve()):
        raise ValueError("The source configuration leaves the workflow state directory")
    path = contained_file(directory, artifact.path)
    if hash_file(path) != artifact.sha256:
        raise ValueError("The source configuration hash does not match its run manifest")
    config = ProjectConfig.model_validate(read_json(path))
    if hash_file(path) != artifact.sha256:
        raise ValueError("The source configuration changed while it was being read")
    return config, artifact.sha256


def _finish(destination: Path, provenance: dict[str, Any]) -> None:
    write_json(destination / "provenance.json", provenance)
    files = [
        {"path": path.name, "bytes": path.stat().st_size, "sha256": hash_file(path)}
        for path in sorted(destination.iterdir())
        if path.is_file() and path.name != "manifest.json"
    ]
    write_json(
        destination / "manifest.json",
        {"schema_version": 1, "status": provenance["status"], "files": files},
    )


def propose_facts(
    project: Path,
    run_id: str,
    query: str,
    output: Path,
    settings: ResearchSettings,
    *,
    limit: int = 5,
) -> Path:
    """Never promote proposals into claims or change scientific source inputs."""
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    destination = Path(output) / f"proposals-{stamp}-{uuid.uuid4().hex[:8]}"
    destination.mkdir(parents=True)
    provider = None
    failure: BaseException | None = None
    provenance: dict[str, Any] = {
        "source_run_id": run_id,
        "status": "running",
        "requested_provider": settings.provider,
        "requested_model": settings.model if settings.provider == "openai" else None,
        "provider": None,
        "model": None,
        "provider_invoked": False,
        "usage": None,
        "promotion": "none; candidate facts require explicit human review",
        "scientific_validation": "not_established",
    }
    try:
        retrieval = search_run(project, run_id, query, limit)
        config, digest = _verified_config(project, run_id)
        provenance["source_config_sha256"] = digest
        request = FactExtractionRequest(
            query=query,
            sources=[
                ExtractionSource(
                    source_id=hit["source_id"], text=hit["text"], content_hash=hit["content_hash"]
                )
                for hit in retrieval["hits"]
            ],
        )
        write_json(destination / "request.json", request)
        write_json(destination / "retrieval.json", retrieval)
        if not request.sources:
            result = validate_fact_proposals(
                request,
                FactExtractionResponse(
                    unresolved_questions=[
                        "No sources matched the query; no model request was made."
                    ],
                ),
            )
            provenance["status"] = "no_sources"
        else:
            if settings.provider == "openai":
                if load_project_config(project).privacy.local_only or config.privacy.local_only:
                    raise ValueError(
                        "The source run or current project forbids external model calls"
                    )
                if not load_configured_key(project):
                    raise ValueError("OPENAI_API_KEY is not configured")
            provider = (
                MockLLMProvider(
                    json_responses=[
                        {
                            "facts": [],
                            "needs_human_review": True,
                            "unresolved_questions": [
                                "Offline fixture: no model extraction was performed."
                            ],
                        }
                    ]
                )
                if settings.provider == "mock"
                else research_provider(settings)
            )
            provenance.update(
                provider=settings.provider,
                model=settings.model if settings.provider == "openai" else None,
                provider_invoked=True,
            )
            result = extract_fact_proposals(request, provider)
            provenance["status"] = "completed"
        write_json(destination / "proposals.json", result)
    except BaseException as error:
        failure = error
        provenance["status"] = "failed" if isinstance(error, Exception) else "interrupted"
    provenance["usage"] = getattr(provider, "last_record", None)
    try:
        if failure is not None:
            write_json(
                destination / "failure.json",
                {"error_type": type(failure).__name__, "message": _error_message(failure)},
            )
        _finish(destination, provenance)
    except Exception as artifact_error:
        provenance["status"] = "failed"
        with suppress(Exception):
            write_json(destination / "provenance.json", provenance)
        if failure is None:
            failure = artifact_error
        else:
            failure.add_note(
                f"Some diagnostic files could not be finalized: {type(artifact_error).__name__}"
            )
    if failure is not None:
        if isinstance(failure, Exception):
            raise ProposalRunError(destination, _error_message(failure)) from failure
        failure.add_note(f"Proposal diagnostics: {destination}")
        raise failure
    return destination
