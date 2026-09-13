from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from manuscriptforge.ingest.bibtex_ingest import ingest_bibtex
from manuscriptforge.ingest.pdf_ingest import summarize_pdf
from manuscriptforge.ingest.table_ingest import TableIngestError, read_table
from manuscriptforge.intake.scaffold import is_scaffold_file
from manuscriptforge.models.project import ProjectConfig
from manuscriptforge.models.style import DEFAULT_ALLOWED_STYLE_MODES, StyleMode
from manuscriptforge.resources import read_config_resource
from manuscriptforge.utils.io import ensure_dir, read_yaml, write_text, write_yaml

DEFAULT_CONFIG: dict[str, Any] = {
    "project_name": "Example Manuscript",
    "article_type": "biomedical_imrad",
    "target_journal": None,
    "target_audience": "biomedical researchers",
    "field": "bioinformatics",
    "corresponding_author_name": None,
    "citation_style": "numeric",
    "privacy": {"local_only": True},
    "llm": {"provider": "mock", "model": None, "temperature": 0.2},
    "manuscript": {
        "include_sections": [
            "Abstract",
            "Introduction",
            "Methods",
            "Results",
            "Discussion",
            "Limitations",
            "Conclusion",
            "References",
        ],
        "structured_abstract": True,
    },
    "style": {
        "use_style_corpus": True,
        "max_style_examples_per_section": 5,
        "active_mode": StyleMode.academic_manuscript.value,
        "allowed_modes": list(DEFAULT_ALLOWED_STYLE_MODES),
        "include_modes": [StyleMode.academic_manuscript.value],
        "exclude_modes": [StyleMode.coursework_explanatory.value],
        "mode_fallback": StyleMode.unknown.value,
        "warn_on_mixed_modes": True,
        "extraction": {
            "enabled": True,
            "cache_extracted_text": True,
            "exclude_references_section": True,
            "max_chunk_words": 900,
            "min_chunk_words": 40,
            "include_pdf": True,
            "include_docx": True,
            "fail_on_extraction_error": False,
        },
        "curation": {
            "use_chunk_registry": True,
            "require_chunk_approval": False,
            "include_needs_review_chunks": True,
            "exclude_warning_chunks_by_default": False,
        },
        "section_controls": {},
    },
    "style_benchmark": {
        "enabled": True,
        "weights": {
            "section_similarity": 0.35,
            "style_memory_alignment": 0.20,
            "generic_phrase_avoidance": 0.10,
            "causal_hedging_alignment": 0.15,
            "citation_integration": 0.10,
            "traceability": 0.10,
        },
    },
    "evidence": {"require_claim_registry": True, "flag_unsupported_claims": True},
    "outputs": {"formats": ["markdown", "docx", "latex", "xlsx"]},
}


@dataclass
class ValidationResult:
    """Collect validation errors, warnings, and informational messages."""

    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    infos: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """Return whether validation found no blocking errors."""
        return not self.errors


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge project config overrides into defaults."""
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_project_config(project_dir: Path) -> ProjectConfig:
    """Load and validate project.yaml over the built-in defaults."""
    project_dir = Path(project_dir)
    config_path = project_dir / "project.yaml"
    data = deep_merge(DEFAULT_CONFIG, read_yaml(config_path))
    return ProjectConfig.model_validate(data)


def write_project_config(project_dir: Path, config: ProjectConfig) -> None:
    """Write a ProjectConfig model to project.yaml."""
    write_yaml(project_dir / "project.yaml", config.model_dump(mode="json"))


def initialize_project(project_dir: Path) -> None:
    """Create a starter ManuscriptForge project skeleton."""
    project_dir = Path(project_dir)
    ensure_dir(project_dir)
    ensure_dir(project_dir / "inputs" / "results_tables")
    ensure_dir(project_dir / "inputs" / "source_pdfs")
    ensure_dir(project_dir / "style_corpus")
    ensure_dir(project_dir / "outputs")
    config = ProjectConfig(project_name=project_dir.name.replace("_", " ").replace("-", " ").title())
    write_project_config(project_dir, config)
    starter_inputs = {
        "abstract.md": "Replace this starter abstract with the manuscript abstract or rationale summary.\n",
        "rationale.md": "Replace this starter rationale with the problem, gap, and objective.\n",
        "methods.md": "Replace this starter methods note with datasets, preprocessing, statistics, software versions, and reproducibility details.\n",
        "interpretation_notes.md": "Replace this starter note with cautious interpretation points and limitations.\n",
        "figure_legends.md": "Replace this starter note with figure legends.\n",
    }
    for filename, text in starter_inputs.items():
        path = project_dir / "inputs" / filename
        if not path.exists():
            write_text(path, text)
    write_text(
        project_dir / "README_PROJECT.md",
        (
            f"# {config.project_name}\n\n"
            "Place manuscript inputs in `inputs/`, result tables in `inputs/results_tables/`, "
            "source PDFs in `inputs/source_pdfs/`, and prior writing samples in `style_corpus/`.\n\n"
            "Run `manuscriptforge validate .` before drafting.\n"
        ),
    )


def validate_project(project_dir: Path) -> ValidationResult:
    """Validate project structure, config, inputs, tables, sources, and PDFs."""
    project_dir = Path(project_dir)
    result = ValidationResult()
    if not project_dir.exists():
        result.errors.append(f"Project directory does not exist: {project_dir}")
        return result

    config: ProjectConfig | None = None
    if not (project_dir / "project.yaml").exists():
        result.errors.append("Missing project.yaml. Run `manuscriptforge init PROJECT_DIR` or add a project config.")
    try:
        config = load_project_config(project_dir)
        if (project_dir / "project.yaml").exists():
            result.infos.append("Loaded project.yaml")
    except Exception as exc:
        result.errors.append(f"Could not load project.yaml: {exc}")

    if config is not None:
        provider = (config.llm.provider or "").strip().lower()
        if not provider:
            result.warnings.append("No LLM provider configured; MockLLMProvider will be used.")
        elif provider not in {"mock", "openai"}:
            result.errors.append(f"Unsupported LLM provider '{config.llm.provider}'. Expected 'mock' or 'openai'.")
        elif provider == "openai" and config.privacy.local_only:
            result.warnings.append(
                "privacy.local_only is true while llm.provider is openai; the workflow will use MockLLMProvider."
            )
        if not config.manuscript.include_sections:
            result.errors.append("manuscript.include_sections must contain at least one section.")
        if "Results" not in config.manuscript.include_sections:
            result.warnings.append("Results is not listed in manuscript.include_sections.")
        profiles = read_config_resource("journal_profiles.yaml")
        if config.target_journal and config.target_journal not in profiles:
            result.warnings.append(
                f"target_journal/profile '{config.target_journal}' is not defined in the bundled journal_profiles.yaml resource"
            )
        if config.article_type not in profiles and config.article_type not in {
            "biomedical_imrad",
            "case_report",
        }:
            result.warnings.append(
                f"article_type '{config.article_type}' has no matching journal profile; generic_biomedical will be used for journal audit."
            )

    inputs = project_dir / "inputs"
    style_corpus = project_dir / "style_corpus"
    tables = inputs / "results_tables"
    pdfs = inputs / "source_pdfs"
    for folder in [inputs, tables, pdfs, style_corpus]:
        if not folder.exists():
            result.errors.append(f"Missing required folder: {folder.relative_to(project_dir)}")
    if not (project_dir / "outputs").exists():
        result.warnings.append("Missing outputs folder; it will be created at runtime")

    required_texts = ["abstract.md", "methods.md"]
    recommended_texts = ["rationale.md", "interpretation_notes.md", "figure_legends.md"]
    for name in required_texts:
        path = inputs / name
        if not path.exists() or not path.read_text(encoding="utf-8").strip():
            result.errors.append(f"Missing or empty required input: inputs/{name}")
    for name in recommended_texts:
        path = inputs / name
        if not path.exists() or not path.read_text(encoding="utf-8").strip():
            result.warnings.append(f"Missing or empty recommended input: inputs/{name}")

    if tables.exists():
        table_files = sorted(
            list(tables.glob("*.csv"))
            + list(tables.glob("*.tsv"))
            + list(tables.glob("*.xlsx"))
            + list(tables.glob("*.xls"))
        )
        if not table_files:
            if (tables / "data_dictionary.md").exists():
                result.warnings.append(
                    "inputs/results_tables only contains data_dictionary.md; add a CSV/TSV/XLSX result table before drafting."
                )
            result.warnings.append("No CSV/XLSX result tables found")
        else:
            result.infos.append(f"Found {len(table_files)} result table(s)")
            for path in table_files:
                rel_path = path.relative_to(project_dir).as_posix()
                try:
                    frame = read_table(path)
                except TableIngestError as exc:
                    result.errors.append(str(exc))
                    continue
                if frame.empty:
                    result.warnings.append(f"Result table has no data rows: {rel_path}")
                if len(frame.columns) == 0:
                    result.errors.append(f"Result table has no columns: {rel_path}")
                blank_columns = [str(column) for column in frame.columns if not str(column).strip()]
                if blank_columns:
                    result.warnings.append(f"Result table has blank column names: {rel_path}")
                if len(frame.columns) != len(set(map(str, frame.columns))):
                    result.warnings.append(f"Result table has duplicate column names: {rel_path}")
                if len(frame) > 50000:
                    result.warnings.append(
                        f"Large result table detected ({len(frame)} rows): {rel_path}. Drafting uses summaries and first rows only."
                    )
                if config is not None:
                    schema = config.tables.schemas.get(path.name)
                    if schema is not None:
                        missing_schema_columns = [
                            column for column in schema.column_roles if column not in set(map(str, frame.columns))
                        ]
                        if missing_schema_columns:
                            result.warnings.append(
                                f"Table schema for {path.name} references missing column(s): {', '.join(missing_schema_columns)}"
                            )
                        result.infos.append(f"Applied table schema hints for {path.name}")
    if style_corpus.exists():
        supported_style_suffixes = {".md", ".txt", ".docx", ".pdf"}
        if config is not None:
            if not config.style.extraction.include_docx:
                supported_style_suffixes.discard(".docx")
            if not config.style.extraction.include_pdf:
                supported_style_suffixes.discard(".pdf")
        style_files = sorted(
            [
                p
                for p in style_corpus.rglob("*")
                if p.is_file() and p.suffix.lower() in supported_style_suffixes and not is_scaffold_file(p)
            ]
        )
        if not style_files:
            if (style_corpus / "README_style_corpus.md").exists():
                result.warnings.append(
                    "README_style_corpus.md is the only detected style corpus file; add real prior writing samples."
                )
            result.warnings.append("No supported style corpus files found (.md, .txt, .docx, or text-extractable .pdf)")
        else:
            result.infos.append(f"Found {len(style_files)} style corpus file(s)")
            try:
                from manuscriptforge.style.document_extractors import load_extraction_manifest

                style_extractions = load_extraction_manifest(project_dir)
            except Exception:
                style_extractions = []
            for item in style_extractions:
                if item.extraction_status == "likely_scanned_or_unextractable":
                    result.warnings.append(f"Style PDF may be scanned or image-only: {item.relative_path}")
                elif item.extraction_status in {"failed", "unsupported"}:
                    result.warnings.append(
                        f"Style document extraction issue ({item.extraction_status}): {item.relative_path}"
                    )

    scaffold_source_list = (inputs / "source_list.md").exists() and is_scaffold_file(inputs / "source_list.md")
    citation_files = sorted(
        list(inputs.glob("*.bib"))
        + list(inputs.glob("*.ris"))
        + list(inputs.glob("citations*.json"))
        + list(inputs.glob("references.json"))
        + [
            inputs / name
            for name in ["references.txt", "citation_list.txt", "source_list.md"]
            if (inputs / name).exists() and not (name == "source_list.md" and scaffold_source_list)
        ]
    )
    if scaffold_source_list:
        result.warnings.append(
            "inputs/source_list.md appears to be scaffold/TODO text; replace it with reviewed source metadata or use references.bib."
        )
    if not citation_files:
        result.warnings.append("No BibTeX or JSON citation source found")
    else:
        result.infos.append(f"Found {len(citation_files)} citation source file(s)")
        try:
            citations = ingest_bibtex(project_dir)
        except Exception as exc:
            result.errors.append(f"Could not parse citation sources: {exc}")
        else:
            if not citations:
                result.warnings.append("Citation source files were found but no citation records were parsed")
            for citation in citations:
                missing = []
                if citation.title.strip().lower() in {"", "untitled source"}:
                    missing.append("title")
                if not citation.authors:
                    missing.append("authors")
                if not citation.year:
                    missing.append("year")
                if missing:
                    result.warnings.append(
                        f"Citation {citation.citation_id} has incomplete metadata: {', '.join(missing)}"
                    )

    if pdfs.exists():
        pdf_files = sorted(pdfs.glob("*.pdf"))
        if pdf_files:
            result.infos.append(f"Found {len(pdf_files)} source PDF(s)")
            for path in pdf_files:
                if path.stat().st_size == 0:
                    result.warnings.append(f"Source PDF is empty: {path.relative_to(project_dir).as_posix()}")
                try:
                    summary = summarize_pdf(path, project_dir)
                except Exception as exc:
                    result.warnings.append(
                        f"Could not inspect PDF text layer for {path.relative_to(project_dir).as_posix()}: {exc}"
                    )
                    continue
                if summary.needs_ocr:
                    message = (
                        f"PDF may need OCR ({summary.extraction_status}): "
                        f"{path.relative_to(project_dir).as_posix()}"
                    )
                    if config is not None and config.sources.require_pdf_text:
                        result.errors.append(message)
                    else:
                        result.warnings.append(message)

    try:
        from manuscriptforge.intake.import_staging import import_staging_status

        staging = import_staging_status(project_dir)
    except Exception:
        staging = {}
    if staging.get("staging_present"):
        incoming_count = int(staging.get("incoming_file_count", 0) or 0)
        pending_count = int(staging.get("pending_import_count", 0) or 0)
        ready_count = int(staging.get("ready_import_count", staging.get("ready_files", 0)) or 0)
        needs_metadata = int(staging.get("needs_metadata_count", staging.get("files_needing_metadata", 0)) or 0)
        needs_privacy = int(staging.get("needs_privacy_review_count", staging.get("files_needing_privacy_review", 0)) or 0)
        duplicates = int(staging.get("duplicate_candidate_count", staging.get("duplicate_files", 0)) or 0)
        if incoming_count or pending_count:
            result.infos.append(
                f"Import staging contains {incoming_count} incoming file(s) and {pending_count} pending candidate(s); staged files are not active inputs."
            )
            result.infos.append(
                "Import staging review counts: "
                f"ready={ready_count}, needs_metadata={needs_metadata}, needs_privacy_review={needs_privacy}, duplicates={duplicates}, "
                f"last_scan={staging.get('last_import_scan') or 'none'}."
            )
        if ready_count:
            result.warnings.append(
                f"Import staging has {ready_count} ready-but-not-imported file(s); run import-apply or leave them staged."
            )

    return result
