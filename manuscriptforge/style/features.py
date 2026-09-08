from __future__ import annotations

import re
from collections import Counter

from manuscriptforge.utils.text import (
    ACADEMIC_VERBS,
    HEDGING_WORDS,
    common_ngrams,
    count_terms,
    find_transition_counts,
    split_paragraphs,
    split_sentences,
    word_tokens,
)

SECTION_TYPES = [
    "title",
    "abstract",
    "introduction",
    "methods",
    "results",
    "discussion",
    "results_and_discussion",
    "limitations",
    "conclusion",
    "figure_legends",
    "references",
    "response_to_reviewers",
    "cover_letter",
    "unknown",
]

PROFILE_SECTION_TYPES = [
    "abstract",
    "introduction",
    "methods",
    "results",
    "discussion",
    "limitations",
    "response_to_reviewers",
]

SECTION_ALIASES = {
    "title": {"title"},
    "abstract": {"abstract", "summary", "structured abstract"},
    "introduction": {"introduction", "background", "rationale"},
    "methods": {"methods", "materials and methods", "methodology", "study design"},
    "results": {"results", "findings"},
    "discussion": {"discussion", "interpretation"},
    "results_and_discussion": {"results and discussion", "results & discussion"},
    "limitations": {"limitations", "limitations and future work", "study limitations"},
    "conclusion": {"conclusion", "conclusions", "concluding remarks"},
    "figure_legends": {"figure legends", "figure legend", "legends"},
    "references": {"references", "reference list", "bibliography", "literature cited", "works cited"},
    "response_to_reviewers": {
        "response to reviewers",
        "response-to-reviewers",
        "reviewer response",
        "rebuttal",
        "rebuttal letter",
        "response letter",
    },
    "cover_letter": {"cover letter", "coverletter"},
}

STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "in",
    "is",
    "of",
    "on",
    "or",
    "that",
    "the",
    "this",
    "to",
    "we",
    "with",
}


def normalize_section_type(value: str | None) -> str:
    if not value:
        return "unknown"
    normalized = re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()
    underscored = normalized.replace(" ", "_")
    for section_type, aliases in SECTION_ALIASES.items():
        if normalized in aliases or underscored == section_type:
            return section_type
    if normalized in {"materials methods", "material methods"}:
        return "methods"
    if normalized.startswith("supplementary ") and normalized.replace("supplementary ", "") in {
        "methods",
        "materials and methods",
        "results",
        "discussion",
        "figure legends",
    }:
        return normalize_section_type(normalized.replace("supplementary ", ""))
    if underscored in SECTION_TYPES:
        return underscored
    return "unknown"


def classify_section_heading(line: str) -> str | None:
    stripped = line.strip()
    if not stripped:
        return None
    stripped = re.sub(r"^#{1,6}\s*", "", stripped)
    stripped = re.sub(r"^\d+(?:\.\d+)*\.?\s+", "", stripped)
    stripped = stripped.strip(" :-\t")
    if len(stripped.split()) > 8:
        return None
    section_type = normalize_section_type(stripped)
    return None if section_type == "unknown" else section_type


def infer_section_type(source_file: str, text: str) -> str:
    filename = source_file.lower().replace("-", " ").replace("_", " ")
    for section_type, aliases in SECTION_ALIASES.items():
        if any(alias in filename for alias in aliases):
            return section_type
    lower = text.lower()
    if re.search(r"\bdear (editor|dr\.|professor|colleagues)\b", lower):
        return "cover_letter"
    if "reviewer" in lower and ("response" in lower or "comment" in lower):
        return "response_to_reviewers"
    if "figure " in lower and "legend" in lower:
        return "figure_legends"
    if any(term in lower for term in ["limitation", "future work", "prospective validation"]):
        return "limitations"
    if any(term in lower for term in ["we used", "software", "version", "statistical", "preprocessing"]):
        return "methods"
    if any(term in lower for term in ["observed", "fold change", "p-value", "significant"]):
        return "results"
    if any(term in lower for term in ["together", "interpret", "warrant", "causality"]):
        return "discussion"
    return "unknown"


def is_probable_title(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    if classify_section_heading(stripped):
        return False
    if len(stripped) > 180 or stripped.endswith((".", "?", "!")):
        return False
    return 3 <= len(word_tokens(stripped)) <= 24


def punctuation_counts(text: str) -> dict[str, int]:
    return {symbol: text.count(symbol) for symbol in [",", ";", ":", "(", ")", "-"] if text.count(symbol)}


def limitation_phrases(sentences: list[str]) -> list[str]:
    phrases = []
    for sentence in sentences:
        lower = sentence.lower()
        if any(term in lower for term in ["limitation", "limited", "cannot", "should not", "need for", "future"]):
            phrases.append(sentence[:220])
    return phrases[:12]


def passive_voice_ratio(sentences: list[str]) -> float:
    if not sentences:
        return 0.0
    passive_count = sum(
        1
        for sentence in sentences
        if re.search(r"\b(?:was|were|is|are|been|be)\s+\w+(?:ed|en)\b", sentence, re.I)
    )
    return round(passive_count / len(sentences), 3)


def first_person_count(tokens: list[str]) -> int:
    return sum(1 for token in tokens if token in {"i", "we", "our", "us", "my"})


def citation_patterns(text: str) -> dict[str, int | str]:
    parenthetical = len(re.findall(r"\([A-Z][A-Za-z]+ et al\.,? \d{4}\)", text))
    numeric = len(re.findall(r"\[\d+(?:,\s*\d+)*\]", text))
    author_year_inline = len(re.findall(r"\b[A-Z][A-Za-z]+ et al\.\s+\(\d{4}\)", text))
    likely = "unknown"
    if numeric or parenthetical or author_year_inline:
        likely = "numeric" if numeric >= parenthetical + author_year_inline else "author-year"
    return {
        "parenthetical_author_year_count": parenthetical,
        "numeric_bracket_count": numeric,
        "inline_author_year_count": author_year_inline,
        "likely_style": likely,
    }


def noun_phrase_patterns(tokens: list[str], limit: int = 12) -> list[str]:
    candidates = []
    for index in range(len(tokens) - 1):
        first, second = tokens[index], tokens[index + 1]
        if first not in STOPWORDS and second not in STOPWORDS and first not in ACADEMIC_VERBS:
            candidates.append(f"{first} {second}")
    counts = Counter(candidates)
    return [phrase for phrase, count in counts.most_common(limit) if count > 1]


def detected_style_features(text: str) -> dict[str, object]:
    sentences = split_sentences(text)
    paragraphs = split_paragraphs(text)
    tokens = word_tokens(text)
    return {
        "hedging_terms": count_terms(tokens, HEDGING_WORDS),
        "transition_phrases": find_transition_counts(text),
        "common_verbs": count_terms(tokens, ACADEMIC_VERBS),
        "noun_phrase_patterns": noun_phrase_patterns(tokens),
        "passive_voice_estimate": passive_voice_ratio(sentences),
        "first_person_usage": first_person_count(tokens),
        "citation_integration_patterns": citation_patterns(text),
        "limitation_phrasing": limitation_phrases(sentences),
        "opening_sentence_patterns": [sentence[:160] for sentence in sentences[:3]],
        "closing_sentence_patterns": [sentence[:160] for sentence in sentences[-3:]],
        "common_bigrams": common_ngrams(tokens, 2, limit=8),
        "paragraph_count": len(paragraphs),
    }
