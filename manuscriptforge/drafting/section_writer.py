from __future__ import annotations

from manuscriptforge.drafting.section_planner import section_goal
from manuscriptforge.models.manuscript import ManuscriptSection
from manuscriptforge.pipeline.run_context import ManuscriptContext
from manuscriptforge.style.features import normalize_section_type
from manuscriptforge.style.memory import load_style_memory
from manuscriptforge.style.retrieval import get_style_exemplars

SOFTENERS = ("may", "suggest", "consistent with", "associated with", "possibility")


def _citation_suffix(citation_ids: list[str]) -> str:
    if not citation_ids:
        return ""
    return " [" + "; ".join(citation_ids) + "]"


def _softened_claim_text(claim_text: str, support_strength: str) -> str:
    lower = claim_text.lower()
    if support_strength in {"weak", "needs_review", "unsupported"} and not any(s in lower for s in SOFTENERS):
        return f"The supplied evidence may indicate that {claim_text[0].lower() + claim_text[1:]}"
    return claim_text


def _claims_for_section(context: ManuscriptContext, section_name: str) -> list:
    if section_name == "Discussion":
        return [
            claim
            for claim in context.claims
            if claim.section_target in {"Discussion", "Introduction"}
            or claim.claim_type in {"interpretation", "background"}
        ]
    if section_name == "Limitations":
        return [
            claim
            for claim in context.claims
            if claim.section_target == "Limitations" or claim.claim_type == "limitation"
        ]
    return [claim for claim in context.claims if claim.section_target == section_name]


def _style_directives(context: ManuscriptContext, section_name: str) -> tuple[list[str], str | None]:
    section_type = normalize_section_type(section_name)
    section_features = context.style_profile.section_features.get(section_type, {})
    global_features = context.style_profile.global_features
    style_memory = load_style_memory(context.project_dir)
    notes: list[str] = []
    transition = None
    sentence_mean = section_features.get("sentence_length_distribution", {}).get(
        "mean"
    ) or section_features.get("sentence_lengths", {}).get(
        "mean", context.style_profile.sentence_length_distribution.get("mean")
    )
    if sentence_mean:
        notes.append(f"Style target: average sentence length near {sentence_mean} words.")
    hedges = section_features.get("hedging_terms") or context.style_profile.hedging_profile.get("terms", {})
    if hedges:
        notes.append("Style target: use cautious hedge terms seen in the corpus when evidence is indirect.")
    transitions = section_features.get("transition_counts") or context.style_profile.transition_profile.get(
        "transition_counts", {}
    )
    if transitions:
        transition = next(iter(transitions.keys()))
        notes.append(f"Style target: section-compatible transition available: '{transition}'.")
    memory_transitions = style_memory.get("preferred_transition_patterns", [])
    if memory_transitions:
        transition = str(memory_transitions[0])
        notes.append(f"Style memory: preferred transition pattern available: '{transition}'.")
    hedge_level = style_memory.get("preferred_hedge_level")
    if hedge_level and hedge_level != "corpus_default":
        notes.append(f"Style memory: preferred hedge level is '{hedge_level}'.")
    controls = context.config.style.section_controls.get(section_type, {})
    if controls:
        control_summary = ", ".join(f"{key}={value}" for key, value in sorted(controls.items()))
        notes.append(f"Section style controls: {control_summary}.")
    passive_ratio = section_features.get("passive_voice_ratio", global_features.get("passive_voice_ratio"))
    if passive_ratio is None:
        passive_ratio = section_features.get(
            "passive_voice_estimate", global_features.get("passive_voice_estimate")
        )
    if passive_ratio is not None:
        notes.append(f"Style target: observed passive voice ratio is {passive_ratio}.")
    return notes, transition


def write_generic_section(context: ManuscriptContext, section_name: str) -> ManuscriptSection:
    claims = _claims_for_section(context, section_name)
    style_examples = get_style_exemplars(
        context.style_profile,
        section_name,
        " ".join(claim.claim_text for claim in claims[:3]),
        max_examples=context.config.style.max_style_examples_per_section,
    )
    unresolved_flags: list[str] = []
    notes: list[str] = [f"Section goal: {section_goal(section_name)}."]
    style_notes, transition = _style_directives(context, section_name)
    notes.extend(style_notes)
    if style_examples:
        used = ", ".join(example["chunk_id"] for example in style_examples if example.get("chunk_id"))
        notes.append(
            "Style exemplars were retrieved from the style corpus but not used as factual evidence"
            + (f": {used}." if used else ".")
        )

    paragraphs: list[str] = []
    if section_name == "Introduction":
        paragraphs.append(
            "The rationale for this manuscript is drawn from supplied background notes and citation records. "
            "The draft frames the problem cautiously and preserves uncertainty where evidence is incomplete."
        )
    elif section_name == "Methods":
        paragraphs.append(
            "Methods are summarized from the supplied methods notes and should be checked against the final analysis record."
        )
    elif section_name == "Results":
        paragraphs.append(
            "Results are described from the supplied tables and figure legends with interpretation minimized."
        )
    elif section_name == "Discussion":
        opener = "The Discussion"
        if transition and transition.lower() not in {"however", "in contrast"}:
            opener = transition.capitalize() + ", the Discussion"
        paragraphs.append(
            f"{opener} interprets the table-supported observations cautiously and separates association from causality."
        )
    elif section_name == "Limitations":
        paragraphs.append(
            "The following limitations require author confirmation before submission."
        )
    elif section_name == "Conclusion":
        paragraphs.append(
            "Overall, the supplied evidence supports a cautious manuscript draft rather than a final scientific conclusion."
        )

    if claims:
        for claim in claims[:12]:
            text = _softened_claim_text(claim.claim_text, claim.support_strength)
            paragraphs.append(text + _citation_suffix(claim.citation_ids))
            if claim.needs_human_review:
                unresolved_flags.append(f"Human review required for {claim.claim_id}: {claim.notes}")
            if claim.needs_citation:
                unresolved_flags.append(f"Citation needed for {claim.claim_id}")
    else:
        paragraphs.append(
            f"No section-specific claims were compiled for {section_name}. The author should add evidence or remove this section."
        )
        unresolved_flags.append(f"No compiled claims available for {section_name}")

    if section_name == "Methods":
        methods_text = "\n\n".join(paragraphs).lower()
        for required in ["software", "version", "statistical", "sample", "preprocessing"]:
            if required not in methods_text:
                unresolved_flags.append(f"Methods may be missing {required} details")

    return ManuscriptSection(
        section_name=section_name,
        section_type=section_name.lower().replace(" ", "_"),
        style_mode=context.style_profile.style_mode,
        content="\n\n".join(paragraphs),
        claim_ids=[claim.claim_id for claim in claims],
        citation_ids=sorted({cid for claim in claims for cid in claim.citation_ids}),
        style_chunk_ids=[
            example["chunk_id"] for example in style_examples if example.get("chunk_id")
        ],
        unresolved_flags=sorted(set(unresolved_flags)),
        revision_notes=notes,
    )
