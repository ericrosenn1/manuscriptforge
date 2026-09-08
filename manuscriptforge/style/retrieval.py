from __future__ import annotations

from typing import Any

from manuscriptforge.models.style import StyleProfile
from manuscriptforge.style.features import normalize_section_type
from manuscriptforge.utils.ids import stable_id
from manuscriptforge.utils.text import common_ngrams, split_sentences, word_tokens


def _profile_chunks(profile: StyleProfile) -> list[dict[str, Any]]:
    chunks = profile.global_features.get("style_chunks", [])
    if isinstance(chunks, list) and chunks:
        return [chunk for chunk in chunks if isinstance(chunk, dict)]
    fallback: list[dict[str, Any]] = []
    for section, examples in profile.examples_by_section.items():
        for text in examples:
            fallback.append(
                {
                    "chunk_id": stable_id("sty", f"{section}:{text}"),
                    "source_file": "legacy_style_profile",
                    "section_type": normalize_section_type(section),
                    "style_mode": profile.style_mode,
                    "text": text,
                    "token_estimate": len(word_tokens(text)),
                    "sentence_count": len(split_sentences(text)),
                    "content_hash": stable_id("hash", text),
                }
            )
    return fallback


def _topic_text(topic_terms: str | list[str]) -> str:
    if isinstance(topic_terms, str):
        return topic_terms
    return " ".join(topic_terms)


def _sentence_mean(text: str) -> float | None:
    lengths = [len(word_tokens(sentence)) for sentence in split_sentences(text)]
    if not lengths:
        return None
    return sum(lengths) / len(lengths)


def _target_sentence_mean(profile: StyleProfile, section_type: str) -> float | None:
    section_profile = profile.section_features.get(section_type, {})
    mean = section_profile.get("sentence_length_distribution", {}).get("mean")
    if mean is None:
        mean = section_profile.get("sentence_lengths", {}).get("mean")
    if mean is None:
        mean = profile.sentence_length_distribution.get("mean")
    return float(mean) if isinstance(mean, (int, float)) else None


def _style_score(
    profile: StyleProfile,
    chunk: dict[str, Any],
    section_type: str,
    query_tokens: set[str],
    query_phrases: set[str],
) -> tuple[float, list[str]]:
    chunk_text = str(chunk.get("text", ""))
    chunk_tokens = set(word_tokens(chunk_text))
    chunk_phrases = set(common_ngrams(word_tokens(chunk_text), 2, limit=40))
    chunk_section = normalize_section_type(str(chunk.get("section_type", "unknown")))
    reasons: list[str] = []

    section_score = 0.15
    if chunk_section == section_type:
        section_score = 1.0
        reasons.append("section match")
    elif chunk_section == "unknown":
        section_score = 0.35
        reasons.append("fallback unknown-section example")

    overlap = len(query_tokens & chunk_tokens) / max(len(query_tokens | chunk_tokens), 1)
    if overlap:
        reasons.append("keyword overlap")
    phrase_overlap = len(query_phrases & chunk_phrases) / max(len(query_phrases | chunk_phrases), 1)
    if phrase_overlap:
        reasons.append("phrase overlap")

    length_score = 0.5
    target_mean = _target_sentence_mean(profile, section_type)
    chunk_mean = _sentence_mean(chunk_text)
    if target_mean and chunk_mean:
        length_score = max(0.0, 1.0 - abs(target_mean - chunk_mean) / max(target_mean, 1.0))
        if length_score > 0.75:
            reasons.append("sentence-length match")

    total = (section_score * 0.5) + (overlap * 0.25) + (phrase_overlap * 0.15) + (length_score * 0.1)
    if not reasons:
        reasons.append("nearest available style example")
    return round(total, 4), reasons


def get_style_exemplars(
    profile: StyleProfile,
    section_type: str,
    topic_terms: str | list[str],
    max_examples: int = 3,
) -> list[dict[str, Any]]:
    normalized_section = normalize_section_type(section_type)
    query = _topic_text(topic_terms)
    query_tokens = set(word_tokens(query))
    query_phrases = set(common_ngrams(word_tokens(query), 2, limit=40))
    scored = []
    for chunk in _profile_chunks(profile):
        score, reasons = _style_score(profile, chunk, normalized_section, query_tokens, query_phrases)
        scored.append((score, chunk, reasons))
    exemplars = []
    for score, chunk, reasons in sorted(scored, key=lambda item: item[0], reverse=True)[:max_examples]:
        exemplars.append(
            {
                "chunk_id": str(chunk.get("chunk_id", "")),
                "section_type": normalize_section_type(str(chunk.get("section_type", "unknown"))),
                "style_mode": str(chunk.get("style_mode", profile.style_mode)),
                "source_file": str(chunk.get("source_file", "")),
                "text": str(chunk.get("text", "")),
                "score": score,
                "why_selected": "; ".join(reasons),
                "style_example_label": "style exemplar only; not factual evidence",
                "content_hash": str(chunk.get("content_hash", "")),
            }
        )
    return exemplars


def retrieve_style_examples(profile: StyleProfile, section: str, query: str, limit: int = 3) -> list[str]:
    return [item["text"] for item in get_style_exemplars(profile, section, query, max_examples=limit)]
