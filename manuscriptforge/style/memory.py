from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from manuscriptforge.models.style import StyleProfile
from manuscriptforge.utils.dates import utc_iso
from manuscriptforge.utils.io import read_json, write_json


def _read_preferences(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            loaded = json.loads(line)
        except json.JSONDecodeError:
            records.append({"question": {"question_id": f"malformed_line_{line_number}"}, "answer": None})
            continue
        if isinstance(loaded, dict):
            records.append(loaded)
    return records


def load_style_memory(project_dir: Path) -> dict[str, Any]:
    path = project_dir / "style_memory.json"
    if not path.exists():
        return {}
    loaded = read_json(path)
    return loaded if isinstance(loaded, dict) else {}


def _answer_text(answer: Any) -> str:
    if answer is None:
        return ""
    if isinstance(answer, str):
        return answer
    if isinstance(answer, list):
        return " ".join(str(item) for item in answer)
    if isinstance(answer, dict):
        return " ".join(f"{key}:{value}" for key, value in answer.items())
    return str(answer)


def build_style_memory(project_dir: Path, profile: StyleProfile | None = None) -> dict[str, Any]:
    records = _read_preferences(project_dir / "style_preferences.jsonl")
    answered = [record for record in records if _answer_text(record.get("answer")).strip()]
    hedge_counter: Counter[str] = Counter()
    transition_counter: Counter[str] = Counter()
    disliked_phrases: list[str] = []
    accepted_rewrites: list[str] = []
    causal_language = "cautious"
    citation_style = profile.citation_style.get("likely_style", "unknown") if profile else "unknown"
    limitation_phrasing: list[str] = []

    for record in answered:
        question = record.get("question", {})
        if not isinstance(question, dict):
            question = {}
        target = str(question.get("model_feature_target", ""))
        qtype = str(question.get("question_type", ""))
        answer = _answer_text(record.get("answer")).strip()
        answer_lower = answer.lower()
        if target == "hedging" or "hedge" in qtype:
            hedge_counter.update([answer_lower])
        if target == "transition_style" or "transition" in qtype:
            transition_counter.update([answer])
        if target == "causal_language":
            causal_language = "stronger" if any(term in answer_lower for term in ["strong", "direct"]) else "cautious"
        if "least acceptable" in qtype or target == "causal_language":
            options = question.get("options", [])
            if isinstance(options, list):
                disliked_phrases.extend(str(option) for option in options if str(option).lower() in answer_lower)
        if "rewrite" in qtype:
            accepted_rewrites.append(answer)
        if target == "citation_style":
            citation_style = answer
        if target == "limitation_style":
            limitation_phrasing.append(answer)

    section_profiles = profile.section_features if profile else {}
    sentence_density = (
        profile.sentence_length_distribution.get("mean") if profile is not None else None
    )
    paragraph_density = (
        profile.paragraph_length_distribution.get("mean") if profile is not None else None
    )
    memory = {
        "style_mode": profile.style_mode if profile else "unknown",
        "updated_utc": utc_iso(),
        "preference_records": len(records),
        "answered_preferences": len(answered),
        "preferred_hedge_level": hedge_counter.most_common(1)[0][0] if hedge_counter else "corpus_default",
        "preferred_causal_language": causal_language,
        "preferred_transition_patterns": [
            item for item, _count in transition_counter.most_common(6)
        ]
        or list((profile.transition_profile.get("transition_counts", {}) if profile else {}).keys())[:6],
        "preferred_sentence_density": sentence_density,
        "preferred_paragraph_density": paragraph_density,
        "preferred_citation_integration": citation_style,
        "preferred_limitation_phrasing": limitation_phrasing
        or list((profile.global_features.get("limitation_phrases", []) if profile else [])[:6]),
        "disliked_phrases": sorted(set(disliked_phrases + (profile.avoided_phrases if profile else []))),
        "accepted_rewrites": accepted_rewrites[-20:],
        "section_profile_counts": {
            section: values.get("chunk_count", 0) for section, values in section_profiles.items()
        },
    }
    return memory


def update_style_memory(project_dir: Path, profile: StyleProfile | None = None) -> dict[str, Any]:
    memory = build_style_memory(project_dir, profile)
    write_json(project_dir / "style_memory.json", memory)
    return memory
