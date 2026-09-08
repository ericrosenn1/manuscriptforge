from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

from manuscriptforge.utils.dates import run_timestamp


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    ensure_dir(path.parent)
    path.write_text(text, encoding="utf-8", newline="\n")


def read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        loaded = yaml.safe_load(handle) or {}
    if not isinstance(loaded, dict):
        raise ValueError(f"Expected YAML mapping in {path}")
    return loaded


def write_yaml(path: Path, data: dict[str, Any]) -> None:
    ensure_dir(path.parent)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        yaml.safe_dump(data, handle, sort_keys=False)


def model_to_data(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, list):
        return [model_to_data(item) for item in value]
    if isinstance(value, dict):
        return {key: model_to_data(item) for key, item in value.items()}
    return value


def write_json(path: Path, data: Any) -> None:
    ensure_dir(path.parent)
    path.write_text(json.dumps(model_to_data(data), indent=2, sort_keys=True), encoding="utf-8")


def write_jsonl(path: Path, rows: list[Any]) -> None:
    ensure_dir(path.parent)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(model_to_data(row), sort_keys=True) + "\n")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def project_outputs_dir(project_dir: Path) -> Path:
    return ensure_dir(project_dir / "outputs")


def create_run_dir(project_dir: Path) -> Path:
    runs_dir = ensure_dir(project_outputs_dir(project_dir) / "runs")
    base = run_timestamp()
    run_dir = runs_dir / base
    suffix = 1
    while run_dir.exists():
        suffix += 1
        run_dir = runs_dir / f"{base}_{suffix}"
    ensure_dir(run_dir)
    ensure_dir(run_dir / "intermediate")
    ensure_dir(run_dir / "logs")
    return run_dir


def latest_run_dir(project_dir: Path) -> Path | None:
    runs_dir = project_dir / "outputs" / "runs"
    if not runs_dir.exists():
        return None
    candidates = sorted([p for p in runs_dir.iterdir() if p.is_dir()])
    return candidates[-1] if candidates else None


def update_latest_copy(project_dir: Path, run_dir: Path) -> Path:
    outputs_dir = project_outputs_dir(project_dir).resolve()
    latest = outputs_dir / "latest"
    if latest.exists():
        resolved = latest.resolve()
        if resolved.parent != outputs_dir or resolved.name != "latest":
            raise RuntimeError(f"Refusing to remove unexpected latest path: {latest}")
        shutil.rmtree(latest)
    shutil.copytree(run_dir, latest)
    return latest
