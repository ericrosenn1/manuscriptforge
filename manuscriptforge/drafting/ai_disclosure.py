from __future__ import annotations

from manuscriptforge.pipeline.run_context import ManuscriptContext


def write_ai_disclosure(context: ManuscriptContext) -> str:
    return (
        "Draft disclosure for author review: ManuscriptForge was used as a local workflow aid "
        f"with the configured `{context.provider.name}` provider to organize supplied inputs, "
        "compile an evidence-linked claim registry, generate a preliminary manuscript draft, "
        "and produce audit reports. The system was not treated as an author. The human author "
        "must verify all results, citations, interpretations, and journal-specific disclosure requirements."
    )
