from __future__ import annotations

from manuscriptforge.models.style import StyleProfile


def render_style_guide(profile: StyleProfile) -> str:
    features = profile.global_features
    sentence = profile.sentence_length_distribution
    paragraph = profile.paragraph_length_distribution
    hedging = profile.hedging_profile
    lines = [
        "# Style Guide",
        "",
        "This guide is derived from the supplied style corpus. It is an approximation, not a claim of perfect imitation.",
        "",
        "## Corpus",
        f"- Files: {len(profile.corpus_files)}",
        f"- Words: {features.get('word_count', 0)}",
        f"- Sentences: {features.get('sentence_count', 0)}",
        f"- Paragraphs: {features.get('paragraph_count', 0)}",
        "",
        "## Length Patterns",
        f"- Typical sentence length: {sentence.get('mean')} words on average",
        f"- Typical paragraph length: {paragraph.get('mean')} words on average",
        "",
        "## Hedging",
        f"- Hedging terms per 1000 words: {hedging.get('hedging_terms_per_1000_words', 0)}",
        f"- Common hedges: {', '.join(hedging.get('terms', {}).keys()) or 'none detected'}",
        "",
        "## Transitions",
        f"- Common transitions: {', '.join(profile.transition_profile.get('transition_counts', {}).keys()) or 'none detected'}",
        f"- Passive voice ratio: {features.get('passive_voice_ratio', 0)}",
        f"- First-person pronouns: {features.get('first_person_usage', features.get('first_person_count', 0))}",
        "",
        "## Limitation Phrasing",
    ]
    limitation_phrases = features.get("limitation_phrases", [])
    if limitation_phrases:
        lines.extend(f"- {phrase}" for phrase in limitation_phrases[:6])
    else:
        lines.append("- No recurring limitation phrasing detected.")
    lines.extend(
        [
            "",
            "## Common Three-Word Sequences",
            "These are frequency counts from the selected corpus, not instructions to repeat them.",
        ]
    )
    if profile.preferred_phrases:
        lines.extend(f"- {phrase}" for phrase in profile.preferred_phrases[:12])
    else:
        lines.append("- No recurring three-word sequences detected.")
    if profile.avoided_phrases:
        lines.extend(
            [
                "",
                "## Default Phrases to Review",
                "This project checklist is not inferred from the corpus.",
            ]
        )
        lines.extend(f"- {phrase}" for phrase in profile.avoided_phrases)
    lines.extend(
        [
            "",
            "## Citation Integration",
            f"- Likely citation style: {profile.citation_style.get('likely_style', 'unknown')}",
            "",
            "## Section Patterns",
        ]
    )
    for section, values in profile.section_features.items():
        lines.append(f"### {section}")
        if not values.get("chunk_count"):
            lines.append("- No corpus chunks detected for this section.")
            lines.append("")
            continue
        openings = values.get("opening_sentence_patterns", values.get("common_openings", []))
        if openings:
            lines.append(f"- Example opening pattern: {openings[0]}")
        lines.append(
            "- Average sentence length: "
            f"{values.get('sentence_length_distribution', values.get('sentence_lengths', {})).get('mean')}"
        )
        transitions = values.get("transition_phrases", {})
        if transitions:
            lines.append(f"- Section transitions: {', '.join(transitions.keys())}")
        lines.append("")
    return "\n".join(lines).strip() + "\n"
