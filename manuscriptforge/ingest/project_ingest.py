from __future__ import annotations

from pathlib import Path
from typing import Any

from manuscriptforge.config import load_project_config
from manuscriptforge.ingest.metadata_enrichment import BaseMetadataProvider
from manuscriptforge.ingest.source_ingest import ingest_sources
from manuscriptforge.ingest.style_ingest import ingest_style_corpus
from manuscriptforge.ingest.table_ingest import ingest_result_tables
from manuscriptforge.models.claim import EvidenceItem
from manuscriptforge.utils.hashing import sha256_file
from manuscriptforge.utils.io import write_json

TEXT_INPUTS = {
    "abstract": "abstract.md",
    "rationale": "rationale.md",
    "methods": "methods.md",
    "interpretation_notes": "interpretation_notes.md",
    "figure_legends": "figure_legends.md",
}


def read_text_inputs(project_dir: Path) -> dict[str, dict[str, str]]:
    inputs_dir = project_dir / "inputs"
    data: dict[str, dict[str, str]] = {}
    for key, filename in TEXT_INPUTS.items():
        path = inputs_dir / filename
        if path.exists():
            data[key] = {
                "source_path": path.relative_to(project_dir).as_posix(),
                "text": path.read_text(encoding="utf-8"),
                "content_hash": sha256_file(path),
            }
        else:
            data[key] = {"source_path": f"inputs/{filename}", "text": "", "content_hash": ""}
    return data


def text_inputs_as_evidence(project_dir: Path, text_inputs: dict[str, dict[str, str]]) -> list[EvidenceItem]:
    evidence: list[EvidenceItem] = []
    mapping = {
        "methods": ("method_note", "Methods notes"),
        "interpretation_notes": ("interpretation_note", "Interpretation notes"),
        "figure_legends": ("figure", "Figure legends"),
        "abstract": ("manual", "Abstract"),
        "rationale": ("manual", "Rationale"),
    }
    for key, item in text_inputs.items():
        text = item.get("text", "").strip()
        if not text:
            continue
        evidence_type, label = mapping[key]
        evidence.append(
            EvidenceItem(
                evidence_id=f"ev_{key}",
                evidence_type=evidence_type,  # type: ignore[arg-type]
                source_path=item["source_path"],
                source_label=label,
                locator=None,
                text=text,
                metadata={"input_key": key},
                content_hash=item["content_hash"],
            )
        )
    return evidence


def ingest_project(
    project_dir: Path,
    run_dir: Path,
    enrich_sources: bool = False,
    metadata_provider: BaseMetadataProvider | None = None,
) -> dict[str, Any]:
    config = load_project_config(project_dir)
    text_inputs = read_text_inputs(project_dir)
    table_summaries, table_evidence = ingest_result_tables(project_dir, config.tables.schemas)
    citations, pdf_evidence, metadata_cache_files = ingest_sources(
        project_dir, config=config, enrich_sources=enrich_sources, metadata_provider=metadata_provider
    )
    style_samples = ingest_style_corpus(project_dir)
    evidence_items = text_inputs_as_evidence(project_dir, text_inputs) + table_evidence + pdf_evidence
    source_hashes = {}
    for path in sorted((project_dir / "inputs").rglob("*")):
        if path.is_file():
            source_hashes[path.relative_to(project_dir).as_posix()] = sha256_file(path)
    data = {
        "text_inputs": text_inputs,
        "tables": table_summaries,
        "citations": citations,
        "style_corpus": style_samples,
        "evidence_items": evidence_items,
        "pdf_sources": [
            item.metadata.get("pdf_summary")
            for item in pdf_evidence
            if isinstance(item.metadata.get("pdf_summary"), dict)
        ],
        "metadata_cache_files": [path.relative_to(project_dir).as_posix() for path in metadata_cache_files],
        "source_hashes": source_hashes,
    }
    write_json(run_dir / "intermediate" / "ingested_project.json", data)
    write_json(run_dir / "intermediate" / "evidence_items.json", evidence_items)
    write_json(run_dir / "intermediate" / "pdf_sources.json", data["pdf_sources"])
    write_json(run_dir / "citation_registry.json", citations)
    return data
