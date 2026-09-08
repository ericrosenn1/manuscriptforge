from __future__ import annotations

from manuscriptforge.pipeline.run_context import ManuscriptContext


def write_title_candidates(context: ManuscriptContext) -> list[str]:
    field = context.config.field or "biomedical research"
    project_name = context.config.project_name
    result_terms = []
    for claim in context.claims:
        if claim.section_target == "Results":
            result_terms.extend(claim.claim_text.split()[:6])
    signal = " ".join(result_terms[:8]).strip()
    candidates = [
        project_name,
        f"An auditable analysis of evidence-linked findings in {field}",
        f"Evidence-grounded manuscript draft for {field} results",
    ]
    if signal:
        candidates.append(f"Cautious interpretation of {signal}")
    return candidates
