"""Optional model-backed section writer; the legacy drafting path stays unchanged."""

from __future__ import annotations

from manuscriptforge.llm.grounded import (
    draft_grounded_section,
    request_from_context,
    validate_grounded_response,
)
from manuscriptforge.models.manuscript import ManuscriptSection
from manuscriptforge.pipeline.run_context import ManuscriptContext


def write_grounded_section(
    context: ManuscriptContext, section_name: str, *, max_words: int = 800
) -> ManuscriptSection:
    request = request_from_context(context, section_name, max_words=max_words)
    response = draft_grounded_section(request, context.provider)
    report = validate_grounded_response(request, response)
    return ManuscriptSection(
        section_name=section_name,
        section_type=section_name.lower().replace(" ", "_"),
        style_mode=context.style_profile.style_mode,
        content="\n\n".join(
            paragraph.text
            + (" [" + "; ".join(paragraph.citation_ids) + "]" if paragraph.citation_ids else "")
            for paragraph in response.paragraphs
        ),
        claim_ids=sorted({cid for paragraph in response.paragraphs for cid in paragraph.claim_ids}),
        citation_ids=sorted(
            {cid for paragraph in response.paragraphs for cid in paragraph.citation_ids}
        ),
        style_chunk_ids=sorted(
            {sid for paragraph in response.paragraphs for sid in paragraph.style_chunk_ids}
        ),
        unresolved_flags=[
            "Model-generated paraphrase requires human semantic and scientific review.",
            *response.unresolved_questions,
        ],
        revision_notes=[
            f"Grounded request SHA-256: {report.request_hash}",
            "Grounding checks establish reference and numeric consistency, not scientific validity.",
        ],
    )
