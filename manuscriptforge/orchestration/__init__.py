"""Trusted, local, resumable workflow orchestration.

Only explicitly registered Python callables may execute. The engine does not
perform API calls, scan user directories, or modify original source files.
"""

from manuscriptforge.orchestration.engine import Engine, Orchestrator
from manuscriptforge.orchestration.models import (
    ArtifactRecord,
    NodeRecord,
    NodeSpec,
    PipelineSpec,
    RunRecord,
    VerificationReport,
    WorkerContext,
)
from manuscriptforge.orchestration.storage import RunLockedError

__all__ = [
    "ArtifactRecord",
    "Engine",
    "NodeRecord",
    "NodeSpec",
    "Orchestrator",
    "PipelineSpec",
    "RunLockedError",
    "RunRecord",
    "VerificationReport",
    "WorkerContext",
]
