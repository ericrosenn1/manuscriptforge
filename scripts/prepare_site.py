"""Materialize the static documentation source from the canonical Markdown files."""

from __future__ import annotations

import shutil
from pathlib import Path

GITHUB_BLOB = "https://github.com/ericrosenn1/manuscriptforge/blob/main/"


def rewritten(text: str, *, page: str) -> str:
    """Keep canonical Markdown while making source links useful on the docs site."""
    if page == "index.md":
        return (
            text.replace("](pyproject.toml)", f"]({GITHUB_BLOB}pyproject.toml)")
            .replace("](CITATION.cff)", f"]({GITHUB_BLOB}CITATION.cff)")
            .replace("](scripts/", f"]({GITHUB_BLOB}scripts/")
        )
    return (
        text.replace("](../manuscriptforge/", f"]({GITHUB_BLOB}manuscriptforge/")
        .replace("](../examples/", f"]({GITHUB_BLOB}examples/")
        .replace("](../README.md)", "](../index.md)")
    )


def main() -> None:
    root = Path(__file__).resolve().parent.parent
    destination = root / "build" / "site-source"
    if not destination.resolve().is_relative_to(root):
        raise ValueError("Generated documentation must stay inside the repository")
    if destination.is_symlink():
        raise ValueError("Generated documentation cannot replace a linked directory")
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)
    (destination / "index.md").write_text(
        rewritten((root / "README.md").read_text(encoding="utf-8"), page="index.md"),
        encoding="utf-8",
    )
    for source in (root / "docs").glob("*.md"):
        target = destination / "docs" / source.name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            rewritten(source.read_text(encoding="utf-8"), page=source.name), encoding="utf-8"
        )
    assets = root / "docs" / "assets"
    if assets.exists():
        shutil.copytree(assets, destination / "docs" / "assets")


if __name__ == "__main__":
    main()
