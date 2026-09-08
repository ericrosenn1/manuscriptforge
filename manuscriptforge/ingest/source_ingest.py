from __future__ import annotations

from pathlib import Path

from manuscriptforge.ingest.bibtex_ingest import ingest_bibtex
from manuscriptforge.ingest.metadata_enrichment import BaseMetadataProvider, enrich_citations
from manuscriptforge.ingest.pdf_ingest import ingest_pdfs
from manuscriptforge.models.citation import CitationRecord
from manuscriptforge.models.claim import EvidenceItem
from manuscriptforge.models.project import ProjectConfig


def ingest_sources(
    project_dir: Path,
    config: ProjectConfig | None = None,
    enrich_sources: bool = False,
    metadata_provider: BaseMetadataProvider | None = None,
) -> tuple[list[CitationRecord], list[EvidenceItem], list[Path]]:
    citations = ingest_bibtex(project_dir)
    cache_files: list[Path] = []
    if config is not None:
        citations, cache_files = enrich_citations(
            citations,
            project_dir,
            config,
            provider=metadata_provider,
            force=enrich_sources,
        )
    pdf_evidence = ingest_pdfs(project_dir)
    return citations, pdf_evidence, cache_files
