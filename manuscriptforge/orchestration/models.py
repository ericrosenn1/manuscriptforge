"""Typed contracts for the local, content-addressed workflow engine."""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator

NodeStatus = Literal["pending", "running", "succeeded", "cached", "failed", "cancelled", "blocked"]
RunStatus = Literal["pending", "running", "succeeded", "failed", "cancelled"]


def safe_id(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", value):
        raise ValueError("IDs must contain 1–80 letters, numbers, underscores, or hyphens")
    return value


def relative_path(value: str) -> str:
    """Require portable, relative paths; never silently repair traversal."""
    if not value or "\\" in value or ":" in value or "\x00" in value:
        raise ValueError(f"Expected a portable relative path: {value!r}")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in value.split("/")):
        raise ValueError(f"Unsafe relative path: {value!r}")
    return path.as_posix()


class NodeSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
    id: str
    handler: str
    version: str = "1"
    depends_on: list[str] = Field(default_factory=list)
    parameters: dict[str, JsonValue] = Field(default_factory=dict)
    input_paths: list[str] = Field(default_factory=list)
    optional_input_paths: list[str] = Field(default_factory=list)
    expected_outputs: list[str] = Field(default_factory=list)
    timeout_seconds: float = Field(default=300, gt=0, le=86400)
    max_attempts: int = Field(default=1, ge=1, le=3)
    cacheable: bool = True

    _id = field_validator("id", "handler")(safe_id)

    @field_validator("version")
    @classmethod
    def validate_version(cls, value: str) -> str:
        if not value or len(value) > 200:
            raise ValueError("A short, explicit handler version is required")
        return value

    @field_validator("input_paths", "optional_input_paths", "expected_outputs")
    @classmethod
    def validate_paths(cls, values: list[str]) -> list[str]:
        cleaned = [relative_path(value) for value in values]
        if len(set(cleaned)) != len(cleaned):
            raise ValueError("A path is declared more than once")
        return cleaned

    @field_validator("depends_on")
    @classmethod
    def validate_dependencies(cls, values: list[str]) -> list[str]:
        if len(set(values)) != len(values):
            raise ValueError("Duplicate dependencies are not allowed")
        return [safe_id(value) for value in values]


class PipelineSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str
    nodes: list[NodeSpec] = Field(min_length=1, max_length=1000)
    max_workers: int = Field(default=2, ge=1, le=32)
    _id = field_validator("id")(safe_id)

    @model_validator(mode="after")
    def validate_graph(self) -> PipelineSpec:
        by_id = {node.id: node for node in self.nodes}
        if len(by_id) != len(self.nodes):
            raise ValueError("Node IDs must be unique")
        for node in self.nodes:
            for dependency in node.depends_on:
                if dependency not in by_id:
                    raise ValueError(f"Node {node.id} has missing dependency {dependency}")
        visited: set[str] = set()
        pending = set(by_id)
        while pending:
            ready = {key for key in pending if set(by_id[key].depends_on) <= visited}
            if not ready:
                raise ValueError("Pipeline graph contains a dependency cycle")
            visited.update(ready)
            pending.difference_update(ready)
        return self

    def ordered_nodes(self) -> list[NodeSpec]:
        result: list[NodeSpec] = []
        seen: set[str] = set()
        remaining = {node.id: node for node in self.nodes}
        while remaining:
            for key in sorted(remaining):
                node = remaining[key]
                if set(node.depends_on) <= seen:
                    result.append(node)
                    seen.add(key)
                    del remaining[key]
                    break
        return result


class ArtifactRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    path: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    bytes: int = Field(ge=0)
    _path = field_validator("path")(relative_path)


class NodeRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    status: NodeStatus
    fingerprint: str = ""
    attempts: int = 0
    artifact_dir: str | None = None
    provenance_path: str | None = None
    artifacts: list[ArtifactRecord] = Field(default_factory=list)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)
    error: str | None = None
    reused_from: str | None = None
    completed_at: str | None = None


class RunRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: int = 1
    run_id: str
    pipeline_id: str
    status: RunStatus
    run_dir: str
    created_at: str
    updated_at: str
    snapshot_fingerprint: str
    spec_fingerprint: str
    nodes: dict[str, NodeRecord] = Field(default_factory=dict)


class VerificationReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: str
    valid: bool
    checked_nodes: int
    checked_artifacts: int
    errors: list[str] = Field(default_factory=list)


class WorkerContext(BaseModel):
    """Paths private to one attempt except read-only inputs and dependencies.

    Handlers write only under work_dir. Adapters for helpers that alter project
    metadata must first copy inputs into a private work_dir subdirectory.
    """

    model_config = ConfigDict(extra="forbid")
    project_dir: Path
    work_dir: Path
    inputs: dict[str, Path]
    dependencies: dict[str, dict[str, Path]]
    parameters: dict[str, JsonValue]
    node_id: str
    run_id: str
    attempt: int

    def output_path(self, value: str) -> Path:
        path = self.work_dir / relative_path(value)
        if not path.resolve().is_relative_to(self.work_dir.resolve()):
            raise ValueError("Output path leaves the attempt directory")
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def read_input(self, value: str) -> Path:
        value = relative_path(value)
        if value not in self.inputs:
            raise KeyError(f"Input was not declared by this node: {value}")
        return self.inputs[value]

    def dependency_path(self, node_id: str, value: str) -> Path:
        return self.dependencies[node_id][relative_path(value)]

    @property
    def scratch_dir(self) -> Path:
        """Private attempt scratch, excluded from artifact/cache inventories."""
        path = self.work_dir.parent / "scratch"
        path.mkdir(parents=True, exist_ok=True)
        return path


class WorkerJob(BaseModel):
    """Internal protocol generated from a trusted Python handler registry."""

    model_config = ConfigDict(extra="forbid")
    schema_version: int = 1
    attempt_id: str
    fingerprint: str
    callable_module: str
    callable_name: str
    context: WorkerContext
    expected_outputs: list[str]
    snapshot_manifest: list[ArtifactRecord]
    receipt_path: Path
    database_path: Path
    handler_sha256: str
    implementation_hashes: dict[str, str] = Field(default_factory=dict)


class WorkerReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    schema_version: int = 1
    attempt_id: str
    fingerprint: str
    node_id: str
    run_id: str
    status: Literal["succeeded", "failed"]
    artifacts: list[ArtifactRecord] = Field(default_factory=list)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)
    error: str | None = None
    started_at: str
    completed_at: str


HandlerResult = dict[str, Any] | None
