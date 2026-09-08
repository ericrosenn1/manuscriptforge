from __future__ import annotations

from collections import Counter
from typing import Any

from manuscriptforge.models.style import StyleProfile


def build_style_summary(profile: StyleProfile, memory: dict[str, Any]) -> dict[str, Any]:
    chunk_index = profile.global_features.get("style_chunk_index", {})
    section_distribution = profile.global_features.get("section_distribution", {})
    style_mode_distribution = profile.global_features.get("style_mode_distribution", {})
    extension_distribution = profile.global_features.get("source_extension_distribution", {})
    mode_extension_distribution = profile.global_features.get("style_mode_extension_distribution", {})
    extraction_summary = profile.global_features.get("style_extraction_summary", {})
    curation_summary = profile.global_features.get("style_chunk_curation", {})
    if not section_distribution and isinstance(chunk_index, dict):
        section_distribution = chunk_index.get("section_distribution", {})
    if not style_mode_distribution and isinstance(chunk_index, dict):
        style_mode_distribution = chunk_index.get("style_mode_distribution", {})
    if not extension_distribution and isinstance(chunk_index, dict):
        extension_distribution = chunk_index.get("source_extension_distribution", {})
    if not mode_extension_distribution and isinstance(chunk_index, dict):
        mode_extension_distribution = chunk_index.get("style_mode_extension_distribution", {})
    hedges = profile.hedging_profile.get("terms", {})
    transitions = profile.transition_profile.get("transition_counts", {})
    section_counts = {
        section: values.get("chunk_count", 0)
        for section, values in profile.section_features.items()
        if values.get("chunk_count", 0)
    }
    unresolved = []
    if not profile.corpus_files:
        unresolved.append("No style documents were supplied.")
    if not section_counts.get("methods"):
        unresolved.append("No Methods-style chunks were detected.")
    if not section_counts.get("results"):
        unresolved.append("No Results-style chunks were detected.")
    if memory.get("answered_preferences", 0) == 0:
        unresolved.append("No answered style preference questions have been collected.")
    if not hedges:
        unresolved.append("Hedging preference is ambiguous because few hedge terms were detected.")
    active_mode = str(profile.global_features.get("active_style_mode") or profile.style_mode)
    if active_mode and active_mode not in style_mode_distribution and profile.corpus_files:
        unresolved.append(f"No style chunks were detected for active mode `{active_mode}`.")
    extraction_by_mode = extraction_summary.get("by_style_mode", {}) if isinstance(extraction_summary, dict) else {}
    extracted_count = int(extraction_summary.get("files_extracted", 0)) if isinstance(extraction_summary, dict) else 0
    if extraction_by_mode.get(active_mode, 0) and not extracted_count:
        unresolved.append(f"Active mode `{active_mode}` has files, but none produced usable extracted text.")

    next_questions = []
    if not section_counts.get("methods"):
        next_questions.append("Add Methods samples or answer a Methods precision preference question.")
    if not section_counts.get("limitations"):
        next_questions.append("Add Limitations samples or answer limitation phrasing questions.")
    if memory.get("answered_preferences", 0) < 5:
        next_questions.append("Answer at least five style questions covering hedging, transitions, and citations.")
    if not next_questions:
        next_questions.append("Review the highest-ranked style exemplars during the next draft run.")

    signals = {
        "sentence_mean": profile.sentence_length_distribution.get("mean"),
        "paragraph_mean": profile.paragraph_length_distribution.get("mean"),
        "hedging_terms": hedges,
        "transition_phrases": transitions,
        "citation_style": profile.citation_style.get("likely_style", "unknown"),
        "section_coverage": section_counts,
    }
    return {
        "style_documents": len(profile.corpus_files),
        "style_chunks": int(chunk_index.get("chunk_count", 0)) if isinstance(chunk_index, dict) else 0,
        "active_style_mode": active_mode,
        "style_mode_distribution": dict(Counter(style_mode_distribution)),
        "style_assets_by_extension": dict(Counter(extraction_summary.get("by_extension", {})))
        if isinstance(extraction_summary, dict)
        else {},
        "style_extraction_summary": extraction_summary if isinstance(extraction_summary, dict) else {},
        "style_chunk_curation": curation_summary if isinstance(curation_summary, dict) else {},
        "style_chunks_by_extension": dict(Counter(extension_distribution)),
        "style_chunks_by_mode_and_extension": mode_extension_distribution,
        "section_controls": profile.global_features.get("section_controls", {}),
        "section_distribution": dict(Counter(section_distribution)),
        "preference_answers_collected": memory.get("answered_preferences", 0),
        "preference_records": memory.get("preference_records", 0),
        "strongest_style_signals": signals,
        "unresolved_style_ambiguities": unresolved,
        "recommended_next_questions": next_questions,
    }


def render_style_summary(summary: dict[str, Any]) -> str:
    lines = [
        "# Style Summary",
        "",
        f"- Style documents: {summary.get('style_documents', 0)}",
        f"- Style chunks: {summary.get('style_chunks', 0)}",
        f"- Active style mode: {summary.get('active_style_mode', 'unknown')}",
        f"- Preference answers collected: {summary.get('preference_answers_collected', 0)}",
        "",
        "## Extraction Summary",
    ]
    extraction = summary.get("style_extraction_summary", {})
    if extraction:
        lines.extend(
            [
                f"- Files considered: {extraction.get('files_considered', 0)}",
                f"- Successfully extracted: {extraction.get('files_extracted', 0)}",
                f"- Failed: {extraction.get('failed', 0)}",
                f"- Unsupported: {extraction.get('unsupported', 0)}",
                f"- Likely scanned/unextractable: {extraction.get('likely_scanned_or_unextractable', 0)}",
            ]
        )
    else:
        lines.append("- No extraction manifest found.")
    curation = summary.get("style_chunk_curation", {})
    lines.extend(["", "## Chunk Curation"])
    if curation:
        lines.append(f"- Chunk registry used: {curation.get('chunk_registry_used', False)}")
        lines.append(f"- Chunks before curation: {curation.get('chunks_before_curation', 0)}")
        lines.append(f"- Chunks after curation: {curation.get('chunks_after_curation', 0)}")
        lines.append(f"- Approved style-profile chunks: {curation.get('approved_style_profile_chunks', 0)}")
        if curation.get("warning"):
            lines.append(f"- Warning: {curation.get('warning')}")
    else:
        lines.append("- No chunk registry applied.")
    lines.extend(["", "## Style Assets By Extension"])
    assets_by_extension = summary.get("style_assets_by_extension", {})
    if assets_by_extension:
        for extension, count in sorted(assets_by_extension.items()):
            lines.append(f"- {extension}: {count}")
    else:
        lines.append("- No style assets detected.")
    lines.extend(
        [
            "",
            "## Style Chunks By Extension",
        ]
    )
    chunks_by_extension = summary.get("style_chunks_by_extension", {})
    if chunks_by_extension:
        for extension, count in sorted(chunks_by_extension.items()):
            lines.append(f"- {extension}: {count}")
    else:
        lines.append("- No style chunks detected.")
    lines.extend(
        [
            "",
            "## Style Mode Distribution",
        ]
    )
    mode_distribution = summary.get("style_mode_distribution", {})
    if mode_distribution:
        for mode, count in sorted(mode_distribution.items()):
            lines.append(f"- {mode}: {count}")
    else:
        lines.append("- No style modes detected.")
    lines.extend(
        [
            "",
            "## Section Controls",
        ]
    )
    controls = summary.get("section_controls", {})
    if controls:
        for section, values in sorted(controls.items()):
            if isinstance(values, dict):
                rendered = ", ".join(f"{key}={value}" for key, value in sorted(values.items()))
                lines.append(f"- {section}: {rendered}")
    else:
        lines.append("- None configured.")
    lines.extend(
        [
            "",
            "## Section Distribution",
        ]
    )
    distribution = summary.get("section_distribution", {})
    if distribution:
        for section, count in sorted(distribution.items()):
            lines.append(f"- {section}: {count}")
    else:
        lines.append("- No style chunks detected.")
    lines.extend(["", "## Strongest Signals"])
    signals = summary.get("strongest_style_signals", {})
    lines.append(f"- Sentence mean: {signals.get('sentence_mean')}")
    lines.append(f"- Paragraph mean: {signals.get('paragraph_mean')}")
    lines.append(f"- Citation style: {signals.get('citation_style')}")
    hedges = signals.get("hedging_terms", {})
    lines.append(f"- Hedges: {', '.join(hedges.keys()) if isinstance(hedges, dict) and hedges else 'none detected'}")
    transitions = signals.get("transition_phrases", {})
    lines.append(
        f"- Transitions: {', '.join(transitions.keys()) if isinstance(transitions, dict) and transitions else 'none detected'}"
    )
    lines.extend(["", "## Unresolved Ambiguities"])
    for item in summary.get("unresolved_style_ambiguities", []) or ["None detected."]:
        lines.append(f"- {item}")
    lines.extend(["", "## Recommended Next Questions"])
    for item in summary.get("recommended_next_questions", []):
        lines.append(f"- {item}")
    return "\n".join(lines).strip() + "\n"
