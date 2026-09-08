from __future__ import annotations

import re

from manuscriptforge.utils.hashing import sha256_text


def stable_id(prefix: str, text: str, length: int = 10) -> str:
    return f"{prefix}_{sha256_text(text)[:length]}"


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return slug or "untitled"
