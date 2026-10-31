"""Original, nonmedical synthetic inputs for the complete research workflow."""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

from manuscriptforge.config import validate_project, write_project_config
from manuscriptforge.demo import PASSAGES
from manuscriptforge.models.project import ProjectConfig
from manuscriptforge.orchestration.storage import is_link
from manuscriptforge.style.curation import build_style_chunk_registry, update_style_chunk_approval
from manuscriptforge.style.document_extractors import extract_style_corpus
from manuscriptforge.utils.io import write_json, write_text

MARKER = "research_demo_manifest.json"


def _inventory(project: Path) -> dict[str, str]:
    """Track all demo inputs; generated run artifacts are deliberately excluded."""
    result = {}
    for path in sorted(project.rglob("*")):
        if path.relative_to(project).parts[0] == "outputs" or path == project / MARKER:
            continue
        if is_link(path):
            raise ValueError("Linked demo inputs are not supported")
        if path.is_file():
            result[path.relative_to(project).as_posix()] = hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
    return result


def prepare_research_demo(project_dir: Path) -> Path:
    """Create or verify unchanged demo inputs; never replace an existing project."""
    project = Path(project_dir).resolve()
    marker = project / MARKER
    if project.exists() and any(project.iterdir()):
        if marker.is_symlink() or (marker.exists() and is_link(marker)):
            raise ValueError("The demo manifest must be a regular file")
        if not marker.is_file():
            raise FileExistsError("Use an empty directory for the research demo")
        manifest = json.loads(marker.read_text(encoding="utf-8"))
        if manifest.get("demo_id") != "research-records-v1":
            raise ValueError("Directory is not a recognized research demo")
        if _inventory(project) != manifest.get("inputs"):
            raise ValueError(
                "Demo inputs were added, removed, or edited; preserve them and choose a new destination"
            )
        for relative, digest in manifest["inputs"].items():
            path = project / relative
            if not path.resolve().is_relative_to(project) or not path.is_file():
                raise ValueError("Demo input is missing or unsafe; use a new destination")
            if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                raise ValueError(
                    "Demo inputs were edited; preserve them and choose a new destination"
                )
        return project
    project.mkdir(parents=True, exist_ok=True)
    (project / "inputs/source_pdfs").mkdir(parents=True, exist_ok=True)
    config = ProjectConfig(
        project_name="Synthetic record-processing workflow",
        article_type="generic_research",
        field="research software",
        target_audience="research software reviewers",
    )
    config.style.curation.require_chunk_approval = True
    config.style.curation.include_needs_review_chunks = False
    config.style.extraction.fail_on_extraction_error = True
    write_project_config(project, config)
    notes = {
        "abstract.md": "This original synthetic example demonstrates a traceable record-processing workflow. It reports counts from generated toy records, not a real experiment or an instrument evaluation.",
        "rationale.md": "Separating original records from summaries makes software transformations inspectable. This example asks whether each displayed count can be traced to supplied synthetic records; it makes no claim about scientific validity or tool efficacy.",
        "methods.md": "We constructed three synthetic record collections with deterministic labels. Collection A contains ten records, collection B contains twelve, and collection C contains eight. Missing labels were inserted explicitly. Summary counts were calculated from the generated records. No subjects, instruments, or real measurements were involved. Software transformations and source hashes are retained for reproducibility; no inferential statistical test was applied.",
        "interpretation_notes.md": "The counts describe only the generated records. Missing labels remain missing; they are not imputed. This small synthetic example cannot establish performance in scientific research. It is a functional demonstration of source traceability and reviewable output.",
        "figure_legends.md": "No empirical figures are supplied for this synthetic example.",
        "source_list.md": "No external research sources or publication metadata are claimed. All input prose and toy records are original synthetic demonstration material.",
    }
    for name, text in notes.items():
        write_text(project / "inputs" / name, text + "\n")
    records = []
    summaries = []
    for collection, count, missing in (("A", 10, 1), ("B", 12, 0), ("C", 8, 2)):
        current = [
            {
                "collection": collection,
                "record_id": f"{collection}-{index + 1:02d}",
                "label": "" if index < missing else f"label-{index + 1}",
            }
            for index in range(count)
        ]
        records.extend(current)
        summaries.append(
            {
                "collection": collection,
                "record_count": len(current),
                "missing_labels": sum(not row["label"] for row in current),
            }
        )
    for relative, rows in (
        ("inputs/source_data/toy_records.csv", records),
        ("inputs/results_tables/record_summary.csv", summaries),
    ):
        path = project / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    style_path = project / "style_corpus/academic_manuscript/original_demo.md"
    write_text(
        style_path,
        "\n\n".join(f"## {section.title()}\n\n{text}" for section, text in PASSAGES.items()) + "\n",
    )
    extract_style_corpus(project, mode="academic_manuscript")
    registry = build_style_chunk_registry(project, mode="academic_manuscript")
    for source in sorted({row.source_file for row in registry.rows}):
        for use in ("style_profile", "exemplar_retrieval"):
            update_style_chunk_approval(
                project,
                source_file=source,
                approve=True,
                approve_for=use,
                note="Scripted approval of original synthetic demonstration text; not a real author judgment.",
            )
    validation = validate_project(project)
    if not validation.ok:
        raise ValueError("Demo validation failed: " + "; ".join(validation.errors))
    write_text(
        project / "README_PROJECT.md",
        "# Synthetic research workflow\n\n"
        "All records and prose are original demonstration material. Counts are derived from the toy records, not from an empirical study. "
        "Run `manuscriptforge research run .` for a complete offline workflow. "
        "Inspect the resulting audits and source provenance; computational completion is not scientific validation.\n",
    )
    write_json(
        marker, {"demo_id": "research-records-v1", "synthetic": True, "inputs": _inventory(project)}
    )
    return project
