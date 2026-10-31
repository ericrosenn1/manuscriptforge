from __future__ import annotations

import math
import re

import numpy as np

from manuscriptforge.utils.text import (
    HEDGING_WORDS,
    find_transition_counts,
    split_paragraphs,
    split_sentences,
    word_tokens,
)

FEATURE_NAMES = (
    "log_words",
    "mean_sentence_words",
    "sentence_length_sd",
    "mean_word_length",
    "mean_paragraph_words",
    "hedges_per_1000",
    "first_person_per_1000",
    "transitions_per_1000",
    "passive_pattern_rate",
    "semicolons_per_1000",
    "parentheses_per_1000",
    "type_token_ratio",
)


def feature_vector(text: str) -> np.ndarray:
    words = word_tokens(text)
    count = max(1, len(words))
    sentences = split_sentences(text)
    lengths = [len(word_tokens(sentence)) for sentence in sentences]
    paragraphs = split_paragraphs(text)
    values = [
        math.log1p(len(words)),
        float(np.mean(lengths)) if lengths else 0.0,
        float(np.std(lengths)) if lengths else 0.0,
        float(np.mean([len(word) for word in words])) if words else 0.0,
        len(words) / max(1, len(paragraphs)),
        1000 * sum(word in HEDGING_WORDS for word in words) / count,
        1000 * sum(word in {"i", "we", "our", "us", "my"} for word in words) / count,
        1000 * sum(find_transition_counts(text).values()) / count,
        len(re.findall(r"\b(?:was|were|is|are|been)\s+\w+(?:ed|en)\b", text, re.I))
        / max(1, len(sentences)),
        1000 * text.count(";") / count,
        1000 * text.count("(") / count,
        len(set(words)) / count,
    ]
    return np.asarray(values, dtype=float)
