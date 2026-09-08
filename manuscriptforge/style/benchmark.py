from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from manuscriptforge.audit.style_audit import GENERIC_LLM_PHRASES, HYPE_TERMS
from manuscriptforge.export.excel_reporter import write_workbook
from manuscriptforge.models.manuscript import Manuscript
from manuscriptforge.models.project import ProjectConfig
from manuscriptforge.models.style import (
    StyleBenchmarkMetric,
    StyleBenchmarkReport,
    StyleEvalResult,
    StyleProfile,
)
from manuscriptforge.style.features import normalize_section_type, passive_voice_ratio
from manuscriptforge.utils.dates import utc_iso
from manuscriptforge.utils.hashing import sha256_file, sha256_text
from manuscriptforge.utils.ids import stable_id
from manuscriptforge.utils.io import ensure_dir, read_json, write_json, write_text
from manuscriptforge.utils.text import (
    HEDGING_WORDS,
    find_transition_counts,
    split_paragraphs,
    split_sentences,
    word_tokens,
)

BENCHMARK_SECTION_TYPES = [
    "abstract",
    "introduction",
    "methods",
    "results",
    "discussion",
    "limitations",
]

DEFAULT_WEIGHTS = {
    "section_similarity": 0.35,
    "style_memory_alignment": 0.20,
    "generic_phrase_avoidance": 0.10,
    "causal_hedging_alignment": 0.15,
    "citation_integration": 0.10,
    "traceability": 0.10,
}


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _average(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 4) if values else None


def _numeric_similarity(observed: float | None, target: float | None) -> float | None:
    if observed is None or target is None:
        return None
    scale = max(abs(observed), abs(target), 1.0)
    return round(_clamp(1.0 - abs(observed - target) / scale), 4)


def _token_similarity(left: str, right: str) -> float:
    left_tokens = set(word_tokens(left))
    right_tokens = set(word_tokens(right))
    if not left_tokens and not right_tokens:
        return 1.0
    return round(len(left_tokens & right_tokens) / max(len(left_tokens | right_tokens), 1), 4)


def _sentence_mean(text: str) -> float | None:
    lengths = [len(word_tokens(sentence)) for sentence in split_sentences(text)]
    return round(sum(lengths) / len(lengths), 4) if lengths else None


def _paragraph_mean(text: str) -> float | None:
    lengths = [len(word_tokens(paragraph)) for paragraph in split_paragraphs(text)]
    return round(sum(lengths) / len(lengths), 4) if lengths else None


def _terms_per_1000(text: str, terms: set[str]) -> float:
    tokens = word_tokens(text)
    count = sum(1 for token in tokens if token in terms)
    return round(1000 * count / max(len(tokens), 1), 4)


def _first_person_per_1000(text: str) -> float:
    return _terms_per_1000(text, {"i", "we", "our", "us", "my"})


def _phrase_counts(text: str, phrases: list[str]) -> dict[str, int]:
    lower = text.lower()
    return {phrase: lower.count(phrase.lower()) for phrase in phrases if lower.count(phrase.lower())}


def _citation_style(text: str) -> str:
    numeric = len(re.findall(r"\[\d+(?:,\s*\d+)*\]", text))
    citation_id = len(re.findall(r"\[(?:cit|ref|src)_[A-Za-z0-9_;,\s-]+\]", text))
    author_year = len(re.findall(r"\([A-Z][A-Za-z]+ et al\.,? \d{4}\)", text))
    if citation_id:
        return "id-bracket"
    if numeric:
        return "numeric"
    if author_year:
        return "author-year"
    return "unknown"


def _metric(
    name: str,
    *,
    section_type: str | None,
    score: float | None,
    status: str = "scored",
    explanation: str,
    evidence: dict[str, Any] | None = None,
    suggested_fix: str | None = None,
) -> StyleBenchmarkMetric:
    normalized = round(_clamp(score), 4) if score is not None else None
    return StyleBenchmarkMetric(
        metric_id=stable_id("sbm", f"{name}:{section_type}:{explanation}"),
        metric_name=name,
        section_type=section_type,
        score=normalized,
        normalized_score=normalized,
        status=status,  # type: ignore[arg-type]
        explanation=explanation,
        evidence=evidence or {},
        suggested_fix=suggested_fix,
    )


def _read_json_if_exists(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return read_json(path)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        loaded = json.loads(line)
        if isinstance(loaded, dict):
            rows.append(loaded)
    return rows


def _manuscript_run_dirs(project_dir: Path) -> list[Path]:
    runs_dir = project_dir / "outputs" / "runs"
    if not runs_dir.exists():
        return []
    return sorted(
        [
            path
            for path in runs_dir.iterdir()
            if path.is_dir() and ((path / "manuscript.md").exists() or (path / "manuscript.json").exists())
        ]
    )


def resolve_benchmark_run(project_dir: Path, run_ref: str | None = None) -> Path:
    project_dir = Path(project_dir)
    if run_ref:
        if run_ref == "latest":
            latest = project_dir / "outputs" / "latest"
            if latest.exists():
                return latest
        candidate = Path(run_ref)
        if candidate.exists():
            return candidate
        candidate = project_dir / "outputs" / "runs" / run_ref
        if candidate.exists():
            return candidate
        raise FileNotFoundError(f"Could not resolve run '{run_ref}'.")
    runs = _manuscript_run_dirs(project_dir)
    if not runs:
        raise FileNotFoundError("No manuscript run found. Run `manuscriptforge draft PROJECT_DIR` first.")
    return runs[-1]


def resolve_compare_run(project_dir: Path, primary_run: Path, compare_ref: str | None) -> Path | None:
    if not compare_ref:
        return None
    if compare_ref != "previous":
        return resolve_benchmark_run(project_dir, compare_ref)
    runs = _manuscript_run_dirs(project_dir)
    resolved_primary = primary_run.resolve()
    for index, run_dir in enumerate(runs):
        if run_dir.resolve() == resolved_primary:
            if index == 0:
                raise FileNotFoundError("No previous manuscript run is available for comparison.")
            return runs[index - 1]
    if len(runs) >= 2:
        return runs[-2]
    raise FileNotFoundError("No previous manuscript run is available for comparison.")


def _latest_file(project_dir: Path, filename: str, preferred_run: Path) -> Path | None:
    preferred = preferred_run / filename
    if preferred.exists():
        return preferred
    for run_dir in reversed(sorted((project_dir / "outputs" / "runs").glob("*"))):
        candidate = run_dir / filename
        if candidate.exists():
            return candidate
    return None


def _load_manuscript(run_dir: Path) -> tuple[str, dict[str, str]]:
    manuscript_path = run_dir / "manuscript.json"
    markdown_path = run_dir / "manuscript.md"
    if manuscript_path.exists():
        manuscript = Manuscript.model_validate(read_json(manuscript_path))
        sections = {"abstract": manuscript.abstract}
        for section in manuscript.sections:
            section_type = normalize_section_type(section.section_type or section.section_name)
            sections[section_type] = (sections.get(section_type, "") + "\n\n" + section.content).strip()
        all_text = "\n\n".join([manuscript.title, manuscript.abstract] + [section.content for section in manuscript.sections])
        return all_text, sections
    text = markdown_path.read_text(encoding="utf-8") if markdown_path.exists() else ""
    parsed_sections: dict[str, str] = {}
    current = "unknown"
    buffer: list[str] = []
    for line in text.splitlines():
        heading = re.match(r"^#{1,3}\s+(.+?)\s*$", line)
        if heading:
            if buffer:
                parsed_sections[current] = "\n".join(buffer).strip()
                buffer = []
            current = normalize_section_type(heading.group(1))
            continue
        buffer.append(line)
    if buffer:
        parsed_sections[current] = "\n".join(buffer).strip()
    return text, parsed_sections


def _load_style_memory(project_dir: Path, run_dir: Path) -> dict[str, Any]:
    for path in [run_dir / "style_memory.json", project_dir / "style_memory.json"]:
        if path.exists():
            loaded = read_json(path)
            return loaded if isinstance(loaded, dict) else {}
    return {}


def _input_hashes(project_dir: Path, run_dir: Path) -> dict[str, str]:
    paths: list[Path] = [
        run_dir / "manuscript.md",
        run_dir / "manuscript.json",
        run_dir / "style_profile.json",
        run_dir / "style_chunks.jsonl",
        run_dir / "style_memory.json",
        run_dir / "source_map.json",
        run_dir / "style_report.json",
        project_dir / "style_memory.json",
        project_dir / "project.yaml",
    ]
    for filename in [
        "style_eval_pairs.jsonl",
        "style_eval_rewrite_prompts.jsonl",
        "style_eval_rank_questions.jsonl",
    ]:
        latest = _latest_file(project_dir, filename, run_dir)
        if latest is not None:
            paths.append(latest)
    hashes: dict[str, str] = {}
    for path in paths:
        if path.exists() and path.is_file():
            try:
                key = path.relative_to(project_dir).as_posix()
            except ValueError:
                key = str(path)
            hashes[key] = sha256_file(path)
    return hashes


def _normalized_weights(config: ProjectConfig, warnings: list[str]) -> dict[str, float]:
    weights = dict(DEFAULT_WEIGHTS)
    weights.update(config.style_benchmark.weights)
    total = sum(value for value in weights.values() if value > 0)
    if total <= 0:
        warnings.append("Style benchmark weights were invalid; default weights were used.")
        return dict(DEFAULT_WEIGHTS)
    if abs(total - 1.0) > 0.001:
        warnings.append(f"Style benchmark weights summed to {round(total, 4)} and were normalized.")
    return {key: round(max(value, 0.0) / total, 6) for key, value in weights.items()}


def _section_metric(
    section_type: str,
    section_text: str,
    profile: StyleProfile,
) -> StyleBenchmarkMetric:
    section_profile = profile.section_features.get(section_type, {})
    if not section_profile.get("chunk_count"):
        return _metric(
            f"{section_type}_style_score",
            section_type=section_type,
            score=None,
            status="insufficient_data",
            explanation=f"No reliable {section_type} style profile was available.",
            suggested_fix=f"Add prior {section_type} writing samples or answer style questions for this section.",
        )
    if not section_text.strip():
        return _metric(
            f"{section_type}_style_score",
            section_type=section_type,
            score=0.0,
            status="warning",
            explanation=f"No generated {section_type} text was available.",
            suggested_fix=f"Draft or supply the {section_type} section before benchmarking style.",
        )

    components: list[float] = []
    evidence: dict[str, Any] = {}
    sentence_score = _numeric_similarity(
        _sentence_mean(section_text),
        section_profile.get("sentence_length_distribution", {}).get("mean"),
    )
    if sentence_score is not None:
        components.append(sentence_score)
        evidence["sentence_length_similarity"] = sentence_score
    paragraph_score = _numeric_similarity(
        _paragraph_mean(section_text),
        section_profile.get("paragraph_length_distribution", {}).get("mean"),
    )
    if paragraph_score is not None:
        components.append(paragraph_score)
        evidence["paragraph_length_similarity"] = paragraph_score
    target_hedges = section_profile.get("hedging_terms_per_1000_words")
    if isinstance(target_hedges, (int, float)):
        hedge_score = _numeric_similarity(_terms_per_1000(section_text, HEDGING_WORDS), float(target_hedges))
        if hedge_score is not None:
            components.append(hedge_score)
            evidence["hedge_similarity"] = hedge_score
    target_transitions = set(section_profile.get("transition_phrases", {}).keys())
    if target_transitions:
        observed_transitions = set(find_transition_counts(section_text).keys())
        transition_score = len(target_transitions & observed_transitions) / len(target_transitions)
        components.append(round(transition_score, 4))
        evidence["transition_similarity"] = round(transition_score, 4)
    passive_target = section_profile.get("passive_voice_estimate")
    if isinstance(passive_target, (int, float)):
        passive_score = _numeric_similarity(passive_voice_ratio(split_sentences(section_text)), float(passive_target))
        if passive_score is not None:
            components.append(passive_score)
            evidence["passive_voice_similarity"] = passive_score
    generic_matches = _phrase_counts(section_text, GENERIC_LLM_PHRASES)
    generic_score = _clamp(1.0 - sum(generic_matches.values()) / 4.0)
    components.append(round(generic_score, 4))
    evidence["generic_phrase_matches"] = generic_matches

    score = _average(components)
    if score is None:
        return _metric(
            f"{section_type}_style_score",
            section_type=section_type,
            score=None,
            status="insufficient_data",
            explanation=f"The {section_type} profile lacked usable numeric style features.",
        )
    status = "warning" if score < 0.6 else "scored"
    return _metric(
        f"{section_type}_style_score",
        section_type=section_type,
        score=score,
        status=status,
        explanation=f"{section_type.title()} style similarity was {round(score * 100, 1)}%.",
        evidence=evidence,
        suggested_fix="Use section-specific exemplars and answer calibration questions." if score < 0.75 else None,
    )


def _global_metrics(
    full_text: str,
    sections: dict[str, str],
    profile: StyleProfile,
    style_memory: dict[str, Any],
    source_map: dict[str, Any],
) -> list[StyleBenchmarkMetric]:
    metrics: list[StyleBenchmarkMetric] = []
    word_count = profile.global_features.get("word_count", 0)
    if word_count:
        metrics.append(
            _metric(
                "sentence_length_similarity",
                section_type=None,
                score=_numeric_similarity(_sentence_mean(full_text), profile.sentence_length_distribution.get("mean")),
                explanation="Generated sentence pacing compared with the global style profile.",
                evidence={
                    "observed_mean": _sentence_mean(full_text),
                    "target_mean": profile.sentence_length_distribution.get("mean"),
                },
            )
        )
        metrics.append(
            _metric(
                "paragraph_length_similarity",
                section_type=None,
                score=_numeric_similarity(_paragraph_mean(full_text), profile.paragraph_length_distribution.get("mean")),
                explanation="Generated paragraph pacing compared with the global style profile.",
                evidence={
                    "observed_mean": _paragraph_mean(full_text),
                    "target_mean": profile.paragraph_length_distribution.get("mean"),
                },
            )
        )
    else:
        metrics.extend(
            [
                _metric(
                    "sentence_length_similarity",
                    section_type=None,
                    score=None,
                    status="insufficient_data",
                    explanation="No style corpus sentence statistics were available.",
                ),
                _metric(
                    "paragraph_length_similarity",
                    section_type=None,
                    score=None,
                    status="insufficient_data",
                    explanation="No style corpus paragraph statistics were available.",
                ),
            ]
        )

    target_hedges = profile.hedging_profile.get("hedging_terms_per_1000_words")
    hedge_score = (
        _numeric_similarity(_terms_per_1000(full_text, HEDGING_WORDS), float(target_hedges))
        if isinstance(target_hedges, (int, float)) and word_count
        else None
    )
    metrics.append(
        _metric(
            "hedge_usage_similarity",
            section_type=None,
            score=hedge_score,
            status="scored" if hedge_score is not None else "insufficient_data",
            explanation="Hedge usage compared with the style corpus.",
            evidence={"observed_per_1000": _terms_per_1000(full_text, HEDGING_WORDS), "target_per_1000": target_hedges},
            suggested_fix="Add cautious qualifiers where evidence is indirect." if hedge_score is not None and hedge_score < 0.65 else None,
        )
    )

    causal_matches = _phrase_counts(full_text, HYPE_TERMS)
    causal_penalty = sum(causal_matches.values()) / max(len(split_sentences(full_text)), 1)
    causal_score = _clamp(1.0 - causal_penalty * 2.0)
    if style_memory.get("preferred_causal_language") == "cautious" and causal_matches:
        causal_score = _clamp(causal_score - 0.1)
    metrics.append(
        _metric(
            "causal_language_restraint_score",
            section_type=None,
            score=round(causal_score, 4),
            status="warning" if causal_matches else "scored",
            explanation="Strong causal or hype wording was penalized.",
            evidence={"strong_language_matches": causal_matches},
            suggested_fix="Replace strong causal wording with claim-supported, hedged language." if causal_matches else None,
        )
    )

    target_transitions = set(profile.transition_profile.get("transition_counts", {}).keys())
    if target_transitions:
        observed_transitions = set(find_transition_counts(full_text).keys())
        transition_score = len(target_transitions & observed_transitions) / len(target_transitions)
        metrics.append(
            _metric(
                "transition_phrase_similarity",
                section_type=None,
                score=round(transition_score, 4),
                explanation="Generated transitions compared with transitions found in the style corpus.",
                evidence={"target_transitions": sorted(target_transitions), "observed_transitions": sorted(observed_transitions)},
            )
        )
    else:
        metrics.append(
            _metric(
                "transition_phrase_similarity",
                section_type=None,
                score=None,
                status="insufficient_data",
                explanation="No transition phrases were detected in the style corpus.",
            )
        )

    target_citation_style = profile.citation_style.get("likely_style", "unknown")
    observed_citation_style = _citation_style(full_text)
    citation_score: float | None
    if target_citation_style == "unknown":
        citation_score = None
    elif observed_citation_style == target_citation_style:
        citation_score = 1.0
    elif observed_citation_style == "id-bracket" and target_citation_style in {"numeric", "author-year"}:
        citation_score = 0.7
    elif observed_citation_style == "unknown":
        citation_score = 0.4
    else:
        citation_score = 0.25
    metrics.append(
        _metric(
            "citation_integration_similarity",
            section_type=None,
            score=citation_score,
            status="scored" if citation_score is not None else "insufficient_data",
            explanation="Citation integration pattern compared with the style profile.",
            evidence={"observed": observed_citation_style, "target": target_citation_style},
            suggested_fix="Align citation placement with the target journal and style corpus." if citation_score is not None and citation_score < 0.75 else None,
        )
    )

    generic_matches = _phrase_counts(full_text, GENERIC_LLM_PHRASES)
    generic_count = sum(generic_matches.values())
    metrics.append(
        _metric(
            "generic_phrase_penalty",
            section_type=None,
            score=round(_clamp(1.0 - generic_count / 4.0), 4),
            status="warning" if generic_count else "scored",
            explanation="Generic LLM-like phrase avoidance score.",
            evidence={"matches": generic_matches, "count": generic_count},
            suggested_fix="Replace generic phrases with concrete manuscript-specific wording." if generic_count else None,
        )
    )

    passive_target = profile.global_features.get("passive_voice_estimate", profile.global_features.get("passive_voice_ratio"))
    passive_score = (
        _numeric_similarity(passive_voice_ratio(split_sentences(full_text)), float(passive_target))
        if isinstance(passive_target, (int, float)) and word_count
        else None
    )
    metrics.append(
        _metric(
            "passive_voice_similarity",
            section_type=None,
            score=passive_score,
            status="scored" if passive_score is not None else "insufficient_data",
            explanation="Passive voice rate compared with the global style profile.",
            evidence={"observed_ratio": passive_voice_ratio(split_sentences(full_text)), "target_ratio": passive_target},
        )
    )

    first_target_count = profile.global_features.get("first_person_usage", profile.global_features.get("first_person_count", 0))
    first_target = 1000 * float(first_target_count or 0) / max(float(word_count or 0), 1.0)
    first_score = _numeric_similarity(_first_person_per_1000(full_text), first_target) if word_count else None
    metrics.append(
        _metric(
            "first_person_usage_similarity",
            section_type=None,
            score=first_score,
            status="scored" if first_score is not None else "insufficient_data",
            explanation="First-person usage compared with the global style profile.",
            evidence={"observed_per_1000": _first_person_per_1000(full_text), "target_per_1000": round(first_target, 4)},
        )
    )

    target_sections = [
        section for section, values in profile.section_features.items() if values.get("chunk_count", 0) > 0
    ]
    if target_sections:
        covered = [section for section in target_sections if sections.get(normalize_section_type(section), "").strip()]
        coverage_score = len(covered) / len(target_sections)
        metrics.append(
            _metric(
                "section_coverage_score",
                section_type=None,
                score=round(coverage_score, 4),
                explanation="Generated manuscript sections compared with section coverage in the style corpus.",
                evidence={"target_sections": target_sections, "covered_sections": covered},
            )
        )
    else:
        metrics.append(
            _metric(
                "section_coverage_score",
                section_type=None,
                score=None,
                status="insufficient_data",
                explanation="No section-labeled style chunks were available for coverage scoring.",
            )
        )

    metrics.append(_style_memory_metric(full_text, style_memory))
    metrics.extend(_traceability_metrics(source_map))
    return metrics


def _style_memory_metric(full_text: str, style_memory: dict[str, Any]) -> StyleBenchmarkMetric:
    if not style_memory:
        return _metric(
            "style_memory_alignment_score",
            section_type=None,
            score=None,
            status="insufficient_data",
            explanation="No style_memory.json was available.",
            suggested_fix="Run ask-style and answer preference questions before benchmarking memory alignment.",
        )
    components: list[float] = []
    lower = full_text.lower()
    disliked = [str(item) for item in style_memory.get("disliked_phrases", []) if str(item).strip()]
    disliked_matches = [phrase for phrase in disliked if phrase.lower() in lower]
    components.append(_clamp(1.0 - len(disliked_matches) / max(len(disliked), 1)))
    hedge = str(style_memory.get("preferred_hedge_level", "corpus_default"))
    if hedge and hedge != "corpus_default":
        components.append(1.0 if hedge.lower() in lower else 0.35)
    transitions = [str(item) for item in style_memory.get("preferred_transition_patterns", []) if str(item).strip()]
    if transitions:
        components.append(1.0 if any(item.lower() in lower for item in transitions) else 0.5)
    citation_pref = str(style_memory.get("preferred_citation_integration", "unknown")).lower()
    if citation_pref and citation_pref != "unknown":
        observed = _citation_style(full_text)
        components.append(1.0 if citation_pref in observed or observed in citation_pref else 0.6)
    score = _average(components) if components else 0.75
    return _metric(
        "style_memory_alignment_score",
        section_type=None,
        score=score,
        status="warning" if disliked_matches else "scored",
        explanation="Generated text compared with stored style preferences.",
        evidence={
            "answered_preferences": style_memory.get("answered_preferences", 0),
            "disliked_phrase_matches": disliked_matches,
            "preferred_hedge_level": hedge,
            "preferred_transition_patterns": transitions,
        },
        suggested_fix="Remove disliked phrases or answer more style questions." if disliked_matches else None,
    )


def _traceability_metrics(source_map: dict[str, Any]) -> list[StyleBenchmarkMetric]:
    if not source_map:
        return [
            _metric(
                "style_exemplar_usage_score",
                section_type=None,
                score=0.0,
                status="failed",
                explanation="source_map.json was missing, so style exemplar usage could not be verified.",
                suggested_fix="Run draft or audit to regenerate source_map.json.",
            ),
            _metric(
                "source_map_traceability_completeness",
                section_type=None,
                score=0.0,
                status="failed",
                explanation="source_map.json was missing.",
                suggested_fix="Run draft or audit to regenerate source_map.json.",
            ),
        ]
    sections = source_map.get("sections", [])
    section_count = len(sections)
    sections_with_style = sum(1 for section in sections if section.get("style_chunk_ids"))
    exemplar_score = sections_with_style / max(section_count, 1)
    required_fields = ["claim_ids", "citation_ids", "style_mode", "style_chunk_ids", "unresolved_flags", "generated_by"]
    present = 0
    total = 0
    for section in sections:
        for paragraph in section.get("paragraphs", []):
            for field in required_fields:
                total += 1
                if field in paragraph:
                    present += 1
    completeness = present / max(total, 1)
    return [
        _metric(
            "style_exemplar_usage_score",
            section_type=None,
            score=round(exemplar_score, 4),
            explanation="Share of sections with style chunk IDs recorded in source_map.json.",
            evidence={"sections": section_count, "sections_with_style_chunk_ids": sections_with_style},
            suggested_fix="Ensure section drafting retrieves and records style exemplars." if exemplar_score < 0.75 else None,
        ),
        _metric(
            "source_map_traceability_completeness",
            section_type=None,
            score=round(completeness, 4),
            explanation="Completeness of expected paragraph-level traceability fields in source_map.json.",
            evidence={"fields_present": present, "fields_expected": total},
            suggested_fix="Regenerate source_map.json with claim, citation, style, unresolved, and provider fields." if completeness < 1.0 else None,
        ),
    ]


def _eval_files(project_dir: Path, run_dir: Path) -> dict[str, Path]:
    files: dict[str, Path] = {}
    for key, filename in {
        "pairs": "style_eval_pairs.jsonl",
        "rewrites": "style_eval_rewrite_prompts.jsonl",
        "ranks": "style_eval_rank_questions.jsonl",
    }.items():
        latest = _latest_file(project_dir, filename, run_dir)
        if latest is not None:
            files[key] = latest
    return files


def _score_style_eval(project_dir: Path, run_dir: Path, sections: dict[str, str], full_text: str) -> list[StyleEvalResult]:
    eval_results: list[StyleEvalResult] = []
    files = _eval_files(project_dir, run_dir)
    for row in _read_jsonl(files.get("pairs", Path("__missing__"))):
        section_type = normalize_section_type(str(row.get("section_type", "unknown")))
        target_text = sections.get(section_type, full_text)
        positive_score = _token_similarity(target_text, str(row.get("positive", "")))
        negative_score = _token_similarity(target_text, str(row.get("negative", "")))
        score = _clamp(0.5 + positive_score - negative_score)
        eval_results.append(
            StyleEvalResult(
                eval_id=str(row.get("example_id", stable_id("seval", json.dumps(row, sort_keys=True)))),
                eval_type=str(row.get("task", "pair_similarity")),
                section_type=section_type,
                score=round(score, 4),
                passed=positive_score >= negative_score,
                explanation="Generated text was compared with positive user-style text and deterministic distractor text.",
                feature_target="sentence_similarity",
                source_chunk_id=row.get("source_chunk_id"),
            )
        )
    for row in _read_jsonl(files.get("rewrites", Path("__missing__"))):
        section_type = normalize_section_type(str(row.get("section_type", "unknown")))
        target_text = sections.get(section_type, full_text)
        user_score = _token_similarity(target_text, str(row.get("target_user_paragraph", "")))
        neutral_score = _token_similarity(target_text, str(row.get("neutral_paragraph", "")))
        score = _clamp(0.5 + user_score - neutral_score)
        eval_results.append(
            StyleEvalResult(
                eval_id=str(row.get("example_id", stable_id("seval", json.dumps(row, sort_keys=True)))),
                eval_type=str(row.get("task", "rewrite_similarity")),
                section_type=section_type,
                score=round(score, 4),
                passed=user_score >= neutral_score,
                explanation="Generated section text was compared with user paragraph and neutral rewrite.",
                feature_target="paragraph_similarity",
                source_chunk_id=row.get("source_chunk_id"),
            )
        )
    for row in _read_jsonl(files.get("ranks", Path("__missing__"))):
        section_type = normalize_section_type(str(row.get("section_type", "unknown")))
        target_text = sections.get(section_type, full_text)
        if row.get("task") == "section_classification":
            score = 1.0 if target_text.strip() and _token_similarity(target_text, str(row.get("text", ""))) > 0 else 0.0
            passed = bool(target_text.strip())
            feature_target = "section_classification"
        else:
            options = [str(option) for option in row.get("options", [])]
            preferred = int(row.get("preferred_option_index", 0))
            option_scores = [_token_similarity(target_text, option) for option in options]
            best_index = max(range(len(option_scores)), key=option_scores.__getitem__) if option_scores else -1
            score = option_scores[preferred] if 0 <= preferred < len(option_scores) else 0.0
            passed = best_index == preferred
            feature_target = "ranked_style_preference"
        eval_results.append(
            StyleEvalResult(
                eval_id=str(row.get("example_id", stable_id("seval", json.dumps(row, sort_keys=True)))),
                eval_type=str(row.get("task", "rank_question")),
                section_type=section_type,
                score=round(score, 4),
                passed=passed,
                explanation="Style eval rank/classification item was scored with deterministic token overlap.",
                feature_target=feature_target,
                source_chunk_id=row.get("source_chunk_id"),
            )
        )
    return eval_results


def _metric_by_name(metrics: list[StyleBenchmarkMetric], name: str) -> StyleBenchmarkMetric | None:
    for metric in metrics:
        if metric.metric_name == name:
            return metric
    return None


def _category_scores(metrics: list[StyleBenchmarkMetric], warnings: list[str]) -> dict[str, float | None]:
    section_values = [
        metric.normalized_score
        for metric in metrics
        if metric.section_type and metric.metric_name.endswith("_style_score") and metric.normalized_score is not None
    ]
    hedge = _metric_by_name(metrics, "hedge_usage_similarity")
    causal = _metric_by_name(metrics, "causal_language_restraint_score")
    traceability = _metric_by_name(metrics, "source_map_traceability_completeness")
    exemplar = _metric_by_name(metrics, "style_exemplar_usage_score")
    categories = {
        "section_similarity": _average([float(value) for value in section_values]),
        "style_memory_alignment": (_metric_by_name(metrics, "style_memory_alignment_score") or StyleBenchmarkMetric(
            metric_id="missing",
            metric_name="missing",
            explanation="missing",
        )).normalized_score,
        "generic_phrase_avoidance": (_metric_by_name(metrics, "generic_phrase_penalty") or StyleBenchmarkMetric(
            metric_id="missing",
            metric_name="missing",
            explanation="missing",
        )).normalized_score,
        "causal_hedging_alignment": _average(
            [
                value
                for value in [
                    hedge.normalized_score if hedge else None,
                    causal.normalized_score if causal else None,
                ]
                if value is not None
            ]
        ),
        "citation_integration": (_metric_by_name(metrics, "citation_integration_similarity") or StyleBenchmarkMetric(
            metric_id="missing",
            metric_name="missing",
            explanation="missing",
        )).normalized_score,
        "traceability": _average(
            [
                value
                for value in [
                    traceability.normalized_score if traceability else None,
                    exemplar.normalized_score if exemplar else None,
                ]
                if value is not None
            ]
        ),
    }
    for key, value in categories.items():
        if value is None:
            warnings.append(f"Benchmark category '{key}' had insufficient data and was excluded from the overall score.")
    return categories


def _overall_score(categories: dict[str, float | None], weights: dict[str, float]) -> float:
    numerator = 0.0
    denominator = 0.0
    for key, weight in weights.items():
        value = categories.get(key)
        if value is None:
            continue
        numerator += value * weight
        denominator += weight
    if denominator <= 0:
        return 0.0
    return round(100 * numerator / denominator, 2)


def _section_scores(metrics: list[StyleBenchmarkMetric]) -> dict[str, Any]:
    scores: dict[str, Any] = {}
    for metric in metrics:
        if metric.section_type and metric.metric_name.endswith("_style_score"):
            scores[metric.section_type] = {
                "score": round((metric.normalized_score or 0.0) * 100, 2) if metric.normalized_score is not None else None,
                "status": metric.status,
                "explanation": metric.explanation,
            }
    return scores


def _audit_warning_count(run_dir: Path) -> int:
    findings = _read_json_if_exists(run_dir / "audit_findings.json", [])
    if not isinstance(findings, list):
        return 0
    return sum(
        1
        for finding in findings
        if isinstance(finding, dict)
        and finding.get("category") in {"style", "overclaiming"}
        and finding.get("severity") in {"warning", "serious"}
    )


def _comparison(primary: StyleBenchmarkReport, baseline: StyleBenchmarkReport, primary_run: Path, baseline_run: Path) -> dict[str, Any]:
    section_changes = {}
    improved = []
    worsened = []
    for section, values in primary.section_scores.items():
        old_score = (baseline.section_scores.get(section) or {}).get("score")
        new_score = values.get("score")
        if isinstance(old_score, (int, float)) and isinstance(new_score, (int, float)):
            delta = round(new_score - old_score, 2)
            section_changes[section] = delta
            if delta >= 5:
                improved.append(section)
            elif delta <= -5:
                worsened.append(section)
    primary_generic = (_metric_by_name(primary.metric_scores, "generic_phrase_penalty") or StyleBenchmarkMetric(
        metric_id="missing",
        metric_name="missing",
        explanation="missing",
    )).evidence.get("count", 0)
    baseline_generic = (_metric_by_name(baseline.metric_scores, "generic_phrase_penalty") or StyleBenchmarkMetric(
        metric_id="missing",
        metric_name="missing",
        explanation="missing",
    )).evidence.get("count", 0)
    primary_exemplar = (_metric_by_name(primary.metric_scores, "style_exemplar_usage_score") or StyleBenchmarkMetric(
        metric_id="missing",
        metric_name="missing",
        explanation="missing",
    )).normalized_score
    baseline_exemplar = (_metric_by_name(baseline.metric_scores, "style_exemplar_usage_score") or StyleBenchmarkMetric(
        metric_id="missing",
        metric_name="missing",
        explanation="missing",
    )).normalized_score
    primary_memory = (_metric_by_name(primary.metric_scores, "style_memory_alignment_score") or StyleBenchmarkMetric(
        metric_id="missing",
        metric_name="missing",
        explanation="missing",
    )).normalized_score
    baseline_memory = (_metric_by_name(baseline.metric_scores, "style_memory_alignment_score") or StyleBenchmarkMetric(
        metric_id="missing",
        metric_name="missing",
        explanation="missing",
    )).normalized_score
    recommendations = []
    if worsened:
        recommendations.append("Review worsened sections against section-specific exemplars: " + ", ".join(worsened))
    if primary_generic > baseline_generic:
        recommendations.append("Remove newly introduced generic phrases before the next draft.")
    if not recommendations:
        recommendations.append("Use the weakest current section score to choose the next style question.")
    return {
        "compared_run_id": baseline.run_id,
        "overall_score_change": round(primary.overall_score - baseline.overall_score, 2),
        "section_score_changes": section_changes,
        "generic_phrase_count_change": int(primary_generic) - int(baseline_generic),
        "overclaiming_style_warning_change": _audit_warning_count(primary_run) - _audit_warning_count(baseline_run),
        "style_exemplar_usage_change": (
            round((primary_exemplar or 0.0) - (baseline_exemplar or 0.0), 4)
            if primary_exemplar is not None or baseline_exemplar is not None
            else None
        ),
        "style_memory_alignment_change": (
            round((primary_memory or 0.0) - (baseline_memory or 0.0), 4)
            if primary_memory is not None or baseline_memory is not None
            else None
        ),
        "newly_improved_sections": improved,
        "worsened_sections": worsened,
        "recommended_next_edits": recommendations,
    }


def _recommendations(report: StyleBenchmarkReport, metrics: list[StyleBenchmarkMetric], eval_results: list[StyleEvalResult]) -> list[str]:
    recommendations = []
    low_sections = [
        metric.section_type
        for metric in metrics
        if metric.section_type and metric.normalized_score is not None and metric.normalized_score < 0.7
    ]
    if low_sections:
        recommendations.append("Answer style questions or add exemplars for weaker sections: " + ", ".join(sorted(set(low_sections))))
    if (_metric_by_name(metrics, "generic_phrase_penalty") or StyleBenchmarkMetric(
        metric_id="missing",
        metric_name="missing",
        explanation="missing",
    )).status == "warning":
        recommendations.append("Revise generic LLM-like phrases into concrete manuscript-specific prose.")
    if (_metric_by_name(metrics, "style_memory_alignment_score") or StyleBenchmarkMetric(
        metric_id="missing",
        metric_name="missing",
        explanation="missing",
        status="insufficient_data",
    )).status == "insufficient_data":
        recommendations.append("Run ask-style and answer preferences before relying on memory alignment scores.")
    if not eval_results:
        recommendations.append("Run build-style-eval to add local style evaluation items for benchmark context.")
    failed_eval = [result for result in eval_results if not result.passed]
    if failed_eval:
        recommendations.append(f"Review {len(failed_eval)} failed deterministic style eval item(s).")
    if not recommendations:
        recommendations.append("Use the benchmark comparison workflow after the next draft to confirm style movement.")
    return recommendations


def build_style_benchmark_report(
    project_dir: Path,
    run_dir: Path,
    config: ProjectConfig,
    section_filter: str | None = None,
    compare_run_dir: Path | None = None,
) -> tuple[StyleBenchmarkReport, dict[str, float]]:
    warnings: list[str] = []
    if not config.style_benchmark.enabled:
        warnings.append("style_benchmark.enabled is false; benchmark was run explicitly anyway.")
    weights = _normalized_weights(config, warnings)
    full_text, sections = _load_manuscript(run_dir)
    profile_path = run_dir / "style_profile.json"
    profile = StyleProfile.model_validate(read_json(profile_path)) if profile_path.exists() else StyleProfile()
    style_memory = _load_style_memory(project_dir, run_dir)
    source_map = _read_json_if_exists(run_dir / "source_map.json", {})
    source_map = source_map if isinstance(source_map, dict) else {}
    metrics = _global_metrics(full_text, sections, profile, style_memory, source_map)
    requested_sections = [normalize_section_type(section_filter)] if section_filter else BENCHMARK_SECTION_TYPES
    for section_type in requested_sections:
        metrics.append(_section_metric(section_type, sections.get(section_type, ""), profile))
    active_style_mode = config.style.active_mode
    for metric in metrics:
        metric.active_style_mode = active_style_mode
    eval_results = _score_style_eval(project_dir, run_dir, sections, full_text)
    categories = _category_scores(metrics, warnings)
    overall = _overall_score(categories, weights)
    report = StyleBenchmarkReport(
        run_id=run_dir.name,
        compared_run_id=compare_run_dir.name if compare_run_dir else None,
        active_style_mode=active_style_mode,
        overall_score=overall,
        section_scores=_section_scores(metrics),
        metric_scores=metrics,
        eval_results=eval_results,
        warnings=warnings,
        recommendations=[],
        generated_at=utc_iso(),
        input_hashes=_input_hashes(project_dir, run_dir),
        config_hash=sha256_text(config.model_dump_json()),
    )
    report.recommendations = _recommendations(report, metrics, eval_results)
    return report, weights


def _score_interpretation(score: float) -> str:
    if score >= 85:
        return "close to the measured style profile"
    if score >= 70:
        return "promising but still needs targeted style revision"
    if score >= 50:
        return "mixed alignment with measurable style gaps"
    return "weak alignment or insufficient style evidence"


def render_style_benchmark_markdown(report: StyleBenchmarkReport) -> str:
    lines = [
        "# Style Benchmark Report",
        "",
        f"- Run: {report.run_id}",
        f"- Compared run: {report.compared_run_id or 'none'}",
        f"- Active style mode: {report.active_style_mode}",
        f"- Overall score: {report.overall_score}/100",
        f"- Interpretation: {_score_interpretation(report.overall_score)}",
        "",
        "## Section Scores",
    ]
    if report.section_scores:
        for section, values in report.section_scores.items():
            lines.append(f"- {section}: {values.get('score')} ({values.get('status')})")
    else:
        lines.append("- No section scores were available.")

    scored_metrics = [metric for metric in report.metric_scores if metric.normalized_score is not None]
    strongest = sorted(scored_metrics, key=lambda item: item.normalized_score or 0, reverse=True)[:5]
    weakest = sorted(scored_metrics, key=lambda item: item.normalized_score or 1)[:5]
    lines.extend(["", "## Strongest Matches"])
    lines.extend(f"- {metric.metric_name}: {round((metric.normalized_score or 0) * 100, 1)}%" for metric in strongest)
    lines.extend(["", "## Weakest Matches"])
    lines.extend(
        f"- {metric.metric_name}: {round((metric.normalized_score or 0) * 100, 1)}% - {metric.suggested_fix or metric.explanation}"
        for metric in weakest
    )

    generic = next((metric for metric in report.metric_scores if metric.metric_name == "generic_phrase_penalty"), None)
    lines.extend(["", "## Generic Phrase Findings"])
    matches = (generic.evidence.get("matches", {}) if generic else {}) or {}
    if matches:
        lines.extend(f"- {phrase}: {count}" for phrase, count in matches.items())
    else:
        lines.append("- No configured generic phrases were detected.")

    lines.extend(["", "## Style Memory Alignment"])
    memory = next((metric for metric in report.metric_scores if metric.metric_name == "style_memory_alignment_score"), None)
    lines.append(f"- Status: {memory.status if memory else 'missing'}")
    lines.append(f"- Score: {round((memory.normalized_score or 0) * 100, 1) if memory and memory.normalized_score is not None else 'insufficient data'}")

    lines.extend(["", "## Style Exemplar Use"])
    exemplar = next((metric for metric in report.metric_scores if metric.metric_name == "style_exemplar_usage_score"), None)
    if exemplar:
        lines.append(f"- Score: {round((exemplar.normalized_score or 0) * 100, 1)}%")
        lines.append(f"- Evidence: {json.dumps(exemplar.evidence, sort_keys=True)}")

    lines.extend(["", "## Traceability Completeness"])
    traceability = next((metric for metric in report.metric_scores if metric.metric_name == "source_map_traceability_completeness"), None)
    if traceability:
        lines.append(f"- Score: {round((traceability.normalized_score or 0) * 100, 1)}%")
        lines.append(f"- Evidence: {json.dumps(traceability.evidence, sort_keys=True)}")

    if report.comparison:
        lines.extend(["", "## Run Comparison"])
        lines.append(f"- Overall score change: {report.comparison.get('overall_score_change')}")
        lines.append(f"- Generic phrase count change: {report.comparison.get('generic_phrase_count_change')}")
        improved = ", ".join(report.comparison.get("newly_improved_sections", [])) or "none"
        worsened = ", ".join(report.comparison.get("worsened_sections", [])) or "none"
        lines.append(f"- Newly improved sections: {improved}")
        lines.append(f"- Worsened sections: {worsened}")

    lines.extend(["", "## Recommended Next Style Questions"])
    for warning in report.warnings:
        lines.append(f"- Resolve benchmark warning: {warning}")
    lines.extend(["", "## Recommended Revision Actions"])
    lines.extend(f"- {item}" for item in report.recommendations)
    return "\n".join(lines).strip() + "\n"


def _benchmark_workbook_rows(report: StyleBenchmarkReport) -> dict[str, list[dict[str, Any]]]:
    generic = next((metric for metric in report.metric_scores if metric.metric_name == "generic_phrase_penalty"), None)
    memory = next((metric for metric in report.metric_scores if metric.metric_name == "style_memory_alignment_score"), None)
    comparison = report.comparison or {}
    return {
        "Summary": [
            {
                "run_id": report.run_id,
                "compared_run_id": report.compared_run_id,
                "active_style_mode": report.active_style_mode,
                "overall_score": report.overall_score,
                "interpretation": _score_interpretation(report.overall_score),
                "warning_count": len(report.warnings),
                "recommendation_count": len(report.recommendations),
            }
        ],
        "Section Scores": [
            {"section_type": section, **values} for section, values in report.section_scores.items()
        ],
        "Metric Details": [metric.model_dump(mode="json") for metric in report.metric_scores],
        "Style Eval Results": [result.model_dump(mode="json") for result in report.eval_results],
        "Generic Phrase Findings": [
            {"phrase": phrase, "count": count}
            for phrase, count in ((generic.evidence.get("matches", {}) if generic else {}) or {}).items()
        ],
        "Style Memory Alignment": [memory.model_dump(mode="json")] if memory else [],
        "Run Comparison": [comparison] if comparison else [],
        "Recommendations": [{"recommendation": item} for item in report.recommendations],
    }


def write_style_benchmark_outputs(
    project_dir: Path,
    run_dir: Path,
    report: StyleBenchmarkReport,
    weights: dict[str, float],
) -> Path:
    write_text(run_dir / "style_benchmark_report.md", render_style_benchmark_markdown(report))
    write_json(run_dir / "style_benchmark_scores.json", report)
    write_workbook(run_dir / "style_benchmark_details.xlsx", _benchmark_workbook_rows(report))
    output_files = [
        "style_benchmark_report.md",
        "style_benchmark_scores.json",
        "style_benchmark_details.xlsx",
    ]
    manifest = {
        "command": "style-benchmark",
        "generated_at": report.generated_at,
        "project_dir": str(project_dir),
        "run_dir": str(run_dir),
        "run_id": report.run_id,
        "compared_run_id": report.compared_run_id,
        "overall_score": report.overall_score,
        "active_style_mode": report.active_style_mode,
        "normalized_weights": weights,
        "input_hashes": report.input_hashes,
        "config_hash": report.config_hash,
        "warnings": report.warnings,
        "output_files": output_files,
    }
    write_json(run_dir / "style_benchmark_manifest.json", manifest)
    return run_dir


def run_style_benchmark(
    project_dir: Path,
    config: ProjectConfig,
    *,
    run_ref: str | None = None,
    compare_run_ref: str | None = None,
    section_filter: str | None = None,
) -> Path:
    project_dir = Path(project_dir)
    run_dir = resolve_benchmark_run(project_dir, run_ref)
    compare_run_dir = resolve_compare_run(project_dir, run_dir, compare_run_ref)
    report, weights = build_style_benchmark_report(
        project_dir,
        run_dir,
        config,
        section_filter=section_filter,
        compare_run_dir=compare_run_dir,
    )
    if compare_run_dir is not None:
        baseline, _baseline_weights = build_style_benchmark_report(project_dir, compare_run_dir, config)
        report.comparison = _comparison(report, baseline, run_dir, compare_run_dir)
        report.compared_run_id = compare_run_dir.name
        report.recommendations.extend(report.comparison.get("recommended_next_edits", []))
    ensure_dir(run_dir)
    return write_style_benchmark_outputs(project_dir, run_dir, report, weights)
