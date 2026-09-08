from __future__ import annotations

import json
import os
import stat
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from manuscriptforge.config import load_project_config
from manuscriptforge.ingest.pdf_ingest import extract_pdf_pages, pdf_page_count
from manuscriptforge.intake.registry import intake_dir, load_eligible_writing_samples
from manuscriptforge.intake.scaffold import is_scaffold_file
from manuscriptforge.models.project import ProjectConfig
from manuscriptforge.models.style import (
    StyleDocumentExtraction,
    StyleMode,
    normalize_style_mode,
)
from manuscriptforge.style.features import classify_section_heading
from manuscriptforge.utils.dates import utc_iso
from manuscriptforge.utils.hashing import sha256_file, sha256_text
from manuscriptforge.utils.ids import stable_id
from manuscriptforge.utils.io import ensure_dir, read_json, write_json, write_jsonl, write_text
from manuscriptforge.utils.text import split_paragraphs, word_tokens

STYLE_EXTRACTABLE_SUFFIXES = {".md", ".txt", ".docx", ".pdf"}
TEXT_STYLE_SUFFIXES = {".md", ".txt"}
SUCCESSFUL_EXTRACTION_STATUSES = {"extracted", "partial"}
REFERENCE_SECTIONS = {"references"}


@dataclass
class StyleExtractionRunResult:
    """Describe one style-corpus extraction run and its persisted outputs."""

    project_dir: Path
    mode: str | None
    output_dir: Path
    manifest_path: Path
    report_path: Path
    extractions: list[StyleDocumentExtraction]
    texts_by_relative_path: dict[str, str] = field(default_factory=dict)

    @property
    def summary(self) -> dict[str, Any]:
        return summarize_extractions(self.extractions)


@dataclass
class StyleCorpusDoctorResult:
    """Describe one style-corpus diagnostic report."""

    project_dir: Path
    mode: str | None
    report_path: Path
    json_path: Path
    data: dict[str, Any]


@dataclass
class _ExtractionPayload:
    record: StyleDocumentExtraction
    text: str = ""


def extraction_output_dir(project_dir: Path) -> Path:
    """Return the project-local folder used for cached style text."""
    return Path(project_dir) / "planning" / "intake" / "extracted_style_text"


def extraction_manifest_path(project_dir: Path) -> Path:
    """Return the style extraction manifest path."""
    return extraction_output_dir(project_dir) / "extraction_manifest.jsonl"


def extraction_report_path(project_dir: Path) -> Path:
    """Return the style extraction Markdown report path."""
    return extraction_output_dir(project_dir) / "extraction_report.md"


def style_corpus_doctor_report_path(project_dir: Path) -> Path:
    """Return the style-corpus doctor Markdown report path."""
    return intake_dir(project_dir) / "style_corpus_doctor.md"


def style_corpus_doctor_json_path(project_dir: Path) -> Path:
    """Return the style-corpus doctor JSON report path."""
    return intake_dir(project_dir) / "style_corpus_doctor.json"


def style_extension_extractable(extension: str, config: ProjectConfig | None = None) -> bool:
    """Return whether a style extension is configured for extraction."""
    suffix = extension.lower()
    if suffix not in STYLE_EXTRACTABLE_SUFFIXES:
        return False
    if config is None:
        return True
    if suffix == ".docx" and not config.style.extraction.include_docx:
        return False
    return not (suffix == ".pdf" and not config.style.extraction.include_pdf)


def _load_config(project_dir: Path) -> ProjectConfig:
    # The shared loader supplies defaults only when project.yaml is absent.
    # Errors in an existing policy must propagate before any extraction occurs.
    return load_project_config(project_dir)


def _validated_project_path(project_dir: Path, path: Path) -> Path:
    """Reject escaping paths and linked descendants before reading or writing."""
    root = project_dir.absolute()
    candidate = path.absolute()
    try:
        relative = candidate.relative_to(root)
        candidate.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError(f"Style path must stay inside the project: {path}") from exc
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink() or (
            current.exists()
            and getattr(current.lstat(), "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
        ):
            raise ValueError(f"Linked style paths are not allowed: {current}")
    return candidate


def _style_document_paths(project_dir: Path) -> list[Path]:
    """List corpus files without descending into linked directories."""
    paths: list[Path] = []
    for folder, directories, filenames in os.walk(project_dir / "style_corpus", followlinks=False):
        for name in directories:
            _validated_project_path(project_dir, Path(folder) / name)
        for name in filenames:
            paths.append(_validated_project_path(project_dir, Path(folder) / name))
    return sorted(paths)


def _rel(path: Path, project_dir: Path) -> str:
    try:
        return path.relative_to(project_dir).as_posix()
    except ValueError:
        return path.as_posix()


def _style_mode_from_path(project_dir: Path, path: Path, fallback: str = StyleMode.unknown.value) -> str:
    try:
        parts = path.relative_to(project_dir / "style_corpus").parts
    except ValueError:
        return fallback
    if len(parts) >= 2:
        return normalize_style_mode(parts[0], fallback=fallback)
    return fallback


def _read_manifest_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            loaded = read_json_from_line(line)
        except ValueError:
            continue
        if isinstance(loaded, dict):
            rows.append(loaded)
    return rows


def read_json_from_line(line: str) -> Any:
    """Parse a JSONL row without exposing json as a module dependency elsewhere."""
    import json

    try:
        return json.loads(line)
    except json.JSONDecodeError as exc:
        raise ValueError(str(exc)) from exc


def load_extraction_manifest(project_dir: Path) -> list[StyleDocumentExtraction]:
    """Load previously recorded style extraction rows."""
    rows = _read_manifest_rows(_validated_project_path(project_dir, extraction_manifest_path(project_dir)))
    extractions: list[StyleDocumentExtraction] = []
    for row in rows:
        try:
            extractions.append(StyleDocumentExtraction.model_validate(row))
        except Exception:
            continue
    return extractions


def extraction_by_relative_path(project_dir: Path) -> dict[str, StyleDocumentExtraction]:
    """Return extraction manifest entries keyed by project-relative source path."""
    return {item.relative_path: item for item in load_extraction_manifest(project_dir)}


def discover_style_documents(project_dir: Path, mode: str | None = None) -> list[dict[str, Any]]:
    """Find style documents eligible for extraction, respecting intake mode filters."""
    project_dir = Path(project_dir).absolute()
    style_dir = project_dir / "style_corpus"
    config = _load_config(project_dir)
    _validated_project_path(project_dir, style_dir)
    if not style_dir.exists():
        return []
    selected_mode = normalize_style_mode(mode or config.style.active_mode)
    include_modes = [selected_mode] if mode else config.style.include_modes or [selected_mode]
    include = set(include_modes)
    exclude = set(config.style.exclude_modes)
    if not mode and not (project_dir / "project.yaml").exists():
        # Bare folders predate mode configuration; keep their no-config behavior.
        include.add(StyleMode.unknown.value)
    eligible = load_eligible_writing_samples(
        project_dir,
        active_mode=selected_mode,
        include_modes=include_modes,
        exclude_modes=config.style.exclude_modes,
    )
    candidates: list[dict[str, Any]] = []
    if eligible is not None:
        for rel_path, metadata in sorted(eligible.items()):
            path = _validated_project_path(project_dir, project_dir / rel_path)
            if not path.is_relative_to(style_dir):
                raise ValueError(f"Registered style source must be under style_corpus: {rel_path}")
            if not path.exists() or not path.is_file() or is_scaffold_file(path):
                continue
            if path.suffix.lower() not in STYLE_EXTRACTABLE_SUFFIXES:
                continue
            candidates.append(
                {
                    "path": path,
                    "asset_id": str(metadata.get("asset_id") or ""),
                    "style_mode": normalize_style_mode(str(metadata.get("style_mode") or selected_mode)),
                    "content_hash": str(metadata.get("content_hash") or ""),
                }
            )
        return candidates

    for path in _style_document_paths(project_dir):
        if path.suffix.lower() not in STYLE_EXTRACTABLE_SUFFIXES or is_scaffold_file(path):
            continue
        style_mode = _style_mode_from_path(project_dir, path, fallback=config.style.mode_fallback)
        if style_mode not in include or style_mode in exclude:
            continue
        candidates.append(
            {"path": path, "asset_id": "", "style_mode": style_mode, "content_hash": ""}
        )
    return candidates


def _cache_path(project_dir: Path, asset_id: str, path: Path, config: ProjectConfig) -> Path:
    policy_hash = sha256_text(json.dumps(config.style.extraction.model_dump(), sort_keys=True))[:16]
    identifier = stable_id("stylex", f"{asset_id}:{_rel(path, project_dir)}:{policy_hash}")
    return _validated_project_path(project_dir, extraction_output_dir(project_dir) / f"{identifier}.txt")


def _cached_payload(
    project_dir: Path,
    existing: StyleDocumentExtraction | None,
    *,
    content_hash: str,
    force: bool,
    expected_cache_path: Path,
    style_mode: str,
    asset_id: str,
) -> _ExtractionPayload | None:
    if (
        force
        or existing is None
        or existing.content_hash != content_hash
        or existing.style_mode != style_mode
        or existing.asset_id != asset_id
        or existing.extraction_status not in SUCCESSFUL_EXTRACTION_STATUSES
        or existing.extracted_text_path != expected_cache_path.relative_to(project_dir).as_posix()
    ):
        return None
    if not expected_cache_path.exists():
        return None
    text = expected_cache_path.read_text(encoding="utf-8", errors="ignore")
    if not text.strip():
        return None
    return _ExtractionPayload(record=existing, text=text)


def _extract_docx_text(path: Path) -> tuple[str, int, list[str]]:
    warnings: list[str] = []
    try:
        from docx import Document
    except Exception as exc:
        return "", 0, [f"DOCX extraction dependency unavailable: {exc}"]
    try:
        document = Document(str(path))
    except Exception as exc:
        return "", 0, [f"DOCX extraction failed: {exc}"]
    paragraphs = [paragraph.text.strip() for paragraph in document.paragraphs if paragraph.text.strip()]
    for table in document.tables:
        for row in table.rows:
            for cell in row.cells:
                cell_text = " ".join(part.text.strip() for part in cell.paragraphs if part.text.strip())
                if cell_text:
                    paragraphs.append(cell_text)
    if not paragraphs:
        warnings.append("DOCX contained no extractable paragraph text.")
    return "\n\n".join(paragraphs), len(paragraphs), warnings


def _extract_pdf_text(path: Path) -> tuple[str, str, int | None, list[str]]:
    warnings: list[str] = []
    try:
        pages = extract_pdf_pages(path)
    except Exception as exc:
        page_count = pdf_page_count(path)
        return "", "failed", page_count, [f"PDF text extraction failed: {exc}"]
    page_count = len(pages)
    text_pages = [page.strip() for page in pages if page.strip()]
    text = "\n\n".join(text_pages)
    empty_pages = page_count - len(text_pages)
    extracted_chars = len(text)
    if page_count == 0:
        warnings.append("PDF had no pages.")
        return text, "empty", page_count, warnings
    if not text.strip():
        warnings.append("No extractable text was found in the PDF.")
        return text, "empty", page_count, warnings
    if extracted_chars / max(page_count, 1) < 50:
        warnings.append("Very little text was extracted per page; the PDF may be scanned or image-only.")
        return text, "likely_scanned_or_unextractable", page_count, warnings
    if empty_pages:
        warnings.append(f"{empty_pages} page(s) had no extractable text.")
        return text, "partial", page_count, warnings
    return text, "extracted", page_count, warnings


def _build_record(
    project_dir: Path,
    path: Path,
    *,
    asset_id: str,
    style_mode: str,
    content_hash: str,
    status: str,
    text: str,
    extracted_text_path: str | None,
    page_count: int | None,
    paragraph_count: int | None,
    warnings: list[str],
) -> StyleDocumentExtraction:
    rel_path = _rel(path, project_dir)
    return StyleDocumentExtraction(
        asset_id=asset_id or stable_id("asset", f"{rel_path}:{content_hash}"),
        source_path=str(path),
        relative_path=rel_path,
        filename=path.name,
        extension=path.suffix.lower(),
        style_mode=style_mode,
        extraction_status=status,  # type: ignore[arg-type]
        extracted_text_path=extracted_text_path,
        extracted_char_count=len(text),
        extracted_word_count=len(word_tokens(text)),
        page_count=page_count,
        paragraph_count=paragraph_count,
        warnings=warnings,
        content_hash=content_hash,
        extracted_at=utc_iso(),
    )


def _extract_style_document_payload(
    project_dir: Path,
    path: Path,
    *,
    asset_id: str = "",
    style_mode: str = StyleMode.unknown.value,
    force: bool = False,
    config: ProjectConfig | None = None,
    existing: StyleDocumentExtraction | None = None,
) -> _ExtractionPayload:
    project_dir = Path(project_dir).absolute()
    path = _validated_project_path(project_dir, Path(path))
    config = config or _load_config(project_dir)
    content_hash = sha256_file(path)
    effective_asset_id = asset_id or stable_id("asset", f"{_rel(path, project_dir)}:{content_hash}")
    if config.style.extraction.enabled and config.style.extraction.cache_extracted_text:
        cached = _cached_payload(
            project_dir,
            existing,
            content_hash=content_hash,
            force=force,
            expected_cache_path=_cache_path(project_dir, effective_asset_id, path, config),
            style_mode=style_mode,
            asset_id=effective_asset_id,
        )
        if cached is not None:
            return cached

    warnings: list[str] = []
    status = "unsupported"
    text = ""
    page_count: int | None = None
    paragraph_count: int | None = None
    suffix = path.suffix.lower()

    if not config.style.extraction.enabled:
        warnings.append("Style extraction is disabled in project.yaml.")
    elif suffix not in STYLE_EXTRACTABLE_SUFFIXES:
        warnings.append(f"Unsupported style document extension: {suffix or '<none>'}.")
    elif suffix == ".docx" and not config.style.extraction.include_docx:
        warnings.append("DOCX style extraction is disabled in project.yaml.")
    elif suffix == ".pdf" and not config.style.extraction.include_pdf:
        warnings.append("PDF style extraction is disabled in project.yaml.")
    elif suffix in TEXT_STYLE_SUFFIXES:
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError as exc:
            status = "failed"
            warnings.append(f"Text extraction failed: {exc}")
        else:
            status = "extracted" if text.strip() else "empty"
    elif suffix == ".docx":
        text, paragraph_count, warnings = _extract_docx_text(path)
        status = "extracted" if text.strip() else ("failed" if warnings else "empty")
    elif suffix == ".pdf":
        text, status, page_count, warnings = _extract_pdf_text(path)

    if paragraph_count is None and text.strip():
        paragraph_count = len(split_paragraphs(text))
    extracted_text_rel: str | None = None
    if text.strip() and config.style.extraction.cache_extracted_text:
        cache_path = _cache_path(project_dir, effective_asset_id, path, config)
        write_text(cache_path, text)
        extracted_text_rel = cache_path.relative_to(project_dir).as_posix()

    record = _build_record(
        project_dir,
        path,
        asset_id=effective_asset_id,
        style_mode=style_mode,
        content_hash=content_hash,
        status=status,
        text=text,
        extracted_text_path=extracted_text_rel,
        page_count=page_count,
        paragraph_count=paragraph_count,
        warnings=warnings,
    )
    return _ExtractionPayload(record=record, text=text)


def extract_style_document(
    project_dir: Path,
    path: Path,
    *,
    asset_id: str = "",
    style_mode: str = StyleMode.unknown.value,
    force: bool = False,
    config: ProjectConfig | None = None,
) -> StyleDocumentExtraction:
    """Extract one style document and return its auditable extraction record."""
    return _extract_style_document_payload(
        project_dir,
        path,
        asset_id=asset_id,
        style_mode=style_mode,
        force=force,
        config=config,
    ).record


def summarize_extractions(extractions: list[StyleDocumentExtraction]) -> dict[str, Any]:
    """Return compact counts for style extraction reports."""
    by_status = Counter(item.extraction_status for item in extractions)
    by_extension = Counter(item.extension for item in extractions)
    by_mode = Counter(item.style_mode for item in extractions)
    by_mode_extension: dict[str, dict[str, int]] = defaultdict(dict)
    for item in extractions:
        by_mode_extension[item.style_mode][item.extension] = (
            by_mode_extension[item.style_mode].get(item.extension, 0) + 1
        )
    return {
        "files_considered": len(extractions),
        "files_extracted": int(by_status.get("extracted", 0) + by_status.get("partial", 0)),
        "failed": int(by_status.get("failed", 0)),
        "unsupported": int(by_status.get("unsupported", 0)),
        "empty": int(by_status.get("empty", 0)),
        "likely_scanned_or_unextractable": int(by_status.get("likely_scanned_or_unextractable", 0)),
        "by_status": dict(by_status),
        "by_extension": dict(by_extension),
        "by_style_mode": dict(by_mode),
        "by_style_mode_and_extension": {mode: dict(values) for mode, values in by_mode_extension.items()},
        "warnings": [
            {
                "relative_path": item.relative_path,
                "status": item.extraction_status,
                "warnings": item.warnings,
            }
            for item in extractions
            if item.warnings
        ],
    }


def _render_extraction_report(result: StyleExtractionRunResult) -> str:
    summary = result.summary
    lines = [
        "# Style Corpus Extraction Report",
        "",
        f"- Mode: `{result.mode or 'configured/default'}`",
        f"- Files considered: {summary['files_considered']}",
        f"- Successfully extracted: {summary['files_extracted']}",
        f"- Failed: {summary['failed']}",
        f"- Unsupported: {summary['unsupported']}",
        f"- Empty: {summary['empty']}",
        f"- Likely scanned/unextractable: {summary['likely_scanned_or_unextractable']}",
        "",
        "## By Extension",
    ]
    for extension, count in sorted(summary["by_extension"].items()):
        lines.append(f"- {extension}: {count}")
    if not summary["by_extension"]:
        lines.append("- None.")
    lines.extend(["", "## By Status"])
    for status, count in sorted(summary["by_status"].items()):
        lines.append(f"- {status}: {count}")
    if not summary["by_status"]:
        lines.append("- None.")
    lines.extend(["", "## Files"])
    for item in result.extractions:
        warning_text = f" Warnings: {'; '.join(item.warnings)}" if item.warnings else ""
        cache_text = f" -> `{item.extracted_text_path}`" if item.extracted_text_path else ""
        lines.append(
            f"- `{item.relative_path}`: {item.extraction_status}, "
            f"{item.extracted_word_count} words{cache_text}.{warning_text}"
        )
    if not result.extractions:
        lines.append("- No eligible style documents were found.")
    return "\n".join(lines).rstrip() + "\n"


def extract_style_corpus(project_dir: Path, *, mode: str | None = None, force: bool = False) -> StyleExtractionRunResult:
    """Extract all eligible style documents and write manifest/report files."""
    project_dir = Path(project_dir).absolute()
    config = _load_config(project_dir)
    selected_mode = normalize_style_mode(mode) if mode else None
    existing = extraction_by_relative_path(project_dir)
    output_dir = ensure_dir(_validated_project_path(project_dir, extraction_output_dir(project_dir)))
    extractions: list[StyleDocumentExtraction] = []
    texts_by_relative_path: dict[str, str] = {}
    for candidate in discover_style_documents(project_dir, selected_mode):
        path = Path(candidate["path"])
        rel_path = _rel(path, project_dir)
        payload = _extract_style_document_payload(
            project_dir,
            path,
            asset_id=str(candidate.get("asset_id") or ""),
            style_mode=normalize_style_mode(str(candidate.get("style_mode") or StyleMode.unknown.value)),
            force=force,
            config=config,
            existing=existing.get(rel_path),
        )
        if (
            config.style.extraction.fail_on_extraction_error
            and payload.record.extraction_status in {"failed", "unsupported"}
        ):
            raise RuntimeError(
                f"Style extraction failed for {payload.record.relative_path}: "
                + "; ".join(payload.record.warnings or [payload.record.extraction_status])
            )
        extractions.append(payload.record)
        if payload.text:
            texts_by_relative_path[payload.record.relative_path] = payload.text
    result = StyleExtractionRunResult(
        project_dir=project_dir,
        mode=selected_mode,
        output_dir=output_dir,
        manifest_path=extraction_manifest_path(project_dir),
        report_path=extraction_report_path(project_dir),
        extractions=extractions,
        texts_by_relative_path=texts_by_relative_path,
    )
    write_jsonl(result.manifest_path, result.extractions)
    write_text(result.report_path, _render_extraction_report(result))
    return result


def _text_for_extraction(project_dir: Path, item: StyleDocumentExtraction, text_cache: dict[str, str]) -> str:
    if item.relative_path in text_cache:
        return text_cache[item.relative_path]
    if item.extracted_text_path:
        path = _validated_project_path(project_dir, project_dir / item.extracted_text_path)
        if path.exists():
            return path.read_text(encoding="utf-8", errors="ignore")
    return ""


def _detected_sections(text: str) -> list[str]:
    return sorted({section for line in text.splitlines() if (section := classify_section_heading(line)) is not None})


def _is_reference_heavy(text: str) -> bool:
    lines = text.splitlines()
    reference_start = None
    for index, line in enumerate(lines):
        if classify_section_heading(line) in REFERENCE_SECTIONS:
            reference_start = index
            break
    if reference_start is None:
        return False
    reference_words = len(word_tokens("\n".join(lines[reference_start:])))
    total_words = max(len(word_tokens(text)), 1)
    return reference_words / total_words >= 0.35


def _render_doctor_report(data: dict[str, Any]) -> str:
    lines = [
        "# Style Corpus Doctor",
        "",
        f"- Selected mode: `{data.get('selected_mode')}`",
        f"- Files considered: {data.get('files_considered', 0)}",
        f"- Extracted files: {data.get('extracted_files', 0)}",
        "",
        "## Files By Mode",
    ]
    for mode, count in sorted(data.get("files_by_mode", {}).items()):
        lines.append(f"- {mode}: {count}")
    if not data.get("files_by_mode"):
        lines.append("- None.")
    lines.extend(["", "## Files By Extension"])
    for extension, count in sorted(data.get("files_by_extension", {}).items()):
        lines.append(f"- {extension}: {count}")
    if not data.get("files_by_extension"):
        lines.append("- None.")
    lines.extend(["", "## Extraction Status"])
    for status, count in sorted(data.get("extraction_status", {}).items()):
        lines.append(f"- {status}: {count}")
    if not data.get("extraction_status"):
        lines.append("- None.")
    lines.extend(["", "## Findings"])
    findings = data.get("findings", {})
    for key in [
        "too_short",
        "likely_scanned_pdfs",
        "references_heavy",
        "scaffold_or_todo_files",
        "missing_section_headings",
    ]:
        values = findings.get(key, [])
        lines.append(f"### {key.replace('_', ' ').title()}")
        if values:
            lines.extend(f"- `{value}`" for value in values)
        else:
            lines.append("- None.")
        lines.append("")
    lines.append("## Recommendations")
    for recommendation in data.get("recommendations", []) or ["No immediate fixes detected."]:
        lines.append(f"- {recommendation}")
    curation = data.get("curation", {})
    lines.extend(["", "## Curation Outputs"])
    if curation:
        lines.append(f"- Chunk registry present: {curation.get('chunk_registry_present', False)}")
        lines.append(f"- Chunk approval counts: {curation.get('chunk_approval_status', {})}")
        lines.append(f"- Coverage report present: {curation.get('coverage_report_present', False)}")
        lines.append(f"- Style cards available: {curation.get('style_cards_available', 0)}")
    else:
        lines.append("- No curation outputs found.")
    return "\n".join(lines).rstrip() + "\n"


def _curation_status(project_dir: Path) -> dict[str, Any]:
    chunk_registry = project_dir / "planning" / "intake" / "style_chunks" / "style_chunks_registry.jsonl"
    coverage_json = project_dir / "planning" / "intake" / "style_coverage_report.json"
    cards_manifest = project_dir / "planning" / "intake" / "style_cards" / "style_cards_manifest.json"
    approval_counts: Counter[str] = Counter()
    if chunk_registry.exists():
        for row in _read_manifest_rows(chunk_registry):
            approval_counts[str(row.get("approval_status") or "unknown")] += 1
    coverage_strength: dict[str, str] = {}
    if coverage_json.exists():
        try:
            coverage = read_json(coverage_json)
        except Exception:
            coverage = {}
        for row in coverage.get("coverage", []) if isinstance(coverage, dict) else []:
            if isinstance(row, dict):
                coverage_strength[str(row.get("section_type"))] = str(row.get("coverage_strength"))
    style_cards_available = 0
    if cards_manifest.exists():
        try:
            cards = read_json(cards_manifest)
        except Exception:
            cards = {}
        if isinstance(cards, dict) and isinstance(cards.get("cards"), list):
            style_cards_available = len(cards["cards"])
    return {
        "chunk_registry_present": chunk_registry.exists(),
        "chunk_approval_status": dict(approval_counts),
        "coverage_report_present": coverage_json.exists(),
        "coverage_strength": coverage_strength,
        "style_cards_present": cards_manifest.exists(),
        "style_cards_available": style_cards_available,
    }


def style_corpus_doctor(project_dir: Path, *, mode: str | None = None) -> StyleCorpusDoctorResult:
    """Diagnose style corpus extraction readiness and likely style-profile gaps."""
    project_dir = Path(project_dir)
    config = _load_config(project_dir)
    selected_mode = normalize_style_mode(mode or config.style.active_mode)
    extraction = extract_style_corpus(project_dir, mode=selected_mode)
    summary = extraction.summary
    findings: dict[str, list[str]] = {
        "too_short": [],
        "likely_scanned_pdfs": [],
        "references_heavy": [],
        "scaffold_or_todo_files": [],
        "missing_section_headings": [],
    }
    style_dir = project_dir / "style_corpus"
    if style_dir.exists():
        findings["scaffold_or_todo_files"] = [
            _rel(path, project_dir)
            for path in _style_document_paths(project_dir)
            if path.is_file() and is_scaffold_file(path)
        ]
    for item in extraction.extractions:
        text = _text_for_extraction(project_dir, item, extraction.texts_by_relative_path)
        if item.extraction_status == "likely_scanned_or_unextractable" and item.extension == ".pdf":
            findings["likely_scanned_pdfs"].append(item.relative_path)
        if item.extraction_status in SUCCESSFUL_EXTRACTION_STATUSES and item.extracted_word_count < config.style.extraction.min_chunk_words:
            findings["too_short"].append(item.relative_path)
        if item.extraction_status in SUCCESSFUL_EXTRACTION_STATUSES and text:
            if _is_reference_heavy(text):
                findings["references_heavy"].append(item.relative_path)
            if not _detected_sections(text):
                findings["missing_section_headings"].append(item.relative_path)
    recommendations: list[str] = []
    if not extraction.extractions:
        recommendations.append(
            "Add real writing samples under style_corpus/academic_manuscript/ before profiling style."
        )
    if not summary["files_extracted"] and extraction.extractions:
        recommendations.append("No selected style files extracted usable text; check DOCX/PDF text layers or use TXT/MD copies.")
    if findings["likely_scanned_pdfs"]:
        recommendations.append("Replace scanned PDFs with DOCX, text-extractable PDFs, or approved TXT/MD exports.")
    if findings["missing_section_headings"]:
        recommendations.append("Add clear section headings to long samples so chunks map to manuscript sections.")
    data = {
        "generated_at": utc_iso(),
        "selected_mode": selected_mode,
        "files_considered": summary["files_considered"],
        "extracted_files": summary["files_extracted"],
        "files_by_mode": summary["by_style_mode"],
        "files_by_extension": summary["by_extension"],
        "extraction_status": summary["by_status"],
        "extraction_warnings": summary["warnings"],
        "findings": findings,
        "recommendations": recommendations,
        "curation": _curation_status(project_dir),
    }
    report_path = style_corpus_doctor_report_path(project_dir)
    json_path = style_corpus_doctor_json_path(project_dir)
    ensure_dir(report_path.parent)
    write_json(json_path, data)
    write_text(report_path, _render_doctor_report(data))
    return StyleCorpusDoctorResult(project_dir, selected_mode, report_path, json_path, data)
