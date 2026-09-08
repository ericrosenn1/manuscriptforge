from __future__ import annotations

from pathlib import Path
from typing import Any

from manuscriptforge.evidence.citation_mapper import map_citations_to_claims
from manuscriptforge.evidence.claim_extractor import extract_claims
from manuscriptforge.models.citation import CitationRecord
from manuscriptforge.models.claim import ScientificClaim
from manuscriptforge.utils.io import write_json


def build_claim_registry(
    project_dir: Path,
    run_dir: Path,
    ingested: dict[str, Any],
) -> list[ScientificClaim]:
    citations = [
        item if isinstance(item, CitationRecord) else CitationRecord.model_validate(item)
        for item in ingested.get("citations", [])
    ]
    claims = extract_claims(project_dir, ingested)
    claims = map_citations_to_claims(claims, citations)
    write_json(run_dir / "claim_registry.json", claims)
    write_json(run_dir / "intermediate" / "claim_registry.json", claims)
    from manuscriptforge.export.excel_reporter import write_claim_registry_xlsx

    write_claim_registry_xlsx(run_dir / "claim_registry.xlsx", claims)
    return claims
