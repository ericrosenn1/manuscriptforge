"""Verified search over evidence artifacts from completed research runs.

Style samples are never search evidence. Record text hashes and the original
evidence hashes are separate, explicitly named provenance fields.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from manuscriptforge.models.claim import EvidenceItem
from manuscriptforge.orchestration.models import safe_id
from manuscriptforge.orchestration.storage import contained_file, hash_file
from manuscriptforge.research.pipeline import research_engine
from manuscriptforge.retrieval import LexicalIndex, RetrievalRecord

INDEX_ARTIFACT = "evidence_index.json"


def _is_style_source(value: Mapping[str, Any]) -> bool:
    path_parts = str(value.get("source_path", "")).replace("\\", "/").casefold().split("/")
    kind = str(value.get("evidence_type", "")).casefold()
    return "style_corpus" in path_parts or kind in {
        "style",
        "style_corpus",
        "style_sample",
        "writing_sample",
    }


def build_evidence_index(ingested: Mapping[str, Any], output_path: Path) -> dict[str, Any]:
    items = ingested.get("evidence_items", [])
    if not isinstance(items, (list, tuple)):
        raise ValueError("ingested.evidence_items must be a list")
    records = []
    excluded_styles = 0
    blank_records = 0
    for raw in items:
        if isinstance(raw, EvidenceItem):
            value = raw.model_dump(mode="json")
        elif isinstance(raw, Mapping):
            value = dict(raw)
        else:
            raise ValueError("Each evidence item must be a structured record")
        if _is_style_source(value):
            excluded_styles += 1
            continue
        item = EvidenceItem.model_validate(value)
        if not item.text.strip():
            blank_records += 1
            continue
        records.append(
            RetrievalRecord.from_text(
                item.evidence_id,
                item.text,
                item.evidence_id,
                {
                    "source_path": item.source_path,
                    "source_label": item.source_label,
                    "source_hash": item.content_hash,
                    "source_hash_semantics": "original EvidenceItem.content_hash; distinct from exact record text hash",
                    "evidence_type": item.evidence_type,
                    "locator": item.locator,
                    "evidence_metadata": item.metadata,
                },
            )
        )
    index = LexicalIndex(records, strategy="bm25", tokenizer="unicode")
    output_path = Path(output_path)
    index.save(output_path)
    return {
        "index_path": output_path.name,
        "index_hash": index.index_sha256,
        "index_file_sha256": hash_file(output_path),
        "strategy": index.strategy,
        "index_records": len(records),
        "excluded_style_records": excluded_styles,
        "blank_evidence_records": blank_records,
    }


def search_run(project_dir: Path, run_id: str, query: str, limit: int = 5) -> dict[str, Any]:
    """Read a verified saved index; never rebuild or execute nodes during search."""
    safe_id(run_id)
    engine = research_engine(Path(project_dir))
    run = engine.status(run_id)
    if run.status != "succeeded":
        raise ValueError("Search requires a completed successful research run")
    verified = engine.verify_run(run_id)
    if not verified.valid:
        raise ValueError("Research run verification failed: " + "; ".join(verified.errors))
    node = run.nodes.get("ingest")
    if node is None or node.status not in {"succeeded", "cached"} or not node.artifact_dir:
        raise ValueError("The verified run has no completed ingestion node")
    artifact = next((item for item in node.artifacts if item.path == INDEX_ARTIFACT), None)
    if artifact is None:
        raise ValueError("This run has no saved evidence index; create a new indexed research run")
    directory = Path(node.artifact_dir)
    if not directory.resolve().is_relative_to(engine.state_dir.resolve()):
        raise ValueError("Evidence index is outside the engine artifact directory")
    path = contained_file(directory, INDEX_ARTIFACT)
    if hash_file(path) != artifact.sha256:
        raise ValueError("Evidence index artifact hash does not match its run manifest")
    index = LexicalIndex.load(path)
    if hash_file(path) != artifact.sha256:
        raise ValueError("Evidence index changed while it was being read")
    recorded_hash = node.metadata.get("index_hash")
    if recorded_hash is not None and recorded_hash != index.index_sha256:
        raise ValueError("Evidence index identity does not match ingestion metadata")
    if index.strategy != "bm25":
        raise ValueError("The saved research evidence index uses an unexpected strategy")
    hits = [
        {
            "id": hit.record_id,
            "source_id": hit.source_id,
            "text": hit.text,
            "content_hash": hit.content_hash,
            "score": hit.score,
            "rank": hit.rank,
            "metadata": hit.to_dict()["metadata"],
        }
        for hit in index.search(query, limit=limit)
    ]
    return {
        "run_id": run.run_id,
        "index_hash": index.index_sha256,
        "index_file_sha256": artifact.sha256,
        "strategy": index.strategy,
        "query": query,
        "limit": limit,
        "hits": hits,
        "provenance": "Verified completed-run evidence artifacts; lexical relevance is not evidential support",
    }
