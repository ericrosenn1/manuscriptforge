from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from manuscriptforge.llm.base import LLMProvider
from manuscriptforge.models.citation import CitationRecord
from manuscriptforge.models.claim import ScientificClaim
from manuscriptforge.models.project import ProjectConfig
from manuscriptforge.models.style import StyleProfile


@dataclass
class ManuscriptContext:
    project_dir: Path
    run_dir: Path
    config: ProjectConfig
    ingested: dict[str, Any]
    style_profile: StyleProfile
    claims: list[ScientificClaim]
    citations: list[CitationRecord]
    provider: LLMProvider
