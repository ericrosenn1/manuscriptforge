from __future__ import annotations

import csv
import json
import shutil
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from manuscriptforge.config import load_project_config
from manuscriptforge.export.excel_reporter import write_rows_xlsx, write_workbook
from manuscriptforge.ingest.style_ingest import build_style_chunks
from manuscriptforge.intake.scaffold import is_scaffold_file, text_has_scaffold_markers
from manuscriptforge.models.project import ProjectConfig
from manuscriptforge.models.style import (
    StyleChunk,
    StyleChunkRegistryRow,
    StyleMode,
    normalize_style_mode,
)
from manuscriptforge.style.document_extractors import (
    SUCCESSFUL_EXTRACTION_STATUSES,
    extract_style_corpus,
)
from manuscriptforge.style.features import (
    ACADEMIC_VERBS,
    HEDGING_WORDS,
    citation_patterns,
    limitation_phrases,
)
from manuscriptforge.utils.dates import run_timestamp, utc_iso
from manuscriptforge.utils.ids import slugify, stable_id
from manuscriptforge.utils.io import (
    ensure_dir,
    write_json,
    write_jsonl,
    write_text,
    write_yaml,
)
from manuscriptforge.utils.text import (
    count_terms,
    distribution,
    find_transition_counts,
    split_paragraphs,
    split_sentences,
    word_tokens,
)

CHUNK_APPROVED_USES = ["style_profile", "exemplar_retrieval", "benchmark", "style_eval"]
STYLE_CARD_SECTIONS = [
    "abstract",
    "introduction",
    "methods",
    "results",
    "discussion",
    "limitations",
    "figure_legends",
]


@dataclass
class StyleChunkRegistryResult:
    """Describe files and rows produced by a chunk-registry rebuild."""

    project_dir: Path
    registry_dir: Path
    rows: list[StyleChunkRegistryRow]
    warnings: list[str]
    paths: dict[str, Path]

    @property
    def summary(self) -> dict[str, Any]:
        return chunk_registry_summary(self.rows, self.warnings)


@dataclass
class StyleChunkReportResult:
    """Describe chunk quality report outputs."""

    project_dir: Path
    report_path: Path
    json_path: Path
    xlsx_path: Path
    data: dict[str, Any]


@dataclass
class StyleChunkApprovalResult:
    """Describe a chunk approval/exclusion decision."""

    project_dir: Path
    matched_rows: list[StyleChunkRegistryRow]
    decisions_path: Path
    registry_paths: dict[str, Path]


@dataclass
class StyleCoverageReportResult:
    """Describe style coverage report outputs."""

    project_dir: Path
    report_path: Path
    json_path: Path
    matrix_path: Path
    data: dict[str, Any]


@dataclass
class StyleCardsResult:
    """Describe generated style-card files."""

    project_dir: Path
    cards_dir: Path
    manifest_path: Path
    card_paths: list[Path]
    data: dict[str, Any]


@dataclass
class PrepStyleModesResult:
    """Describe style-mode scaffold outputs."""

    project_dir: Path
    created: list[Path]
    existing: list[Path]
    unclassified_files: list[str]


@dataclass
class JournalAdapterResult:
    """Describe journal adapter scaffold outputs."""

    project_dir: Path
    journal: str
    adapter_dir: Path
    created: list[Path]
    existing: list[Path]


def _load_config(project_dir: Path) -> ProjectConfig:
    return load_project_config(project_dir)


def style_chunks_dir(project_dir: Path) -> Path:
    """Return the chunk curation registry directory."""
    return Path(project_dir) / "planning" / "intake" / "style_chunks"


def chunk_registry_csv_path(project_dir: Path) -> Path:
    """Return the chunk registry CSV path."""
    return style_chunks_dir(project_dir) / "style_chunks_registry.csv"


def chunk_registry_jsonl_path(project_dir: Path) -> Path:
    """Return the chunk registry JSONL path."""
    return style_chunks_dir(project_dir) / "style_chunks_registry.jsonl"


def chunk_decisions_path(project_dir: Path) -> Path:
    """Return the append-only chunk decision log path."""
    return style_chunks_dir(project_dir) / "style_chunk_decisions.jsonl"


def chunk_review_path(project_dir: Path) -> Path:
    """Return the chunk review Markdown path."""
    return style_chunks_dir(project_dir) / "style_chunk_review.md"


def chunk_review_xlsx_path(project_dir: Path) -> Path:
    """Return the chunk review XLSX path."""
    return style_chunks_dir(project_dir) / "style_chunk_review.xlsx"


def chunk_warnings_path(project_dir: Path) -> Path:
    """Return the chunk warning Markdown path."""
    return style_chunks_dir(project_dir) / "style_chunk_warnings.md"


def chunk_report_md_path(project_dir: Path) -> Path:
    """Return the chunk quality report Markdown path."""
    return style_chunks_dir(project_dir) / "style_chunk_report.md"


def chunk_report_json_path(project_dir: Path) -> Path:
    """Return the chunk quality report JSON path."""
    return style_chunks_dir(project_dir) / "style_chunk_report.json"


def chunk_report_xlsx_path(project_dir: Path) -> Path:
    """Return the chunk quality report XLSX path."""
    return style_chunks_dir(project_dir) / "style_chunk_report.xlsx"


def coverage_report_md_path(project_dir: Path) -> Path:
    """Return the style coverage report Markdown path."""
    return Path(project_dir) / "planning" / "intake" / "style_coverage_report.md"


def coverage_report_json_path(project_dir: Path) -> Path:
    """Return the style coverage report JSON path."""
    return Path(project_dir) / "planning" / "intake" / "style_coverage_report.json"


def coverage_matrix_path(project_dir: Path) -> Path:
    """Return the style coverage matrix CSV path."""
    return Path(project_dir) / "planning" / "intake" / "style_coverage_matrix.csv"


def style_cards_dir(project_dir: Path) -> Path:
    """Return the style card output directory."""
    return Path(project_dir) / "planning" / "intake" / "style_cards"


def style_cards_manifest_path(project_dir: Path) -> Path:
    """Return the style card manifest path."""
    return style_cards_dir(project_dir) / "style_cards_manifest.json"


def _rel(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


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


def _backup_if_exists(path: Path) -> Path | None:
    if not path.exists():
        return None
    backup = path.with_name(f"{path.name}.bak_{run_timestamp()}")
    shutil.copy2(path, backup)
    return backup


def _read_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _read_jsonl_rows(path: Path) -> list[dict[str, Any]]:
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


def _write_csv(path: Path, rows: list[dict[str, Any]], *, backup: bool = True) -> None:
    ensure_dir(path.parent)
    if backup:
        _backup_if_exists(path)
    columns = [
        "chunk_id",
        "source_asset_id",
        "source_file",
        "source_extension",
        "extracted_text_path",
        "style_mode",
        "section_type",
        "chunk_index",
        "word_count",
        "sentence_count",
        "paragraph_count",
        "content_hash",
        "extraction_status",
        "section_confidence",
        "approval_status",
        "approved_for",
        "warning_flags",
        "notes",
        "missing",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in columns})


def _write_generic_csv(
    path: Path,
    rows: list[dict[str, Any]],
    columns: list[str],
    *,
    backup: bool = False,
) -> None:
    ensure_dir(path.parent)
    if backup:
        _backup_if_exists(path)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in columns})


def _row_to_csv_data(row: StyleChunkRegistryRow) -> dict[str, Any]:
    data = row.model_dump(mode="json")
    data["approved_for"] = _join_list(row.approved_for)
    data["warning_flags"] = _join_list(row.warning_flags)
    return data


def _row_from_dict(data: dict[str, Any]) -> StyleChunkRegistryRow | None:
    prepared = dict(data)
    prepared["approved_for"] = _split_list(prepared.get("approved_for"))
    prepared["warning_flags"] = _split_list(prepared.get("warning_flags"))
    if isinstance(prepared.get("missing"), str):
        prepared["missing"] = str(prepared.get("missing")).lower() in {"true", "1", "yes"}
    try:
        return StyleChunkRegistryRow.model_validate(prepared)
    except Exception:
        return None


def load_chunk_registry(project_dir: Path) -> list[StyleChunkRegistryRow]:
    """Load chunk registry rows from JSONL or CSV."""
    jsonl = chunk_registry_jsonl_path(project_dir)
    csv_path = chunk_registry_csv_path(project_dir)
    raw_rows = _read_jsonl_rows(jsonl) if jsonl.exists() else _read_csv_rows(csv_path)
    rows: list[StyleChunkRegistryRow] = []
    for raw in raw_rows:
        row = _row_from_dict(raw)
        if row is not None:
            rows.append(row)
    return rows


def _old_decisions(project_dir: Path) -> dict[str, StyleChunkRegistryRow]:
    rows = load_chunk_registry(project_dir)
    return {row.chunk_id: row for row in rows}


def _samples_from_extraction(project_dir: Path, mode: str | None, force: bool) -> list[dict[str, Any]]:
    extraction = extract_style_corpus(project_dir, mode=mode, force=force)
    samples: list[dict[str, Any]] = []
    for item in extraction.extractions:
        if item.extraction_status not in SUCCESSFUL_EXTRACTION_STATUSES:
            continue
        text = extraction.texts_by_relative_path.get(item.relative_path, "")
        if not text.strip():
            continue
        samples.append(
            {
                "source_path": item.relative_path,
                "text": text,
                "content_hash": item.content_hash,
                "style_mode": item.style_mode,
                "asset_id": item.asset_id,
                "source_extension": item.extension,
                "extraction_status": item.extraction_status,
                "extracted_text_path": item.extracted_text_path,
                "extraction_warnings": item.warnings,
            }
        )
    return samples


def _section_confidence(chunk: StyleChunk) -> str:
    if chunk.section_type == "unknown":
        return "low"
    if chunk.token_estimate < 25:
        return "medium"
    if chunk.section_type in {"title"}:
        return "medium"
    return "high"


def _warning_flags(chunk: StyleChunk, config: ProjectConfig) -> list[str]:
    flags: list[str] = []
    text_lower = chunk.text.lower()
    if chunk.token_estimate < config.style.extraction.min_chunk_words:
        flags.append("very_short")
    if chunk.token_estimate > config.style.extraction.max_chunk_words:
        flags.append("very_long")
    if chunk.section_type == "references" or "references\n" in text_lower or "bibliography" in text_lower:
        flags.append("references_or_bibliography_leak")
    if text_has_scaffold_markers(chunk.text):
        flags.append("scaffold_or_todo_text")
    if _section_confidence(chunk) in {"low", "unknown"}:
        flags.append("section_uncertain")
    return sorted(set(flags))


def _default_status(flags: list[str]) -> str:
    if "references_or_bibliography_leak" in flags or "scaffold_or_todo_text" in flags:
        return "excluded"
    if flags:
        return "needs_review"
    return "new"


def _registry_rows_from_chunks(
    project_dir: Path,
    chunks: list[StyleChunk],
    *,
    old_by_chunk: dict[str, StyleChunkRegistryRow],
) -> list[StyleChunkRegistryRow]:
    config = _load_config(project_dir)
    source_counts: Counter[str] = Counter()
    content_counts = Counter(chunk.content_hash for chunk in chunks)
    rows: list[StyleChunkRegistryRow] = []
    for chunk in chunks:
        source_counts[chunk.source_file] += 1
        flags = _warning_flags(chunk, config)
        if content_counts[chunk.content_hash] > 1:
            flags.append("repeated_boilerplate")
        # Equal text in another source is a separate review decision.
        old = old_by_chunk.get(chunk.chunk_id)
        row = StyleChunkRegistryRow(
            chunk_id=chunk.chunk_id,
            source_asset_id=chunk.source_asset_id,
            source_file=chunk.source_file,
            source_extension=chunk.source_extension,
            extracted_text_path=chunk.extracted_text_path,
            style_mode=chunk.style_mode,
            section_type=chunk.section_type,
            chunk_index=int(source_counts[chunk.source_file]),
            word_count=chunk.token_estimate,
            sentence_count=chunk.sentence_count,
            paragraph_count=chunk.paragraph_count,
            content_hash=chunk.content_hash,
            extraction_status=chunk.extraction_status,
            section_confidence=_section_confidence(chunk),  # type: ignore[arg-type]
            approval_status=_default_status(flags),  # type: ignore[arg-type]
            approved_for=[],
            warning_flags=sorted(set(flags)),
        )
        if old is not None:
            row.approval_status = old.approval_status
            row.approved_for = old.approved_for
            row.notes = old.notes
        rows.append(row)
    current_ids = {row.chunk_id for row in rows}
    for old in old_by_chunk.values():
        if old.chunk_id in current_ids:
            continue
        old.missing = True
        old.warning_flags = sorted(set(old.warning_flags + ["missing_source_or_chunk"]))
        rows.append(old)
    return rows


def _registry_warnings(rows: list[StyleChunkRegistryRow]) -> list[str]:
    warnings: list[str] = []
    for row in rows:
        if row.warning_flags:
            warnings.append(f"{row.chunk_id}: {', '.join(row.warning_flags)} ({row.source_file})")
    return warnings


def _render_chunk_review(rows: list[StyleChunkRegistryRow], warnings: list[str]) -> str:
    summary = chunk_registry_summary(rows, warnings)
    lines = [
        "# Style Chunk Review",
        "",
        f"- Total chunks: {summary['total_chunks']}",
        f"- Approved chunks: {summary['approval_status'].get('approved', 0)}",
        f"- Needs review: {summary['approval_status'].get('needs_review', 0)}",
        f"- Excluded: {summary['approval_status'].get('excluded', 0)}",
        "",
        "## By Section",
    ]
    for section, count in sorted(summary["section_distribution"].items()):
        lines.append(f"- {section}: {count}")
    if not summary["section_distribution"]:
        lines.append("- No chunks found.")
    lines.extend(["", "## Review Queue"])
    review_rows = [row for row in rows if row.approval_status in {"needs_review", "new"} or row.warning_flags]
    for row in review_rows[:100]:
        flags = ", ".join(row.warning_flags) or "none"
        lines.append(
            f"- `{row.chunk_id}` {row.section_type}, {row.word_count} words, "
            f"{row.approval_status}, flags={flags}, source=`{row.source_file}`"
        )
    if not review_rows:
        lines.append("- No chunks currently need review.")
    return "\n".join(lines).rstrip() + "\n"


def _render_chunk_warnings(warnings: list[str]) -> str:
    lines = ["# Style Chunk Warnings", ""]
    lines.extend(f"- {warning}" for warning in warnings or ["None."])
    return "\n".join(lines).rstrip() + "\n"


def _write_chunk_registry_files(project_dir: Path, rows: list[StyleChunkRegistryRow], warnings: list[str]) -> dict[str, Path]:
    registry_dir = ensure_dir(style_chunks_dir(project_dir))
    paths = {
        "csv": chunk_registry_csv_path(project_dir),
        "jsonl": chunk_registry_jsonl_path(project_dir),
        "decisions": chunk_decisions_path(project_dir),
        "review": chunk_review_path(project_dir),
        "xlsx": chunk_review_xlsx_path(project_dir),
        "warnings": chunk_warnings_path(project_dir),
    }
    _write_csv(paths["csv"], [_row_to_csv_data(row) for row in rows], backup=True)
    if paths["jsonl"].exists():
        _backup_if_exists(paths["jsonl"])
    write_jsonl(paths["jsonl"], rows)
    if paths["review"].exists():
        _backup_if_exists(paths["review"])
    write_text(paths["review"], _render_chunk_review(rows, warnings))
    if paths["warnings"].exists():
        _backup_if_exists(paths["warnings"])
    write_text(paths["warnings"], _render_chunk_warnings(warnings))
    if not paths["decisions"].exists():
        write_text(paths["decisions"], "")
    write_rows_xlsx(paths["xlsx"], [_row_to_csv_data(row) for row in rows], "style_chunks")
    ensure_dir(registry_dir)
    return paths


def chunk_registry_summary(rows: list[StyleChunkRegistryRow], warnings: list[str] | None = None) -> dict[str, Any]:
    """Summarize chunk registry rows."""
    return {
        "total_chunks": len([row for row in rows if not row.missing]),
        "missing_chunks": len([row for row in rows if row.missing]),
        "approval_status": dict(Counter(row.approval_status for row in rows)),
        "section_distribution": dict(Counter(row.section_type for row in rows if not row.missing)),
        "mode_distribution": dict(Counter(row.style_mode for row in rows if not row.missing)),
        "warning_count": len(warnings or []),
    }


def build_style_chunk_registry(
    project_dir: Path,
    *,
    mode: str | None = None,
    force: bool = False,
) -> StyleChunkRegistryResult:
    """Build and persist the chunk-level style registry."""
    project_dir = Path(project_dir)
    selected_mode = normalize_style_mode(mode) if mode else None
    samples = _samples_from_extraction(project_dir, selected_mode, force)
    chunks = build_style_chunks(project_dir, samples)
    old_by_chunk = _old_decisions(project_dir)
    rows = _registry_rows_from_chunks(project_dir, chunks, old_by_chunk=old_by_chunk)
    warnings = _registry_warnings(rows)
    paths = _write_chunk_registry_files(project_dir, rows, warnings)
    return StyleChunkRegistryResult(project_dir, style_chunks_dir(project_dir), rows, warnings, paths)


def _registry_by_chunk(project_dir: Path) -> dict[str, StyleChunkRegistryRow]:
    return {row.chunk_id: row for row in load_chunk_registry(project_dir)}


def apply_chunk_curation_to_profile_chunks(
    project_dir: Path,
    chunks: list[StyleChunk],
    *,
    required_use: str = "style_profile",
) -> tuple[list[StyleChunk], dict[str, Any]]:
    """Filter chunks according to the optional registry and an intended use."""
    if required_use not in CHUNK_APPROVED_USES:
        raise ValueError(f"Unsupported chunk use: {required_use}")
    config = _load_config(project_dir)
    registry_path = chunk_registry_jsonl_path(project_dir)
    if not registry_path.exists() and chunk_registry_csv_path(project_dir).exists():
        registry_path = chunk_registry_csv_path(project_dir)
    if not config.style.curation.use_chunk_registry or not registry_path.exists():
        if config.style.curation.require_chunk_approval:
            return [], {
                "chunk_registry_used": False,
                "require_chunk_approval": True,
                f"approved_{required_use}_chunks": 0,
                "chunks_before_curation": len(chunks),
                "chunks_after_curation": 0,
                "excluded_chunks": len(chunks),
                "warning": "Chunk approval is required, but the registry is missing or disabled; no chunks were used.",
            }
        return chunks, {"chunk_registry_used": False, "warning": ""}
    rows_by_chunk = _registry_by_chunk(project_dir)
    approved_count = sum(
        1
        for row in rows_by_chunk.values()
        if row.approval_status == "approved" and required_use in row.approved_for and not row.missing
    )
    filtered: list[StyleChunk] = []
    excluded = 0
    needs_review_included = 0
    warning_excluded = 0
    for chunk in chunks:
        row = rows_by_chunk.get(chunk.chunk_id)
        if row is None:
            if config.style.curation.require_chunk_approval:
                excluded += 1
                continue
            filtered.append(chunk)
            continue
        if row.approval_status == "excluded" or row.missing:
            excluded += 1
            continue
        if config.style.curation.exclude_warning_chunks_by_default and row.warning_flags:
            warning_excluded += 1
            continue
        if config.style.curation.require_chunk_approval:
            if row.approval_status == "approved" and required_use in row.approved_for:
                filtered.append(chunk)
            else:
                excluded += 1
            continue
        if row.approval_status == "approved":
            if required_use in row.approved_for:
                filtered.append(chunk)
            else:
                excluded += 1
            continue
        if row.approval_status == "needs_review":
            if config.style.curation.include_needs_review_chunks:
                needs_review_included += 1
                filtered.append(chunk)
            else:
                excluded += 1
            continue
        filtered.append(chunk)
    warning = ""
    if approved_count == 0 and not config.style.curation.require_chunk_approval:
        warning = "Chunk registry exists but no chunks are explicitly approved; using non-excluded fallback chunks."
    return filtered, {
        "chunk_registry_used": True,
        "chunk_registry_path": registry_path.relative_to(project_dir).as_posix(),
        "require_chunk_approval": config.style.curation.require_chunk_approval,
        f"approved_{required_use}_chunks": approved_count,
        "chunks_before_curation": len(chunks),
        "chunks_after_curation": len(filtered),
        "excluded_chunks": excluded,
        "needs_review_chunks_included": needs_review_included,
        "warning_chunks_excluded": warning_excluded,
        "warning": warning,
    }


def _word_distribution(rows: list[StyleChunkRegistryRow]) -> dict[str, Any]:
    return distribution([row.word_count for row in rows if not row.missing])


def _report_findings(rows: list[StyleChunkRegistryRow]) -> dict[str, list[str]]:
    findings: dict[str, list[str]] = {
        "likely_bad_chunks": [],
        "references_bibliography_leaks": [],
        "section_heading_uncertainty": [],
        "repeated_boilerplate": [],
        "very_short_chunks": [],
        "very_long_chunks": [],
        "scaffold_todo_text": [],
    }
    for row in rows:
        if row.warning_flags:
            findings["likely_bad_chunks"].append(row.chunk_id)
        if "references_or_bibliography_leak" in row.warning_flags:
            findings["references_bibliography_leaks"].append(row.chunk_id)
        if row.section_confidence in {"low", "unknown"} or "section_uncertain" in row.warning_flags:
            findings["section_heading_uncertainty"].append(row.chunk_id)
        if "repeated_boilerplate" in row.warning_flags:
            findings["repeated_boilerplate"].append(row.chunk_id)
        if "very_short" in row.warning_flags:
            findings["very_short_chunks"].append(row.chunk_id)
        if "very_long" in row.warning_flags:
            findings["very_long_chunks"].append(row.chunk_id)
        if "scaffold_or_todo_text" in row.warning_flags:
            findings["scaffold_todo_text"].append(row.chunk_id)
    return findings


def build_style_chunk_report(
    project_dir: Path,
    *,
    mode: str | None = None,
    section: str | None = None,
) -> StyleChunkReportResult:
    """Write chunk-quality reports from the current chunk registry."""
    project_dir = Path(project_dir)
    if not chunk_registry_jsonl_path(project_dir).exists():
        build_style_chunk_registry(project_dir, mode=mode)
    rows = [row for row in load_chunk_registry(project_dir) if not row.missing]
    selected_mode = normalize_style_mode(mode) if mode else None
    if selected_mode:
        rows = [row for row in rows if row.style_mode == selected_mode]
    if section:
        rows = [row for row in rows if row.section_type == section]
    section_distribution = dict(Counter(row.section_type for row in rows))
    mode_distribution = dict(Counter(row.style_mode for row in rows))
    word_count_distribution = _word_distribution(rows)
    findings = _report_findings(rows)
    data: dict[str, Any] = {
        "generated_at": utc_iso(),
        "selected_mode": selected_mode,
        "selected_section": section,
        "total_chunks": len(rows),
        "approved_chunks": sum(1 for row in rows if row.approval_status == "approved"),
        "needs_review_chunks": sum(1 for row in rows if row.approval_status == "needs_review"),
        "excluded_chunks": sum(1 for row in rows if row.approval_status == "excluded"),
        "section_distribution": section_distribution,
        "mode_distribution": mode_distribution,
        "word_count_distribution": word_count_distribution,
        "findings": findings,
    }
    lines = [
        "# Style Chunk Report",
        "",
        f"- Total chunks: {data['total_chunks']}",
        f"- Approved chunks: {data['approved_chunks']}",
        f"- Needs review: {data['needs_review_chunks']}",
        f"- Excluded chunks: {data['excluded_chunks']}",
        "",
        "## Section Distribution",
    ]
    for key, value in sorted(section_distribution.items()):
        lines.append(f"- {key}: {value}")
    if not section_distribution:
        lines.append("- No chunks found.")
    lines.extend(["", "## Findings"])
    for key, values in findings.items():
        lines.append(f"- {key}: {len(values)}")
    report_path = chunk_report_md_path(project_dir)
    json_path = chunk_report_json_path(project_dir)
    xlsx_path = chunk_report_xlsx_path(project_dir)
    write_text(report_path, "\n".join(lines).rstrip() + "\n")
    write_json(json_path, data)
    write_workbook(
        xlsx_path,
        {
            "Summary": [{k: v for k, v in data.items() if k != "findings"}],
            "Findings": [
                {"finding_type": key, "chunk_id": chunk_id}
                for key, values in findings.items()
                for chunk_id in values
            ],
            "Chunks": [_row_to_csv_data(row) for row in rows],
        },
    )
    return StyleChunkReportResult(project_dir, report_path, json_path, xlsx_path, data)


def _append_decision(project_dir: Path, decision: dict[str, Any]) -> Path:
    path = chunk_decisions_path(project_dir)
    ensure_dir(path.parent)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(decision, sort_keys=True) + "\n")
    return path


def update_style_chunk_approval(
    project_dir: Path,
    *,
    chunk_id: str | None = None,
    source_file: str | None = None,
    section: str | None = None,
    mode: str | None = None,
    approve: bool = False,
    exclude: bool = False,
    needs_review: bool = False,
    approve_for: str | None = None,
    note: str | None = None,
) -> StyleChunkApprovalResult:
    """Approve, exclude, or mark matching chunks for review."""
    project_dir = Path(project_dir)
    if not chunk_registry_jsonl_path(project_dir).exists():
        build_style_chunk_registry(project_dir, mode=mode)
    rows = load_chunk_registry(project_dir)
    selected_mode = normalize_style_mode(mode) if mode else None
    if sum([approve, exclude, needs_review]) != 1:
        raise ValueError("Choose exactly one of --approve, --exclude, or --needs-review.")
    if not any([chunk_id, source_file, section, selected_mode]):
        raise ValueError("Choose chunks with --chunk-id, --source-file, --section, or --mode.")
    matched: list[StyleChunkRegistryRow] = []
    for row in rows:
        if chunk_id and row.chunk_id != chunk_id:
            continue
        if source_file and row.source_file != source_file:
            continue
        if section and row.section_type != section:
            continue
        if selected_mode and row.style_mode != selected_mode:
            continue
        matched.append(row)
    if not matched:
        raise ValueError("No style chunks matched the supplied filters.")
    status = "approved" if approve else ("excluded" if exclude else "needs_review")
    for row in matched:
        row.approval_status = status  # type: ignore[assignment]
        if approve_for:
            if approve_for not in CHUNK_APPROVED_USES:
                raise ValueError(f"Unsupported --approve-for value: {approve_for}")
            if approve_for not in row.approved_for:
                row.approved_for.append(approve_for)  # type: ignore[arg-type]
        elif approve and not row.approved_for:
            row.approved_for = CHUNK_APPROVED_USES.copy()  # type: ignore[assignment]
        if note:
            row.notes = (row.notes + " " + note).strip() if row.notes else note
    warnings = _registry_warnings(rows)
    paths = _write_chunk_registry_files(project_dir, rows, warnings)
    decision = {
        "decision_id": stable_id("chunk_decision", f"{utc_iso()}:{','.join(row.chunk_id for row in matched)}:{note or ''}"),
        "created_at": utc_iso(),
        "chunk_ids": [row.chunk_id for row in matched],
        "status": status,
        "approve_for": approve_for,
        "source_file": source_file,
        "section": section,
        "mode": selected_mode,
        "note": note or "",
    }
    decisions = _append_decision(project_dir, decision)
    return StyleChunkApprovalResult(project_dir, matched, decisions, paths)


def coverage_strength(approved_count: int) -> str:
    """Return coverage strength from approved chunk count."""
    if approved_count <= 0:
        return "absent"
    if approved_count <= 2:
        return "sparse"
    if approved_count <= 7:
        return "usable"
    return "strong"


def build_style_coverage_report(project_dir: Path, *, mode: str | None = None) -> StyleCoverageReportResult:
    """Write section coverage reports from the chunk registry."""
    project_dir = Path(project_dir)
    selected_mode = normalize_style_mode(mode or _load_config(project_dir).style.active_mode)
    if not chunk_registry_jsonl_path(project_dir).exists():
        build_style_chunk_registry(project_dir, mode=selected_mode)
    rows = [row for row in load_chunk_registry(project_dir) if not row.missing]
    mode_rows = [row for row in rows if row.style_mode == selected_mode]
    section_rows: list[dict[str, Any]] = []
    for section in STYLE_CARD_SECTIONS + ["response_to_reviewers"]:
        total = sum(1 for row in mode_rows if row.section_type == section)
        approved = sum(
            1
            for row in mode_rows
            if row.section_type == section and row.approval_status == "approved" and "style_profile" in row.approved_for
        )
        strength = coverage_strength(approved)
        section_rows.append(
            {
                "style_mode": selected_mode,
                "section_type": section,
                "total_chunks": total,
                "approved_chunks": approved,
                "coverage_strength": strength,
                "ready": strength in {"usable", "strong"},
            }
        )
    coursework_count = sum(1 for row in rows if row.style_mode == StyleMode.coursework_explanatory.value)
    academic_count = sum(1 for row in rows if row.style_mode == StyleMode.academic_manuscript.value)
    warnings: list[str] = []
    if selected_mode == StyleMode.academic_manuscript.value and coursework_count:
        warnings.append("Coursework chunks exist; keep them excluded from academic manuscript mode unless intentional.")
    if selected_mode == StyleMode.academic_manuscript.value and not academic_count:
        warnings.append("No academic_manuscript chunks are available.")
    recommendations = [
        f"Add or approve more {row['section_type']} samples."
        for row in section_rows
        if row["coverage_strength"] in {"absent", "sparse"}
    ][:8]
    data = {
        "generated_at": utc_iso(),
        "selected_mode": selected_mode,
        "files_by_style_mode": dict(Counter(row.style_mode for row in rows)),
        "chunks_by_section_type": dict(Counter(row.section_type for row in mode_rows)),
        "approved_chunks_by_section_type": dict(
            Counter(
                row.section_type
                for row in mode_rows
                if row.approval_status == "approved" and "style_profile" in row.approved_for
            )
        ),
        "coverage": section_rows,
        "coursework_isolated_from_academic": not (
            selected_mode == StyleMode.academic_manuscript.value
            and any(row.style_mode != selected_mode for row in mode_rows)
        ),
        "warnings": warnings,
        "recommended_next_samples": recommendations,
    }
    lines = [
        "# Style Coverage Report",
        "",
        f"- Selected mode: `{selected_mode}`",
        f"- Coursework isolated from academic: {data['coursework_isolated_from_academic']}",
        "- Passage-count indicators: absent is 0 approved chunks, sparse is 1 to 2, usable is 3 to 7, and strong is 8 or more.",
        "- The check is true from 3 approved chunks. It is not a quality, validity, or statistical readiness measure.",
        "",
        "## Coverage Matrix",
        "",
        "| Section | Total chunks | Approved chunks | Passage-count indicator | Meets 3-passage check |",
        "| --- | ---: | ---: | --- | --- |",
    ]
    for row in section_rows:
        lines.append(
            f"| {row['section_type']} | {row['total_chunks']} | {row['approved_chunks']} | {row['coverage_strength']} | {row['ready']} |"
        )
    lines.extend(["", "## Recommended Next Samples"])
    lines.extend(f"- {item}" for item in recommendations or ["No additional recommendations from approved chunks."])
    lines.extend(["", "## Warnings"])
    lines.extend(f"- {warning}" for warning in warnings or ["None."])
    report_path = coverage_report_md_path(project_dir)
    json_path = coverage_report_json_path(project_dir)
    matrix_path = coverage_matrix_path(project_dir)
    write_text(report_path, "\n".join(lines).rstrip() + "\n")
    write_json(json_path, data)
    _write_generic_csv(
        matrix_path,
        section_rows,
        ["style_mode", "section_type", "total_chunks", "approved_chunks", "coverage_strength", "ready"],
        backup=False,
    )
    return StyleCoverageReportResult(project_dir, report_path, json_path, matrix_path, data)


def _chunks_for_cards(project_dir: Path, mode: str) -> tuple[list[StyleChunk], dict[str, Any]]:
    samples = _samples_from_extraction(project_dir, mode, force=False)
    chunks = build_style_chunks(project_dir, samples)
    return apply_chunk_curation_to_profile_chunks(project_dir, chunks)


def _short_example(text: str, limit: int = 220) -> str:
    cleaned = " ".join(text.split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 3].rstrip() + "..."


def _card_confidence(chunk_count: int) -> str:
    if chunk_count == 0:
        return "absent"
    if chunk_count <= 2:
        return "low"
    if chunk_count <= 7:
        return "medium"
    return "high"


def _render_style_card(mode: str, section: str, chunks: list[StyleChunk]) -> tuple[str, dict[str, Any]]:
    text = "\n\n".join(chunk.text for chunk in chunks)
    sentences = split_sentences(text)
    paragraphs = split_paragraphs(text)
    tokens = word_tokens(text)
    sentence_lengths = [len(word_tokens(sentence)) for sentence in sentences]
    paragraph_lengths = [len(word_tokens(paragraph)) for paragraph in paragraphs]
    hedges = count_terms(tokens, HEDGING_WORDS)
    transitions = find_transition_counts(text)
    verbs = count_terms(tokens, ACADEMIC_VERBS)
    citation_style = citation_patterns(text)
    limitation_style = limitation_phrases(sentences)
    causal_terms = count_terms(tokens, {"cause", "causes", "caused", "prove", "proves", "demonstrate", "demonstrates"})
    examples = [
        {"source_file": chunk.source_file, "text": _short_example(chunk.text)}
        for chunk in chunks[:3]
    ]
    corpus_files = sorted({chunk.source_file for chunk in chunks})
    typical_sentence_length = distribution(sentence_lengths)
    typical_paragraph_length = distribution(paragraph_lengths)
    sentence_mean = typical_sentence_length.get("mean")
    paragraph_mean = typical_paragraph_length.get("mean")
    sentence_length_label = "not available (no passages)" if sentence_mean is None else f"{sentence_mean} words on average"
    paragraph_length_label = "not available (no passages)" if paragraph_mean is None else f"{paragraph_mean} words on average"
    data: dict[str, Any] = {
        "style_mode": mode,
        "section_type": section,
        "confidence": _card_confidence(len(chunks)),
        "corpus_files_used": corpus_files,
        "chunk_count": len(chunks),
        "typical_sentence_length": typical_sentence_length,
        "typical_paragraph_length": typical_paragraph_length,
        "common_transitions": transitions,
        "common_hedges": hedges,
        "common_verbs": verbs,
        "causal_language_tendency": causal_terms,
        "citation_integration_notes": citation_style,
        "limitation_phrasing_notes": limitation_style[:5],
        "representative_short_examples": examples,
        "do_not_overinterpret_warnings": [
            "Style cards summarize supplied writing samples; they are not factual evidence.",
            "Low chunk counts should be treated as provisional.",
        ],
        "recommended_drafting_instructions": [
            "Use these patterns for tone and pacing only.",
            "Keep claims tied to the claim registry and supplied citations.",
        ],
    }
    lines = [
        f"# Style Card: {mode} / {section}",
        "",
        f"- Style mode: `{mode}`",
        f"- Section type: `{section}`",
        f"- Passage-count indicator: {data['confidence']}",
        f"- Chunk count: {len(chunks)}",
        f"- Corpus files used: {', '.join(corpus_files) if corpus_files else 'none'}",
        f"- Typical sentence length: {sentence_length_label}",
        f"- Typical paragraph length: {paragraph_length_label}",
        "",
        "## Common Signals",
        f"- Transitions: {', '.join(transitions) if transitions else 'none detected'}",
        f"- Hedges: {', '.join(hedges) if hedges else 'none detected'}",
        f"- Verbs: {', '.join(verbs) if verbs else 'none detected'}",
        f"- Causal language tendency: {', '.join(causal_terms) if causal_terms else 'none detected'}",
        f"- Citation integration: {citation_style.get('likely_style', 'unknown')}",
        "",
        "## Representative Short Examples",
    ]
    if examples:
        lines.extend(f"- `{item['source_file']}`: {item['text']}" for item in examples)
    else:
        lines.append("- None available.")
    lines.extend(
        [
            "",
            "## Interpreting This Card",
            "- The passage-count indicator is absent for 0 chunks, low for 1 to 2, medium for 3 to 7, and high for 8 or more.",
            "- It is a count-based guide, not a quality, validity, or statistical confidence measure.",
            "- Style cards summarize supplied writing samples; they are not factual evidence.",
            "",
            "## Recommended Drafting Instructions",
            "- Use these patterns for tone and pacing only.",
            "- Keep claims tied to the claim registry and supplied citations.",
        ]
    )
    return "\n".join(lines).rstrip() + "\n", data


def build_style_cards(project_dir: Path, *, mode: str | None = None) -> StyleCardsResult:
    """Write local style cards by mode and section."""
    project_dir = Path(project_dir)
    selected_mode = normalize_style_mode(mode or _load_config(project_dir).style.active_mode)
    chunks, curation_summary = _chunks_for_cards(project_dir, selected_mode)
    cards_dir = ensure_dir(style_cards_dir(project_dir))
    card_paths: list[Path] = []
    cards: list[dict[str, Any]] = []
    for section in STYLE_CARD_SECTIONS + ["overall"]:
        section_chunks = chunks if section == "overall" else [chunk for chunk in chunks if chunk.section_type == section]
        rendered, data = _render_style_card(selected_mode, section, section_chunks)
        path = cards_dir / f"{selected_mode}__{section}.md"
        write_text(path, rendered)
        card_paths.append(path)
        cards.append({"path": path.relative_to(project_dir).as_posix(), **data})
    manifest = {
        "generated_at": utc_iso(),
        "style_mode": selected_mode,
        "cards": cards,
        "curation_summary": curation_summary,
    }
    manifest_path = style_cards_manifest_path(project_dir)
    write_json(manifest_path, manifest)
    return StyleCardsResult(project_dir, cards_dir, manifest_path, card_paths, manifest)


STYLE_MODE_DESCRIPTIONS = {
    "academic_manuscript": "First-author or majority-written manuscript sections and papers.",
    "coursework_explanatory": "Coursework or explanatory prose that should stay isolated from manuscript style.",
    "response_to_reviewers": "Reviewer response letters and rebuttal prose.",
    "journal_cover_letter": "Journal cover letters and submission letters.",
    "grant_or_proposal": "Grant, proposal, or project pitch prose.",
    "review_article": "Review article prose and narrative synthesis.",
    "journal_adapted_academic": "Manuscript prose adapted for a specific journal after manual review.",
}


def prep_style_modes(
    project_dir: Path,
    *,
    include_coursework: bool = True,
    include_journal: bool = True,
) -> PrepStyleModesResult:
    """Create style-mode folders and local README guidance."""
    project_dir = Path(project_dir)
    style_dir = ensure_dir(project_dir / "style_corpus")
    modes = list(STYLE_MODE_DESCRIPTIONS)
    if not include_coursework:
        modes.remove("coursework_explanatory")
    if not include_journal:
        modes = [mode for mode in modes if mode not in {"journal_cover_letter", "journal_adapted_academic"}]
    created: list[Path] = []
    existing: list[Path] = []
    for mode in modes:
        folder = style_dir / mode
        if folder.exists():
            existing.append(folder)
        else:
            ensure_dir(folder)
            created.append(folder)
        readme = folder / "README.md"
        if readme.exists():
            existing.append(readme)
            continue
        write_text(
            readme,
            f"# {mode}\n\n"
            f"{STYLE_MODE_DESCRIPTIONS[mode]}\n\n"
            "- Add copies of approved local writing samples here.\n"
            "- These files are style exemplars only, not scientific evidence.\n"
            "- Keep sensitive or collaborator-heavy material out unless intentionally reviewed.\n",
        )
        created.append(readme)
    unclassified = [
        path.name
        for path in sorted(style_dir.iterdir())
        if path.is_file() and not is_scaffold_file(path)
    ]
    if unclassified:
        write_text(
            style_dir / "UNCLASSIFIED_STYLE_FILES.md",
            "# Unclassified Style Files\n\n"
            + "\n".join(f"- `{name}`" for name in unclassified)
            + "\n\nMove these into a named style-mode folder before profiling when practical.\n",
        )
    return PrepStyleModesResult(project_dir, created, existing, unclassified)


def prep_journal_adapter(project_dir: Path, *, journal: str) -> JournalAdapterResult:
    """Create a local journal-adapter scaffold without network access."""
    project_dir = Path(project_dir)
    root = ensure_dir(project_dir / "planning" / "journal_adapters")
    adapter_dir = ensure_dir(root / slugify(journal))
    created: list[Path] = []
    existing: list[Path] = []
    root_files = {
        root / "journal_adapter_inventory.csv": "journal,slug,status,notes\n",
        root / "journal_adapter_template.md": "# Journal Adapter Template\n\nFill this manually from official instructions for authors.\n",
        root / "journal_adapter_plan.md": "# Journal Adapter Plan\n\nNo journal requirements are downloaded or invented. Paste reviewed local notes into adapter folders.\n",
    }
    for path, text in root_files.items():
        if path.exists():
            existing.append(path)
        else:
            write_text(path, text)
            created.append(path)
    files = {
        "instructions_for_authors.md": "# Instructions For Authors\n\nPaste manually reviewed journal instructions here.\n",
        "article_type_requirements.md": "# Article Type Requirements\n\nFill manually.\n",
        "structure_notes.md": "# Structure Notes\n\nFill manually.\n",
        "abstract_requirements.md": "# Abstract Requirements\n\nFill manually.\n",
        "style_observations.md": "# Style Observations\n\nFill manually from the journal's published examples or instructions.\n",
        "checklist.md": "# Journal Checklist\n\n- [ ] Requirements reviewed manually\n- [ ] Abstract requirements filled\n- [ ] Section order checked\n",
    }
    for filename, text in files.items():
        path = adapter_dir / filename
        if path.exists():
            existing.append(path)
        else:
            write_text(path, text)
            created.append(path)
    config_path = adapter_dir / "adapter_config.yaml"
    if config_path.exists():
        existing.append(config_path)
    else:
        write_yaml(
            config_path,
            {
                "journal": journal,
                "abstract_word_limit": None,
                "structured_abstract": None,
                "required_sections": [],
                "preferred_section_order": [],
                "max_references": None,
                "figure_limit": None,
                "table_limit": None,
                "reporting_checklists": [],
                "tone_notes": "",
                "novelty_emphasis": "",
                "methods_detail_level": "",
                "discussion_length_preference": "",
                "limitation_required": None,
            },
        )
        created.append(config_path)
    return JournalAdapterResult(project_dir, journal, adapter_dir, created, existing)
