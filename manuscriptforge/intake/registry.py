from __future__ import annotations

import csv
import json
import shutil
import stat
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from manuscriptforge.ingest.bibtex_ingest import _fallback_parse_bibtex
from manuscriptforge.intake.models import (
    DataAsset,
    ResultTableAsset,
    SourceAsset,
    WritingSampleAsset,
)
from manuscriptforge.intake.scaffold import is_scaffold_file
from manuscriptforge.models.style import StyleMode, normalize_style_mode
from manuscriptforge.style.features import classify_section_heading, infer_section_type
from manuscriptforge.utils.dates import run_timestamp, utc_iso
from manuscriptforge.utils.hashing import sha256_file, sha256_text
from manuscriptforge.utils.ids import stable_id
from manuscriptforge.utils.io import (
    ensure_dir,
    read_json,
    read_yaml,
    write_json,
    write_jsonl,
    write_text,
)

TABLE_SUFFIXES = {".csv", ".tsv", ".xlsx", ".xls"}
STYLE_SUFFIXES = {".md", ".txt", ".docx", ".pdf"}
SOURCE_SUFFIXES = {".bib", ".ris", ".json", ".txt", ".md"}
MANUSCRIPT_NOTE_FILES = {
    "abstract.md",
    "rationale.md",
    "methods.md",
    "interpretation_notes.md",
    "figure_legends.md",
}
RESULT_ROLE_HINTS = {
    "feature": "feature",
    "marker": "feature",
    "gene": "feature",
    "protein": "feature",
    "comparison": "comparison",
    "contrast": "comparison",
    "group": "group",
    "log2_fold_change": "effect_size",
    "effect": "effect_size",
    "effect_size": "effect_size",
    "p": "p_value",
    "p_value": "p_value",
    "padj": "adjusted_p_value",
    "q_value": "adjusted_p_value",
    "fdr": "adjusted_p_value",
    "adjusted_p_value": "adjusted_p_value",
    "n": "sample_size",
    "sample_size": "sample_size",
    "figure": "figure_reference",
    "notes": "notes",
}


@dataclass
class IntakeScanResult:
    """Bundle intake scan records, warnings, and output locations."""

    project_dir: Path
    intake_dir: Path
    data_assets: list[DataAsset]
    writing_samples: list[WritingSampleAsset]
    result_tables: list[ResultTableAsset]
    sources: list[SourceAsset]
    summary: dict[str, Any]
    warnings: list[str]
    written_paths: list[Path]


@dataclass
class IntakeReportResult:
    """Bundle a rendered intake report with its JSON payload."""

    project_dir: Path
    intake_dir: Path
    report_path: Path
    json_path: Path
    data: dict[str, Any]


@dataclass
class IntakeApprovalResult:
    """Record the asset updated by an intake approval decision."""

    project_dir: Path
    asset: DataAsset
    decisions_path: Path
    registry_paths: list[Path]


def intake_dir(project_dir: Path) -> Path:
    """Return the planning intake directory for a project."""
    return Path(project_dir) / "planning" / "intake"


def data_assets_path(project_dir: Path) -> Path:
    """Return the canonical data-assets registry path."""
    return intake_dir(project_dir) / "data_assets.jsonl"


def _rel(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _utc_from_timestamp(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, UTC).replace(microsecond=0).isoformat()


def _split_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if value is None:
        return []
    text = str(value).strip()
    if not text:
        return []
    return [item.strip() for item in text.replace(",", ";").split(";") if item.strip()]


def _join_list(values: list[Any]) -> str:
    return "; ".join(str(value) for value in values if str(value).strip())


def _read_jsonl_dicts(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            loaded = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(loaded, dict):
            rows.append(loaded)
    return rows


def _read_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _backup_if_exists(path: Path) -> Path | None:
    if not path.exists():
        return None
    backup = path.with_name(f"{path.name}.bak_{run_timestamp()}")
    shutil.copy2(path, backup)
    return backup


def _write_csv(path: Path, rows: list[dict[str, Any]], columns: list[str], *, backup: bool) -> None:
    ensure_dir(path.parent)
    if backup:
        _backup_if_exists(path)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in columns})


def _write_jsonl_with_backup(path: Path, rows: list[Any], *, backup: bool) -> None:
    if backup:
        _backup_if_exists(path)
    write_jsonl(path, rows)


def _asset_type_for_path(project_dir: Path, path: Path) -> str:
    rel = _rel(path, project_dir)
    rel_lower = rel.lower()
    if rel_lower.startswith("style_corpus/"):
        return "writing_sample" if path.suffix.lower() in STYLE_SUFFIXES and not is_scaffold_file(path) else "other"
    if rel_lower.startswith("inputs/results_tables/"):
        return "result_table" if path.suffix.lower() in TABLE_SUFFIXES else "other"
    if rel_lower.startswith("inputs/source_pdfs/") and path.suffix.lower() == ".pdf":
        return "source_pdf"
    if rel_lower == "inputs/source_list.md":
        return "source_list"
    if rel_lower == "inputs/references.bib":
        return "bibtex"
    if rel_lower.startswith("inputs/") and path.name in MANUSCRIPT_NOTE_FILES:
        return "manuscript_note"
    if rel_lower.startswith("inputs/") and path.suffix.lower() in SOURCE_SUFFIXES:
        return "source_list"
    if rel_lower.startswith("feedback/") and path.suffix.lower() == ".jsonl":
        return "feedback"
    return "other"


def _scan_candidate_paths(project_dir: Path, include_outputs: bool) -> list[Path]:
    candidates: list[Path] = []
    inputs = project_dir / "inputs"
    if inputs.exists():
        candidates.extend(path for path in inputs.glob("*") if path.is_file())
        results = inputs / "results_tables"
        if results.exists():
            candidates.extend(path for path in results.rglob("*") if path.is_file())
        pdfs = inputs / "source_pdfs"
        if pdfs.exists():
            candidates.extend(path for path in pdfs.rglob("*") if path.is_file())
    style_dir = project_dir / "style_corpus"
    if style_dir.exists():
        candidates.extend(path for path in style_dir.rglob("*") if path.is_file())
    feedback_dir = project_dir / "feedback"
    if feedback_dir.exists():
        candidates.extend(path for path in feedback_dir.rglob("*.jsonl") if path.is_file())
    if include_outputs and (project_dir / "outputs").exists():
        candidates.extend(path for path in (project_dir / "outputs").rglob("*") if path.is_file())
    return sorted(set(candidates))


def _style_mode_from_path(project_dir: Path, path: Path, fallback: str = StyleMode.unknown.value) -> str:
    try:
        parts = path.relative_to(project_dir / "style_corpus").parts
    except ValueError:
        return fallback
    if len(parts) >= 2:
        return normalize_style_mode(parts[0], fallback=fallback)
    return fallback


def _sidecar_metadata(path: Path) -> dict[str, Any]:
    for candidate in [
        path.with_suffix(path.suffix + ".yaml"),
        path.with_suffix(path.suffix + ".yml"),
        path.with_suffix(path.suffix + ".json"),
        path.with_name(f"{path.stem}.metadata.yaml"),
        path.with_name(f"{path.stem}.metadata.json"),
    ]:
        if not candidate.exists():
            continue
        try:
            if candidate.suffix.lower() == ".json":
                loaded = read_json(candidate)
            else:
                loaded = read_yaml(candidate)
        except Exception:
            return {}
        return loaded if isinstance(loaded, dict) else {}
    return {}


def _section_types_for_sample(path: Path) -> list[str]:
    if path.suffix.lower() not in {".md", ".txt"}:
        return []
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []
    sections = {section for line in text.splitlines() if (section := classify_section_heading(line)) is not None}
    inferred = infer_section_type(path.name, text)
    if inferred != "unknown":
        sections.add(inferred)
    return sorted(sections)


def _document_type_for_sample(path: Path, style_mode: str, section_types: list[str]) -> str:
    name = path.stem.lower()
    if style_mode == StyleMode.coursework_explanatory.value:
        return "homework"
    if style_mode == StyleMode.response_to_reviewers.value:
        return "response_to_reviewers"
    if style_mode == StyleMode.journal_cover_letter.value:
        return "cover_letter"
    if style_mode == StyleMode.grant_or_proposal.value:
        return "grant_or_proposal"
    for section in ["abstract", "introduction", "methods", "results", "discussion", "limitations"]:
        if section in name or section in section_types:
            return section
    if "figure" in name:
        return "figure_legends"
    return "full_manuscript" if len(section_types) >= 3 else "unknown"


def _normalized_column(column: object) -> str:
    return str(column).strip().lower().replace(" ", "_").replace("-", "_")


def _detect_table_roles(path: Path) -> tuple[dict[str, str], list[str], str | None]:
    try:
        if path.suffix.lower() == ".csv":
            frame = pd.read_csv(path, nrows=25, encoding="utf-8-sig", on_bad_lines="error")
        elif path.suffix.lower() == ".tsv":
            frame = pd.read_csv(path, sep="\t", nrows=25, encoding="utf-8-sig", on_bad_lines="error")
        elif path.suffix.lower() in {".xlsx", ".xls"}:
            frame = pd.read_excel(path, nrows=25)
        else:
            return {}, [], "unsupported table format"
    except Exception as exc:
        return {}, [], f"could not read table: {exc}"
    roles: dict[str, str] = {}
    for column in frame.columns:
        normalized = _normalized_column(column)
        role = RESULT_ROLE_HINTS.get(normalized)
        if role is None and "adjusted" in normalized:
            role = "adjusted_p_value"
        elif role is None and "p_value" in normalized:
            role = "p_value"
        elif role is None and "figure" in normalized:
            role = "figure_reference"
        if role:
            roles[str(column)] = role
    key_columns = [
        column for column, role in roles.items() if role in {"feature", "comparison", "effect_size", "adjusted_p_value", "p_value"}
    ]
    warnings = []
    if frame.empty:
        warnings.append("empty table")
    if not roles:
        warnings.append("no obvious result-column roles detected")
    return roles, key_columns, "; ".join(warnings) if warnings else None


def _citation_count_for_source(path: Path) -> tuple[int, int, str]:
    if is_scaffold_file(path):
        return 0, 0, "scaffold"
    if path.suffix.lower() == ".bib":
        try:
            raw = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return 0, 0, "incomplete"
        try:
            import bibtexparser

            entries = bibtexparser.loads(raw).entries
        except Exception:
            entries = _fallback_parse_bibtex(raw)
        incomplete = 0
        for entry in entries:
            missing = [field for field in ["title", "year"] if not str(entry.get(field, "")).strip()]
            if missing:
                incomplete += 1
        return len(entries), incomplete, "parsed" if entries else "incomplete"
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return 0, 0, "incomplete"
    records = [line for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]
    return len(records), 0, "parsed" if records else "incomplete"


def _new_data_asset(project_dir: Path, path: Path) -> DataAsset:
    stat = path.stat()
    asset_type = _asset_type_for_path(project_dir, path)
    scaffold = is_scaffold_file(path)
    content_hash = sha256_file(path)
    rel = _rel(path, project_dir)
    status = "needs_metadata" if scaffold or asset_type in {"source_pdf", "result_table"} else "scanned"
    approved_for: list[str] = []
    notes = ""
    if scaffold:
        notes = "Detected scaffold/template markers; not counted as real data."
    if asset_type == "writing_sample" and not scaffold:
        approved_for = ["style_profile", "benchmark"]
    elif asset_type == "result_table" and not scaffold:
        approved_for = ["claim_generation"]
    elif asset_type in {"source_list", "bibtex"} and not scaffold:
        approved_for = ["citation_mapping"]
    return DataAsset(
        asset_id=stable_id("asset", f"{rel}:{content_hash}"),
        asset_type=asset_type,  # type: ignore[arg-type]
        path=str(path),
        relative_path=rel,
        filename=path.name,
        extension=path.suffix.lower(),
        content_hash=content_hash,
        size_bytes=int(stat.st_size),
        created_or_detected_at=utc_iso(),
        modified_at=_utc_from_timestamp(stat.st_mtime),
        status=status,  # type: ignore[arg-type]
        approved_for=approved_for,  # type: ignore[arg-type]
        notes=notes,
        metadata={
            "style_extractable": asset_type == "writing_sample" and path.suffix.lower() in STYLE_SUFFIXES,
            "style_extraction_status": "not_extracted" if asset_type == "writing_sample" else "",
        },
    )


def _merge_data_assets(current: list[DataAsset], old_rows: list[dict[str, Any]]) -> list[DataAsset]:
    old_by_id = {str(row.get("asset_id", "")): row for row in old_rows if row.get("asset_id")}
    old_by_hash = {str(row.get("content_hash", "")): row for row in old_rows if row.get("content_hash")}
    old_by_path = {str(row.get("relative_path", "")): row for row in old_rows if row.get("relative_path")}
    merged: list[DataAsset] = []
    seen_old_ids: set[str] = set()
    for asset in current:
        old = old_by_id.get(asset.asset_id) or old_by_hash.get(asset.content_hash) or old_by_path.get(asset.relative_path)
        if old:
            seen_old_ids.add(str(old.get("asset_id", "")))
            old_status = str(old.get("status", "") or asset.status)
            if old_status in {"new", "scanned", "needs_metadata", "needs_privacy_review", "approved", "excluded", "archived"}:
                asset.status = old_status  # type: ignore[assignment]
            old_approved = _split_list(old.get("approved_for"))
            if old_approved:
                asset.approved_for = old_approved  # type: ignore[assignment]
            asset.privacy_flags = _split_list(old.get("privacy_flags"))
            old_notes = str(old.get("notes", "") or "").strip()
            if old_notes:
                asset.notes = old_notes
            old_created = str(old.get("created_or_detected_at", "") or "").strip()
            if old_created:
                asset.created_or_detected_at = old_created
            old_metadata = old.get("metadata", {})
            if isinstance(old_metadata, dict):
                asset.metadata = old_metadata | asset.metadata
        merged.append(asset)
    current_keys = {asset.relative_path for asset in current}
    for row in old_rows:
        if str(row.get("asset_id", "")) in seen_old_ids:
            continue
        if str(row.get("relative_path", "")) in current_keys:
            continue
        try:
            archived = DataAsset.model_validate(row)
        except Exception:
            continue
        archived.status = "archived"
        archived.missing = True
        note = "File missing on latest intake scan."
        archived.notes = (archived.notes + " " + note).strip() if archived.notes else note
        merged.append(archived)
    return merged


def _apply_style_extraction_metadata(project_dir: Path, assets: list[DataAsset]) -> None:
    try:
        from manuscriptforge.config import load_project_config
        from manuscriptforge.style.document_extractors import (
            extraction_by_relative_path,
            style_extension_extractable,
        )

        config = load_project_config(project_dir)
        extraction_manifest = extraction_by_relative_path(project_dir)
    except Exception:
        config = None
        extraction_manifest = {}
    for asset in assets:
        if asset.asset_type != "writing_sample":
            continue
        extractable = bool(
            style_extension_extractable(asset.extension, config) if config is not None else asset.extension in STYLE_SUFFIXES
        )
        extraction = extraction_manifest.get(asset.relative_path)
        asset.metadata["style_extractable"] = extractable
        asset.metadata["style_extraction_status"] = extraction.extraction_status if extraction else "not_extracted"
        asset.metadata["style_extracted_text_path"] = extraction.extracted_text_path if extraction else ""
        asset.metadata["style_extraction_warnings"] = extraction.warnings if extraction else []
        if extraction and extraction.extraction_status == "likely_scanned_or_unextractable":
            note = "Style extraction suggests this PDF may be scanned or image-only."
            if note.lower() not in asset.notes.lower():
                asset.notes = (asset.notes + " " + note).strip() if asset.notes else note


def _existing_registry_rows(project_dir: Path) -> dict[str, list[dict[str, str]]]:
    folder = intake_dir(project_dir)
    return {
        "writing": _read_csv_rows(folder / "writing_samples_registry.csv"),
        "tables": _read_csv_rows(folder / "result_tables_registry.csv"),
        "sources": _read_csv_rows(folder / "sources_registry.csv"),
    }


def _old_row_for_asset(rows: list[dict[str, str]], asset: DataAsset) -> dict[str, str]:
    for row in rows:
        if row.get("asset_id") == asset.asset_id or row.get("file") == asset.relative_path or row.get("file_or_key") == asset.relative_path:
            return row
    return {}


def _writing_sample_for_asset(project_dir: Path, asset: DataAsset, old_rows: list[dict[str, str]]) -> WritingSampleAsset | None:
    path = project_dir / asset.relative_path
    if asset.asset_type != "writing_sample" or asset.missing or not path.exists() or is_scaffold_file(path):
        return None
    old = _old_row_for_asset(old_rows, asset)
    sidecar = _sidecar_metadata(path)
    section_types = _split_list(old.get("section_types")) or _split_list(sidecar.get("section_types")) or _section_types_for_sample(path)
    style_mode = normalize_style_mode(
        old.get("style_mode") or sidecar.get("style_mode") or _style_mode_from_path(project_dir, path)
    )
    document_type = old.get("document_type") or sidecar.get("document_type") or _document_type_for_sample(path, style_mode, section_types)
    approved_modes = _split_list(old.get("approved_for_modes")) or _split_list(sidecar.get("approved_for_modes")) or [style_mode]
    exclude_modes = _split_list(old.get("exclude_from_modes")) or _split_list(sidecar.get("exclude_from_modes"))
    return WritingSampleAsset(
        asset_id=asset.asset_id,
        style_mode=style_mode,
        document_type=str(document_type or "unknown"),  # type: ignore[arg-type]
        section_types=section_types,
        author_share=str(old.get("author_share") or sidecar.get("author_share") or "unknown"),  # type: ignore[arg-type]
        quality_for_style=str(old.get("quality_for_style") or sidecar.get("quality_for_style") or "medium"),  # type: ignore[arg-type]
        current_style_match=str(old.get("current_style_match") or sidecar.get("current_style_match") or "unknown"),  # type: ignore[arg-type]
        coauthor_edited=str(old.get("coauthor_edited") or sidecar.get("coauthor_edited") or "unknown"),  # type: ignore[arg-type]
        approved_for_modes=approved_modes,
        exclude_from_modes=exclude_modes,
        contains_sensitive_text=str(old.get("contains_sensitive_text") or sidecar.get("contains_sensitive_text") or "").lower()
        in {"true", "yes", "1"},
        privacy_reviewed=str(old.get("privacy_reviewed") or sidecar.get("privacy_reviewed") or "").lower()
        in {"true", "yes", "1"},
        notes=str(old.get("notes") or sidecar.get("notes") or ""),
    )


def _result_table_for_asset(project_dir: Path, asset: DataAsset, old_rows: list[dict[str, str]]) -> ResultTableAsset | None:
    path = project_dir / asset.relative_path
    if asset.asset_type != "result_table" or asset.missing or not path.exists():
        return None
    old = _old_row_for_asset(old_rows, asset)
    detected_roles, key_columns, warning = _detect_table_roles(path)
    schema_status = "needs_review" if warning else ("inferred" if detected_roles else "not_inferred")
    return ResultTableAsset(
        asset_id=asset.asset_id,
        table_role=str(old.get("table_role") or "unknown"),  # type: ignore[arg-type]
        analysis_type=str(old.get("analysis_type") or ""),
        primary_comparison=str(old.get("primary_comparison") or ""),
        row_unit=str(old.get("row_unit") or ""),
        key_columns=_split_list(old.get("key_columns")) or key_columns,
        detected_column_roles=detected_roles,
        schema_review_status=str(old.get("schema_review_status") or schema_status),  # type: ignore[arg-type]
        claim_generation_status=str(old.get("claim_generation_status") or "needs_review"),  # type: ignore[arg-type]
        contains_sensitive_data=str(old.get("contains_sensitive_data") or "").lower() in {"true", "yes", "1"},
        deidentified=str(old.get("deidentified") or "").lower() in {"true", "yes", "1"},
        notes=str(old.get("notes") or warning or ""),
    )


def _source_for_asset(project_dir: Path, asset: DataAsset, old_rows: list[dict[str, str]]) -> SourceAsset | None:
    path = project_dir / asset.relative_path
    if asset.asset_type not in {"source_list", "bibtex", "source_pdf"} or asset.missing:
        return None
    old = _old_row_for_asset(old_rows, asset)
    if asset.asset_type == "source_pdf":
        source_type = "pdf"
        citation_count, incomplete, status = 0, 0, "needs_review"
    elif asset.asset_type == "bibtex":
        source_type = "bibtex"
        citation_count, incomplete, status = _citation_count_for_source(path)
    else:
        source_type = "source_list"
        citation_count, incomplete, status = _citation_count_for_source(path)
    return SourceAsset(
        asset_id=asset.asset_id,
        source_type=source_type,  # type: ignore[arg-type]
        metadata_status=str(old.get("metadata_status") or status),  # type: ignore[arg-type]
        citation_count=int(old.get("citation_count") or citation_count or 0),
        incomplete_metadata_count=int(old.get("incomplete_metadata_count") or incomplete or 0),
        approved_for_citation_mapping=str(old.get("approved_for_citation_mapping") or "").lower() in {"true", "yes", "1"},
        notes=str(old.get("notes") or ""),
    )


def _counts_by_type(data_assets: list[DataAsset]) -> dict[str, int]:
    return dict(Counter(asset.asset_type for asset in data_assets if not asset.missing))


def _style_mode_inventory(writing_samples: list[WritingSampleAsset]) -> dict[str, Any]:
    by_mode: dict[str, list[WritingSampleAsset]] = defaultdict(list)
    for sample in writing_samples:
        by_mode[sample.style_mode].append(sample)
    return {
        mode: {
            "sample_count": len(samples),
            "section_counts": dict(Counter(section for sample in samples for section in sample.section_types)),
            "files": [sample.asset_id for sample in samples],
        }
        for mode, samples in sorted(by_mode.items())
    }


def _style_extraction_summary(project_dir: Path, data_assets: list[DataAsset]) -> dict[str, Any]:
    writing_assets = [asset for asset in data_assets if asset.asset_type == "writing_sample" and not asset.missing]
    status_counts = Counter(str(asset.metadata.get("style_extraction_status") or "not_extracted") for asset in writing_assets)
    extension_counts = Counter(asset.extension for asset in writing_assets)
    try:
        from manuscriptforge.style.document_extractors import (
            load_extraction_manifest,
            summarize_extractions,
        )

        manifest_rows = load_extraction_manifest(project_dir)
    except Exception:
        manifest_rows = []
    manifest_summary = summarize_extractions(manifest_rows) if manifest_rows else {}
    return {
        "writing_samples": len(writing_assets),
        "extractable_writing_samples": sum(
            1 for asset in writing_assets if bool(asset.metadata.get("style_extractable"))
        ),
        "extracted_writing_samples": int(status_counts.get("extracted", 0) + status_counts.get("partial", 0)),
        "failed_writing_samples": int(status_counts.get("failed", 0)),
        "likely_scanned_or_unextractable": int(status_counts.get("likely_scanned_or_unextractable", 0)),
        "status_counts": dict(status_counts),
        "extension_counts": dict(extension_counts),
        "manifest": manifest_summary,
    }


def _style_curation_summary(project_dir: Path) -> dict[str, Any]:
    folder = intake_dir(project_dir)
    chunk_registry = folder / "style_chunks" / "style_chunks_registry.jsonl"
    coverage_json = folder / "style_coverage_report.json"
    cards_manifest = folder / "style_cards" / "style_cards_manifest.json"
    style_dir = project_dir / "style_corpus"
    rows = _read_jsonl_dicts(chunk_registry)
    approval_counts = Counter(str(row.get("approval_status") or "unknown") for row in rows)
    coverage_strength: dict[str, str] = {}
    if coverage_json.exists():
        try:
            coverage = read_json(coverage_json)
        except Exception:
            coverage = {}
        if isinstance(coverage, dict):
            for item in coverage.get("coverage", []):
                if isinstance(item, dict):
                    coverage_strength[str(item.get("section_type"))] = str(item.get("coverage_strength"))
    card_count = 0
    if cards_manifest.exists():
        try:
            manifest = read_json(cards_manifest)
        except Exception:
            manifest = {}
        if isinstance(manifest, dict) and isinstance(manifest.get("cards"), list):
            card_count = len(manifest["cards"])
    unclassified = []
    if style_dir.exists():
        unclassified = [
            path.name
            for path in sorted(style_dir.iterdir())
            if path.is_file() and not is_scaffold_file(path)
        ]
    return {
        "chunk_registry_present": chunk_registry.exists(),
        "chunk_count": len(rows),
        "chunk_approval_status": dict(approval_counts),
        "coverage_report_present": coverage_json.exists(),
        "coverage_strength": coverage_strength,
        "style_cards_present": cards_manifest.exists(),
        "style_cards_count": card_count,
        "unclassified_style_files": unclassified,
    }


def _import_staging_summary(project_dir: Path) -> dict[str, Any]:
    try:
        from manuscriptforge.intake.import_staging import import_staging_status

        status = import_staging_status(project_dir)
    except Exception:
        return {}
    return status if isinstance(status, dict) else {}


def _append_style_curation_lines(lines: list[str], curation: dict[str, Any]) -> None:
    lines.extend(["", "## Style Curation Readiness"])
    if not curation:
        lines.append("- No style curation metadata available.")
        return
    lines.append(f"- Chunk registry present: {curation.get('chunk_registry_present', False)}")
    lines.append(f"- Registered chunks: {curation.get('chunk_count', 0)}")
    approval = curation.get("chunk_approval_status", {})
    if approval:
        lines.append("- Chunk approvals: " + ", ".join(f"{status}={count}" for status, count in sorted(approval.items())))
    else:
        lines.append("- Chunk approvals: none")
    lines.append(f"- Coverage report present: {curation.get('coverage_report_present', False)}")
    coverage = curation.get("coverage_strength", {})
    if coverage:
        lines.append("- Coverage strength: " + ", ".join(f"{section}={strength}" for section, strength in sorted(coverage.items())))
    else:
        lines.append("- Coverage strength: not generated")
    lines.append(f"- Style cards generated: {curation.get('style_cards_count', 0)}")
    unclassified = curation.get("unclassified_style_files", [])
    if unclassified:
        lines.append("- Unclassified style files: " + ", ".join(f"`{name}`" for name in unclassified))
    else:
        lines.append("- Unclassified style files: none")


def _append_import_staging_lines(lines: list[str], staging: dict[str, Any]) -> None:
    lines.extend(["", "## Import Staging"])
    if not staging or not staging.get("staging_present"):
        lines.append("- Import staging has not been prepared.")
        return
    lines.append(f"- Incoming files: {staging.get('incoming_file_count', 0)}")
    lines.append(f"- Staged file count: {staging.get('staged_file_count', 0)}")
    lines.append(f"- Candidates: {staging.get('candidate_count', 0)}")
    lines.append(f"- Pending imports: {staging.get('pending_import_count', 0)}")
    lines.append(f"- Ready imports: {staging.get('ready_import_count', staging.get('ready_files', 0))}")
    lines.append(f"- Needs metadata: {staging.get('needs_metadata_count', staging.get('files_needing_metadata', 0))}")
    lines.append(
        f"- Needs privacy review: {staging.get('needs_privacy_review_count', staging.get('files_needing_privacy_review', 0))}"
    )
    lines.append(f"- Duplicate candidates: {staging.get('duplicate_candidate_count', staging.get('duplicate_files', 0))}")
    lines.append(f"- Last import scan: {staging.get('last_import_scan') or 'none'}")
    if int(staging.get("pending_import_count", 0) or 0):
        lines.append("- Warning: Import staging contains files not yet imported into active project folders.")


def _intake_warnings(
    data_assets: list[DataAsset],
    writing_samples: list[WritingSampleAsset],
    result_tables: list[ResultTableAsset],
    sources: list[SourceAsset],
) -> list[str]:
    warnings = []
    if not result_tables:
        warnings.append("No result tables were found. Add at least one clean CSV/TSV/XLSX table for a real pilot.")
    academic = [sample for sample in writing_samples if sample.style_mode == StyleMode.academic_manuscript.value]
    if not academic:
        warnings.append("No academic_manuscript writing samples were found.")
    elif len(academic) < 5:
        warnings.append("Fewer than five academic_manuscript writing samples are available for the first pilot.")
    if not sources:
        warnings.append("No source-list, BibTeX, or source PDF assets were found.")
    for asset in data_assets:
        if "scaffold/template" in asset.notes.lower():
            warnings.append(f"Scaffold-only file detected: {asset.relative_path}")
    for table in result_tables:
        if table.schema_review_status in {"not_inferred", "needs_review"}:
            warnings.append(f"Result table needs schema review: {table.asset_id}")
    for source in sources:
        if source.metadata_status in {"scaffold", "incomplete", "needs_review"}:
            warnings.append(f"Source metadata needs review: {source.asset_id}")
    return sorted(set(warnings))


def _summary_payload(
    project_dir: Path,
    data_assets: list[DataAsset],
    writing_samples: list[WritingSampleAsset],
    result_tables: list[ResultTableAsset],
    sources: list[SourceAsset],
    warnings: list[str],
) -> dict[str, Any]:
    status_counts = Counter(asset.status for asset in data_assets)
    return {
        "project_dir": str(project_dir),
        "generated_at": utc_iso(),
        "total_assets": len(data_assets),
        "assets_by_type": _counts_by_type(data_assets),
        "assets_by_status": dict(status_counts),
        "approved_assets": int(status_counts.get("approved", 0)),
        "assets_needing_metadata": int(status_counts.get("needs_metadata", 0)),
        "assets_needing_privacy_review": int(status_counts.get("needs_privacy_review", 0)),
        "writing_sample_count": len(writing_samples),
        "result_table_count": len(result_tables),
        "source_asset_count": len(sources),
        "style_mode_inventory": _style_mode_inventory(writing_samples),
        "style_extraction_summary": _style_extraction_summary(project_dir, data_assets),
        "style_curation_summary": _style_curation_summary(project_dir),
        "import_staging_summary": _import_staging_summary(project_dir),
        "warnings": warnings,
    }


def _render_summary(summary: dict[str, Any]) -> str:
    lines = [
        "# Intake Summary",
        "",
        f"- Total assets: {summary.get('total_assets', 0)}",
        f"- Approved assets: {summary.get('approved_assets', 0)}",
        f"- Assets needing metadata: {summary.get('assets_needing_metadata', 0)}",
        f"- Assets needing privacy review: {summary.get('assets_needing_privacy_review', 0)}",
        f"- Writing samples: {summary.get('writing_sample_count', 0)}",
        f"- Result tables: {summary.get('result_table_count', 0)}",
        f"- Source assets: {summary.get('source_asset_count', 0)}",
        "",
        "## Assets By Type",
    ]
    for asset_type, count in sorted(summary.get("assets_by_type", {}).items()):
        lines.append(f"- {asset_type}: {count}")
    extraction = summary.get("style_extraction_summary", {})
    lines.extend(["", "## Style Extraction Readiness"])
    if extraction:
        lines.append(f"- Extractable writing samples: {extraction.get('extractable_writing_samples', 0)}")
        lines.append(f"- Extracted writing samples: {extraction.get('extracted_writing_samples', 0)}")
        lines.append(f"- Failed writing samples: {extraction.get('failed_writing_samples', 0)}")
        lines.append(f"- Likely scanned/unextractable: {extraction.get('likely_scanned_or_unextractable', 0)}")
    else:
        lines.append("- No writing-sample extraction metadata available.")
    _append_style_curation_lines(lines, summary.get("style_curation_summary", {}))
    _append_import_staging_lines(lines, summary.get("import_staging_summary", {}))
    lines.extend(["", "## Style Modes"])
    inventory = summary.get("style_mode_inventory", {})
    if inventory:
        for mode, values in inventory.items():
            lines.append(f"- {mode}: {values.get('sample_count', 0)} sample(s)")
    else:
        lines.append("- No writing samples detected.")
    lines.extend(["", "## Warnings"])
    lines.extend(f"- {warning}" for warning in summary.get("warnings", []) or ["None."])
    return "\n".join(lines).rstrip() + "\n"


def _render_warnings(warnings: list[str]) -> str:
    lines = ["# Intake Warnings", ""]
    lines.extend(f"- {warning}" for warning in warnings or ["None."])
    return "\n".join(lines).rstrip() + "\n"


def _render_style_inventory(summary: dict[str, Any]) -> str:
    lines = ["# Style Mode Inventory", ""]
    inventory = summary.get("style_mode_inventory", {})
    if not inventory:
        lines.append("No writing samples were detected.")
        return "\n".join(lines).rstrip() + "\n"
    for mode, values in inventory.items():
        lines.extend([f"## {mode}", "", f"- Samples: {values.get('sample_count', 0)}"])
        section_counts = values.get("section_counts", {})
        if section_counts:
            lines.append("- Sections: " + ", ".join(f"{section}={count}" for section, count in sorted(section_counts.items())))
        else:
            lines.append("- Sections: none detected")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _write_templates(folder: Path) -> list[Path]:
    templates = {
        "writing_sample_metadata_template.csv": [
            "file",
            "style_mode",
            "document_type",
            "section_types",
            "extractable",
            "extraction_status",
            "extracted_text_path",
            "extraction_warnings",
            "author_share",
            "quality_for_style",
            "current_style_match",
            "coauthor_edited",
            "approved_for_modes",
            "exclude_from_modes",
            "contains_sensitive_text",
            "privacy_reviewed",
            "use_in_pilot",
            "notes",
        ],
        "result_table_metadata_template.csv": [
            "file",
            "table_role",
            "analysis_type",
            "primary_comparison",
            "row_unit",
            "key_columns",
            "p_value_column",
            "adjusted_p_value_column",
            "effect_size_column",
            "figure_target",
            "contains_sensitive_data",
            "deidentified",
            "schema_review_status",
            "claim_generation_status",
            "notes",
        ],
        "source_metadata_template.csv": [
            "file_or_key",
            "source_type",
            "doi",
            "pmid",
            "title",
            "year",
            "manuscript_role",
            "claim_supported",
            "metadata_status",
            "approved_for_citation_mapping",
            "notes",
        ],
    }
    written: list[Path] = []
    for filename, columns in templates.items():
        path = folder / filename
        if not path.exists():
            _write_csv(path, [], columns, backup=False)
            written.append(path)
    plan_path = folder / "style_mode_plan.md"
    if not plan_path.exists():
        write_text(
            plan_path,
            "# Style Mode Plan\n\n"
            "- Put first-author manuscripts in `academic_manuscript`.\n"
            "- Put homework/explanatory assignments in `coursework_explanatory`.\n"
            "- Do not mix coursework into academic manuscript mode unless intentionally included.\n"
            "- Add reviewed samples that cover the manuscript sections relevant to your use.\n"
            "- Keep samples with different purposes in separate style modes.\n",
        )
        written.append(plan_path)
    return written


def _writing_rows(project_dir: Path, assets: list[DataAsset], samples: list[WritingSampleAsset]) -> list[dict[str, Any]]:
    assets_by_id = {asset.asset_id: asset for asset in assets}
    rows = []
    for sample in samples:
        asset = assets_by_id[sample.asset_id]
        rows.append(
            {
                "asset_id": sample.asset_id,
                "file": asset.relative_path,
                "style_mode": sample.style_mode,
                "document_type": sample.document_type,
                "section_types": _join_list(sample.section_types),
                "extractable": asset.metadata.get("style_extractable", ""),
                "extraction_status": asset.metadata.get("style_extraction_status", "not_extracted"),
                "extracted_text_path": asset.metadata.get("style_extracted_text_path", ""),
                "extraction_warnings": _join_list(asset.metadata.get("style_extraction_warnings", [])),
                "author_share": sample.author_share,
                "quality_for_style": sample.quality_for_style,
                "current_style_match": sample.current_style_match,
                "coauthor_edited": sample.coauthor_edited,
                "approved_for_modes": _join_list(sample.approved_for_modes),
                "exclude_from_modes": _join_list(sample.exclude_from_modes),
                "contains_sensitive_text": sample.contains_sensitive_text,
                "privacy_reviewed": sample.privacy_reviewed,
                "use_in_pilot": StyleMode.academic_manuscript.value in sample.approved_for_modes,
                "notes": sample.notes,
            }
        )
    return rows


def _table_rows(project_dir: Path, assets: list[DataAsset], tables: list[ResultTableAsset]) -> list[dict[str, Any]]:
    assets_by_id = {asset.asset_id: asset for asset in assets}
    rows = []
    for table in tables:
        asset = assets_by_id[table.asset_id]
        rows.append(
            {
                "asset_id": table.asset_id,
                "file": asset.relative_path,
                "table_role": table.table_role,
                "analysis_type": table.analysis_type,
                "primary_comparison": table.primary_comparison,
                "row_unit": table.row_unit,
                "key_columns": _join_list(table.key_columns),
                "detected_column_roles": json.dumps(table.detected_column_roles, sort_keys=True),
                "schema_review_status": table.schema_review_status,
                "claim_generation_status": table.claim_generation_status,
                "contains_sensitive_data": table.contains_sensitive_data,
                "deidentified": table.deidentified,
                "notes": table.notes,
            }
        )
    return rows


def _source_rows(project_dir: Path, assets: list[DataAsset], sources: list[SourceAsset]) -> list[dict[str, Any]]:
    assets_by_id = {asset.asset_id: asset for asset in assets}
    rows = []
    for source in sources:
        asset = assets_by_id[source.asset_id]
        rows.append(
            {
                "asset_id": source.asset_id,
                "file_or_key": asset.relative_path,
                "source_type": source.source_type,
                "metadata_status": source.metadata_status,
                "citation_count": source.citation_count,
                "incomplete_metadata_count": source.incomplete_metadata_count,
                "approved_for_citation_mapping": source.approved_for_citation_mapping,
                "notes": source.notes,
            }
        )
    return rows


def _write_registries(result: IntakeScanResult, *, backup: bool) -> list[Path]:
    folder = ensure_dir(result.intake_dir)
    paths = [
        folder / "data_assets.jsonl",
        folder / "writing_samples_registry.csv",
        folder / "result_tables_registry.csv",
        folder / "sources_registry.csv",
        folder / "intake_summary.md",
        folder / "intake_summary.json",
        folder / "intake_warnings.md",
        folder / "style_mode_inventory.md",
    ]
    _write_jsonl_with_backup(paths[0], result.data_assets, backup=backup)
    _write_csv(
        paths[1],
        _writing_rows(result.project_dir, result.data_assets, result.writing_samples),
        [
            "asset_id",
            "file",
            "style_mode",
            "document_type",
            "section_types",
            "extractable",
            "extraction_status",
            "extracted_text_path",
            "extraction_warnings",
            "author_share",
            "quality_for_style",
            "current_style_match",
            "coauthor_edited",
            "approved_for_modes",
            "exclude_from_modes",
            "contains_sensitive_text",
            "privacy_reviewed",
            "use_in_pilot",
            "notes",
        ],
        backup=backup,
    )
    _write_csv(
        paths[2],
        _table_rows(result.project_dir, result.data_assets, result.result_tables),
        [
            "asset_id",
            "file",
            "table_role",
            "analysis_type",
            "primary_comparison",
            "row_unit",
            "key_columns",
            "detected_column_roles",
            "schema_review_status",
            "claim_generation_status",
            "contains_sensitive_data",
            "deidentified",
            "notes",
        ],
        backup=backup,
    )
    _write_csv(
        paths[3],
        _source_rows(result.project_dir, result.data_assets, result.sources),
        [
            "asset_id",
            "file_or_key",
            "source_type",
            "metadata_status",
            "citation_count",
            "incomplete_metadata_count",
            "approved_for_citation_mapping",
            "notes",
        ],
        backup=backup,
    )
    if backup:
        for path in paths[4:]:
            _backup_if_exists(path)
    write_text(paths[4], _render_summary(result.summary))
    write_json(paths[5], result.summary)
    write_text(paths[6], _render_warnings(result.warnings))
    write_text(paths[7], _render_style_inventory(result.summary))
    paths.extend(_write_templates(folder))
    return paths


def scan_project(project_dir: Path, *, write: bool = False, include_outputs: bool = False, debug: bool = False) -> IntakeScanResult:
    """Scan a project for manuscript inputs and optionally write intake registries."""
    del debug
    project_dir = Path(project_dir)
    folder = ensure_dir(intake_dir(project_dir)) if write else intake_dir(project_dir)
    old_asset_rows = _read_jsonl_dicts(data_assets_path(project_dir))
    old_registry_rows = _existing_registry_rows(project_dir)
    current_assets = [_new_data_asset(project_dir, path) for path in _scan_candidate_paths(project_dir, include_outputs)]
    data_assets = _merge_data_assets(current_assets, old_asset_rows)
    _apply_style_extraction_metadata(project_dir, data_assets)
    writing_samples = [
        sample
        for asset in data_assets
        if (sample := _writing_sample_for_asset(project_dir, asset, old_registry_rows["writing"])) is not None
    ]
    result_tables = [
        table
        for asset in data_assets
        if (table := _result_table_for_asset(project_dir, asset, old_registry_rows["tables"])) is not None
    ]
    sources = [
        source
        for asset in data_assets
        if (source := _source_for_asset(project_dir, asset, old_registry_rows["sources"])) is not None
    ]
    warnings = _intake_warnings(data_assets, writing_samples, result_tables, sources)
    summary = _summary_payload(project_dir, data_assets, writing_samples, result_tables, sources, warnings)
    result = IntakeScanResult(
        project_dir=project_dir,
        intake_dir=folder,
        data_assets=data_assets,
        writing_samples=writing_samples,
        result_tables=result_tables,
        sources=sources,
        summary=summary,
        warnings=warnings,
        written_paths=[],
    )
    if write:
        result.written_paths = _write_registries(result, backup=bool(old_asset_rows or any(old_registry_rows.values())))
    return result


def _load_scan_or_scan(project_dir: Path) -> IntakeScanResult:
    if data_assets_path(project_dir).exists():
        return scan_project(project_dir, write=False)
    return scan_project(project_dir, write=True)


def _active_mode_from_config(project_dir: Path) -> str:
    data = read_yaml(project_dir / "project.yaml")
    style = data.get("style", {}) if isinstance(data.get("style", {}), dict) else {}
    return normalize_style_mode(str(style.get("active_mode") or StyleMode.academic_manuscript.value))


def build_intake_report(project_dir: Path, *, mode: str | None = None, debug: bool = False) -> IntakeReportResult:
    """Write a human-readable intake readiness report."""
    del debug
    project_dir = Path(project_dir)
    scan = _load_scan_or_scan(project_dir)
    selected_mode = normalize_style_mode(mode or _active_mode_from_config(project_dir))
    mode_samples = [sample for sample in scan.writing_samples if sample.style_mode == selected_mode]
    schema_status = Counter(table.schema_review_status for table in scan.result_tables)
    source_status = Counter(source.metadata_status for source in scan.sources)
    readiness = {
        "mode": selected_mode,
        "has_methods": (project_dir / "inputs" / "methods.md").exists(),
        "has_interpretation_notes": (project_dir / "inputs" / "interpretation_notes.md").exists(),
        "has_result_table": bool(scan.result_tables),
        "has_sources": bool(scan.sources),
        "mode_sample_count": len(mode_samples),
        "enough_academic_for_pilot": selected_mode == StyleMode.academic_manuscript.value and len(mode_samples) >= 5,
    }
    data = {
        **scan.summary,
        "selected_mode": selected_mode,
        "readiness": readiness,
        "result_table_schema_status": dict(schema_status),
        "source_metadata_status": dict(source_status),
        "section_coverage_by_mode": {
            mode_name: values.get("section_counts", {})
            for mode_name, values in scan.summary.get("style_mode_inventory", {}).items()
        },
    }
    lines = [
        "# Intake Report",
        "",
        f"- Total assets: {data['total_assets']}",
        f"- Approved assets: {data['approved_assets']}",
        f"- Needs metadata: {data['assets_needing_metadata']}",
        f"- Needs privacy review: {data['assets_needing_privacy_review']}",
        f"- Result tables: {data['result_table_count']}",
        f"- Source assets: {data['source_asset_count']}",
        f"- Selected style mode: `{selected_mode}`",
        f"- Samples in selected mode: {len(mode_samples)}",
        f"- Enough academic samples for first pilot: {readiness['enough_academic_for_pilot']}",
        "",
        "## Result Table Schema Status",
    ]
    if schema_status:
        lines.extend(f"- {status}: {count}" for status, count in sorted(schema_status.items()))
    else:
        lines.append("- No result tables found.")
    lines.extend(["", "## Source Metadata Status"])
    if source_status:
        lines.extend(f"- {status}: {count}" for status, count in sorted(source_status.items()))
    else:
        lines.append("- No source metadata assets found.")
    lines.extend(["", "## Style Corpus By Mode"])
    for mode_name, values in sorted(scan.summary.get("style_mode_inventory", {}).items()):
        lines.append(f"- {mode_name}: {values.get('sample_count', 0)} sample(s)")
    if not scan.summary.get("style_mode_inventory"):
        lines.append("- No writing samples detected.")
    extraction = scan.summary.get("style_extraction_summary", {})
    lines.extend(["", "## Style Extraction Readiness"])
    if extraction:
        lines.append(f"- Extractable writing samples: {extraction.get('extractable_writing_samples', 0)}")
        lines.append(f"- Extracted writing samples: {extraction.get('extracted_writing_samples', 0)}")
        lines.append(f"- Failed writing samples: {extraction.get('failed_writing_samples', 0)}")
        lines.append(f"- Likely scanned/unextractable: {extraction.get('likely_scanned_or_unextractable', 0)}")
        status_counts = extraction.get("status_counts", {})
        if status_counts:
            lines.append("- Status counts: " + ", ".join(f"{status}={count}" for status, count in sorted(status_counts.items())))
    else:
        lines.append("- No extraction metadata available. Run `extract-style-corpus` after adding style files.")
    _append_style_curation_lines(lines, scan.summary.get("style_curation_summary", {}))
    _append_import_staging_lines(lines, scan.summary.get("import_staging_summary", {}))
    lines.extend(["", "## Warnings"])
    lines.extend(f"- {warning}" for warning in scan.warnings or ["None."])

    folder = ensure_dir(scan.intake_dir)
    report_path = folder / "intake_report.md"
    json_path = folder / "intake_report.json"
    _backup_if_exists(report_path)
    _backup_if_exists(json_path)
    write_text(report_path, "\n".join(lines).rstrip() + "\n")
    write_json(json_path, data)
    return IntakeReportResult(project_dir, folder, report_path, json_path, data)


def build_style_mode_summary(project_dir: Path, *, mode: str | None = None, debug: bool = False) -> IntakeReportResult:
    """Write a style-mode-specific inventory and warning report."""
    del debug
    project_dir = Path(project_dir)
    scan = _load_scan_or_scan(project_dir)
    selected_mode = normalize_style_mode(mode or _active_mode_from_config(project_dir))
    selected = [sample for sample in scan.writing_samples if sample.style_mode == selected_mode]
    excluded = [
        sample
        for sample in scan.writing_samples
        if selected_mode in sample.exclude_from_modes or sample.quality_for_style == "exclude"
    ]
    mode_counts = Counter(sample.style_mode for sample in scan.writing_samples)
    section_counts = Counter(section for sample in selected for section in sample.section_types)
    assets_by_id = {asset.asset_id: asset for asset in scan.data_assets}
    extraction_status_by_mode: dict[str, Counter[str]] = defaultdict(Counter)
    for sample in scan.writing_samples:
        asset = assets_by_id.get(sample.asset_id)
        status = str(asset.metadata.get("style_extraction_status") if asset else "not_extracted") or "not_extracted"
        extraction_status_by_mode[sample.style_mode][status] += 1
    selected_extraction_status = dict(extraction_status_by_mode.get(selected_mode, Counter()))
    warnings: list[str] = []
    if not selected:
        warnings.append(f"No `{selected_mode}` writing samples are available.")
    elif not any(selected_extraction_status.get(status, 0) for status in ["extracted", "partial"]):
        warnings.append(f"No `{selected_mode}` writing samples have extracted usable text yet.")
    if selected_mode == StyleMode.academic_manuscript.value and mode_counts.get(StyleMode.coursework_explanatory.value, 0):
        warnings.append("Coursework samples exist; keep them excluded from academic manuscript drafting unless intentional.")
    if selected_mode == StyleMode.academic_manuscript.value and len(selected) < 5:
        warnings.append("Fewer than five academic manuscript samples are available.")
    data = {
        "generated_at": utc_iso(),
        "selected_mode": selected_mode,
        "mode_counts": dict(mode_counts),
        "selected_sample_count": len(selected),
        "section_counts": dict(section_counts),
        "extraction_status_by_mode": {mode_name: dict(counts) for mode_name, counts in extraction_status_by_mode.items()},
        "selected_extraction_status": selected_extraction_status,
        "style_curation_summary": scan.summary.get("style_curation_summary", {}),
        "selected_asset_ids": [sample.asset_id for sample in selected],
        "excluded_asset_ids": [sample.asset_id for sample in excluded],
        "warnings": warnings,
    }
    lines = [
        "# Style Mode Summary",
        "",
        f"- Selected mode: `{selected_mode}`",
        f"- Samples in selected mode: {len(selected)}",
        "",
        "## Samples By Mode",
    ]
    if mode_counts:
        lines.extend(f"- {mode_name}: {count}" for mode_name, count in sorted(mode_counts.items()))
    else:
        lines.append("- No writing samples detected.")
    lines.extend(["", "## Section Coverage"])
    if section_counts:
        lines.extend(f"- {section}: {count}" for section, count in sorted(section_counts.items()))
    else:
        lines.append("- No sections detected for this mode.")
    lines.extend(["", "## Extraction Status"])
    if selected_extraction_status:
        lines.extend(f"- {status}: {count}" for status, count in sorted(selected_extraction_status.items()))
    else:
        lines.append("- No extraction status recorded for this mode.")
    _append_style_curation_lines(lines, data.get("style_curation_summary", {}))
    lines.extend(["", "## Excluded From Active Mode"])
    lines.extend(f"- {sample.asset_id}" for sample in excluded or [])
    if not excluded:
        lines.append("- None.")
    lines.extend(["", "## Warnings"])
    lines.extend(f"- {warning}" for warning in warnings or ["None."])

    folder = ensure_dir(scan.intake_dir)
    report_path = folder / "style_mode_summary.md"
    json_path = folder / "style_mode_summary.json"
    _backup_if_exists(report_path)
    _backup_if_exists(json_path)
    write_text(report_path, "\n".join(lines).rstrip() + "\n")
    write_json(json_path, data)
    return IntakeReportResult(project_dir, folder, report_path, json_path, data)


def _rewrite_registries_after_asset_change(project_dir: Path, assets: list[DataAsset]) -> list[Path]:
    old_registry_rows = _existing_registry_rows(project_dir)
    _apply_style_extraction_metadata(project_dir, assets)
    writing_samples = [
        sample
        for asset in assets
        if (sample := _writing_sample_for_asset(project_dir, asset, old_registry_rows["writing"])) is not None
    ]
    result_tables = [
        table
        for asset in assets
        if (table := _result_table_for_asset(project_dir, asset, old_registry_rows["tables"])) is not None
    ]
    sources = [
        source
        for asset in assets
        if (source := _source_for_asset(project_dir, asset, old_registry_rows["sources"])) is not None
    ]
    warnings = _intake_warnings(assets, writing_samples, result_tables, sources)
    summary = _summary_payload(project_dir, assets, writing_samples, result_tables, sources, warnings)
    result = IntakeScanResult(project_dir, intake_dir(project_dir), assets, writing_samples, result_tables, sources, summary, warnings, [])
    return _write_registries(result, backup=True)


def _apply_writing_mode_decision(project_dir: Path, asset: DataAsset, for_style_mode: str | None, exclude: bool) -> None:
    if not for_style_mode or asset.asset_type != "writing_sample":
        return
    mode = normalize_style_mode(for_style_mode)
    path = intake_dir(project_dir) / "writing_samples_registry.csv"
    rows = _read_csv_rows(path)
    changed = False
    for row in rows:
        if row.get("asset_id") != asset.asset_id and row.get("file") != asset.relative_path:
            continue
        approved_modes = _split_list(row.get("approved_for_modes"))
        excluded_modes = _split_list(row.get("exclude_from_modes"))
        if exclude:
            if mode not in excluded_modes:
                excluded_modes.append(mode)
            approved_modes = [item for item in approved_modes if item != mode]
        elif mode not in approved_modes:
            approved_modes.append(mode)
        row["approved_for_modes"] = _join_list(approved_modes)
        row["exclude_from_modes"] = _join_list(excluded_modes)
        changed = True
    if changed:
        columns = list(rows[0].keys()) if rows else []
        _write_csv(path, rows, columns, backup=True)


def _find_asset(assets: list[DataAsset], asset_id: str | None, path: Path | None, project_dir: Path, asset_type: str | None) -> DataAsset:
    if asset_id:
        for asset in assets:
            if asset.asset_id == asset_id:
                return asset
        raise ValueError(f"No intake asset found with asset_id '{asset_id}'.")
    if path is not None:
        resolved = path if path.is_absolute() else project_dir / path
        rel = _rel(resolved, project_dir)
        for asset in assets:
            if (asset.relative_path == rel or Path(asset.path) == resolved) and (
                asset_type is None or asset.asset_type == asset_type
            ):
                return asset
        raise ValueError(f"No intake asset found for path '{path}'. Run intake-scan --write first.")
    raise ValueError("Pass --asset-id or --path to choose an asset.")


def approve_asset(
    project_dir: Path,
    *,
    asset_id: str | None = None,
    path: Path | None = None,
    asset_type: str | None = None,
    for_style_mode: str | None = None,
    approve_for: str | None = None,
    exclude: bool = False,
    note: str | None = None,
    debug: bool = False,
) -> IntakeApprovalResult:
    """Apply one approval/exclusion decision to the intake registries."""
    del debug
    project_dir = Path(project_dir)
    if not data_assets_path(project_dir).exists():
        scan_project(project_dir, write=True)
    assets = [DataAsset.model_validate(row) for row in _read_jsonl_dicts(data_assets_path(project_dir))]
    asset = _find_asset(assets, asset_id, path, project_dir, asset_type)
    if exclude:
        asset.status = "excluded"
    else:
        asset.status = "approved"
    if approve_for and approve_for not in asset.approved_for:
        asset.approved_for.append(approve_for)  # type: ignore[arg-type]
    if note:
        asset.notes = (asset.notes + " " + note).strip() if asset.notes else note
    _apply_writing_mode_decision(project_dir, asset, for_style_mode, exclude)
    registry_paths = _rewrite_registries_after_asset_change(project_dir, assets)

    decision = {
        "decision_id": stable_id("dec", sha256_text(f"{asset.asset_id}:{utc_iso()}:{note or ''}")),
        "created_at": utc_iso(),
        "asset_id": asset.asset_id,
        "relative_path": asset.relative_path,
        "asset_type": asset.asset_type,
        "status": asset.status,
        "approve_for": approve_for,
        "for_style_mode": normalize_style_mode(for_style_mode) if for_style_mode else None,
        "exclude": exclude,
        "note": note or "",
    }
    decisions_path = ensure_dir(intake_dir(project_dir)) / "intake_decisions.jsonl"
    with decisions_path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(decision, sort_keys=True) + "\n")
    return IntakeApprovalResult(project_dir, asset, decisions_path, registry_paths)


def load_eligible_writing_samples(
    project_dir: Path,
    *,
    active_mode: str,
    include_modes: list[str],
    exclude_modes: list[str],
) -> dict[str, dict[str, Any]] | None:
    """Return registry-approved style sample metadata, or None when no registry exists."""
    if not data_assets_path(project_dir).exists():
        return None
    active_mode = normalize_style_mode(active_mode)
    assets = [DataAsset.model_validate(row) for row in _read_jsonl_dicts(data_assets_path(project_dir))]
    writing_rows = _read_csv_rows(intake_dir(project_dir) / "writing_samples_registry.csv")
    writing_by_id = {row.get("asset_id", ""): row for row in writing_rows}
    include = {normalize_style_mode(mode) for mode in (include_modes or [active_mode])}
    exclude = {normalize_style_mode(mode) for mode in exclude_modes}
    eligible: dict[str, dict[str, Any]] = {}
    for asset in assets:
        if asset.asset_type != "writing_sample" or asset.status == "excluded" or asset.missing:
            continue
        if "style_profile" not in asset.approved_for and asset.status != "approved":
            continue
        row = writing_by_id.get(asset.asset_id, {})
        style_mode = normalize_style_mode(row.get("style_mode") or StyleMode.unknown.value)
        approved_modes = {normalize_style_mode(mode) for mode in _split_list(row.get("approved_for_modes"))}
        excluded_modes = {normalize_style_mode(mode) for mode in _split_list(row.get("exclude_from_modes"))}
        if style_mode in exclude or style_mode not in include:
            continue
        if active_mode in excluded_modes:
            continue
        if approved_modes and active_mode not in approved_modes and style_mode != active_mode:
            continue
        path = project_dir / asset.relative_path
        root = project_dir.absolute()
        try:
            path.absolute().relative_to(root / "style_corpus")
            path.resolve().relative_to((root / "style_corpus").resolve())
        except ValueError as exc:
            raise ValueError(
                f"Registered style source must stay inside style_corpus: {asset.relative_path}"
            ) from exc
        current = root
        for part in path.absolute().relative_to(root).parts:
            current = current / part
            if current.is_symlink() or (
                current.exists()
                and getattr(current.lstat(), "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
            ):
                raise ValueError(f"Linked style paths are not allowed: {current}")
        if not path.exists() or is_scaffold_file(path):
            continue
        eligible[asset.relative_path] = {
            "asset_id": asset.asset_id,
            "style_mode": style_mode,
            "document_type": row.get("document_type") or "unknown",
            "section_types": _split_list(row.get("section_types")),
            "content_hash": asset.content_hash,
        }
    return eligible
