from __future__ import annotations

import re
from collections import Counter
from statistics import mean, median

HEDGING_WORDS = {
    "may",
    "might",
    "could",
    "suggest",
    "suggests",
    "suggested",
    "consistent",
    "possible",
    "potential",
    "appears",
    "associated",
}

ACADEMIC_VERBS = {
    "show",
    "shows",
    "showed",
    "suggest",
    "suggests",
    "indicate",
    "indicates",
    "support",
    "supports",
    "evaluate",
    "evaluates",
    "compare",
    "compares",
    "identify",
    "identifies",
    "observe",
    "observed",
}

TRANSITION_PHRASES = [
    "in contrast",
    "however",
    "therefore",
    "together",
    "in addition",
    "consistent with",
    "by contrast",
    "as a result",
    "notably",
]


def normalize_space(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def split_sentences(text: str) -> list[str]:
    cleaned = normalize_space(text)
    if not cleaned:
        return []
    pieces = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9])", cleaned)
    return [piece.strip() for piece in pieces if piece.strip()]


def split_paragraphs(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"\n\s*\n", text.strip()) if p.strip()]


def word_tokens(text: str) -> list[str]:
    return re.findall(r"[A-Za-z][A-Za-z\-']*", text.lower())


def summarize_numbers(values: list[float]) -> dict[str, float | int | None]:
    if not values:
        return {"count": 0, "mean": None, "median": None, "min": None, "max": None}
    return {
        "count": len(values),
        "mean": round(mean(values), 3),
        "median": round(median(values), 3),
        "min": round(min(values), 3),
        "max": round(max(values), 3),
    }


def distribution(values: list[int]) -> dict[str, float | int | None]:
    if not values:
        return {"count": 0, "mean": None, "median": None, "min": None, "max": None}
    return {
        "count": len(values),
        "mean": round(mean(values), 3),
        "median": round(median(values), 3),
        "min": min(values),
        "max": max(values),
    }


def common_ngrams(tokens: list[str], n: int, limit: int = 12) -> list[str]:
    if len(tokens) < n:
        return []
    grams = Counter(" ".join(tokens[i : i + n]) for i in range(len(tokens) - n + 1))
    return [gram for gram, count in grams.most_common(limit) if count > 1 or n == 1]


def count_terms(tokens: list[str], terms: set[str], limit: int = 20) -> dict[str, int]:
    counts = Counter(token for token in tokens if token in terms)
    return dict(counts.most_common(limit))


def find_transition_counts(text: str) -> dict[str, int]:
    lower = text.lower()
    return {phrase: lower.count(phrase) for phrase in TRANSITION_PHRASES if lower.count(phrase)}
