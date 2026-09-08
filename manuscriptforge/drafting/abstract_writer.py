from __future__ import annotations

from manuscriptforge.pipeline.run_context import ManuscriptContext


def _first_claim(context: ManuscriptContext, section: str) -> str:
    for claim in context.claims:
        if claim.section_target == section:
            return claim.claim_text
    return ""


def write_abstract(context: ManuscriptContext) -> str:
    background = _first_claim(context, "Introduction") or context.ingested["text_inputs"].get("abstract", {}).get("text", "")
    method = _first_claim(context, "Methods") or "Methods details were derived from supplied notes."
    result = _first_claim(context, "Results") or "The supplied result tables were reviewed for auditable claims."
    conclusion = (
        "The draft should be interpreted as a human-review manuscript aid, with unsupported claims and missing details resolved before submission."
    )
    if context.config.manuscript.structured_abstract:
        return (
            f"Background: {background}\n"
            f"Methods: {method}\n"
            f"Results: {result}\n"
            f"Conclusions: {conclusion}"
        )
    return " ".join([background, method, result, conclusion]).strip()
