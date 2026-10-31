"""Materialize the static documentation source from the canonical Markdown files."""

from __future__ import annotations

import shutil
import stat
from pathlib import Path

GITHUB_BLOB = "https://github.com/ericrosenn1/manuscriptforge/blob/main/"


def reject_linked_path(path: Path) -> None:
    """Reject symlinks and Windows junctions before resolving any ancestor."""
    absolute = path.absolute()
    for current in (absolute, *absolute.parents):
        try:
            info = current.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & getattr(
            stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400
        ):
            raise ValueError("Documentation paths cannot contain links or reparse points")


def reject_linked_tree(root: Path) -> None:
    """Preflight a tree without descending into linked directories."""
    reject_linked_path(root)
    if not root.exists():
        return
    pending = [root]
    while pending:
        current = pending.pop()
        reject_linked_path(current)
        if current.is_dir():
            for child in current.iterdir():
                reject_linked_path(child)
                pending.append(child)
        elif not current.is_file():
            raise ValueError("Documentation paths must be regular files or directories")


def rewritten(text: str, *, page: str) -> str:
    """Keep canonical Markdown while making source links useful on the docs site."""
    if page == "index.md":
        return (
            text.replace("](pyproject.toml)", f"]({GITHUB_BLOB}pyproject.toml)")
            .replace("](CITATION.cff)", f"]({GITHUB_BLOB}CITATION.cff)")
            .replace("](scripts/", f"]({GITHUB_BLOB}scripts/")
            .replace("](benchmarks/", f"]({GITHUB_BLOB}benchmarks/")
        )
    return (
        text.replace("](../manuscriptforge/", f"]({GITHUB_BLOB}manuscriptforge/")
        .replace("](../examples/", f"]({GITHUB_BLOB}examples/")
        .replace("](../README.md)", "](../index.md)")
    )


def main() -> None:
    root = Path(__file__).absolute().parent.parent
    reject_linked_path(root)
    root = root.resolve(strict=True)
    destination = root / "build" / "site-source"
    reject_linked_tree(destination)
    if not destination.resolve().is_relative_to(root):
        raise ValueError("Generated documentation must stay inside the repository")
    reject_linked_path(root / "README.md")
    reject_linked_path(root / "docs")
    sources = sorted((root / "docs").glob("*.md"))
    for source in sources:
        reject_linked_path(source)
    assets = root / "docs" / "assets"
    reject_linked_tree(assets)
    if destination.exists():
        if not destination.is_dir():
            raise ValueError("Generated documentation destination must be a directory")
        shutil.rmtree(destination)
    destination.mkdir(parents=True)
    (destination / "index.md").write_text(
        rewritten((root / "README.md").read_text(encoding="utf-8"), page="index.md"),
        encoding="utf-8",
    )
    for source in sources:
        target = destination / "docs" / source.name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            rewritten(source.read_text(encoding="utf-8"), page=source.name), encoding="utf-8"
        )
    if assets.exists():
        shutil.copytree(assets, destination / "docs" / "assets")


if __name__ == "__main__":
    main()
