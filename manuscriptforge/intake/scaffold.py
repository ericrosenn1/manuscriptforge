from __future__ import annotations

from pathlib import Path

SCAFFOLD_FILENAMES = {
    "readme_style_corpus.md",
    "readme_feedback.md",
}
SCAFFOLD_PATTERNS = [
    "todo",
    "replace each placeholder",
    "replace this starter",
    "replace this entire section",
    "use this file",
    "template:",
    "example content: templates contain instructions only",
]


def is_scaffold_filename(path: Path | str) -> bool:
    """Return whether a filename is a known scaffold-only helper."""
    return Path(path).name.lower() in SCAFFOLD_FILENAMES


def text_has_scaffold_markers(text: str) -> bool:
    """Return whether text still looks like instructions or TODO placeholders."""
    lowered = text.lower()
    return any(pattern in lowered for pattern in SCAFFOLD_PATTERNS)


def is_scaffold_file(path: Path) -> bool:
    """Return whether a local file should be treated as scaffold, not real data."""
    if is_scaffold_filename(path):
        return True
    parts = [part.lower() for part in path.parts]
    if path.name.lower() == "readme.md" and "style_corpus" in parts:
        return True
    if not path.exists() or not path.is_file():
        return False
    if path.suffix.lower() not in {".md", ".txt", ".jsonl", ".csv"}:
        return False
    try:
        return text_has_scaffold_markers(path.read_text(encoding="utf-8", errors="ignore"))
    except OSError:
        return False
