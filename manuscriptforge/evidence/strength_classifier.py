from __future__ import annotations

from manuscriptforge.models.claim import SupportStrength

SOFT_ALLOWED = [
    "suggests",
    "is consistent with",
    "may indicate",
    "raises the possibility",
    "was associated with",
]

DIRECT_ALLOWED = ["reports", "shows", "was", "were", "is consistent with", "was associated with"]

FORBIDDEN_STRONG = [
    "proves",
    "prove",
    "proved",
    "causes",
    "caused",
    "causal",
    "drives",
    "predicts",
    "diagnoses",
    "demonstrates causality",
    "establishes",
    "confirms",
    "determines",
    "validates clinically",
    "cures",
    "guarantees",
]

UNSUPPORTED_LANGUAGE = {
    "prove",
    "proves",
    "proved",
    "causes",
    "caused",
    "causal",
    "drives",
    "drove",
    "predicts",
    "predicted",
    "diagnoses",
    "diagnostic",
    "validates clinically",
    "clinically validated",
    "cures",
    "guarantees",
}


def allowed_language_for_support(support: SupportStrength) -> list[str]:
    if support == "direct":
        return DIRECT_ALLOWED
    if support in {"indirect", "weak", "needs_review"}:
        return SOFT_ALLOWED
    return ["may be considered", "requires support", "needs review"]


def forbidden_language_for_support(support: SupportStrength) -> list[str]:
    if support == "direct":
        return ["proves", "guarantees", "cures", "validates clinically"]
    return FORBIDDEN_STRONG


def classify_sentence_support(source_kind: str, sentence: str) -> SupportStrength:
    lower = sentence.lower()
    if source_kind == "interpretation_note" and any(term in lower for term in UNSUPPORTED_LANGUAGE):
        return "unsupported"
    if source_kind == "table":
        return "direct"
    if source_kind == "method_note":
        return "direct"
    if source_kind == "figure":
        return "indirect"
    if "need" in lower or "limitation" in lower or "avoid" in lower:
        return "weak"
    if source_kind == "interpretation_note":
        return "weak"
    return "needs_review"
