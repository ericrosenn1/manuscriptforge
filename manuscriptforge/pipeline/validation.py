from __future__ import annotations

from pathlib import Path

from manuscriptforge.config import ValidationResult, validate_project


def validate_or_raise(project_dir: Path) -> ValidationResult:
    result = validate_project(project_dir)
    if not result.ok:
        raise ValueError("; ".join(result.errors))
    return result
