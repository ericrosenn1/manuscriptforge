"""Fixed trusted DAG: project data never supplies executable handlers."""

from __future__ import annotations

import hashlib
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypedDict

from manuscriptforge.config import load_project_config
from manuscriptforge.orchestration import NodeSpec, Orchestrator, PipelineSpec
from manuscriptforge.orchestration.models import RunRecord, relative_path, safe_id
from manuscriptforge.orchestration.storage import atomic_json, exclusive_lock, hash_file, is_link
from manuscriptforge.research.providers import load_configured_key
from manuscriptforge.research.settings import ResearchSettings
from manuscriptforge.utils.io import read_json

INPUT_DIRECTORIES = (
    "inputs",
    "style_corpus",
    "planning/intake",
    "feedback",
)
OPTIONAL_FILES = ("style_memory.json", "style_preferences.jsonl")


class _NodeExecution(TypedDict):
    version: str
    timeout_seconds: float
    max_attempts: int
    cacheable: bool


def selected_inputs(project_dir: Path) -> list[str]:
    """Select relevant project inputs only, without personal-folder discovery."""
    root = project_dir.resolve()
    selected: list[str] = []
    configured_cache = relative_path(load_project_config(root).sources.metadata_cache_dir)
    key_file = os.environ.get("MANUSCRIPTFORGE_KEY_FILE")
    credential = Path(key_file).resolve() if key_file else None
    for relative in (*INPUT_DIRECTORIES, configured_cache):
        folder = root / relative
        if not folder.exists():
            continue
        if is_link(folder) or not folder.resolve().is_relative_to(root):
            raise ValueError(f"Linked input directory is not supported: {relative}")
        if not any(folder.iterdir()):
            selected.append(relative)
        pending = [folder]
        while pending:
            current = pending.pop()
            for path in sorted(current.iterdir()):
                if (
                    path.name == ".env"
                    or path.name.startswith(".env.")
                    or path.name
                    in {
                        "node_modules",
                        "__pycache__",
                    }
                ):
                    continue
                if credential is not None and path.resolve() == credential:
                    continue
                if is_link(path):
                    raise ValueError(f"Linked input is not supported: {path.relative_to(root)}")
                if path.is_dir():
                    if not any(path.iterdir()):
                        selected.append(path.relative_to(root).as_posix())
                    pending.append(path)
                elif path.is_file():
                    selected.append(path.relative_to(root).as_posix())
    return sorted(set(selected))


def implementation_version() -> str:
    """Invalidate nodes when package code or bundled configuration changes."""
    root = Path(__file__).parents[1]
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.suffix in {".py", ".yaml", ".json"}:
            digest.update(path.relative_to(root).as_posix().encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()


def build_research_plan(project_dir: Path, settings: ResearchSettings) -> PipelineSpec:
    from manuscriptforge.research.search import INDEX_ARTIFACT

    root = Path(project_dir).resolve()
    config = load_project_config(root)
    if settings.provider == "openai" and config.privacy.local_only:
        raise ValueError(
            "privacy.local_only forbids external model calls; change the selected project configuration explicitly first"
        )
    selected = selected_inputs(root)
    version = implementation_version()
    parameters = settings.model_dump(
        mode="json", exclude={"max_workers", "node_timeout_seconds", "max_attempts", "use_cache"}
    )
    shared = _NodeExecution(
        version=version,
        timeout_seconds=settings.node_timeout_seconds,
        max_attempts=settings.max_attempts,
        cacheable=settings.use_cache,
    )
    nodes = [
        NodeSpec(
            id="validate",
            handler="validate",
            input_paths=["project.yaml", *selected],
            optional_input_paths=list(OPTIONAL_FILES),
            parameters={},
            expected_outputs=["validation.json"],
            **shared,
        ),
        NodeSpec(
            id="ingest",
            handler="ingest",
            depends_on=["validate"],
            input_paths=["project.yaml", *selected],
            optional_input_paths=list(OPTIONAL_FILES),
            parameters={},
            expected_outputs=[
                "intermediate/ingested_project.json",
                "citation_registry.json",
                INDEX_ARTIFACT,
            ],
            **shared,
        ),
        NodeSpec(
            id="profile",
            handler="profile",
            depends_on=["ingest"],
            input_paths=["project.yaml", *selected],
            optional_input_paths=list(OPTIONAL_FILES),
            parameters={},
            expected_outputs=["style_profile.json"],
            **shared,
        ),
        NodeSpec(
            id="claims",
            handler="claims",
            depends_on=["ingest"],
            input_paths=["project.yaml"],
            parameters={},
            expected_outputs=["claim_registry.json"],
            **shared,
        ),
    ]
    sections = [name for name in config.manuscript.include_sections if name != "References"]
    if not sections:
        raise ValueError("At least one manuscript section is required")
    if len(set(sections)) != len(sections):
        raise ValueError("Manuscript sections must not be duplicated")
    section_ids: list[str] = []
    for index, section in enumerate(sections):
        identity = f"section-{index:02d}"
        section_ids.append(identity)
        nodes.append(
            NodeSpec(
                id=identity,
                handler="section",
                depends_on=["ingest", "profile", "claims"],
                input_paths=["project.yaml"],
                optional_input_paths=list(OPTIONAL_FILES),
                parameters=parameters | {"section_name": section},
                expected_outputs=["section.json", "candidate.json", "grounding.json"],
                **shared,
            )
        )
    nodes.extend(
        [
            NodeSpec(
                id="assemble",
                handler="assemble",
                depends_on=["ingest", "claims", "profile", *section_ids],
                input_paths=["project.yaml"],
                parameters=parameters | {"section_nodes": section_ids},
                expected_outputs=[
                    "manuscript.json",
                    "manuscript.md",
                    "manuscript.docx",
                    "manuscript.tex",
                ],
                **shared,
            ),
            NodeSpec(
                id="audit",
                handler="audit",
                depends_on=["assemble", "ingest", "claims", "profile"],
                input_paths=["project.yaml"],
                parameters=parameters,
                expected_outputs=["audit_findings.json", "source_map.json", "qualification.json"],
                **shared,
            ),
            NodeSpec(
                id="export",
                handler="export",
                depends_on=["assemble", "ingest", "claims", "profile", "audit"],
                parameters=parameters,
                expected_outputs=["research_bundle.zip", "package/manifest.json"],
                **shared,
            ),
        ]
    )
    return PipelineSpec(id="research-manuscript-v1", nodes=nodes, max_workers=settings.max_workers)


def research_engine(project_dir: Path, state_dir: Path | None = None) -> Orchestrator:
    from manuscriptforge.research.handlers import HANDLERS

    return Orchestrator(Path(project_dir), HANDLERS, state_dir=state_dir)


def _read_request(requests: Path, original: Path, active: Path, run_id: str) -> dict[str, Any]:
    if not original.is_file() or is_link(original):
        raise ValueError(
            "The original research request is missing or linked; settings cannot be inferred"
        )
    selected = original
    if os.path.lexists(active):
        if is_link(active):
            raise ValueError("The active request pointer must be a regular file")
        pointer = read_json(active)
        if not isinstance(pointer, dict):
            raise ValueError("The active request pointer must be a JSON object")
        if pointer.get("original_sha256") != hash_file(original):
            raise ValueError("The original research request changed after it was recorded")
        selected = requests / relative_path(pointer["request_path"])
        if selected != original and selected.parent != requests / f"{run_id}.revisions":
            raise ValueError("The request pointer does not belong to this run")
        if not selected.resolve().is_relative_to(requests.resolve()):
            raise ValueError("Request revision path leaves the request directory")
        current = selected
        while current != requests:
            if current.exists() and is_link(current):
                raise ValueError("Linked request revision paths are not allowed")
            current = current.parent
        if hash_file(selected) != pointer.get("request_sha256"):
            raise ValueError("The recorded research request revision has changed")
    request = read_json(selected)
    if not isinstance(request, dict) or request.get("run_id") != run_id:
        raise ValueError("Research request identity does not match the selected run")
    return request


def run_research(
    project_dir: Path,
    settings: ResearchSettings | None = None,
    *,
    run_id: str | None = None,
    resume: bool = False,
    state_dir: Path | None = None,
) -> RunRecord:
    engine = research_engine(project_dir, state_dir)
    if resume and not run_id:
        raise ValueError("Resume requires an explicit run ID")
    run_id = run_id or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:12]
    safe_id(run_id)
    requests = engine.state_dir / "requests"
    if requests.exists() and is_link(requests):
        raise ValueError("Research requests must stay inside an ordinary state directory")
    requests.mkdir(parents=True, exist_ok=True)
    original = requests / f"{run_id}.json"
    active = requests / f"{run_id}.active.json"
    # This lock covers admission through execution. Two callers cannot record
    # conflicting envelopes for one run before its scheduler lock is acquired.
    with exclusive_lock(requests / f".{run_id}.lock"):
        if resume:
            previous = _read_request(requests, original, active, run_id)
            if settings is None:
                settings = ResearchSettings.model_validate(previous["settings"])
        elif os.path.lexists(original) or os.path.lexists(active):
            raise FileExistsError(f"Run request already exists: {run_id}")
        settings = settings or ResearchSettings()
        if settings.provider == "openai" and not load_configured_key(Path(project_dir)):
            raise ValueError(
                "OPENAI_API_KEY is not configured; credentials are never accepted through CLI arguments"
            )
        plan = build_research_plan(project_dir, settings)
        request_file = original
        if resume:
            revisions = requests / f"{run_id}.revisions"
            if revisions.exists() and is_link(revisions):
                raise ValueError("Request revisions must stay in an ordinary directory")
            revisions.mkdir(exist_ok=True)
            request_file = (
                revisions / f"{datetime.now(UTC):%Y%m%dT%H%M%S%f}-{uuid.uuid4().hex[:12]}.json"
            )
        atomic_json(
            request_file,
            {
                "schema_version": 1,
                "run_id": run_id,
                "request_kind": "resume" if resume else "initial",
                "created_at": datetime.now(UTC).isoformat(),
                "settings": settings.model_dump(mode="json"),
                "plan": plan.model_dump(mode="json"),
            },
        )
        atomic_json(
            active,
            {
                "schema_version": 1,
                "request_path": request_file.relative_to(requests).as_posix(),
                "request_sha256": hash_file(request_file),
                "original_sha256": hash_file(original),
            },
        )
        return engine.run(plan, run_id=run_id, resume=resume)
