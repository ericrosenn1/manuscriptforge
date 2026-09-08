"""Read-only configuration and templates shipped with installed packages."""

from importlib.resources import files
from importlib.resources.abc import Traversable
from typing import Any

import yaml


def config_resource(name: str) -> Traversable:
    """Locate a bundled resource independently of the checkout directory."""
    return files("manuscriptforge").joinpath("data", name)


def read_config_resource(name: str) -> dict[str, Any]:
    """Load a bundled YAML mapping."""
    data = yaml.safe_load(config_resource(name).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Bundled configuration must be a mapping: {name}")
    return data
