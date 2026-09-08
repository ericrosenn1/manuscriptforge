from __future__ import annotations

import csv
import json
import re
import shutil
from collections import Counter
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from manuscriptforge.intake.models import ImportCandidate, ImportDecision
from manuscriptforge.models.style import StyleMode, normalize_style_mode
from manuscriptforge.utils.dates import utc_iso
from manuscriptforge.utils.hashing import sha256_file, sha256_text
from manuscriptforge.utils.ids import stable_id
from manuscriptforge.utils.io import ensure_dir, write_json, write_jsonl, write_text

TABLE_SUFFIXES = {".csv", ".tsv", ".xlsx", ".xls"}
STYLE_SUFFIXES = {".md", ".txt", ".docx", ".pdf"}
SOURCE_SUFFIXES = {".bib", ".ris", ".json", ".txt", ".md"}
TEXT_SIGNATURE_SUFFIXES = {".md", ".txt", ".bib", ".ris", ".json", ".jsonl", ".csv", ".tsv"}
TEXT_PRIVACY_SCAN_SUFFIXES = {".md", ".txt", ".csv", ".tsv", ".bib", ".ris", ".json", ".jsonl"}
MAX_PRIVACY_SCAN_BYTES = 20_000
MANUSCRIPT_NOTE_FILES = {
    "abstract.md",
    "rationale.md",
    "methods.md",
    "interpretation_notes.md",
    "figure_legends.md",
}
PRIVACY_TERMS = {
    "patient",
    "phi",
    "mrn",
    "dob",
    "date of birth",
    "confidential",
    "private",
    "raw",
    "deidentified",
    "de-identified",
    "irb",
    "identifier",
    "subject id",
    "participant id",
    "sample id",
}
PRIVACY_PATTERNS = {
    "email_address": re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE),
    "phone_number": re.compile(r"(?:\+?1[-.\s]?)?(?:\(?\d{3}\)?[-.\s]?)\d{3}[-.\s]?\d{4}\b"),
    "mrn_like_identifier": re.compile(r"\bMRN[-_:\s]*[A-Z0-9]{4,}\b", re.IGNORECASE),
    "subject_like_identifier": re.compile(r"\b(?:subject|participant|sample)[-_ ]?id[-_:\s]*[A-Z0-9]{3,}\b", re.IGNORECASE),
}
DOCUMENT_TYPE_HINTS = {
    "abstract": "abstract",
    "introduction": "introduction",
    "intro": "introduction",
    "methods": "methods",
    "method": "methods",
    "results": "results",
    "discussion": "discussion",
    "limitations": "limitations",
    "figure": "figure_legends",
    "legend": "figure_legends",
    "reviewer": "response_to_reviewers",
    "response": "response_to_reviewers",
    "rebuttal": "response_to_reviewers",
    "homework": "homework",
    "essay": "essay",
    "cover": "cover_letter",
    "letter": "cover_letter",
    "review": "review_article",
    "proposal": "grant_or_proposal",
    "grant": "grant_or_proposal",
}
STYLE_MODE_HINTS = {
    "academic_manuscript": StyleMode.academic_manuscript.value,
    "manuscript": StyleMode.academic_manuscript.value,
    "paper": StyleMode.academic_manuscript.value,
    "abstract": StyleMode.academic_manuscript.value,
    "methods": StyleMode.academic_manuscript.value,
    "results": StyleMode.academic_manuscript.value,
    "discussion": StyleMode.academic_manuscript.value,
    "coursework": StyleMode.coursework_explanatory.value,
    "homework": StyleMode.coursework_explanatory.value,
    "essay": StyleMode.coursework_explanatory.value,
    "response_to_reviewers": StyleMode.response_to_reviewers.value,
    "reviewer": StyleMode.response_to_reviewers.value,
    "rebuttal": StyleMode.response_to_reviewers.value,
    "cover_letter": StyleMode.journal_cover_letter.value,
    "cover": StyleMode.journal_cover_letter.value,
    "grant": StyleMode.grant_or_proposal.value,
    "proposal": StyleMode.grant_or_proposal.value,
    "review_article": StyleMode.review_article.value,
}
STAGING_FOLDERS = [
    "incoming/writing_samples",
    "incoming/result_tables",
    "incoming/sources",
    "incoming/source_pdfs",
    "incoming/feedback",
    "incoming/miscellaneous",
    "manifests",
    "reports",
    "accepted",
    "rejected",
    "archive",
]
WRITING_TEMPLATE_COLUMNS = [
    "source_path",
    "destination_filename",
    "style_mode",
    "document_type",
    "section_types",
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
    "decision_note",
]
RESULT_TEMPLATE_COLUMNS = [
    "source_path",
    "destination_filename",
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
    "decision_note",
]
SOURCE_TEMPLATE_COLUMNS = [
    "source_path_or_key",
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
    "decision_note",
]


@dataclass
class PrepImportStagingResult:
    """Describe import-staging scaffolding created or found."""

    project_dir: Path
    staging_dir: Path
    created: list[Path] = field(default_factory=list)
    existing: list[Path] = field(default_factory=list)


@dataclass
class ImportScanResult:
    """Describe import candidates found during a staging scan."""

    project_dir: Path
    candidates: list[ImportCandidate]
    report_path: Path
    json_path: Path
    written_paths: list[Path]
    summary: dict[str, Any]


@dataclass
class ImportPlanResult:
    """Describe proposed import actions without applying them."""

    project_dir: Path
    candidates: list[ImportCandidate]
    report_path: Path
    json_path: Path
    csv_path: Path
    data: dict[str, Any]


@dataclass
class ImportApplyResult:
    """Describe an import-copy or import-move operation."""

    project_dir: Path
    selected: list[ImportCandidate]
    copied: list[dict[str, str]]
    warnings: list[str]
    report_path: Path
    json_path: Path
    decisions_path: Path
    dry_run: bool


@dataclass
class ImportRejectResult:
    """Describe rejected staged import candidates."""

    project_dir: Path
    rejected: list[ImportCandidate]
    archive_paths: list[Path]
    decisions_path: Path


@dataclass
class ImportSummaryResult:
    """Describe the current import-staging state."""

    project_dir: Path
    report_path: Path
    json_path: Path
    data: dict[str, Any]


@dataclass
class ImportMetadataUpdateResult:
    """Describe candidate metadata updated from an import template CSV."""

    project_dir: Path
    updated: list[ImportCandidate]
    unmatched_rows: list[dict[str, str]]
    report_path: Path
    json_path: Path


@dataclass
class ImportDoctorResult:
    """Describe import-staging readiness diagnostics."""

    project_dir: Path
    report_path: Path
    json_path: Path
    data: dict[str, Any]


@dataclass
class ImportPreviewResult:
    """Describe a safe preview of one staged import candidate."""

    project_dir: Path
    candidate: ImportCandidate
    report_path: Path
    data: dict[str, Any]


@dataclass
class ImportCreateDemoResult:
    """Describe toy import-staging files created for local rehearsal."""

    project_dir: Path
    created: list[Path] = field(default_factory=list)
    skipped: list[Path] = field(default_factory=list)
    overwritten: list[Path] = field(default_factory=list)


@dataclass
class ImportRehearsalResult:
    """Describe a safe import-staging rehearsal run."""

    project_dir: Path
    report_path: Path
    json_path: Path
    data: dict[str, Any]


def import_staging_dir(project_dir: Path) -> Path:
    """Return the root import-staging folder for a project."""
    return Path(project_dir) / "planning" / "import_staging"


def incoming_dir(project_dir: Path) -> Path:
    """Return the incoming drop-zone directory."""
    return import_staging_dir(project_dir) / "incoming"


def manifests_dir(project_dir: Path) -> Path:
    """Return the import-staging manifests directory."""
    return import_staging_dir(project_dir) / "manifests"


def reports_dir(project_dir: Path) -> Path:
    """Return the import-staging reports directory."""
    return import_staging_dir(project_dir) / "reports"


def candidates_jsonl_path(project_dir: Path) -> Path:
    """Return the canonical import candidates JSONL path."""
    return manifests_dir(project_dir) / "import_candidates.jsonl"


def candidates_csv_path(project_dir: Path) -> Path:
    """Return the canonical import candidates CSV path."""
    return manifests_dir(project_dir) / "import_candidates.csv"


def decisions_path(project_dir: Path) -> Path:
    """Return the append-only import decisions path."""
    return manifests_dir(project_dir) / "import_decisions.jsonl"


def _rel(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _slug_filename(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def _csv_join(values: list[Any]) -> str:
    return "; ".join(str(value) for value in values if str(value).strip())


def _md_cell(value: Any) -> str:
    text = _csv_join(value) if isinstance(value, list) else str(value or "")
    return text.replace("|", "\\|").replace("\n", " ").strip() or "-"


def _markdown_table(rows: list[dict[str, Any]], columns: list[str]) -> list[str]:
    if not rows:
        return []
    lines = [
        "| " + " | ".join(columns) + " |",
        "| " + " | ".join("---" for _ in columns) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(_md_cell(row.get(column, "")) for column in columns) + " |")
    return lines


def _split_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value or "").strip()
    if not text:
        return []
    return [item.strip() for item in text.replace(",", ";").split(";") if item.strip()]


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "reviewed", "approved", "ready"}


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
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


def _write_csv(path: Path, rows: list[dict[str, Any]], columns: list[str]) -> None:
    ensure_dir(path.parent)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in columns})


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _candidate_to_row(candidate: ImportCandidate) -> dict[str, Any]:
    data = candidate.model_dump(mode="json")
    data["warnings"] = _csv_join(candidate.warnings)
    data["privacy_hits"] = _csv_join(candidate.privacy_hits)
    data["metadata"] = json.dumps(candidate.metadata, sort_keys=True)
    return data


def _candidate_columns() -> list[str]:
    return [
        "import_id",
        "source_path",
        "relative_source_path",
        "filename",
        "extension",
        "size_bytes",
        "content_hash",
        "detected_kind",
        "suggested_destination",
        "suggested_style_mode",
        "suggested_document_type",
        "suggested_asset_type",
        "duplicate_status",
        "privacy_status",
        "privacy_scan_status",
        "privacy_hits",
        "import_status",
        "warnings",
        "notes",
        "metadata",
    ]


def _write_candidates(project_dir: Path, candidates: list[ImportCandidate]) -> list[Path]:
    paths = [candidates_jsonl_path(project_dir), candidates_csv_path(project_dir)]
    write_jsonl(paths[0], candidates)
    _write_csv(paths[1], [_candidate_to_row(candidate) for candidate in candidates], _candidate_columns())
    return paths


def load_import_candidates(project_dir: Path) -> list[ImportCandidate]:
    """Load current import candidates from JSONL."""
    candidates: list[ImportCandidate] = []
    for row in _read_jsonl(candidates_jsonl_path(project_dir)):
        try:
            candidates.append(ImportCandidate.model_validate(row))
        except Exception:
            continue
    return candidates


def _append_decision(project_dir: Path, decision: ImportDecision) -> Path:
    path = decisions_path(project_dir)
    ensure_dir(path.parent)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(decision.model_dump(mode="json"), sort_keys=True) + "\n")
    return path


def _read_import_decisions(project_dir: Path) -> list[ImportDecision]:
    decisions: list[ImportDecision] = []
    for row in _read_jsonl(decisions_path(project_dir)):
        try:
            decisions.append(ImportDecision.model_validate(row))
        except Exception:
            continue
    return decisions


def _write_template(path: Path, columns: list[str]) -> None:
    if path.exists():
        return
    _write_csv(path, [], columns)


def _ensure_template(path: Path, columns: list[str]) -> None:
    if not path.exists():
        _write_template(path, columns)
        return
    rows = _read_csv(path)
    existing_columns = list(rows[0].keys()) if rows else []
    if not existing_columns:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.reader(handle)
            existing_columns = next(reader, [])
    merged = existing_columns + [column for column in columns if column not in existing_columns]
    if merged != existing_columns:
        _write_csv(path, rows, merged)


def prep_import_staging(project_dir: Path) -> PrepImportStagingResult:
    """Create import-staging folders and metadata templates."""
    project_dir = Path(project_dir)
    root = import_staging_dir(project_dir)
    created: list[Path] = []
    existing: list[Path] = []
    for rel in STAGING_FOLDERS:
        path = root / rel
        if path.exists():
            existing.append(path)
        else:
            ensure_dir(path)
            created.append(path)
    readme = root / "README_import_staging.md"
    if readme.exists():
        existing.append(readme)
    else:
        write_text(
            readme,
            "# Import Staging\n\n"
            "Drop candidate files under `incoming/`, run `import-scan`, review the plan, "
            "then use `import-apply` to copy approved files into active project folders.\n\n"
            "Staged files are not active manuscript inputs until imported.\n",
        )
        created.append(readme)
    templates = {
        manifests_dir(project_dir) / "writing_sample_import_template.csv": WRITING_TEMPLATE_COLUMNS,
        manifests_dir(project_dir) / "result_table_import_template.csv": RESULT_TEMPLATE_COLUMNS,
        manifests_dir(project_dir) / "source_import_template.csv": SOURCE_TEMPLATE_COLUMNS,
    }
    for path, columns in templates.items():
        if path.exists():
            _ensure_template(path, columns)
            existing.append(path)
        else:
            _write_template(path, columns)
            created.append(path)
    return PrepImportStagingResult(project_dir, root, created, existing)


def _incoming_folder_names() -> list[str]:
    return [folder.replace("incoming/", "") for folder in STAGING_FOLDERS if folder.startswith("incoming/")]


def _empty_candidate_guidance(project_dir: Path) -> list[str]:
    incoming = incoming_dir(project_dir).as_posix()
    lines = [
        "- No staged candidates found.",
        f"- Place files under `{incoming}/`.",
        "- Incoming subfolders: " + ", ".join(f"`{name}`" for name in _incoming_folder_names()),
        "- Next command: `python -m manuscriptforge.cli import-scan PROJECT_DIR --write`.",
    ]
    return lines


def _incoming_files(project_dir: Path) -> list[Path]:
    root = incoming_dir(project_dir)
    if not root.exists():
        return []
    return sorted(path for path in root.rglob("*") if path.is_file())


def _active_project_files(project_dir: Path) -> list[Path]:
    folders = ["inputs", "style_corpus", "feedback"]
    files: list[Path] = []
    for folder in folders:
        path = project_dir / folder
        if path.exists():
            files.extend(item for item in path.rglob("*") if item.is_file())
    return sorted(files)


def _text_signature(path: Path) -> str:
    if path.suffix.lower() not in TEXT_SIGNATURE_SUFFIXES:
        return ""
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")[:100_000]
    except OSError:
        return ""
    tokens = re.findall(r"[a-z0-9]+", text.lower())
    if len(tokens) < 8:
        return ""
    return sha256_text(" ".join(tokens[:300]))


def _privacy_scan(path: Path) -> tuple[str, list[str], list[str]]:
    """Scan a small text snippet for privacy categories without storing snippets."""
    suffix = path.suffix.lower()
    if suffix not in TEXT_PRIVACY_SCAN_SUFFIXES:
        return "not_scanned_binary", [], []
    try:
        size = path.stat().st_size
    except OSError:
        return "scan_failed", [], ["Privacy scan failed: could not stat file."]
    if size > MAX_PRIVACY_SCAN_BYTES:
        return "skipped_large_file", [], [f"Privacy content scan skipped for file over {MAX_PRIVACY_SCAN_BYTES} bytes."]
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return "scan_failed", [], ["Privacy scan failed: could not read text snippet."]
    lowered = text.lower()
    hits = sorted({term for term in PRIVACY_TERMS if term in lowered})
    for label, pattern in PRIVACY_PATTERNS.items():
        if pattern.search(text):
            hits.append(label)
    hits = sorted(set(hits))
    warnings = [f"privacy content category detected: {hit}" for hit in hits]
    return "scanned_text_snippet", hits, warnings


def _detect_document_type(path: Path, text: str) -> str:
    lowered = text.lower()
    for hint, document_type in DOCUMENT_TYPE_HINTS.items():
        if hint in lowered:
            return document_type
    return "unknown"


def _detect_style_mode(project_dir: Path, path: Path, text: str) -> str:
    try:
        parts = path.relative_to(incoming_dir(project_dir)).parts
    except ValueError:
        parts = path.parts
    for part in parts:
        normalized = part.lower().replace("-", "_").replace(" ", "_")
        mode = normalize_style_mode(normalized, fallback="")
        if mode:
            return mode
    lowered = text.lower().replace("-", "_").replace(" ", "_")
    for hint, mode in STYLE_MODE_HINTS.items():
        if hint in lowered:
            return mode
    return StyleMode.unknown.value


def _detect_kind(project_dir: Path, path: Path) -> str:
    suffix = path.suffix.lower()
    try:
        parts = path.relative_to(incoming_dir(project_dir)).parts
    except ValueError:
        parts = ()
    category = parts[0].lower() if parts else ""
    filename = path.name.lower()
    if category == "writing_samples" and suffix in STYLE_SUFFIXES:
        return "writing_sample"
    if category == "result_tables" and suffix in TABLE_SUFFIXES:
        return "result_table"
    if category == "source_pdfs" and suffix == ".pdf":
        return "source_pdf"
    if category == "source_pdfs":
        return "miscellaneous"
    if category == "feedback" and suffix == ".jsonl":
        return "feedback_jsonl"
    if category == "sources":
        if suffix == ".bib":
            return "bibtex"
        if suffix in SOURCE_SUFFIXES:
            return "source_list"
    if filename in MANUSCRIPT_NOTE_FILES:
        return "manuscript_note"
    if suffix == ".bib":
        return "bibtex"
    if suffix in TABLE_SUFFIXES:
        return "result_table"
    if suffix == ".jsonl":
        return "feedback_jsonl"
    if suffix == ".pdf":
        return "source_pdf"
    if suffix in STYLE_SUFFIXES:
        return "writing_sample"
    return "miscellaneous"


def _suggest_asset_type(kind: str) -> str:
    return {
        "writing_sample": "writing_sample",
        "result_table": "result_table",
        "source_pdf": "source_pdf",
        "source_list": "source_list",
        "bibtex": "bibtex",
        "feedback_jsonl": "feedback",
        "manuscript_note": "manuscript_note",
    }.get(kind, "other")


def _suggest_destination(kind: str, filename: str, style_mode: str) -> str:
    if kind == "writing_sample":
        return f"style_corpus/{normalize_style_mode(style_mode)}/{filename}"
    if kind == "result_table":
        return f"inputs/results_tables/{filename}"
    if kind == "source_pdf":
        return f"inputs/source_pdfs/{filename}"
    if kind in {"source_list", "bibtex", "manuscript_note"}:
        return f"inputs/{filename}"
    if kind == "feedback_jsonl":
        return f"feedback/{filename}"
    return f"planning/import_staging/accepted/miscellaneous/{filename}"


def _privacy_warnings(path: Path, kind: str) -> tuple[str, list[str], str, list[str]]:
    searchable = path.as_posix().lower()
    warnings = [f"privacy term in path: {term}" for term in sorted(PRIVACY_TERMS) if term in searchable]
    scan_status, privacy_hits, scan_warnings = _privacy_scan(path)
    warnings.extend(scan_warnings)
    if privacy_hits:
        return "needs_review", sorted(set(warnings)), scan_status, privacy_hits
    if kind in {"writing_sample", "source_pdf", "result_table"}:
        return "needs_review", sorted(set(warnings)), scan_status, privacy_hits
    return ("needs_review" if warnings else "unknown"), sorted(set(warnings)), scan_status, privacy_hits


def _initial_status(kind: str, duplicate_status: str, privacy_status: str) -> str:
    if duplicate_status != "new":
        return "needs_metadata"
    if kind in {"writing_sample", "result_table", "source_pdf"}:
        return "needs_metadata"
    if privacy_status == "needs_review":
        return "needs_metadata"
    if kind in {"source_list", "bibtex", "feedback_jsonl", "manuscript_note"}:
        return "ready"
    return "staged"


def _duplicate_maps(project_dir: Path) -> tuple[dict[str, Path], dict[str, Path], dict[str, Path]]:
    by_hash: dict[str, Path] = {}
    by_name: dict[str, Path] = {}
    by_signature: dict[str, Path] = {}
    for path in _active_project_files(project_dir):
        with suppress(OSError):
            by_hash.setdefault(sha256_file(path), path)
        by_name.setdefault(_slug_filename(path.name), path)
        signature = _text_signature(path)
        if signature:
            by_signature.setdefault(signature, path)
    return by_hash, by_name, by_signature


def _duplicate_status(
    path: Path,
    content_hash: str,
    active_hashes: dict[str, Path],
    active_names: dict[str, Path],
    active_signatures: dict[str, Path],
    staged_hash_counts: Counter[str],
) -> tuple[str, list[str], list[str]]:
    warnings: list[str] = []
    matches: list[str] = []
    if content_hash in active_hashes or staged_hash_counts[content_hash] > 1:
        warnings.append("Exact duplicate content hash detected.")
        if content_hash in active_hashes:
            matches.append(f"active:{active_hashes[content_hash].as_posix()}")
        if staged_hash_counts[content_hash] > 1:
            matches.append("staged:same_content_hash")
        return "duplicate_same_hash", warnings, matches
    if _slug_filename(path.name) in active_names:
        warnings.append("Possible duplicate filename detected.")
        matches.append(f"active:{active_names[_slug_filename(path.name)].as_posix()}")
        return "possible_duplicate_name", warnings, matches
    signature = _text_signature(path)
    if signature and signature in active_signatures:
        warnings.append("Possible duplicate text signature detected.")
        matches.append(f"active:{active_signatures[signature].as_posix()}")
        return "possible_duplicate_content", warnings, matches
    return "new", warnings, matches


def _previous_candidates(project_dir: Path) -> dict[str, ImportCandidate]:
    candidates = load_import_candidates(project_dir)
    mapping: dict[str, ImportCandidate] = {}
    for candidate in candidates:
        mapping[candidate.import_id] = candidate
        mapping.setdefault(candidate.content_hash, candidate)
        mapping.setdefault(candidate.relative_source_path, candidate)
    return mapping


def _merge_previous(candidate: ImportCandidate, previous: ImportCandidate | None) -> ImportCandidate:
    if previous is None:
        return candidate
    candidate.import_status = previous.import_status
    candidate.privacy_status = previous.privacy_status
    candidate.notes = previous.notes
    candidate.metadata = {**previous.metadata, **candidate.metadata}
    if previous.suggested_destination:
        candidate.suggested_destination = previous.suggested_destination
    if previous.suggested_style_mode != StyleMode.unknown.value:
        candidate.suggested_style_mode = previous.suggested_style_mode
    if previous.suggested_document_type != "unknown":
        candidate.suggested_document_type = previous.suggested_document_type
    return candidate


def _candidate_for_path(
    project_dir: Path,
    path: Path,
    active_hashes: dict[str, Path],
    active_names: dict[str, Path],
    active_signatures: dict[str, Path],
    staged_hash_counts: Counter[str],
) -> ImportCandidate:
    content_hash = sha256_file(path)
    rel = _rel(path, project_dir)
    rel_staging = _rel(path, incoming_dir(project_dir))
    detection_text = f"{rel_staging} {path.stem}"
    kind = _detect_kind(project_dir, path)
    style_mode = _detect_style_mode(project_dir, path, detection_text)
    document_type = _detect_document_type(path, detection_text)
    duplicate_status, duplicate_warnings, duplicate_matches = _duplicate_status(
        path,
        content_hash,
        active_hashes,
        active_names,
        active_signatures,
        staged_hash_counts,
    )
    privacy_status, privacy_warnings, privacy_scan_status, privacy_hits = _privacy_warnings(path, kind)
    warnings = sorted(set(duplicate_warnings + privacy_warnings))
    metadata: dict[str, Any] = {
        "duplicate_matches": duplicate_matches,
        "text_signature": _text_signature(path),
    }
    if kind == "source_pdf":
        metadata["source_pdf_readiness_note"] = (
            "Source PDF can become ready after privacy review when the filename is descriptive; "
            "DOI/PMID/source metadata is still recommended for stronger citation mapping."
        )
        warnings.append("Source PDF needs privacy review and descriptive metadata before import.")
    return ImportCandidate(
        import_id=stable_id("imp", f"{rel}:{content_hash}"),
        source_path=str(path),
        relative_source_path=rel,
        filename=path.name,
        extension=path.suffix.lower(),
        size_bytes=path.stat().st_size,
        content_hash=content_hash,
        detected_kind=kind,  # type: ignore[arg-type]
        suggested_destination=_suggest_destination(kind, path.name, style_mode),
        suggested_style_mode=style_mode,
        suggested_document_type=document_type,
        suggested_asset_type=_suggest_asset_type(kind),
        duplicate_status=duplicate_status,  # type: ignore[arg-type]
        privacy_status=privacy_status,  # type: ignore[arg-type]
        privacy_scan_status=privacy_scan_status,  # type: ignore[arg-type]
        privacy_hits=privacy_hits,
        import_status=_initial_status(kind, duplicate_status, privacy_status),  # type: ignore[arg-type]
        warnings=sorted(set(warnings)),
        metadata=metadata,
    )


def _scan_summary(candidates: list[ImportCandidate]) -> dict[str, Any]:
    return {
        "generated_at": utc_iso(),
        "candidate_count": len(candidates),
        "by_kind": dict(Counter(candidate.detected_kind for candidate in candidates)),
        "by_status": dict(Counter(candidate.import_status for candidate in candidates)),
        "by_duplicate_status": dict(Counter(candidate.duplicate_status for candidate in candidates)),
        "by_privacy_scan_status": dict(Counter(candidate.privacy_scan_status for candidate in candidates)),
        "needs_privacy_review": sum(1 for candidate in candidates if candidate.privacy_status == "needs_review"),
        "needs_metadata": sum(1 for candidate in candidates if candidate.import_status == "needs_metadata"),
        "ready": sum(1 for candidate in candidates if candidate.import_status == "ready"),
        "warnings": sum(len(candidate.warnings) for candidate in candidates),
    }


def _candidate_review_rows(candidates: list[ImportCandidate]) -> list[dict[str, Any]]:
    return [
        {
            "import_id": candidate.import_id,
            "filename": candidate.filename,
            "detected_kind": candidate.detected_kind,
            "suggested_style_mode": candidate.suggested_style_mode,
            "suggested_document_type": candidate.suggested_document_type,
            "duplicate_status": candidate.duplicate_status,
            "privacy_status": candidate.privacy_status,
            "import_status": candidate.import_status,
            "suggested_destination": candidate.suggested_destination,
            "warnings": _csv_join(candidate.warnings),
            "notes": candidate.notes,
        }
        for candidate in candidates
    ]


def _candidate_table_lines(candidates: list[ImportCandidate]) -> list[str]:
    columns = [
        "import_id",
        "filename",
        "detected_kind",
        "suggested_style_mode",
        "suggested_document_type",
        "duplicate_status",
        "privacy_status",
        "import_status",
        "suggested_destination",
        "warnings",
        "notes",
    ]
    return _markdown_table(_candidate_review_rows(candidates), columns)


def _duplicate_group_data(candidates: list[ImportCandidate]) -> dict[str, Any]:
    by_hash: dict[str, list[str]] = {}
    by_name: dict[str, list[str]] = {}
    by_signature: dict[str, list[str]] = {}
    active_matches: list[dict[str, Any]] = []
    already_imported: list[dict[str, Any]] = []
    for candidate in candidates:
        by_hash.setdefault(candidate.content_hash, []).append(candidate.import_id)
        by_name.setdefault(_slug_filename(candidate.filename), []).append(candidate.import_id)
        signature = str(candidate.metadata.get("text_signature") or "")
        if signature:
            by_signature.setdefault(signature, []).append(candidate.import_id)
        matches = [match for match in _split_list(candidate.metadata.get("duplicate_matches")) if match.startswith("active:")]
        if matches:
            active_matches.append(
                {
                    "import_id": candidate.import_id,
                    "filename": candidate.filename,
                    "duplicate_status": candidate.duplicate_status,
                    "matches": matches,
                }
            )
        if candidate.import_status == "imported":
            already_imported.append({"import_id": candidate.import_id, "filename": candidate.filename})
    return {
        "exact_hash_duplicates": {key: ids for key, ids in by_hash.items() if len(ids) > 1},
        "same_normalized_filename": {key: ids for key, ids in by_name.items() if len(ids) > 1 and key},
        "possible_text_signature_duplicates": {key: ids for key, ids in by_signature.items() if len(ids) > 1},
        "already_imported_duplicates": already_imported,
        "duplicates_against_active_project": active_matches,
    }


def _duplicate_group_lines(data: dict[str, Any]) -> list[str]:
    lines = ["## Duplicate Groups", ""]
    labels = [
        ("exact_hash_duplicates", "Exact hash duplicates"),
        ("same_normalized_filename", "Same normalized filename"),
        ("possible_text_signature_duplicates", "Possible text-signature duplicates"),
    ]
    for key, label in labels:
        groups = data.get(key, {})
        if groups:
            lines.append(f"- {label}:")
            for group_key, ids in sorted(groups.items()):
                lines.append(f"  - `{group_key}`: " + ", ".join(f"`{item}`" for item in ids))
        else:
            lines.append(f"- {label}: none")
    active = data.get("duplicates_against_active_project", [])
    if active:
        lines.append("- Duplicate candidates against active project files:")
        for row in active:
            lines.append(
                f"  - `{row['import_id']}` `{row['filename']}` ({row['duplicate_status']}): "
                + ", ".join(f"`{match}`" for match in row.get("matches", []))
            )
    else:
        lines.append("- Duplicate candidates against active project files: none")
    imported = data.get("already_imported_duplicates", [])
    if imported:
        lines.append("- Already imported candidates:")
        for row in imported:
            lines.append(f"  - `{row['import_id']}` `{row['filename']}`")
    else:
        lines.append("- Already imported candidates: none")
    lines.append("- Recommendation: review duplicates manually; ManuscriptForge does not delete or auto-reject staged files.")
    return lines


def _render_scan_report(project_dir: Path, candidates: list[ImportCandidate], summary: dict[str, Any]) -> str:
    lines = [
        "# Import Scan Report",
        "",
        f"- Candidates: {summary['candidate_count']}",
        f"- Needs privacy review: {summary['needs_privacy_review']}",
        f"- Warning flags: {summary['warnings']}",
        "",
        "## By Kind",
    ]
    for kind, count in sorted(summary["by_kind"].items()):
        lines.append(f"- {kind}: {count}")
    if not summary["by_kind"]:
        lines.append("- No staged files found.")
    lines.extend(["", "## Privacy Scan Status"])
    for status, count in sorted(summary.get("by_privacy_scan_status", {}).items()):
        lines.append(f"- {status}: {count}")
    if not summary.get("by_privacy_scan_status"):
        lines.append("- none")
    lines.extend(["", "## Candidates"])
    lines.extend(_candidate_table_lines(candidates[:200]))
    if not candidates:
        lines.extend(_empty_candidate_guidance(project_dir))
    lines.extend([""] + _duplicate_group_lines(_duplicate_group_data(candidates)))
    return "\n".join(lines).rstrip() + "\n"


def scan_import_staging(project_dir: Path, *, write: bool = False) -> ImportScanResult:
    """Scan incoming staged files without importing them."""
    project_dir = Path(project_dir)
    prep_import_staging(project_dir)
    files = _incoming_files(project_dir)
    staged_hash_counts = Counter(sha256_file(path) for path in files)
    active_hashes, active_names, active_signatures = _duplicate_maps(project_dir)
    previous = _previous_candidates(project_dir)
    candidates: list[ImportCandidate] = []
    for path in files:
        candidate = _candidate_for_path(
            project_dir,
            path,
            active_hashes,
            active_names,
            active_signatures,
            staged_hash_counts,
        )
        old = previous.get(candidate.import_id) or previous.get(candidate.content_hash) or previous.get(candidate.relative_source_path)
        candidates.append(_merge_previous(candidate, old))
    summary = _scan_summary(candidates)
    report_path = reports_dir(project_dir) / "import_scan_report.md"
    json_path = reports_dir(project_dir) / "import_scan_report.json"
    written_paths: list[Path] = []
    if write:
        written_paths.extend(_write_candidates(project_dir, candidates))
        write_text(report_path, _render_scan_report(project_dir, candidates, summary))
        write_json(json_path, {"generated_at": utc_iso(), "summary": summary, "candidates": candidates})
        written_paths.extend([report_path, json_path])
    return ImportScanResult(project_dir, candidates, report_path, json_path, written_paths, summary)


def _load_or_scan(project_dir: Path) -> list[ImportCandidate]:
    candidates = load_import_candidates(project_dir)
    if candidates:
        return candidates
    return scan_import_staging(project_dir, write=True).candidates


def _kind_matches(candidate: ImportCandidate, kind: str) -> bool:
    if kind == "all":
        return True
    if kind == "source":
        return candidate.detected_kind in {"source_list", "bibtex"}
    return candidate.detected_kind == kind


def _plan_rows(candidates: list[ImportCandidate]) -> list[dict[str, Any]]:
    return [
        {
            "import_id": candidate.import_id,
            "filename": candidate.filename,
            "detected_kind": candidate.detected_kind,
            "import_status": candidate.import_status,
            "privacy_status": candidate.privacy_status,
            "privacy_scan_status": candidate.privacy_scan_status,
            "privacy_hits": _csv_join(candidate.privacy_hits),
            "duplicate_status": candidate.duplicate_status,
            "source_path": candidate.relative_source_path,
            "suggested_destination": candidate.suggested_destination,
            "suggested_style_mode": candidate.suggested_style_mode,
            "suggested_document_type": candidate.suggested_document_type,
            "warnings": _csv_join(candidate.warnings),
            "notes": candidate.notes,
        }
        for candidate in candidates
    ]


def _render_plan(project_dir: Path, data: dict[str, Any]) -> str:
    lines = [
        "# Import Plan",
        "",
        f"- Candidate count: {data['candidate_count']}",
        f"- Ready: {data['ready_count']}",
        f"- Needs metadata: {data['needs_metadata_count']}",
        f"- Needs privacy review: {data['needs_privacy_review_count']}",
        f"- Duplicates: {data['duplicate_count']}",
        "",
        "## By Kind",
    ]
    for kind, count in sorted(data["by_kind"].items()):
        lines.append(f"- {kind}: {count}")
    if not data["by_kind"]:
        lines.append("- No candidates.")
    lines.extend(["", "## Proposed Imports"])
    lines.extend(
        _markdown_table(
            data["rows"][:200],
            [
                "import_id",
                "filename",
                "detected_kind",
                "suggested_style_mode",
                "suggested_document_type",
                "duplicate_status",
                "privacy_status",
                "import_status",
                "suggested_destination",
                "warnings",
                "notes",
            ],
        )
    )
    if not data["rows"]:
        lines.append("- No matching candidates.")
        lines.extend(_empty_candidate_guidance(project_dir))
    lines.extend(["", "## Warnings"])
    lines.extend(f"- {warning}" for warning in data["warnings"] or ["None."])
    lines.extend([""] + _duplicate_group_lines(data.get("duplicate_groups", {})))
    return "\n".join(lines).rstrip() + "\n"


def build_import_plan(project_dir: Path, *, kind: str = "all", mode: str | None = None) -> ImportPlanResult:
    """Build a proposed import plan without changing active project files."""
    project_dir = Path(project_dir)
    selected_mode = normalize_style_mode(mode, fallback="") if mode else ""
    candidates = [
        candidate
        for candidate in _load_or_scan(project_dir)
        if _kind_matches(candidate, kind)
        and (not selected_mode or candidate.suggested_style_mode == selected_mode)
    ]
    warnings = []
    for candidate in candidates:
        if candidate.duplicate_status != "new":
            warnings.append(f"{candidate.import_id}: duplicate status is {candidate.duplicate_status}")
        if candidate.privacy_status == "needs_review":
            warnings.append(f"{candidate.import_id}: privacy review needed")
        if candidate.import_status == "needs_metadata":
            warnings.append(f"{candidate.import_id}: metadata review needed")
    rows = _plan_rows(candidates)
    data = {
        "generated_at": utc_iso(),
        "kind": kind,
        "mode": selected_mode or None,
        "candidate_count": len(candidates),
        "ready_count": sum(1 for candidate in candidates if candidate.import_status == "ready"),
        "needs_metadata_count": sum(1 for candidate in candidates if candidate.import_status == "needs_metadata"),
        "needs_privacy_review_count": sum(1 for candidate in candidates if candidate.privacy_status == "needs_review"),
        "duplicate_count": sum(1 for candidate in candidates if candidate.duplicate_status != "new"),
        "by_kind": dict(Counter(candidate.detected_kind for candidate in candidates)),
        "duplicate_groups": _duplicate_group_data(candidates),
        "warnings": warnings,
        "rows": rows,
    }
    report_path = reports_dir(project_dir) / "import_plan.md"
    json_path = reports_dir(project_dir) / "import_plan.json"
    csv_path = manifests_dir(project_dir) / "import_plan.csv"
    write_text(report_path, _render_plan(project_dir, data))
    write_json(json_path, data)
    _write_csv(
        csv_path,
        rows,
        [
            "import_id",
            "filename",
            "detected_kind",
            "import_status",
            "privacy_status",
            "privacy_scan_status",
            "privacy_hits",
            "duplicate_status",
            "source_path",
            "suggested_destination",
            "suggested_style_mode",
            "suggested_document_type",
            "warnings",
            "notes",
        ],
    )
    return ImportPlanResult(project_dir, candidates, report_path, json_path, csv_path, data)


def _safe_destination(project_dir: Path, relative_destination: str) -> Path:
    destination = project_dir / relative_destination
    if not destination.exists():
        return destination
    stem = destination.stem
    suffix = destination.suffix
    for index in range(2, 1000):
        candidate = destination.with_name(f"{stem}_{index}{suffix}")
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"Could not find a safe destination suffix for {relative_destination}.")


def _select_candidates(
    project_dir: Path,
    import_id: str | None,
    all_ready: bool,
) -> tuple[list[ImportCandidate], list[ImportCandidate]]:
    candidates = _load_or_scan(project_dir)
    if import_id:
        selected = [candidate for candidate in candidates if candidate.import_id == import_id]
        if not selected:
            raise ValueError(f"No import candidate found with import_id '{import_id}'.")
        return selected, candidates
    if all_ready:
        selected = [candidate for candidate in candidates if candidate.import_status == "ready"]
        return selected, candidates
    raise ValueError("Pass --import-id or --all-ready.")


def _render_apply_report(data: dict[str, Any]) -> str:
    lines = [
        "# Import Apply Report",
        "",
        f"- Dry run: {data['dry_run']}",
        f"- Selected: {data['selected_count']}",
        f"- Copied/moved: {len(data['copied'])}",
        "",
        "## Actions",
    ]
    for item in data["copied"]:
        verb = "would copy" if data["dry_run"] else item["action"]
        lines.append(f"- {verb}: `{item['source']}` -> `{item['destination']}`")
    if not data["copied"]:
        lines.append("- No files selected.")
    lines.extend(["", "## Warnings"])
    lines.extend(f"- {warning}" for warning in data["warnings"] or ["None."])
    lines.extend(["", "## Next Step", "- Run `intake-scan PROJECT_DIR --write` after a real import."])
    return "\n".join(lines).rstrip() + "\n"


def apply_imports(
    project_dir: Path,
    *,
    import_id: str | None = None,
    all_ready: bool = False,
    copy: bool = True,
    move: bool = False,
    dry_run: bool = False,
) -> ImportApplyResult:
    """Copy or explicitly move staged files into active project folders."""
    project_dir = Path(project_dir)
    if move and copy:
        copy = False
    action = "import_move" if move else "import_copy"
    selected, candidates = _select_candidates(project_dir, import_id, all_ready)
    copied: list[dict[str, str]] = []
    warnings: list[str] = []
    candidates_by_id = {candidate.import_id: candidate for candidate in candidates}
    for candidate in selected:
        source = Path(candidate.source_path)
        if not source.exists():
            warnings.append(f"Source file is missing: {candidate.relative_source_path}")
            continue
        destination = _safe_destination(project_dir, candidate.suggested_destination)
        if destination.relative_to(project_dir).as_posix() != candidate.suggested_destination:
            warnings.append(
                f"Destination existed; using safe suffixed path: {destination.relative_to(project_dir).as_posix()}"
            )
        copied.append(
            {
                "import_id": candidate.import_id,
                "source": candidate.relative_source_path,
                "destination": destination.relative_to(project_dir).as_posix(),
                "action": action,
            }
        )
        if dry_run:
            continue
        ensure_dir(destination.parent)
        shutil.copy2(source, destination)
        if sha256_file(source) != sha256_file(destination):
            destination.unlink(missing_ok=True)
            warnings.append(f"Copy verification failed for {candidate.import_id}; destination removed.")
            continue
        if move:
            source.unlink()
        updated = candidates_by_id[candidate.import_id]
        updated.import_status = "imported"
        updated.suggested_destination = destination.relative_to(project_dir).as_posix()
        decision = ImportDecision(
            decision_id=stable_id("impdec", f"{candidate.import_id}:{action}:{utc_iso()}"),
            import_id=candidate.import_id,
            action=action,  # type: ignore[arg-type]
            destination_path=updated.suggested_destination,
            style_mode=updated.suggested_style_mode,
            document_type=updated.suggested_document_type,
            approved_for=_split_list(updated.metadata.get("approved_for") or updated.metadata.get("approved_for_modes")),
            note=str(updated.metadata.get("decision_note") or "Imported from staging."),
            decided_at=utc_iso(),
        )
        _append_decision(project_dir, decision)
    if not dry_run:
        _write_candidates(project_dir, list(candidates_by_id.values()))
    data = {
        "generated_at": utc_iso(),
        "dry_run": dry_run,
        "selected_count": len(selected),
        "copied": copied,
        "warnings": warnings,
    }
    report_path = reports_dir(project_dir) / "import_apply_report.md"
    json_path = reports_dir(project_dir) / "import_apply_report.json"
    write_text(report_path, _render_apply_report(data))
    write_json(json_path, data)
    return ImportApplyResult(project_dir, selected, copied, warnings, report_path, json_path, decisions_path(project_dir), dry_run)


def reject_import(
    project_dir: Path,
    *,
    import_id: str,
    reason: str = "",
    archive: bool = False,
) -> ImportRejectResult:
    """Reject a staged import candidate without deleting its source file."""
    project_dir = Path(project_dir)
    candidates = _load_or_scan(project_dir)
    matched = [candidate for candidate in candidates if candidate.import_id == import_id]
    if not matched:
        raise ValueError(f"No import candidate found with import_id '{import_id}'.")
    archive_paths: list[Path] = []
    for candidate in matched:
        candidate.import_status = "rejected"
        if reason:
            candidate.notes = reason
        if archive and Path(candidate.source_path).exists():
            destination = _safe_destination(
                project_dir,
                f"planning/import_staging/archive/rejected/{candidate.filename}",
            )
            ensure_dir(destination.parent)
            shutil.copy2(candidate.source_path, destination)
            archive_paths.append(destination)
        decision = ImportDecision(
            decision_id=stable_id("impdec", f"{candidate.import_id}:reject:{utc_iso()}"),
            import_id=candidate.import_id,
            action="reject",
            destination_path="; ".join(path.relative_to(project_dir).as_posix() for path in archive_paths),
            style_mode=candidate.suggested_style_mode,
            document_type=candidate.suggested_document_type,
            note=reason,
            decided_at=utc_iso(),
        )
        _append_decision(project_dir, decision)
    _write_candidates(project_dir, candidates)
    return ImportRejectResult(project_dir, matched, archive_paths, decisions_path(project_dir))


def _summary_data(project_dir: Path, candidates: list[ImportCandidate]) -> dict[str, Any]:
    decisions = _read_import_decisions(project_dir)
    duplicate_count = sum(1 for candidate in candidates if candidate.duplicate_status != "new")
    needs_metadata = sum(1 for candidate in candidates if candidate.import_status == "needs_metadata")
    needs_privacy = sum(1 for candidate in candidates if candidate.privacy_status == "needs_review")
    scan_report = reports_dir(project_dir) / "import_scan_report.json"
    last_scan = ""
    if scan_report.exists():
        with suppress(Exception):
            loaded = json.loads(scan_report.read_text(encoding="utf-8"))
            last_scan = str(loaded.get("generated_at") or loaded.get("summary", {}).get("generated_at") or "")
    return {
        "generated_at": utc_iso(),
        "last_import_scan": last_scan,
        "staging_dir": import_staging_dir(project_dir).relative_to(project_dir).as_posix()
        if import_staging_dir(project_dir).exists()
        else "",
        "candidate_count": len(candidates),
        "staged_files": sum(1 for candidate in candidates if candidate.import_status == "staged"),
        "ready_files": sum(1 for candidate in candidates if candidate.import_status == "ready"),
        "imported_files": sum(1 for candidate in candidates if candidate.import_status == "imported"),
        "rejected_files": sum(1 for candidate in candidates if candidate.import_status == "rejected"),
        "archived_files": sum(1 for candidate in candidates if candidate.import_status == "archived"),
        "duplicate_files": duplicate_count,
        "files_needing_metadata": needs_metadata,
        "files_needing_privacy_review": needs_privacy,
        "by_kind": dict(Counter(candidate.detected_kind for candidate in candidates)),
        "by_status": dict(Counter(candidate.import_status for candidate in candidates)),
        "by_privacy_scan_status": dict(Counter(candidate.privacy_scan_status for candidate in candidates)),
        "duplicate_groups": _duplicate_group_data(candidates),
        "candidates": _candidate_review_rows(candidates),
        "decision_count": len(decisions),
        "suggested_next_actions": _next_actions(candidates, needs_metadata, needs_privacy, duplicate_count),
    }


def _next_actions(
    candidates: list[ImportCandidate],
    needs_metadata: int,
    needs_privacy: int,
    duplicate_count: int,
) -> list[str]:
    if not candidates:
        return ["Drop files under planning/import_staging/incoming/ and run import-scan --write."]
    actions = []
    if duplicate_count:
        actions.append("Review duplicate candidates before importing.")
    if needs_privacy:
        actions.append("Complete privacy review for sensitive writing, PDF, and result-table files.")
    if needs_metadata:
        actions.append("Fill an import metadata template and run import-update-metadata.")
    if any(candidate.import_status == "ready" for candidate in candidates):
        actions.append("Run import-apply --all-ready --copy --dry-run, then rerun without --dry-run if correct.")
    return actions or ["Run intake-scan --write after imports, then continue with style/data checks."]


def _render_summary(project_dir: Path, data: dict[str, Any]) -> str:
    lines = [
        "# Import Summary",
        "",
        f"- Candidates: {data['candidate_count']}",
        f"- Staged: {data['staged_files']}",
        f"- Ready: {data['ready_files']}",
        f"- Imported: {data['imported_files']}",
        f"- Rejected: {data['rejected_files']}",
        f"- Duplicates: {data['duplicate_files']}",
        f"- Needs metadata: {data['files_needing_metadata']}",
        f"- Needs privacy review: {data['files_needing_privacy_review']}",
        f"- Last import scan: {data.get('last_import_scan') or 'none'}",
        "",
        "## Suggested Next Actions",
    ]
    lines.extend(f"- {item}" for item in data["suggested_next_actions"])
    lines.extend(["", "## Candidates"])
    candidate_rows = data.get("candidates", [])
    if candidate_rows:
        lines.extend(
            _markdown_table(
                candidate_rows[:200],
                [
                    "import_id",
                    "filename",
                    "detected_kind",
                    "suggested_style_mode",
                    "suggested_document_type",
                    "duplicate_status",
                    "privacy_status",
                    "import_status",
                    "suggested_destination",
                    "warnings",
                    "notes",
                ],
            )
        )
    else:
        lines.extend(_empty_candidate_guidance(project_dir))
    lines.extend([""] + _duplicate_group_lines(data.get("duplicate_groups", {})))
    return "\n".join(lines).rstrip() + "\n"


def summarize_import_staging(project_dir: Path, *, write: bool = True) -> ImportSummaryResult:
    """Write or return a summary of the import-staging state."""
    project_dir = Path(project_dir)
    candidates = _load_or_scan(project_dir) if import_staging_dir(project_dir).exists() else []
    data = _summary_data(project_dir, candidates)
    report_path = reports_dir(project_dir) / "import_summary.md"
    json_path = reports_dir(project_dir) / "import_summary.json"
    if write:
        write_text(report_path, _render_summary(project_dir, data))
        write_json(json_path, data)
    return ImportSummaryResult(project_dir, report_path, json_path, data)


def import_staging_status(project_dir: Path) -> dict[str, Any]:
    """Return a lightweight import-staging status for other reports."""
    project_dir = Path(project_dir)
    root = import_staging_dir(project_dir)
    incoming = _incoming_files(project_dir) if root.exists() else []
    candidates = load_import_candidates(project_dir)
    data = _summary_data(project_dir, candidates) if candidates else {
        "candidate_count": 0,
        "staged_files": 0,
        "ready_files": 0,
        "imported_files": 0,
        "rejected_files": 0,
        "archived_files": 0,
        "duplicate_files": 0,
        "files_needing_metadata": 0,
        "files_needing_privacy_review": 0,
    }
    data["staging_present"] = root.exists()
    data["incoming_file_count"] = len(incoming)
    data["pending_import_count"] = sum(
        1 for candidate in candidates if candidate.import_status not in {"imported", "rejected", "archived"}
    )
    data["staged_file_count"] = len(incoming)
    data["ready_import_count"] = data.get("ready_files", 0)
    data["needs_metadata_count"] = data.get("files_needing_metadata", 0)
    data["needs_privacy_review_count"] = data.get("files_needing_privacy_review", 0)
    data["duplicate_candidate_count"] = data.get("duplicate_files", 0)
    return data


def _match_candidate(row: dict[str, str], candidates: list[ImportCandidate]) -> ImportCandidate | None:
    raw_path = str(row.get("source_path") or row.get("source_path_or_key") or "").strip()
    destination_filename = str(row.get("destination_filename") or "").strip()
    keys = {raw_path.replace("\\", "/"), Path(raw_path).name if raw_path else "", destination_filename}
    keys = {key for key in keys if key}
    for candidate in candidates:
        candidate_keys = {
            candidate.relative_source_path,
            candidate.source_path.replace("\\", "/"),
            candidate.filename,
        }
        if keys & candidate_keys:
            return candidate
    return None


def _set_destination_filename(candidate: ImportCandidate, filename: str) -> None:
    if not filename:
        return
    destination = Path(candidate.suggested_destination)
    candidate.suggested_destination = destination.with_name(filename).as_posix()


def _descriptive_filename(path_or_name: str) -> bool:
    stem = Path(path_or_name).stem.lower()
    tokens = [
        token
        for token in re.findall(r"[a-z0-9]+", stem)
        if token not in {"pdf", "source", "paper", "article", "file", "document", "scan"}
    ]
    return len(tokens) >= 2 or (len(tokens) == 1 and len(tokens[0]) >= 8)


def _refresh_candidate_status(candidate: ImportCandidate) -> None:
    reviewed = _truthy(candidate.metadata.get("privacy_reviewed")) or _truthy(candidate.metadata.get("deidentified"))
    if reviewed:
        candidate.privacy_status = "reviewed"
    if candidate.duplicate_status != "new":
        candidate.import_status = "needs_metadata"
        return
    use_in_pilot = _truthy(candidate.metadata.get("use_in_pilot"))
    schema_ready = str(candidate.metadata.get("schema_review_status", "")).lower() in {"approved", "inferred"}
    citation_ready = _truthy(candidate.metadata.get("approved_for_citation_mapping"))
    feedback_ready = candidate.detected_kind == "feedback_jsonl"
    source_ready = candidate.detected_kind in {"bibtex", "source_list"} and citation_ready
    table_ready = candidate.detected_kind == "result_table" and reviewed and schema_ready
    style_ready = candidate.detected_kind == "writing_sample" and reviewed and use_in_pilot
    pdf_ready = candidate.detected_kind == "source_pdf" and reviewed and _descriptive_filename(candidate.filename)
    note_ready = candidate.detected_kind == "manuscript_note" and use_in_pilot
    if style_ready or table_ready or source_ready or pdf_ready or note_ready or feedback_ready:
        candidate.import_status = "ready"
    elif candidate.detected_kind == "source_pdf":
        candidate.import_status = "needs_metadata"
    if candidate.detected_kind == "source_pdf" and reviewed:
        warning = "Source PDF lacks DOI/PMID metadata; citation mapping will be stronger after source metadata review."
        if warning not in candidate.warnings:
            candidate.warnings.append(warning)


def update_import_metadata(project_dir: Path, *, from_csv: Path) -> ImportMetadataUpdateResult:
    """Update staged candidate metadata from a user-edited CSV template."""
    project_dir = Path(project_dir)
    candidates = _load_or_scan(project_dir)
    rows = _read_csv(Path(from_csv))
    updated: list[ImportCandidate] = []
    unmatched: list[dict[str, str]] = []
    for row in rows:
        candidate = _match_candidate(row, candidates)
        if candidate is None:
            unmatched.append(row)
            continue
        for key, value in row.items():
            text = str(value or "").strip()
            if not text:
                continue
            if key == "destination_filename":
                _set_destination_filename(candidate, text)
            elif key == "style_mode":
                candidate.suggested_style_mode = normalize_style_mode(text)
                if candidate.detected_kind == "writing_sample":
                    candidate.suggested_destination = _suggest_destination(
                        candidate.detected_kind,
                        Path(candidate.suggested_destination).name,
                        candidate.suggested_style_mode,
                    )
            elif key == "document_type":
                candidate.suggested_document_type = text
            elif key == "notes":
                candidate.notes = text
            elif key == "decision_note":
                candidate.metadata[key] = text
            elif key in {"source_path", "source_path_or_key"}:
                continue
            else:
                candidate.metadata[key] = text
        if _truthy(candidate.metadata.get("privacy_reviewed")):
            candidate.privacy_status = "reviewed"
        _refresh_candidate_status(candidate)
        decision = ImportDecision(
            decision_id=stable_id("impdec", f"{candidate.import_id}:metadata:{utc_iso()}"),
            import_id=candidate.import_id,
            action="update_metadata",
            destination_path=candidate.suggested_destination,
            style_mode=candidate.suggested_style_mode,
            document_type=candidate.suggested_document_type,
            approved_for=_split_list(candidate.metadata.get("approved_for_modes")),
            note=str(candidate.metadata.get("decision_note") or "Updated from import metadata CSV."),
            decided_at=utc_iso(),
        )
        _append_decision(project_dir, decision)
        updated.append(candidate)
    _write_candidates(project_dir, candidates)
    data = {
        "generated_at": utc_iso(),
        "source_csv": str(from_csv),
        "updated_count": len(updated),
        "unmatched_count": len(unmatched),
        "updated_import_ids": [candidate.import_id for candidate in updated],
        "unmatched_rows": unmatched,
    }
    report_path = reports_dir(project_dir) / "import_metadata_update_report.md"
    json_path = reports_dir(project_dir) / "import_metadata_update_report.json"
    lines = [
        "# Import Metadata Update Report",
        "",
        f"- Updated candidates: {len(updated)}",
        f"- Unmatched rows: {len(unmatched)}",
    ]
    if unmatched:
        lines.extend(["", "## Unmatched Rows"])
        lines.extend(f"- {row}" for row in unmatched)
    write_text(report_path, "\n".join(lines).rstrip() + "\n")
    write_json(json_path, data)
    return ImportMetadataUpdateResult(project_dir, updated, unmatched, report_path, json_path)


def _template_status(project_dir: Path) -> dict[str, bool]:
    root = manifests_dir(project_dir)
    return {
        "writing_sample_import_template.csv": (root / "writing_sample_import_template.csv").exists(),
        "result_table_import_template.csv": (root / "result_table_import_template.csv").exists(),
        "source_import_template.csv": (root / "source_import_template.csv").exists(),
    }


def _render_doctor(data: dict[str, Any]) -> str:
    lines = [
        "# Import Doctor",
        "",
        f"- Staging folder exists: {data['staging_present']}",
        f"- Incoming files: {data['incoming_file_count']}",
        f"- Candidates: {data['candidate_count']}",
        f"- Needs metadata: {data['files_needing_metadata']}",
        f"- Needs privacy review: {data['files_needing_privacy_review']}",
        f"- Ready: {data['ready_files']}",
        f"- Duplicates: {data['duplicate_files']}",
        f"- Imported: {data['imported_files']}",
        f"- Rejected: {data['rejected_files']}",
        f"- Archived: {data.get('archived_files', 0)}",
        f"- Last import scan: {data.get('last_import_scan') or 'none'}",
        "",
        "## Manifests",
        "",
        f"- import_candidates.jsonl exists: {data['import_candidates_jsonl_exists']}",
        f"- import_plan.csv exists: {data['import_plan_csv_exists']}",
        "",
        "## Metadata Templates",
    ]
    for name, exists in sorted(data["metadata_templates"].items()):
        lines.append(f"- {name}: {exists}")
    lines.extend(["", "## Suggested Next Actions"])
    lines.extend(f"- {item}" for item in data["suggested_next_actions"])
    return "\n".join(lines).rstrip() + "\n"


def diagnose_import_staging(project_dir: Path) -> ImportDoctorResult:
    """Write readiness diagnostics for import staging without importing files."""
    project_dir = Path(project_dir)
    root = import_staging_dir(project_dir)
    staging_present_before = root.exists()
    if not staging_present_before:
        ensure_dir(reports_dir(project_dir))
    status = import_staging_status(project_dir)
    status["staging_present"] = staging_present_before
    status["metadata_templates"] = _template_status(project_dir)
    status["import_candidates_jsonl_exists"] = candidates_jsonl_path(project_dir).exists()
    status["import_plan_csv_exists"] = (manifests_dir(project_dir) / "import_plan.csv").exists()
    if not staging_present_before:
        status["suggested_next_actions"] = ["Run prep-import-staging PROJECT_DIR."]
    elif not status.get("incoming_file_count") and not status.get("candidate_count"):
        status["suggested_next_actions"] = [
            f"Place files under {incoming_dir(project_dir).as_posix()}/.",
            "Use incoming subfolders: " + ", ".join(_incoming_folder_names()) + ".",
            "Run import-scan PROJECT_DIR --write.",
        ]
    else:
        status["suggested_next_actions"] = _next_actions(
            load_import_candidates(project_dir),
            int(status.get("files_needing_metadata", 0) or 0),
            int(status.get("files_needing_privacy_review", 0) or 0),
            int(status.get("duplicate_files", 0) or 0),
        )
    report_path = reports_dir(project_dir) / "import_doctor.md"
    json_path = reports_dir(project_dir) / "import_doctor.json"
    write_text(report_path, _render_doctor(status))
    write_json(json_path, status)
    return ImportDoctorResult(project_dir, report_path, json_path, status)


def _preview_candidate(candidates: list[ImportCandidate], import_id: str | None) -> ImportCandidate:
    if import_id:
        for candidate in candidates:
            if candidate.import_id == import_id:
                return candidate
        raise ValueError(f"No import candidate found with import_id '{import_id}'.")
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise ValueError("No import candidates found. Run import-scan --write after staging files.")
    raise ValueError("Pass --import-id when more than one import candidate exists.")


def _safe_text_preview(candidate: ImportCandidate, limit_chars: int) -> tuple[str, str]:
    path = Path(candidate.source_path)
    if candidate.extension not in TEXT_PRIVACY_SCAN_SUFFIXES:
        return "binary", f"Binary or unsupported preview. Size={candidate.size_bytes} bytes; extension={candidate.extension}."
    if candidate.privacy_hits:
        return "suppressed_privacy_hits", "Text preview suppressed because privacy categories were detected."
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError as exc:
        return "read_failed", f"Could not read text preview: {exc}"
    return "text", text[: max(0, limit_chars)]


def _render_preview(data: dict[str, Any]) -> str:
    lines = [
        "# Import Preview",
        "",
        f"- import_id: `{data['import_id']}`",
        f"- filename: `{data['filename']}`",
        f"- detected_kind: {data['detected_kind']}",
        f"- suggested_destination: `{data['suggested_destination']}`",
        f"- duplicate_status: {data['duplicate_status']}",
        f"- privacy_status: {data['privacy_status']}",
        f"- privacy_scan_status: {data['privacy_scan_status']}",
        f"- privacy_hits: {_csv_join(data.get('privacy_hits', [])) or 'none'}",
        "",
        "## Warnings",
    ]
    lines.extend(f"- {warning}" for warning in data.get("warnings", []) or ["None."])
    lines.extend(["", "## Preview", "", "```text", str(data["preview_text"]), "```"])
    return "\n".join(lines).rstrip() + "\n"


def preview_import_candidate(project_dir: Path, *, import_id: str | None = None, limit_chars: int = 1200) -> ImportPreviewResult:
    """Write and return a safe preview for one staged candidate."""
    project_dir = Path(project_dir)
    candidates = _load_or_scan(project_dir)
    candidate = _preview_candidate(candidates, import_id)
    preview_status, preview_text = _safe_text_preview(candidate, limit_chars)
    data = {
        "generated_at": utc_iso(),
        "import_id": candidate.import_id,
        "filename": candidate.filename,
        "detected_kind": candidate.detected_kind,
        "suggested_destination": candidate.suggested_destination,
        "duplicate_status": candidate.duplicate_status,
        "privacy_status": candidate.privacy_status,
        "privacy_scan_status": candidate.privacy_scan_status,
        "privacy_hits": candidate.privacy_hits,
        "warnings": candidate.warnings,
        "preview_status": preview_status,
        "preview_text": preview_text,
    }
    report_path = reports_dir(project_dir) / f"import_preview_{candidate.import_id}.md"
    write_text(report_path, _render_preview(data))
    return ImportPreviewResult(project_dir, candidate, report_path, data)


DEMO_FILES = {
    "incoming/writing_samples/demo_academic_methods.md": (
        "# DEMO TOY academic_manuscript methods sample\n\n"
        "DEMO/TOY/NOT REAL DATA. This local-only file exists only to test import staging commands.\n\n"
        "Methods\n\nWe used a small toy table to rehearse the workflow. No real scientific claim is made.\n"
    ),
    "incoming/writing_samples/demo_coursework_explanation.md": (
        "# DEMO TOY coursework_explanatory sample\n\n"
        "DEMO/TOY/NOT REAL DATA. This is not an academic style source for a real manuscript.\n"
    ),
    "incoming/result_tables/demo_results.csv": (
        "feature,comparison,effect_size,adjusted_p_value,note\n"
        "DEMO_TOY_NOT_REAL_DATA_FEATURE,DEMO_ONLY,0.0,1.0,DEMO TOY NOT REAL DATA command test\n"
    ),
    "incoming/sources/demo_references.bib": (
        "@misc{demo_toy_reference,\n"
        "  title={DEMO TOY NOT REAL DATA reference placeholder},\n"
        "  year={2026},\n"
        "  note={For local command testing only; not a scientific citation}\n"
        "}\n"
    ),
    "incoming/source_pdfs/README_no_demo_pdf.md": (
        "# DEMO TOY NOT REAL DATA\n\n"
        "No demo PDF is created. Put real source PDFs here only after privacy review.\n"
    ),
    "incoming/feedback/demo_feedback.jsonl": (
        '{"feedback_id":"demo-toy-feedback","source":"DEMO TOY NOT REAL DATA",'
        '"target":"workflow rehearsal","decision":"accepted","note":"Command testing only"}\n'
    ),
}


def create_demo_import_files(project_dir: Path, *, overwrite: bool = False) -> ImportCreateDemoResult:
    """Create clearly labeled toy staged files for local import rehearsal."""
    project_dir = Path(project_dir)
    prep_import_staging(project_dir)
    created: list[Path] = []
    skipped: list[Path] = []
    overwritten: list[Path] = []
    root = import_staging_dir(project_dir)
    for rel, text in DEMO_FILES.items():
        path = root / rel
        ensure_dir(path.parent)
        if path.exists() and not overwrite:
            skipped.append(path)
            continue
        if path.exists():
            overwritten.append(path)
        else:
            created.append(path)
        write_text(path, text)
    return ImportCreateDemoResult(project_dir, created, skipped, overwritten)


def _render_rehearsal(data: dict[str, Any]) -> str:
    lines = [
        "# Import Rehearsal Report",
        "",
        f"- Created demo files: {data['created_demo']}",
        f"- Candidates: {data['scan_summary'].get('candidate_count', 0)}",
        f"- Ready: {data['plan'].get('ready_count', 0)}",
        f"- Needs metadata: {data['plan'].get('needs_metadata_count', 0)}",
        f"- Needs privacy review: {data['plan'].get('needs_privacy_review_count', 0)}",
        f"- Validation ok: {data['validation']['ok']}",
        f"- Dry-run import attempted: {data['dry_run_import']['attempted']}",
        "",
        "## Reports",
    ]
    for key, value in data["reports"].items():
        lines.append(f"- {key}: `{value}`")
    lines.extend(["", "## Validation Warnings"])
    lines.extend(f"- {warning}" for warning in data["validation"]["warnings"] or ["None."])
    return "\n".join(lines).rstrip() + "\n"


def run_import_rehearsal(project_dir: Path, *, create_demo: bool = False) -> ImportRehearsalResult:
    """Run a safe import-staging rehearsal without activating staged files."""
    from manuscriptforge.config import validate_project

    project_dir = Path(project_dir)
    demo_result: ImportCreateDemoResult | None = None
    if create_demo:
        demo_result = create_demo_import_files(project_dir, overwrite=False)
    else:
        prep_import_staging(project_dir)
    scan = scan_import_staging(project_dir, write=True)
    plan = build_import_plan(project_dir, kind="all")
    summary = summarize_import_staging(project_dir)
    doctor = diagnose_import_staging(project_dir)
    validation = validate_project(project_dir)
    dry_run: ImportApplyResult | None = None
    if create_demo:
        dry_run = apply_imports(project_dir, all_ready=True, copy=True, dry_run=True)
    data = {
        "generated_at": utc_iso(),
        "created_demo": create_demo,
        "demo_created": [path.relative_to(project_dir).as_posix() for path in (demo_result.created if demo_result else [])],
        "demo_skipped": [path.relative_to(project_dir).as_posix() for path in (demo_result.skipped if demo_result else [])],
        "scan_summary": scan.summary,
        "plan": plan.data,
        "summary": summary.data,
        "doctor": doctor.data,
        "validation": {
            "ok": validation.ok,
            "errors": validation.errors,
            "warnings": validation.warnings,
            "infos": validation.infos,
        },
        "dry_run_import": {
            "attempted": dry_run is not None,
            "selected": len(dry_run.selected) if dry_run else 0,
            "planned": len(dry_run.copied) if dry_run else 0,
            "report": str(dry_run.report_path) if dry_run else "",
        },
        "reports": {
            "scan": str(scan.report_path),
            "plan": str(plan.report_path),
            "summary": str(summary.report_path),
            "doctor": str(doctor.report_path),
        },
    }
    report_path = reports_dir(project_dir) / "import_rehearsal_report.md"
    json_path = reports_dir(project_dir) / "import_rehearsal_report.json"
    write_text(report_path, _render_rehearsal(data))
    write_json(json_path, data)
    return ImportRehearsalResult(project_dir, report_path, json_path, data)
