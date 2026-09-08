from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from manuscriptforge.models.citation import CitationRecord
from manuscriptforge.models.project import ProjectConfig
from manuscriptforge.utils.hashing import sha256_text
from manuscriptforge.utils.io import ensure_dir, read_json, write_json


class MetadataProviderError(RuntimeError):
    """Raised when metadata enrichment cannot complete."""


class BaseMetadataProvider(ABC):
    name = "base"

    @abstractmethod
    def enrich(self, citation: CitationRecord) -> CitationRecord:
        raise NotImplementedError


class OfflineMetadataProvider(BaseMetadataProvider):
    name = "offline"

    def enrich(self, citation: CitationRecord) -> CitationRecord:
        citation.metadata_status = "offline_skipped"
        citation.metadata_confidence = "unknown"
        citation.retrieval_notes = (
            citation.retrieval_notes + " " if citation.retrieval_notes else ""
        ) + "Network metadata enrichment was not enabled; citation left as supplied/parsed."
        return citation


class MockMetadataProvider(BaseMetadataProvider):
    name = "mock"

    def __init__(self, fixtures: dict[str, dict[str, Any]] | None = None) -> None:
        self.fixtures = fixtures or {}

    def _keys_for(self, citation: CitationRecord) -> list[str]:
        return [
            value.lower()
            for value in [citation.doi, citation.pmid, citation.pmcid, citation.arxiv_id, citation.title]
            if value
        ]

    def enrich(self, citation: CitationRecord) -> CitationRecord:
        for key in self._keys_for(citation):
            if key in self.fixtures:
                data = self.fixtures[key]
                updated = citation.model_copy(update=data)
                updated.metadata_status = "enriched"
                updated.metadata_confidence = "exact_identifier" if citation.doi or citation.pmid else "title_match"
                updated.retrieval_notes = "Enriched from MockMetadataProvider fixture."
                return updated
        citation.metadata_status = "needs_review"
        citation.metadata_confidence = "unknown"
        citation.retrieval_notes = "MockMetadataProvider had no fixture for this citation."
        return citation


def citation_cache_key(citation: CitationRecord) -> str:
    identifier = citation.doi or citation.pmid or citation.pmcid or citation.arxiv_id or citation.title
    return sha256_text(identifier.lower().strip())[:24]


def cache_path_for(project_dir: Path, config: ProjectConfig, citation: CitationRecord) -> Path:
    return project_dir / config.sources.metadata_cache_dir / f"{citation_cache_key(citation)}.json"


def read_cached_metadata(project_dir: Path, config: ProjectConfig, citation: CitationRecord) -> CitationRecord | None:
    path = cache_path_for(project_dir, config, citation)
    if not path.exists():
        return None
    return CitationRecord.model_validate(read_json(path))


def write_cached_metadata(project_dir: Path, config: ProjectConfig, citation: CitationRecord) -> Path:
    path = cache_path_for(project_dir, config, citation)
    ensure_dir(path.parent)
    write_json(path, citation)
    return path


def enrichment_requested(config: ProjectConfig, force: bool = False) -> bool:
    return force or config.sources.enrich_doi or config.sources.enrich_pmid


def should_try_identifier(config: ProjectConfig, citation: CitationRecord) -> bool:
    return bool((config.sources.enrich_doi and citation.doi) or (config.sources.enrich_pmid and citation.pmid))


def enrich_citations(
    citations: list[CitationRecord],
    project_dir: Path,
    config: ProjectConfig,
    provider: BaseMetadataProvider | None = None,
    force: bool = False,
) -> tuple[list[CitationRecord], list[Path]]:
    if not enrichment_requested(config, force):
        return citations, []

    provider = provider or OfflineMetadataProvider()
    cache_files: list[Path] = []
    enriched: list[CitationRecord] = []
    for citation in citations:
        cached = read_cached_metadata(project_dir, config, citation)
        if cached is not None:
            cached.retrieval_notes = (
                cached.retrieval_notes + " " if cached.retrieval_notes else ""
            ) + "Loaded from metadata cache."
            enriched.append(cached)
            cache_files.append(cache_path_for(project_dir, config, citation))
            continue

        if isinstance(provider, OfflineMetadataProvider) or should_try_identifier(config, citation) or force:
            try:
                updated = provider.enrich(citation)
            except Exception as exc:
                if config.sources.fail_on_metadata_error:
                    raise MetadataProviderError(f"Metadata enrichment failed for {citation.citation_id}: {exc}") from exc
                updated = citation.model_copy()
                updated.metadata_status = "failed"
                updated.metadata_confidence = "unknown"
                updated.retrieval_notes = f"Metadata enrichment failed: {exc}"
            enriched.append(updated)
            cache_files.append(write_cached_metadata(project_dir, config, updated))
        else:
            citation.metadata_status = citation.metadata_status or "parsed"
            enriched.append(citation)
    return enriched, cache_files
