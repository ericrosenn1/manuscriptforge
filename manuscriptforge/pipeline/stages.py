from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class StageResult:
    name: str
    run_dir: Path
    message: str
