from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from importlib.resources.abc import Traversable
from io import StringIO
from pathlib import Path
from typing import Any

import pandas as pd

from manuscriptforge.config import DEFAULT_CONFIG, ValidationResult, validate_project
from manuscriptforge.ingest.bibtex_ingest import DOI_RE, PMID_RE, _fallback_parse_bibtex
from manuscriptforge.intake.import_staging import import_staging_status
from manuscriptforge.intake.registry import intake_dir
from manuscriptforge.resources import config_resource
from manuscriptforge.utils.io import ensure_dir, read_yaml, write_json, write_text, write_yaml

PILOT_TEMPLATES = {
    "biomedical_imrad",
    "computational_biology",
    "bioinformatics_methods",
    "review_article",
}

REQUIRED_INPUT_FILES = [
    "inputs/abstract.md",
    "inputs/methods.md",
    "inputs/interpretation_notes.md",
]
RECOMMENDED_INPUT_FILES = [
    "inputs/rationale.md",
    "inputs/figure_legends.md",
    "inputs/source_list.md",
]
PILOT_FOLDERS = [
    "inputs",
    "inputs/results_tables",
    "inputs/source_pdfs",
    "style_corpus",
    "feedback",
    "outputs",
    "planning",
]
TEMPLATE_TARGETS = {
    "inputs/abstract.md": "abstract.template.md",
    "inputs/rationale.md": "rationale.template.md",
    "inputs/methods.md": "methods.template.md",
    "inputs/interpretation_notes.md": "interpretation_notes.template.md",
    "inputs/figure_legends.md": "figure_legends.template.md",
    "inputs/source_list.md": "source_list.template.md",
    "inputs/results_tables/data_dictionary.md": "data_dictionary.template.md",
    "style_corpus/README_style_corpus.md": "style_corpus_readme.template.md",
    "feedback/README_feedback.md": "feedback_readme.template.md",
    "planning/redaction_and_privacy_checklist.md": "privacy_checklist.template.md",
    "planning/table_schema_guide.md": "table_schema_guide.template.md",
}
EXCLUDED_TREE_DIRS = {
    ".git",
    ".venv",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
}
TABLE_SUFFIXES = {".csv", ".tsv", ".xlsx", ".xls"}
STYLE_SUFFIXES = {".md", ".txt", ".docx", ".pdf"}


@dataclass
class PrepPilotResult:
    """Record which scaffold files were created, skipped, or overwritten."""

    project_dir: Path
    template: str
    with_examples: bool
    created: list[str] = field(default_factory=list)
    overwritten: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


@dataclass
class InspectionResult:
    """Bundle pilot inspection status with written report artifact paths."""

    project_dir: Path
    status: str
    report_path: Path
    json_path: Path
    tree_path: Path
    workflow_path: Path
    data: dict[str, Any]


@dataclass
class SchemaInferenceResult:
    """Bundle table schema suggestions with written report artifact paths."""

    project_dir: Path
    report_path: Path
    json_path: Path
    config_updated: bool
    data: dict[str, Any]


@dataclass
class SourceListResult:
    """Record source-list creation status and citation intake warnings."""

    project_dir: Path
    source_list_path: Path
    status_path: Path
    created_or_updated: bool
    citation_count: int
    warnings: list[str] = field(default_factory=list)


@dataclass
class ProjectMapResult:
    """Bundle project-map outputs and the filesystem root that was mapped."""

    project_dir: Path
    tree_path: Path
    architecture_path: Path
    workflow_path: Path
    mapped_root: Path


def _repo_root() -> Path:
    """Locate a source checkout without mapping an installed site-packages tree."""
    candidate = Path(__file__).resolve().parents[2]
    metadata = candidate / "pyproject.toml"
    if not metadata.is_file() or 'name = "manuscriptforge"' not in metadata.read_text(encoding="utf-8"):
        raise ValueError("project-map --repo requires a ManuscriptForge source checkout; omit --repo to map your project.")
    return candidate


def _template_dir() -> Traversable:
    """Return the reusable pilot template directory."""
    return config_resource("pilot_templates")


def _rel(path: Path, root: Path) -> str:
    """Return a POSIX-style relative path when possible."""
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _project_name(project_dir: Path) -> str:
    """Derive a readable project name from a directory name."""
    return project_dir.name.replace("_", " ").replace("-", " ").title() or "Pilot Manuscript"


def _template_context(template: str, project_dir: Path, with_examples: bool) -> dict[str, str]:
    """Build placeholder values used while rendering pilot templates."""
    return {
        "PROJECT_NAME": _project_name(project_dir),
        "TEMPLATE_NAME": template,
        "TOY_EXAMPLE": "true" if with_examples else "",
    }


def _toy_example_for(filename: str) -> str:
    """Return clearly labeled toy example text for selected templates."""
    examples = {
        "abstract.template.md": """

## Clearly labeled toy example

<!-- Toy format example only. Replace this entire section before using real data. -->
Background: This toy analysis evaluates synthetic features in a mock cohort.
Objective: Demonstrate where a one-sentence objective belongs.
Methods: Synthetic tabular results were summarized without external services.
Results: Toy feature MF_DEMO_1 was higher in the mock response group.
Conclusions: This example illustrates wording structure only and is not scientific evidence.
""",
        "rationale.template.md": """

## Clearly labeled toy example

<!-- Toy format example only. Replace this entire section before using real data. -->
- Problem: Synthetic biomarker reports often mix results and interpretation.
- Gap: The toy example lacks an auditable bridge from table rows to cautious claims.
- Objective: Show how to record a focused, evidence-scoped objective.
""",
        "methods.template.md": """

## Clearly labeled toy example

<!-- Toy format example only. Replace this entire section before using real data. -->
- Dataset: Synthetic table with three mock features and two comparison groups.
- Preprocessing: Toy values were already normalized.
- Statistical analysis: No real statistical test was performed; values are placeholders.
- Software: ManuscriptForge mock workflow only.
""",
        "interpretation_notes.template.md": """

## Clearly labeled toy example

<!-- Toy format example only. Replace this entire section before using real data. -->
- Supported interpretation: In the toy table, MF_DEMO_1 has a larger mock effect estimate.
- Do not say: MF_DEMO_1 causes response.
- Limitation: The toy data have no biological or clinical meaning.
""",
        "figure_legends.template.md": """

## Clearly labeled toy example

<!-- Toy format example only. Replace this entire section before using real data. -->
Figure 1. Toy schematic of a synthetic workflow from result table to claim registry.
""",
        "data_dictionary.template.md": """

## Clearly labeled toy example

| Column | Meaning | Unit/scale | Allowed values | Notes |
| --- | --- | --- | --- | --- |
| feature | Synthetic feature identifier | text | MF_DEMO_1, MF_DEMO_2 | Toy identifiers only |
| comparison | Mock group comparison | text | responder_vs_nonresponder | Toy comparison only |
| log2_fold_change | Mock effect estimate | numeric | any real number | Not scientific evidence |
| adjusted_p_value | Mock adjusted p-value | numeric | 0 to 1 | Toy values only |
""",
    }
    return examples.get(filename, "")


def _render_template(filename: str, template: str, project_dir: Path, with_examples: bool) -> str:
    """Render a pilot template file with project-specific placeholders."""
    path = _template_dir() / filename
    text = path.read_text(encoding="utf-8")
    context = _template_context(template, project_dir, with_examples)
    for key, value in context.items():
        text = text.replace("{{" + key + "}}", value)
    text = text.replace("{{WITH_EXAMPLES_NOTE}}", "Toy examples are included below." if with_examples else "")
    text = text.replace("{{TOY_EXAMPLE_BLOCK}}", _toy_example_for(filename) if with_examples else "")
    return text.rstrip() + "\n"


def _write_if_allowed(path: Path, text: str, overwrite: bool, result: PrepPilotResult) -> None:
    """Write a scaffold file only when overwrite rules allow it."""
    if path.exists() and not overwrite:
        result.skipped.append(_rel(path, result.project_dir))
        return
    existed = path.exists()
    write_text(path, text)
    if existed:
        result.overwritten.append(_rel(path, result.project_dir))
    else:
        result.created.append(_rel(path, result.project_dir))


def _default_project_yaml(template: str, project_dir: Path) -> dict[str, Any]:
    """Build the default local-first project.yaml payload for a pilot."""
    data = dict(DEFAULT_CONFIG)
    data["project_name"] = _project_name(project_dir)
    data["article_type"] = "biomedical_imrad" if template == "biomedical_imrad" else template
    data["privacy"] = {"local_only": True}
    data["llm"] = {"provider": "mock", "model": None, "temperature": 0.2}
    data["sources"] = {
        "allow_network_enrichment": False,
        "metadata_cache_dir": ".manuscriptforge_cache/metadata",
        "enrich_doi": False,
        "enrich_pmid": False,
        "fail_on_metadata_error": False,
        "require_pdf_text": False,
    }
    data["tables"] = {"schemas": {}}
    if template == "review_article":
        data["manuscript"] = {
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
            "structured_abstract": False,
        }
    return data


def _planning_data_plan(project_dir: Path, template: str, with_examples: bool) -> str:
    """Build the Markdown data-plan scaffold for a pilot project."""
    note = "Toy examples were added to selected templates." if with_examples else "Templates contain instructions only."
    return f"""# Pilot Project Data Plan

<!-- Fill this plan before running a real draft. Do not paste private identifiers unless they are needed and approved for local processing. -->

Project: {project_dir.name}
Template: {template}
Example content: {note}

## Manuscript Inputs

- Abstract or summary: `inputs/abstract.md`
- Rationale: `inputs/rationale.md`
- Methods: `inputs/methods.md`
- Interpretation notes: `inputs/interpretation_notes.md`
- Figure legends: `inputs/figure_legends.md`

## Result Tables

Place CSV, TSV, XLSX, or XLS files in `inputs/results_tables/`. Add one row per result and document each column in `inputs/results_tables/data_dictionary.md`.

## Source Materials

Use `inputs/references.bib` when you have BibTeX. Use `inputs/source_list.md` for manual source grouping and relevance notes. Put PDFs only in `inputs/source_pdfs/`.

## Prior Writing Samples

Place prior writing samples in `style_corpus/`. Use only files you are comfortable using for local style profiling.

## Readiness Notes

- Remove direct identifiers unless the manuscript genuinely requires them.
- Keep raw data out of this folder unless summarized tables are enough.
- Run `inspect-pilot` before `validate`.
"""


def _checklist_csv(project_dir: Path) -> str:
    """Build the CSV checklist used to track pilot input readiness."""
    rows = [
        ["area", "item", "path", "required_for_first_draft", "status", "notes"],
        ["core", "project config", "project.yaml", "yes", "todo", "Confirm title, field, provider, and privacy settings."],
        ["inputs", "abstract or summary", "inputs/abstract.md", "yes", "todo", "Replace instructions with manuscript-specific text."],
        ["inputs", "methods", "inputs/methods.md", "yes", "todo", "Include datasets, preprocessing, statistics, software versions, and reproducibility notes."],
        ["inputs", "interpretation notes", "inputs/interpretation_notes.md", "yes", "todo", "Separate supported interpretation from speculation."],
        ["tables", "result tables", "inputs/results_tables/", "strongly recommended", "todo", "Add CSV/TSV/XLSX tables and run infer-table-schema."],
        ["sources", "references or source list", "inputs/references.bib or inputs/source_list.md", "strongly recommended", "todo", "Do not invent citation details."],
        ["style", "prior writing samples", "style_corpus/", "recommended", "todo", "Use non-sensitive samples when possible."],
        ["privacy", "redaction review", "planning/redaction_and_privacy_checklist.md", "yes", "todo", "Review before drafting."],
    ]
    output = []
    for row in rows:
        output.append(",".join(_csv_cell(cell) for cell in row))
    return "\n".join(output) + "\n"


def _csv_cell(value: str) -> str:
    """Format one value as a safe CSV cell."""
    buffer = StringIO()
    writer = csv.writer(buffer)
    writer.writerow([value])
    return buffer.getvalue().strip()


def _source_list_guide() -> str:
    """Return guidance for grouping source-list entries by manuscript role."""
    return """# Source List Guide

Use `inputs/source_list.md` to connect sources to manuscript roles. The source list is not a place to invent citation metadata. Keep unknown fields blank and add the source to `references.bib` when exact metadata are available.

## Recommended Groups

- Background: sources that motivate the problem or known biology.
- Methods: datasets, algorithms, statistical methods, standards, and reporting guidance.
- Results interpretation: sources needed to interpret observed results.
- Limitations: sources that constrain claims or explain known caveats.
- Software/database resources: package, database, ontology, and web resource citations.

For each source, explain why it is relevant in one sentence.
"""


def _style_corpus_guide() -> str:
    """Return guidance for selecting local prior-writing samples."""
    return """# Style Corpus Guide

Add prior writing samples to `style_corpus/` only when you are comfortable using them for local style profiling. ManuscriptForge treats style files as voice and structure examples, not factual evidence.

## Good Samples

- Published or submitted prose written by the target author.
- Methods, Results, Discussion, response-to-reviewers, or cover-letter text.
- Files with clear section headings.

## Avoid

- Text from collaborators who should not define the target style.
- Private correspondence unrelated to manuscript writing.
- Files with sensitive identifiers unless local processing has been approved.
"""


def prep_pilot(
    project_dir: Path,
    template: str = "biomedical_imrad",
    overwrite: bool = False,
    with_examples: bool = False,
) -> PrepPilotResult:
    """Create or refresh a pilot project scaffold without clobbering user files."""
    if template not in PILOT_TEMPLATES:
        raise ValueError(f"Unsupported pilot template '{template}'. Expected one of: {', '.join(sorted(PILOT_TEMPLATES))}.")
    project_dir = Path(project_dir)
    result = PrepPilotResult(project_dir=project_dir, template=template, with_examples=with_examples)
    ensure_dir(project_dir)
    for folder in PILOT_FOLDERS:
        existed = (project_dir / folder).exists()
        path = ensure_dir(project_dir / folder)
        if not existed:
            result.created.append(_rel(path, project_dir))

    config_path = project_dir / "project.yaml"
    if config_path.exists() and not overwrite:
        result.skipped.append("project.yaml")
    else:
        existed = config_path.exists()
        write_yaml(config_path, _default_project_yaml(template, project_dir))
        (result.overwritten if existed else result.created).append("project.yaml")

    for rel_path, template_name in TEMPLATE_TARGETS.items():
        _write_if_allowed(
            project_dir / rel_path,
            _render_template(template_name, template, project_dir, with_examples),
            overwrite,
            result,
        )
    _write_if_allowed(
        project_dir / "planning" / "pilot_project_data_plan.md",
        _planning_data_plan(project_dir, template, with_examples),
        overwrite,
        result,
    )
    _write_if_allowed(
        project_dir / "planning" / "pilot_input_checklist.csv",
        _checklist_csv(project_dir),
        overwrite,
        result,
    )
    _write_if_allowed(
        project_dir / "planning" / "source_list_guide.md",
        _source_list_guide(),
        overwrite,
        result,
    )
    _write_if_allowed(
        project_dir / "planning" / "style_corpus_guide.md",
        _style_corpus_guide(),
        overwrite,
        result,
    )
    _write_if_allowed(
        project_dir / "README_PROJECT.md",
        (
            f"# {_project_name(project_dir)}\n\n"
            "This folder is a ManuscriptForge pilot project scaffold. Fill `inputs/`, "
            "`inputs/results_tables/`, `style_corpus/`, and source files before drafting.\n\n"
            "Recommended first checks:\n\n"
            "```powershell\n"
            f".\\.venv\\Scripts\\python -m manuscriptforge.cli inspect-pilot {project_dir} --tree\n"
            f".\\.venv\\Scripts\\python -m manuscriptforge.cli infer-table-schema {project_dir}\n"
            f".\\.venv\\Scripts\\python -m manuscriptforge.cli validate {project_dir}\n"
            "```\n"
        ),
        overwrite,
        result,
    )
    return result


def _nonempty_file(path: Path) -> bool:
    """Return whether a text file exists and has non-whitespace content."""
    return path.exists() and path.is_file() and bool(path.read_text(encoding="utf-8", errors="ignore").strip())


def _table_files(project_dir: Path) -> list[Path]:
    """Find supported result table files inside a pilot project."""
    table_dir = project_dir / "inputs" / "results_tables"
    if not table_dir.exists():
        return []
    return sorted(path for path in table_dir.rglob("*") if path.is_file() and path.suffix.lower() in TABLE_SUFFIXES)


def _style_files(project_dir: Path) -> list[Path]:
    """Find style-corpus files while ignoring scaffold README files."""
    style_dir = project_dir / "style_corpus"
    if not style_dir.exists():
        return []
    return sorted(
        path
        for path in style_dir.rglob("*")
        if path.is_file() and path.suffix.lower() in STYLE_SUFFIXES and not path.name.lower().startswith("readme")
    )


def _citation_source_files(project_dir: Path) -> list[Path]:
    """Find citation and source-list files supplied in inputs."""
    inputs = project_dir / "inputs"
    if not inputs.exists():
        return []
    files = list(inputs.glob("*.bib")) + list(inputs.glob("*.ris"))
    files += list(inputs.glob("citations*.json")) + list(inputs.glob("references.json"))
    files += [
        inputs / name
        for name in ["references.txt", "citation_list.txt", "source_list.md"]
        if (inputs / name).exists()
    ]
    return sorted(set(files))


def _source_pdf_files(project_dir: Path) -> list[Path]:
    """Find source PDF files supplied for local evidence ingestion."""
    pdf_dir = project_dir / "inputs" / "source_pdfs"
    if not pdf_dir.exists():
        return []
    return sorted(path for path in pdf_dir.rglob("*.pdf") if path.is_file())


def _feedback_files(project_dir: Path) -> list[Path]:
    """Find non-output JSONL feedback files in a project."""
    if not project_dir.exists():
        return []
    return sorted(
        path
        for path in project_dir.rglob("*.jsonl")
        if path.is_file() and "outputs/runs" not in path.as_posix().replace("\\", "/")
    )


def _latest_run_outputs(project_dir: Path) -> dict[str, Any]:
    """Summarize the latest generated run outputs when present."""
    runs = project_dir / "outputs" / "runs"
    if not runs.exists():
        return {"run_count": 0, "latest_run": None, "latest_files": []}
    run_dirs = sorted(path for path in runs.iterdir() if path.is_dir())
    if not run_dirs:
        return {"run_count": 0, "latest_run": None, "latest_files": []}
    latest = run_dirs[-1]
    files = sorted(path.name for path in latest.iterdir() if path.is_file())
    return {
        "run_count": len(run_dirs),
        "latest_run": _rel(latest, project_dir),
        "latest_files": files,
        "has_manuscript": (latest / "manuscript.md").exists() or (latest / "manuscript.json").exists(),
        "has_review_plan": (latest / "review_plan.json").exists(),
        "has_variants": (latest / "style_variants.jsonl").exists(),
    }


def _is_example_project(project_dir: Path) -> bool:
    """Detect the bundled synthetic example project."""
    config_path = project_dir / "project.yaml"
    readme_path = project_dir / "README_PROJECT.md"
    text = ""
    if config_path.exists():
        text += config_path.read_text(encoding="utf-8", errors="ignore").lower()
    if readme_path.exists():
        text += readme_path.read_text(encoding="utf-8", errors="ignore").lower()
    normalized = project_dir.as_posix().replace("\\", "/").lower()
    return "examples/project_minimal" in normalized or "synthetic and for workflow demonstration only" in text


def _status(
    project_dir: Path,
    validation: ValidationResult,
    missing_required: list[str],
    table_count: int,
    style_count: int,
    citation_count: int,
    latest_outputs: dict[str, Any],
) -> str:
    """Classify project readiness from required files, counts, and outputs."""
    if not project_dir.exists() or not (project_dir / "project.yaml").exists():
        return "not initialized"
    if _is_example_project(project_dir):
        return "example-only"
    if validation.errors or missing_required:
        return "incomplete pilot"
    if table_count == 0 or style_count == 0 or citation_count == 0:
        return "ready for validation"
    if latest_outputs.get("has_manuscript"):
        return "ready for feedback loop"
    return "ready for draft"


def build_project_tree(root: Path, max_depth: int | None = None, include_runs: bool = False) -> str:
    """Render a filtered text tree for a repo or pilot project."""
    root = Path(root)
    depth_limit = 4 if max_depth is None else max_depth
    lines = [root.name + "/"]
    if not root.exists():
        lines.append("(missing)")
        return "\n".join(lines) + "\n"

    def walk(path: Path, prefix: str, depth: int) -> None:
        """Append visible child paths to the tree text."""
        if depth >= depth_limit:
            return
        children = sorted(path.iterdir(), key=lambda item: (item.is_file(), item.name.lower()))
        visible = []
        for child in children:
            if child.is_dir() and child.name in EXCLUDED_TREE_DIRS:
                continue
            rel = child.relative_to(root).as_posix()
            if not include_runs and rel.startswith("outputs/runs"):
                continue
            visible.append(child)
        for index, child in enumerate(visible):
            connector = "`-- " if index == len(visible) - 1 else "|-- "
            suffix = "/" if child.is_dir() else ""
            lines.append(f"{prefix}{connector}{child.name}{suffix}")
            if child.is_dir():
                extension = "    " if index == len(visible) - 1 else "|   "
                walk(child, prefix + extension, depth + 1)

    walk(root, "", 0)
    return "\n".join(lines) + "\n"


def _workflow_diagram() -> str:
    """Return the conceptual ManuscriptForge pilot workflow diagram."""
    return """# Workflow Diagram

```mermaid
flowchart TD
    A["Pilot inputs"] --> B["ingest"]
    B --> C["table and source evidence"]
    C --> D["claim registry"]
    D --> E["draft"]
    E --> F["claim, citation, overclaiming, no-invention audits"]
    B --> G["style profile"]
    G --> E
    F --> H["review plan"]
    G --> I["style benchmark"]
    H --> J["rewrite variants"]
    J --> K["author feedback"]
    K --> L["style memory and reviewed copy"]
```
"""


def _inspection_report(data: dict[str, Any]) -> str:
    """Render human-readable pilot inspection data as Markdown."""
    lines = [
        "# Pilot Inspection Report",
        "",
        f"Status: **{data['status']}**",
        "",
        "## Counts",
        "",
        f"- Result tables: {data['counts']['result_tables']}",
        f"- Style corpus files: {data['counts']['style_files']}",
        f"- Citation/source list files: {data['counts']['citation_source_files']}",
        f"- Source PDFs: {data['counts']['source_pdfs']}",
        f"- Feedback JSONL files: {data['counts']['feedback_files']}",
        f"- Output runs: {data['latest_outputs']['run_count']}",
        "",
        "## Import Staging",
        "",
    ]
    staging = data.get("import_staging", {})
    if staging.get("staging_present"):
        lines.extend(
            [
                f"- Incoming files: {staging.get('incoming_file_count', 0)}",
                f"- Staged file count: {staging.get('staged_file_count', 0)}",
                f"- Candidates: {staging.get('candidate_count', 0)}",
                f"- Pending imports: {staging.get('pending_import_count', 0)}",
                f"- Ready imports: {staging.get('ready_import_count', staging.get('ready_files', 0))}",
                f"- Needs metadata: {staging.get('needs_metadata_count', staging.get('files_needing_metadata', 0))}",
                f"- Needs privacy review: {staging.get('needs_privacy_review_count', staging.get('files_needing_privacy_review', 0))}",
                f"- Duplicate candidates: {staging.get('duplicate_candidate_count', staging.get('duplicate_files', 0))}",
                f"- Last import scan: {staging.get('last_import_scan') or 'none'}",
            ]
        )
        if int(staging.get("pending_import_count", 0) or 0):
            lines.append("- Warning: Import staging contains files not yet imported into active project folders.")
    else:
        lines.append("- Import staging has not been prepared.")
    lines.extend(
        [
            "",
            "## Missing Required Items",
            "",
        ]
    )
    if data["missing_required"]:
        lines.extend(f"- `{item}`" for item in data["missing_required"])
    else:
        lines.append("- None detected.")
    lines.extend(["", "## Missing Recommended Items", ""])
    if data["missing_recommended"]:
        lines.extend(f"- `{item}`" for item in data["missing_recommended"])
    else:
        lines.append("- None detected.")
    lines.extend(["", "## Validation Messages", ""])
    for level in ["errors", "warnings", "infos"]:
        messages = data["validation"][level]
        if messages:
            lines.append(f"### {level.title()}")
            lines.extend(f"- {message}" for message in messages)
            lines.append("")
    if data.get("intake"):
        intake = data["intake"]
        lines.extend(
            [
                "## Intake Readiness",
                "",
                f"- Registry present: {intake.get('registry_present')}",
                f"- Total assets: {intake.get('total_assets', 0)}",
                f"- Writing samples: {intake.get('writing_sample_count', 0)}",
                f"- Result tables: {intake.get('result_table_count', 0)}",
                f"- Source assets: {intake.get('source_asset_count', 0)}",
                f"- Warnings: {len(intake.get('warnings', []))}",
                "",
            ]
        )
    lines.extend(["## Next Actions", ""])
    lines.extend(f"- {item}" for item in data["next_actions"])
    return "\n".join(lines).rstrip() + "\n"


def inspect_pilot(
    project_dir: Path,
    *,
    max_depth: int | None = None,
    include_tree: bool = False,
) -> InspectionResult:
    """Inspect a pilot project and write readiness reports."""
    project_dir = Path(project_dir)
    planning_dir = ensure_dir(project_dir / "planning")
    validation = validate_project(project_dir)
    tables = _table_files(project_dir)
    style_files = _style_files(project_dir)
    citation_files = _citation_source_files(project_dir)
    pdf_files = _source_pdf_files(project_dir)
    feedback_files = _feedback_files(project_dir)
    latest_outputs = _latest_run_outputs(project_dir)
    staging_summary = import_staging_status(project_dir)
    intake_summary_path = intake_dir(project_dir) / "intake_summary.json"
    intake_summary: dict[str, Any] = {"registry_present": False}
    if intake_summary_path.exists():
        try:
            intake_summary = read_yaml(intake_summary_path)
        except Exception:
            intake_summary = {"registry_present": True, "warnings": ["Could not read intake_summary.json."]}
        intake_summary["registry_present"] = True
    missing_required = [path for path in REQUIRED_INPUT_FILES if not _nonempty_file(project_dir / path)]
    missing_recommended = [path for path in RECOMMENDED_INPUT_FILES if not _nonempty_file(project_dir / path)]
    if not tables:
        missing_recommended.append("inputs/results_tables/*.csv|*.tsv|*.xlsx|*.xls")
    if not style_files:
        missing_recommended.append("style_corpus/*.md|*.txt|*.docx|*.pdf")
    if not citation_files:
        missing_recommended.append("inputs/references.bib or inputs/source_list.md")
    status = _status(
        project_dir,
        validation,
        missing_required,
        len(tables),
        len(style_files),
        len(citation_files),
        latest_outputs,
    )
    next_actions = _next_actions(status, missing_required, missing_recommended, latest_outputs)
    data = {
        "project_dir": str(project_dir),
        "status": status,
        "is_example_project": _is_example_project(project_dir),
        "counts": {
            "result_tables": len(tables),
            "style_files": len(style_files),
            "citation_source_files": len(citation_files),
            "source_pdfs": len(pdf_files),
            "feedback_files": len(feedback_files),
        },
        "files": {
            "result_tables": [_rel(path, project_dir) for path in tables],
            "style_files": [_rel(path, project_dir) for path in style_files],
            "citation_source_files": [_rel(path, project_dir) for path in citation_files],
            "source_pdfs": [_rel(path, project_dir) for path in pdf_files],
            "feedback_files": [_rel(path, project_dir) for path in feedback_files],
        },
        "missing_required": missing_required,
        "missing_recommended": sorted(set(missing_recommended)),
        "validation": {
            "errors": validation.errors,
            "warnings": validation.warnings,
            "infos": validation.infos,
        },
        "latest_outputs": latest_outputs,
        "intake": intake_summary,
        "import_staging": staging_summary,
        "next_actions": next_actions,
    }
    tree_text = "# Project Tree\n\n```text\n" + build_project_tree(project_dir, max_depth, include_runs=max_depth is not None) + "```\n"
    report_path = planning_dir / "pilot_inspection_report.md"
    json_path = planning_dir / "pilot_inspection_report.json"
    tree_path = planning_dir / "project_tree.md"
    workflow_path = planning_dir / "workflow_diagram.md"
    write_text(report_path, _inspection_report(data))
    write_json(json_path, data)
    write_text(tree_path, tree_text)
    write_text(workflow_path, _workflow_diagram())
    if include_tree:
        data["tree"] = build_project_tree(project_dir, max_depth, include_runs=max_depth is not None)
    return InspectionResult(project_dir, status, report_path, json_path, tree_path, workflow_path, data)


def _next_actions(
    status: str,
    missing_required: list[str],
    missing_recommended: list[str],
    latest_outputs: dict[str, Any],
) -> list[str]:
    """Suggest the next pilot-preparation step from inspection status."""
    if status == "not initialized":
        return ["Run `prep-pilot PROJECT_DIR` to create the pilot scaffold."]
    if status == "example-only":
        return ["Use this only for workflow testing; create `pilot_project` for real manuscript inputs."]
    if missing_required:
        return [f"Fill required file `{missing_required[0]}` before validation."]
    if missing_recommended:
        return [f"Add or confirm recommended item `{missing_recommended[0]}` before drafting."]
    if latest_outputs.get("has_manuscript"):
        return ["Run `review-plan`, `generate-variants`, and `capture-feedback` for the feedback loop."]
    return ["Run `validate`, then `draft` when validation is clean."]


def _table_paths_for_inference(project_dir: Path, table: Path | None) -> list[Path]:
    """Resolve the requested table path or discover all project tables."""
    if table is not None:
        path = table if table.is_absolute() else project_dir / table
        return [path]
    return _table_files(project_dir)


def _read_table_for_inference(path: Path) -> tuple[pd.DataFrame | None, list[str], str | None]:
    """Read a result table for schema inference while collecting warnings."""
    warnings: list[str] = []
    try:
        suffix = path.suffix.lower()
        if suffix == ".csv":
            return pd.read_csv(path, encoding="utf-8-sig", on_bad_lines="error"), warnings, None
        if suffix == ".tsv":
            return pd.read_csv(path, sep="\t", encoding="utf-8-sig", on_bad_lines="error"), warnings, None
        if suffix in {".xlsx", ".xls"}:
            sheets = pd.read_excel(path, sheet_name=None)
            if len(sheets) > 1:
                warnings.append(f"multiple sheets detected: {', '.join(map(str, sheets.keys()))}")
            first_name = next(iter(sheets))
            return sheets[first_name], warnings, str(first_name)
    except Exception as exc:
        warnings.append(f"could not read table: {exc}")
        return None, warnings, None
    warnings.append(f"unsupported table format: {path.suffix}")
    return None, warnings, None


def _normalized_column(column: object) -> str:
    """Normalize a table column name for heuristic role matching."""
    return re.sub(r"[^a-z0-9]+", "_", str(column).strip().lower()).strip("_")


def _role_for_column(column: object) -> str | None:
    """Infer a ManuscriptForge table role from a column name."""
    name = _normalized_column(column)
    exact = {
        "feature": "feature",
        "marker": "feature",
        "gene": "feature",
        "gene_symbol": "feature",
        "protein": "feature",
        "pathway": "pathway",
        "comparison": "comparison",
        "contrast": "comparison",
        "group": "group",
        "condition": "condition",
        "estimate": "estimate",
        "effect": "effect_size",
        "effect_size": "effect_size",
        "log2fc": "effect_size",
        "log2_fc": "effect_size",
        "log2_fold_change": "effect_size",
        "odds_ratio": "odds_ratio",
        "or": "odds_ratio",
        "hazard_ratio": "hazard_ratio",
        "hr": "hazard_ratio",
        "beta": "beta",
        "direction": "direction",
        "p": "p_value",
        "p_value": "p_value",
        "pvalue": "p_value",
        "q_value": "adjusted_p_value",
        "qvalue": "adjusted_p_value",
        "fdr": "adjusted_p_value",
        "padj": "adjusted_p_value",
        "p_adj": "adjusted_p_value",
        "adjusted_p_value": "adjusted_p_value",
        "ci": "confidence_interval",
        "confidence_interval": "confidence_interval",
        "lower_ci": "confidence_interval_lower",
        "upper_ci": "confidence_interval_upper",
        "n": "sample_size",
        "sample_size": "sample_size",
        "figure": "figure_reference",
        "figure_ref": "figure_reference",
        "notes": "notes",
        "note": "notes",
    }
    if name in exact:
        return exact[name]
    if "confidence" in name and "interval" in name:
        return "confidence_interval"
    if name.startswith("ci_") or name.endswith("_ci"):
        return "confidence_interval"
    if "sample" in name and ("size" in name or name.endswith("_n")):
        return "sample_size"
    if "figure" in name:
        return "figure_reference"
    if "p_value" in name or name.endswith("_p"):
        return "p_value"
    if "q_value" in name or "fdr" in name or "adjusted" in name:
        return "adjusted_p_value"
    return None


def _infer_one_table(path: Path, project_dir: Path) -> dict[str, Any]:
    """Build schema suggestions and warnings for one result table."""
    df, warnings, sheet_name = _read_table_for_inference(path)
    rel_path = _rel(path, project_dir)
    suggestion: dict[str, Any] = {
        "table": rel_path,
        "sheet_name": sheet_name,
        "column_roles": {},
        "thresholds": {},
        "warnings": warnings,
        "row_count": 0,
        "column_count": 0,
        "columns": [],
        "numeric_columns": [],
        "yaml_snippet": "",
    }
    if df is None:
        return suggestion
    suggestion["row_count"] = int(len(df))
    suggestion["column_count"] = int(len(df.columns))
    suggestion["columns"] = [str(column) for column in df.columns]
    if df.empty:
        suggestion["warnings"].append("empty table")
    unnamed = [str(column) for column in df.columns if str(column).strip().lower().startswith("unnamed")]
    if unnamed:
        suggestion["warnings"].append("possible merged Excel-like structure or blank header columns")
    numeric_columns: list[str] = []
    ambiguous_numeric: list[str] = []
    text_p_values: list[str] = []
    for column in df.columns:
        role = _role_for_column(column)
        if role:
            suggestion["column_roles"][str(column)] = role
        series = df[column]
        if pd.api.types.is_numeric_dtype(series):
            numeric_columns.append(str(column))
            if role is None:
                ambiguous_numeric.append(str(column))
        elif role in {"p_value", "adjusted_p_value"}:
            coerced = pd.to_numeric(series, errors="coerce")
            if coerced.notna().sum() < series.notna().sum():
                text_p_values.append(str(column))
    suggestion["numeric_columns"] = numeric_columns
    if ambiguous_numeric:
        suggestion["warnings"].append("ambiguous numeric columns: " + ", ".join(ambiguous_numeric))
    if text_p_values:
        suggestion["warnings"].append("p-value columns stored as text or mixed values: " + ", ".join(text_p_values))
    roles = set(suggestion["column_roles"].values())
    if not roles.intersection({"feature", "pathway"}):
        suggestion["warnings"].append("missing feature/gene/pathway column")
    if "comparison" not in roles and not roles.intersection({"group", "condition"}):
        suggestion["warnings"].append("missing comparison/group/condition column")
    if "p_value" in roles:
        suggestion["thresholds"]["p_value"] = 0.05
    if "adjusted_p_value" in roles:
        suggestion["thresholds"]["adjusted_p_value"] = 0.05
    suggestion["yaml_snippet"] = _schema_yaml_snippet(path.name, suggestion)
    return suggestion


def _schema_yaml_snippet(table_name: str, suggestion: dict[str, Any]) -> str:
    """Render a project.yaml snippet for one inferred table schema."""
    data = {
        "tables": {
            "schemas": {
                table_name: {
                    "description": f"Schema inferred for {table_name}; review before drafting.",
                    "column_roles": suggestion["column_roles"],
                    "thresholds": suggestion["thresholds"],
                    "claim_generation": {
                        "generate_row_claims": True,
                        "generate_table_summary_claims": True,
                        "max_row_claims": 20,
                    },
                }
            }
        }
    }
    import yaml

    return yaml.safe_dump(data, sort_keys=False)


def _schema_markdown(data: dict[str, Any]) -> str:
    """Render table schema suggestions as Markdown."""
    lines = ["# Table Schema Suggestions", ""]
    if not data["tables"]:
        lines.append("No result tables were found.")
    for table in data["tables"]:
        lines.extend(
            [
                f"## {table['table']}",
                "",
                f"- Rows: {table['row_count']}",
                f"- Columns: {table['column_count']}",
                f"- Numeric columns: {', '.join(table['numeric_columns']) or 'none detected'}",
                "",
                "### Suggested Column Roles",
                "",
            ]
        )
        if table["column_roles"]:
            lines.extend(f"- `{column}`: `{role}`" for column, role in table["column_roles"].items())
        else:
            lines.append("- No confident roles detected.")
        lines.extend(["", "### Warnings", ""])
        if table["warnings"]:
            lines.extend(f"- {warning}" for warning in table["warnings"])
        else:
            lines.append("- None.")
        lines.extend(["", "### YAML Snippet", "", "```yaml", table["yaml_snippet"].rstrip(), "```", ""])
    return "\n".join(lines).rstrip() + "\n"


def infer_table_schema(project_dir: Path, table: Path | None = None, write_config: bool = False) -> SchemaInferenceResult:
    """Infer table schema hints and optionally update project.yaml."""
    project_dir = Path(project_dir)
    planning_dir = ensure_dir(project_dir / "planning")
    paths = _table_paths_for_inference(project_dir, table)
    suggestions = [_infer_one_table(path, project_dir) for path in paths]
    data = {
        "project_dir": str(project_dir),
        "tables": suggestions,
        "warnings": [] if paths else ["No result tables found."],
        "config_updated": False,
    }
    config_updated = False
    if write_config:
        _write_schema_config(project_dir, suggestions)
        data["config_updated"] = True
        config_updated = True
    report_path = planning_dir / "table_schema_suggestions.md"
    json_path = planning_dir / "table_schema_suggestions.json"
    write_text(report_path, _schema_markdown(data))
    write_json(json_path, data)
    return SchemaInferenceResult(project_dir, report_path, json_path, config_updated, data)


def _write_schema_config(project_dir: Path, suggestions: list[dict[str, Any]]) -> None:
    """Merge inferred table schema suggestions into project.yaml."""
    config_path = project_dir / "project.yaml"
    data = read_yaml(config_path) if config_path.exists() else dict(DEFAULT_CONFIG)
    tables = data.setdefault("tables", {})
    schemas = tables.setdefault("schemas", {})
    for suggestion in suggestions:
        if not suggestion.get("column_roles"):
            continue
        table_name = Path(str(suggestion["table"])).name
        schema = schemas.setdefault(table_name, {})
        schema.setdefault("description", f"Schema inferred for {table_name}; review before drafting.")
        schema["column_roles"] = {
            **dict(schema.get("column_roles", {})),
            **suggestion["column_roles"],
        }
        if suggestion.get("thresholds"):
            schema["thresholds"] = {
                **dict(schema.get("thresholds", {})),
                **suggestion["thresholds"],
            }
        schema.setdefault(
            "claim_generation",
            {
                "generate_row_claims": True,
                "generate_table_summary_claims": True,
                "max_row_claims": 20,
            },
        )
    write_yaml(config_path, data)


def _bibtex_entries(path: Path) -> list[dict[str, str]]:
    """Parse BibTeX entries into source-list summary rows."""
    raw = path.read_text(encoding="utf-8")
    try:
        import bibtexparser

        entries = bibtexparser.loads(raw).entries
    except Exception:
        entries = _fallback_parse_bibtex(raw)
    cleaned = []
    for entry in entries:
        cleaned.append(
            {
                "key": str(entry.get("ID") or "").strip(),
                "title": str(entry.get("title") or "").strip("{} "),
                "year": str(entry.get("year") or "").strip(),
                "doi": str(entry.get("doi") or "").strip(),
                "pmid": str(entry.get("pmid") or "").strip(),
            }
        )
    return cleaned


def _text_source_entries(path: Path) -> list[dict[str, str]]:
    """Parse plain-text source lines into source-list summary rows."""
    entries = []
    for index, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = re.sub(r"^\s*(?:[-*]\s+|\d+[\.)]\s*)", "", raw_line).strip()
        if not line or line.startswith("#"):
            continue
        doi_match = DOI_RE.search(line)
        pmid_match = PMID_RE.search(line)
        pmid = next((group for group in (pmid_match.groups() if pmid_match else []) if group), "")
        entries.append(
            {
                "key": f"{path.stem}-{index}",
                "title": line,
                "year": "",
                "doi": doi_match.group(0).rstrip(".,;") if doi_match else "",
                "pmid": pmid,
            }
        )
    return entries


def _source_entries(project_dir: Path, from_bibtex: Path | None, from_text: Path | None) -> tuple[list[dict[str, str]], list[str]]:
    """Load source entries from explicit files or default project references."""
    warnings: list[str] = []
    if from_bibtex is not None:
        path = from_bibtex if from_bibtex.is_absolute() else project_dir / from_bibtex
        return _bibtex_entries(path), warnings
    if from_text is not None:
        path = from_text if from_text.is_absolute() else project_dir / from_text
        return _text_source_entries(path), warnings
    bib_path = project_dir / "inputs" / "references.bib"
    if bib_path.exists():
        return _bibtex_entries(bib_path), warnings
    warnings.append("No references.bib or text source file found; wrote a blank source-list template if needed.")
    return [], warnings


def _source_list_content(entries: list[dict[str, str]]) -> str:
    """Render source-list Markdown without inventing citation metadata."""
    lines = [
        "# Source List",
        "",
        "<!-- Do not invent citation metadata. Leave unknown fields blank and explain relevance only after reviewing the source. -->",
        "",
    ]
    if entries:
        lines.extend(
            [
                "## Supplied Sources To Assign",
                "",
                "| Key | Title or source text | Year | DOI | PMID | Why relevant |",
                "| --- | --- | --- | --- | --- | --- |",
            ]
        )
        for entry in entries:
            lines.append(
                "| "
                + " | ".join(
                    [
                        entry.get("key", "").replace("|", "/"),
                        entry.get("title", "").replace("|", "/"),
                        entry.get("year", ""),
                        entry.get("doi", ""),
                        entry.get("pmid", ""),
                        "TODO: assign after review",
                    ]
                )
                + " |"
            )
        lines.append("")
    for section in [
        "Background",
        "Methods",
        "Results interpretation",
        "Limitations",
        "Software/database resources",
    ]:
        lines.extend(
            [
                f"## {section}",
                "",
                "| Citation/source key | Why relevant | Claim or manuscript area supported | Notes |",
                "| --- | --- | --- | --- |",
                "| TODO | TODO | TODO | TODO |",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def make_source_list(
    project_dir: Path,
    *,
    from_bibtex: Path | None = None,
    from_text: Path | None = None,
) -> SourceListResult:
    """Create or refresh inputs/source_list.md from supplied source metadata."""
    project_dir = Path(project_dir)
    inputs = ensure_dir(project_dir / "inputs")
    planning = ensure_dir(project_dir / "planning")
    source_list = inputs / "source_list.md"
    entries, warnings = _source_entries(project_dir, from_bibtex, from_text)
    created_or_updated = False
    existing_text = source_list.read_text(encoding="utf-8") if source_list.exists() else ""
    untouched_scaffold = "Do not invent citation metadata" in existing_text and "| TODO | TODO | TODO | TODO |" in existing_text
    if not source_list.exists() or (entries and untouched_scaffold):
        write_text(source_list, _source_list_content(entries))
        created_or_updated = True
    status_lines = [
        "# Source List Status",
        "",
        f"- Source list path: `{_rel(source_list, project_dir)}`",
        f"- Source list created or updated: {created_or_updated}",
        f"- Parsed supplied source records: {len(entries)}",
        "",
        "## Warnings",
        "",
    ]
    if warnings:
        status_lines.extend(f"- {warning}" for warning in warnings)
    else:
        status_lines.append("- None.")
    if source_list.exists() and not created_or_updated:
        status_lines.append("")
        status_lines.append("Existing `inputs/source_list.md` was left unchanged.")
    status_path = planning / "source_list_status.md"
    write_text(status_path, "\n".join(status_lines).rstrip() + "\n")
    return SourceListResult(project_dir, source_list, status_path, created_or_updated, len(entries), warnings)


def _architecture_doc(mapped_root: Path, repo: bool) -> str:
    """Render a conceptual architecture note for a repo or pilot project."""
    if repo:
        return """# ManuscriptForge Repository Architecture

## Main Package Areas

- `manuscriptforge/cli.py`: thin Typer command layer.
- `manuscriptforge/config.py`: project config loading, initialization, and validation.
- `manuscriptforge/ingest/`: text, table, citation, PDF, and style corpus ingestion.
- `manuscriptforge/evidence/`: claim registry construction.
- `manuscriptforge/drafting/`: section writers and revision-question generation.
- `manuscriptforge/audit/`: claim, citation, overclaiming, no-invention, journal, reproducibility, style, and reviewer-style audits.
- `manuscriptforge/style/`: style profiling, questions, memory, evaluation, and benchmark.
- `manuscriptforge/review/`: review plan, variants, feedback capture, feedback application, and summaries.
- `manuscriptforge/export/`: Markdown, DOCX, LaTeX, XLSX, and bundle exports.

The CLI should stay thin; reusable behavior belongs in package modules.
"""
    return f"""# Pilot Project Architecture

Mapped root: `{mapped_root}`

## Expected Folders

- `inputs/`: manuscript notes, references, source list, result tables, and source PDFs.
- `inputs/results_tables/`: CSV, TSV, XLSX, or XLS result summaries.
- `inputs/source_pdfs/`: source PDFs only.
- `style_corpus/`: prior writing samples for local style profiling.
- `feedback/`: optional JSONL feedback files for non-interactive testing.
- `planning/`: readiness reports, data plan, schema suggestions, and project maps.
- `outputs/`: generated ManuscriptForge runs.
"""


def project_map(project_dir: Path, *, repo: bool = False, max_depth: int | None = None) -> ProjectMapResult:
    """Write a project tree, architecture note, and workflow diagram."""
    project_dir = Path(project_dir)
    planning = ensure_dir(project_dir / "planning")
    mapped_root = _repo_root() if repo else project_dir
    tree_path = planning / "project_tree.md"
    architecture_path = planning / "project_architecture.md"
    workflow_path = planning / "workflow_diagram.md"
    write_text(
        tree_path,
        "# Project Tree\n\n```text\n"
        + build_project_tree(mapped_root, max_depth, include_runs=max_depth is not None)
        + "```\n",
    )
    write_text(architecture_path, _architecture_doc(mapped_root, repo))
    write_text(workflow_path, _workflow_diagram())
    return ProjectMapResult(project_dir, tree_path, architecture_path, workflow_path, mapped_root)
