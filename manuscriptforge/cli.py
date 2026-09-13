from __future__ import annotations

import subprocess
import sys
import traceback
from enum import StrEnum
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from manuscriptforge.config import initialize_project, validate_project
from manuscriptforge.intake.import_staging import (
    apply_imports as run_import_apply,
)
from manuscriptforge.intake.import_staging import (
    build_import_plan as run_import_plan,
)
from manuscriptforge.intake.import_staging import (
    create_demo_import_files as run_import_create_demo_files,
)
from manuscriptforge.intake.import_staging import (
    diagnose_import_staging as run_import_doctor,
)
from manuscriptforge.intake.import_staging import (
    prep_import_staging as run_prep_import_staging,
)
from manuscriptforge.intake.import_staging import (
    preview_import_candidate as run_import_preview,
)
from manuscriptforge.intake.import_staging import (
    reject_import as run_import_reject,
)
from manuscriptforge.intake.import_staging import (
    run_import_rehearsal,
)
from manuscriptforge.intake.import_staging import (
    scan_import_staging as run_import_scan,
)
from manuscriptforge.intake.import_staging import (
    summarize_import_staging as run_import_summary,
)
from manuscriptforge.intake.import_staging import (
    update_import_metadata as run_import_update_metadata,
)
from manuscriptforge.intake.registry import (
    approve_asset as run_intake_approve,
)
from manuscriptforge.intake.registry import (
    build_intake_report as run_intake_report,
)
from manuscriptforge.intake.registry import (
    build_style_mode_summary as run_style_mode_summary,
)
from manuscriptforge.intake.registry import (
    scan_project as run_intake_scan,
)
from manuscriptforge.pipeline.diffing import run_diff
from manuscriptforge.pipeline.pilot_prep import (
    infer_table_schema as run_infer_table_schema,
)
from manuscriptforge.pipeline.pilot_prep import (
    inspect_pilot as run_inspect_pilot,
)
from manuscriptforge.pipeline.pilot_prep import (
    make_source_list as run_make_source_list,
)
from manuscriptforge.pipeline.pilot_prep import (
    prep_pilot as run_prep_pilot,
)
from manuscriptforge.pipeline.pilot_prep import (
    project_map as run_project_map,
)
from manuscriptforge.pipeline.workflow import (
    run_apply_feedback,
    run_ask_style,
    run_audit,
    run_build_claims,
    run_build_style_eval,
    run_capture_feedback,
    run_draft,
    run_export,
    run_feedback_summary,
    run_generate_variants,
    run_ingest,
    run_profile_style,
    run_review_plan,
    run_reviewer_sim,
    run_style_benchmark,
    run_style_summary,
    validation_summary,
)
from manuscriptforge.style.curation import (
    build_style_cards as run_build_style_cards,
)
from manuscriptforge.style.curation import (
    build_style_chunk_registry as run_build_style_chunk_registry,
)
from manuscriptforge.style.curation import (
    build_style_chunk_report as run_style_chunk_report,
)
from manuscriptforge.style.curation import (
    build_style_coverage_report as run_style_coverage_report,
)
from manuscriptforge.style.curation import (
    prep_journal_adapter as run_prep_journal_adapter,
)
from manuscriptforge.style.curation import (
    prep_style_modes as run_prep_style_modes,
)
from manuscriptforge.style.curation import (
    update_style_chunk_approval as run_style_chunk_approval,
)
from manuscriptforge.style.document_extractors import (
    extract_style_corpus as run_extract_style_corpus,
)
from manuscriptforge.style.document_extractors import (
    style_corpus_doctor as run_style_corpus_doctor,
)
from manuscriptforge.utils.io import read_json

app = typer.Typer(no_args_is_help=True, help="Local writing-corpus analysis and traceable manuscript workflows.")
console = Console()


class PilotTemplate(StrEnum):
    biomedical_imrad = "biomedical_imrad"
    computational_biology = "computational_biology"
    bioinformatics_methods = "bioinformatics_methods"
    review_article = "review_article"


def _print_run(command: str, run_dir: Path) -> None:
    """Print a compact Rich summary for a completed workflow command."""
    if command in {"review-plan", "generate-variants", "capture-feedback", "apply-feedback", "feedback-summary"}:
        table = Table(title=f"{command} complete")
        table.add_column("Field")
        table.add_column("Value")
        table.add_row("run_dir", str(run_dir))
        artifact_map = {
            "review-plan": ["review_plan.json", "review_plan.md", "review_plan.xlsx"],
            "generate-variants": ["style_variants.jsonl", "style_variants.md", "style_variants.xlsx"],
            "capture-feedback": ["style_feedback.jsonl"],
            "apply-feedback": ["manuscript_reviewed.md", "manuscript_patch_plan.md", "feedback_application_report.md"],
            "feedback-summary": ["feedback_summary.json", "feedback_summary.md", "feedback_summary.xlsx"],
        }
        for name in artifact_map[command]:
            if (run_dir / name).exists():
                table.add_row(name, str(run_dir / name))
        console.print(table)
        return
    if command == "style-benchmark" and (run_dir / "style_benchmark_manifest.json").exists():
        manifest = read_json(run_dir / "style_benchmark_manifest.json")
        table = Table(title="style-benchmark complete")
        table.add_column("Field")
        table.add_column("Value")
        table.add_row("run_dir", str(run_dir))
        table.add_row("overall_score", str(manifest.get("overall_score")))
        if manifest.get("compared_run_id"):
            table.add_row("compared_run_id", str(manifest.get("compared_run_id")))
        table.add_row("warnings", str(len(manifest.get("warnings", []))))
        table.add_row("report", str(run_dir / "style_benchmark_report.md"))
        console.print(table)
        return
    manifest_path = run_dir / "run_manifest.json"
    extra = {}
    files_count = 0
    if manifest_path.exists():
        manifest = read_json(manifest_path)
        extra = manifest.get("extra", {})
        files_count = len(manifest.get("files", []))
    table = Table(title=f"{command} complete")
    table.add_column("Field")
    table.add_column("Value")
    table.add_row("run_dir", str(run_dir))
    if files_count:
        table.add_row("files", str(files_count))
    for key in [
        "files_ingested",
        "tables_ingested",
        "sources_parsed",
        "claim_count",
        "citation_count",
        "style_documents",
        "style_chunks",
        "style_eval_pairs",
        "style_eval_rewrite_prompts",
        "style_eval_rank_questions",
        "preference_answers_collected",
        "unsupported_claims",
        "finding_count",
        "question_count",
        "metadata_cache_files",
    ]:
        if key in extra:
            table.add_row(key, str(extra[key]))
    if command == "diff" and (run_dir / "diff_report.md").exists():
        table.add_row("report", str(run_dir / "diff_report.md"))
    console.print(table)


def _run_or_exit(command: str, func, *args, debug: bool = False, **kwargs) -> None:
    """Run a pipeline function and convert exceptions into CLI exits."""
    try:
        _print_run(command, func(*args, **kwargs))
    except Exception as exc:
        console.print(f"[red]{command} failed[/red]: {exc}")
        if debug:
            traceback.print_exc()
        raise typer.Exit(code=1) from exc


DEBUG_OPTION = typer.Option("--debug", help="Show stack traces for unexpected failures.")


@app.command("demo")
def demo(output_dir: Annotated[Path, typer.Argument(help="Directory for the original synthetic offline demonstration.")]) -> None:
    """Extract, curate, profile, and validate a synthetic writing collection."""
    from manuscriptforge.demo import run_demo

    try:
        result = run_demo(output_dir)
        table = Table(title="demo validated")
        table.add_column("Check")
        table.add_column("Result")
        table.add_row("source documents", str(result["source_documents"]))
        table.add_row("extracted passages", str(result["extracted_chunks"]))
        table.add_row("approved passages", str(result["approved_chunks"]))
        table.add_row("excluded duplicate passages", str(result["excluded_duplicate_chunks"]))
        table.add_row("style cards", str(result["style_cards"]))
        console.print(table)
        console.print(f"Report: {output_dir.resolve() / 'DEMO_REPORT.md'}")
        console.print(f"Summary: {output_dir.resolve() / 'demo_summary.json'}")
    except Exception as exc:
        console.print(f"[red]demo failed[/red]: {exc}")
        raise typer.Exit(code=1) from exc


@app.command("prep-pilot")
def prep_pilot(
    project_dir: Annotated[Path, typer.Argument(help="Pilot project directory to create or update.")],
    template: Annotated[
        PilotTemplate,
        typer.Option("--template", help="Pilot template to scaffold."),
    ] = PilotTemplate.biomedical_imrad,
    overwrite: Annotated[bool, typer.Option("--overwrite", help="Overwrite existing scaffold files.")] = False,
    with_examples: Annotated[
        bool,
        typer.Option("--with-examples", help="Include clearly labeled toy example sections."),
    ] = False,
    debug: Annotated[bool, DEBUG_OPTION] = False,
) -> None:
    """Create or update a starter manuscript project."""
    try:
        result = run_prep_pilot(project_dir, template.value, overwrite=overwrite, with_examples=with_examples)
    except Exception as exc:
        console.print(f"[red]prep-pilot failed[/red]: {exc}")
        if debug:
            traceback.print_exc()
        raise typer.Exit(code=1) from exc
    table = Table(title="prep-pilot complete")
    table.add_column("Field")
    table.add_column("Value")
    table.add_row("project_dir", str(result.project_dir))
    table.add_row("template", result.template)
    table.add_row("with_examples", str(result.with_examples))
    table.add_row("created", str(len(result.created)))
    table.add_row("overwritten", str(len(result.overwritten)))
    table.add_row("skipped_existing", str(len(result.skipped)))
    table.add_row("next", f"inspect-pilot {result.project_dir} --tree")
    console.print(table)


@app.command("inspect-pilot")
def inspect_pilot(
    project_dir: Annotated[Path, typer.Argument(help="Pilot project directory to inspect.")],
    tree: Annotated[bool, typer.Option("--tree", help="Print a project tree in the terminal.")] = False,
    max_depth: Annotated[int | None, typer.Option("--max-depth", help="Maximum tree depth.")] = None,
    output_json: Annotated[bool, typer.Option("--json", help="Print machine-readable inspection JSON.")] = False,
    debug: Annotated[bool, DEBUG_OPTION] = False,
) -> None:
    """Inspect pilot readiness, inputs, sources, feedback files, and outputs."""
    try:
        result = run_inspect_pilot(project_dir, max_depth=max_depth, include_tree=tree)
    except Exception as exc:
        console.print(f"[red]inspect-pilot failed[/red]: {exc}")
        if debug:
            traceback.print_exc()
        raise typer.Exit(code=1) from exc
    if output_json:
        console.print_json(data=result.data)
        return
    table_summary = Table(title="inspect-pilot complete")
    table_summary.add_column("Field")
    table_summary.add_column("Value")
    table_summary.add_row("status", result.status)
    table_summary.add_row("report", str(result.report_path))
    table_summary.add_row("json", str(result.json_path))
    table_summary.add_row("tree", str(result.tree_path))
    table_summary.add_row("workflow", str(result.workflow_path))
    counts = result.data["counts"]
    table_summary.add_row("result_tables", str(counts["result_tables"]))
    table_summary.add_row("style_files", str(counts["style_files"]))
    table_summary.add_row("citation_sources", str(counts["citation_source_files"]))
    table_summary.add_row("source_pdfs", str(counts["source_pdfs"]))
    console.print(table_summary)
    if tree:
        console.print(result.data.get("tree", ""))


@app.command("infer-table-schema")
def infer_table_schema(
    project_dir: Annotated[Path, typer.Argument(help="Pilot project directory.")],
    table: Annotated[Path | None, typer.Option("--table", help="Specific table path to inspect.")] = None,
    write_config: Annotated[
        bool,
        typer.Option("--write-config", help="Append/update project.yaml tables.schemas suggestions."),
    ] = False,
    debug: Annotated[bool, DEBUG_OPTION] = False,
) -> None:
    """Infer table schema hints from CSV, TSV, XLSX, or XLS result tables."""
    try:
        result = run_infer_table_schema(project_dir, table=table, write_config=write_config)
    except Exception as exc:
        console.print(f"[red]infer-table-schema failed[/red]: {exc}")
        if debug:
            traceback.print_exc()
        raise typer.Exit(code=1) from exc
    warning_count = sum(len(item.get("warnings", [])) for item in result.data["tables"]) + len(result.data["warnings"])
    table_summary = Table(title="infer-table-schema complete")
    table_summary.add_column("Field")
    table_summary.add_column("Value")
    table_summary.add_row("tables_inspected", str(len(result.data["tables"])))
    table_summary.add_row("warnings", str(warning_count))
    table_summary.add_row("config_updated", str(result.config_updated))
    table_summary.add_row("report", str(result.report_path))
    table_summary.add_row("json", str(result.json_path))
    console.print(table_summary)


@app.command("make-source-list")
def make_source_list(
    project_dir: Annotated[Path, typer.Argument(help="Pilot project directory.")],
    from_bibtex: Annotated[Path | None, typer.Option("--from-bibtex", help="BibTeX file to summarize.")] = None,
    from_text: Annotated[Path | None, typer.Option("--from-text", help="Plain-text source list to summarize.")] = None,
    debug: Annotated[bool, DEBUG_OPTION] = False,
) -> None:
    """Create a structured source_list.md without inventing citation metadata."""
    try:
        result = run_make_source_list(project_dir, from_bibtex=from_bibtex, from_text=from_text)
    except Exception as exc:
        console.print(f"[red]make-source-list failed[/red]: {exc}")
        if debug:
            traceback.print_exc()
        raise typer.Exit(code=1) from exc
    table_summary = Table(title="make-source-list complete")
    table_summary.add_column("Field")
    table_summary.add_column("Value")
    table_summary.add_row("source_list", str(result.source_list_path))
    table_summary.add_row("status", str(result.status_path))
    table_summary.add_row("created_or_updated", str(result.created_or_updated))
    table_summary.add_row("parsed_sources", str(result.citation_count))
    table_summary.add_row("warnings", str(len(result.warnings)))
    console.print(table_summary)


@app.command("project-map")
def project_map(
    project_dir: Annotated[Path, typer.Argument(help="Pilot project directory where planning outputs are written.")],
    repo: Annotated[bool, typer.Option("--repo", help="Map the ManuscriptForge repository architecture.")] = False,
    max_depth: Annotated[int | None, typer.Option("--max-depth", help="Maximum tree depth.")] = None,
    debug: Annotated[bool, DEBUG_OPTION] = False,
) -> None:
    """Write a project tree and conceptual workflow diagrams."""
    try:
        result = run_project_map(project_dir, repo=repo, max_depth=max_depth)
    except Exception as exc:
        console.print(f"[red]project-map failed[/red]: {exc}")
        if debug:
            traceback.print_exc()
        raise typer.Exit(code=1) from exc
    table_summary = Table(title="project-map complete")
    table_summary.add_column("Field")
    table_summary.add_column("Value")
    table_summary.add_row("mapped_root", str(result.mapped_root))
    table_summary.add_row("tree", str(result.tree_path))
    table_summary.add_row("architecture", str(result.architecture_path))
    table_summary.add_row("workflow", str(result.workflow_path))
    console.print(table_summary)


@app.command("intake-scan")
def intake_scan(
    project_dir: Annotated[Path, typer.Argument(help="Project directory to scan for incremental intake assets.")],
    write: Annotated[bool, typer.Option("--write", help="Write or update planning/intake registries.")] = False,
    include_outputs: Annotated[
        bool,
        typer.Option("--include-outputs", help="Include generated outputs in the raw asset scan."),
    ] = False,
    debug: Annotated[bool, DEBUG_OPTION] = False,
) -> None:
    """Scan project inputs, style samples, sources, tables, PDFs, and feedback."""
    try:
        result = run_intake_scan(project_dir, write=write, include_outputs=include_outputs, debug=debug)
    except Exception as exc:
        console.print(f"[red]intake-scan failed[/red]: {exc}")
        if debug:
            traceback.print_exc()
        raise typer.Exit(code=1) from exc
    table_summary = Table(title="intake-scan complete")
    table_summary.add_column("Field")
    table_summary.add_column("Value")
    table_summary.add_row("project_dir", str(result.project_dir))
    table_summary.add_row("assets", str(result.summary["total_assets"]))
    table_summary.add_row("writing_samples", str(result.summary["writing_sample_count"]))
    table_summary.add_row("result_tables", str(result.summary["result_table_count"]))
    table_summary.add_row("source_assets", str(result.summary["source_asset_count"]))
    table_summary.add_row("warnings", str(len(result.warnings)))
    table_summary.add_row("registries_written", str(write))
    if result.written_paths:
        table_summary.add_row("intake_dir", str(result.intake_dir))
    console.print(table_summary)


@app.command("intake-report")
def intake_report(
    project_dir: Annotated[Path, typer.Argument(help="Project directory to report on.")],
    mode: Annotated[str | None, typer.Option("--mode", help="Style mode to focus the report on.")] = None,
    debug: Annotated[bool, DEBUG_OPTION] = False,
) -> None:
    """Summarize intake readiness and mode-specific pilot data coverage."""
    try:
        result = run_intake_report(project_dir, mode=mode, debug=debug)
    except Exception as exc:
        console.print(f"[red]intake-report failed[/red]: {exc}")
        if debug:
            traceback.print_exc()
        raise typer.Exit(code=1) from exc
    table_summary = Table(title="intake-report complete")
    table_summary.add_column("Field")
    table_summary.add_column("Value")
    table_summary.add_row("selected_mode", str(result.data["selected_mode"]))
    table_summary.add_row("assets", str(result.data["total_assets"]))
    table_summary.add_row("result_tables", str(result.data["result_table_count"]))
    table_summary.add_row("source_assets", str(result.data["source_asset_count"]))
    table_summary.add_row("report", str(result.report_path))
    table_summary.add_row("json", str(result.json_path))
    console.print(table_summary)


@app.command("intake-approve")
def intake_approve(
    project_dir: Annotated[Path, typer.Argument(help="Project directory.")],
    asset_id: Annotated[str | None, typer.Option("--asset-id", help="Asset ID from data_assets.jsonl.")] = None,
    path: Annotated[Path | None, typer.Option("--path", help="Project-relative or absolute asset path.")] = None,
    asset_type: Annotated[str | None, typer.Option("--asset-type", help="Optional asset type guard.")] = None,
    for_style_mode: Annotated[
        str | None,
        typer.Option("--for-style-mode", help="Style mode this decision applies to."),
    ] = None,
    approve_for: Annotated[
        str | None,
        typer.Option(
            "--approve-for",
            help="drafting|style_profile|claim_generation|citation_mapping|benchmark|feedback_training",
        ),
    ] = None,
    exclude: Annotated[bool, typer.Option("--exclude", help="Mark the asset excluded instead of approved.")] = False,
    note: Annotated[str | None, typer.Option("--note", help="Decision note to append.")] = None,
    debug: Annotated[bool, DEBUG_OPTION] = False,
) -> None:
    """Approve or exclude one intake asset and append an auditable decision."""
    try:
        result = run_intake_approve(
            project_dir,
            asset_id=asset_id,
            path=path,
            asset_type=asset_type,
            for_style_mode=for_style_mode,
            approve_for=approve_for,
            exclude=exclude,
            note=note,
            debug=debug,
        )
    except Exception as exc:
        console.print(f"[red]intake-approve failed[/red]: {exc}")
        if debug:
            traceback.print_exc()
        raise typer.Exit(code=1) from exc
    table_summary = Table(title="intake-approve complete")
    table_summary.add_column("Field")
    table_summary.add_column("Value")
    table_summary.add_row("asset_id", result.asset.asset_id)
    table_summary.add_row("relative_path", result.asset.relative_path)
    table_summary.add_row("status", result.asset.status)
    table_summary.add_row("approved_for", ", ".join(result.asset.approved_for))
    table_summary.add_row("decisions", str(result.decisions_path))
    console.print(table_summary)


@app.command("prep-import-staging")
def prep_import_staging_command(
    project_dir: Annotated[Path, typer.Argument(help="Project directory.")],
    debug: Annotated[bool, DEBUG_OPTION] = False,
) -> None:
    """Create local import-staging folders and metadata templates."""
    try:
        result = run_prep_import_staging(project_dir)
    except Exception as exc:
        console.print(f"[red]prep-import-staging failed[/red]: {exc}")
        if debug:
            traceback.print_exc()
        raise typer.Exit(code=1) from exc
    table_summary = Table(title="prep-import-staging complete")
    table_summary.add_column("Field")
    table_summary.add_column("Value")
    table_summary.add_row("staging_dir", str(result.staging_dir))
    table_summary.add_row("created", str(len(result.created)))
    table_summary.add_row("existing", str(len(result.existing)))
    console.print(table_summary)


@app.command("import-scan")
def import_scan_command(
    project_dir: Annotated[Path, typer.Argument(help="Project directory.")],
    write: Annotated[bool, typer.Option("--write", help="Write import manifests and scan reports.")] = False,
    debug: Annotated[bool, DEBUG_OPTION] = False,
) -> None:
    """Scan import-staging incoming files without importing them."""
    try:
        result = run_import_scan(project_dir, write=write)
    except Exception as exc:
        console.print(f"[red]import-scan failed[/red]: {exc}")
        if debug:
            traceback.print_exc()
        raise typer.Exit(code=1) from exc
    table_summary = Table(title="import-scan complete")
    table_summary.add_column("Field")
    table_summary.add_column("Value")
    table_summary.add_row("candidates", str(result.summary["candidate_count"]))
    table_summary.add_row("needs_privacy_review", str(result.summary["needs_privacy_review"]))
    table_summary.add_row("warnings", str(result.summary["warnings"]))
    table_summary.add_row("written", str(bool(result.written_paths)))
    table_summary.add_row("report", str(result.report_path))
    console.print(table_summary)


@app.command("import-plan")
def import_plan_command(
    project_dir: Annotated[Path, typer.Argument(help="Project directory.")],
    kind: Annotated[
        str,
        typer.Option("--kind", help="writing_sample|result_table|source_pdf|source|feedback|all"),
    ] = "all",
    mode: Annotated[str | None, typer.Option("--mode", help="Filter by suggested style mode.")] = None,
    debug: Annotated[bool, DEBUG_OPTION] = False,
) -> None:
    """Write a proposed import plan without importing files."""
    kind_map = {"feedback": "feedback_jsonl"}
    normalized_kind = kind_map.get(kind, kind)
    try:
        result = run_import_plan(project_dir, kind=normalized_kind, mode=mode)
    except Exception as exc:
        console.print(f"[red]import-plan failed[/red]: {exc}")
        if debug:
            traceback.print_exc()
        raise typer.Exit(code=1) from exc
    table_summary = Table(title="import-plan complete")
    table_summary.add_column("Field")
    table_summary.add_column("Value")
    table_summary.add_row("candidates", str(result.data["candidate_count"]))
    table_summary.add_row("ready", str(result.data["ready_count"]))
    table_summary.add_row("needs_metadata", str(result.data["needs_metadata_count"]))
    table_summary.add_row("needs_privacy_review", str(result.data["needs_privacy_review_count"]))
    table_summary.add_row("duplicates", str(result.data["duplicate_count"]))
    table_summary.add_row("report", str(result.report_path))
    table_summary.add_row("json", str(result.json_path))
    table_summary.add_row("csv", str(result.csv_path))
    console.print(table_summary)


@app.command("import-apply")
def import_apply_command(
    project_dir: Annotated[Path, typer.Argument(help="Project directory.")],
    import_id: Annotated[str | None, typer.Option("--import-id", help="Specific import candidate ID.")] = None,
    all_ready: Annotated[bool, typer.Option("--all-ready", help="Import all candidates marked ready.")] = False,
    copy: Annotated[bool, typer.Option("--copy", help="Copy staged files into active folders.")] = False,
    move: Annotated[bool, typer.Option("--move", help="Move staged files after verified copy.")] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Write a report without copying files.")] = False,
    debug: Annotated[bool, DEBUG_OPTION] = False,
) -> None:
    """Apply selected staged imports by safe copy or explicit move."""
    try:
        result = run_import_apply(
            project_dir,
            import_id=import_id,
            all_ready=all_ready,
            copy=copy or not move,
            move=move,
            dry_run=dry_run,
        )
    except Exception as exc:
        console.print(f"[red]import-apply failed[/red]: {exc}")
        if debug:
            traceback.print_exc()
        raise typer.Exit(code=1) from exc
    table_summary = Table(title="import-apply complete")
    table_summary.add_column("Field")
    table_summary.add_column("Value")
    table_summary.add_row("dry_run", str(result.dry_run))
    table_summary.add_row("selected", str(len(result.selected)))
    table_summary.add_row("copied_or_planned", str(len(result.copied)))
    table_summary.add_row("warnings", str(len(result.warnings)))
    table_summary.add_row("report", str(result.report_path))
    table_summary.add_row("json", str(result.json_path))
    table_summary.add_row("decisions", str(result.decisions_path))
    console.print(table_summary)


@app.command("import-reject")
def import_reject_command(
    project_dir: Annotated[Path, typer.Argument(help="Project directory.")],
    import_id: Annotated[str, typer.Option("--import-id", help="Import candidate ID to reject.")],
    reason: Annotated[str, typer.Option("--reason", help="Reason for rejection.")] = "",
    archive: Annotated[bool, typer.Option("--archive", help="Copy rejected file into archive/rejected.")] = False,
    debug: Annotated[bool, DEBUG_OPTION] = False,
) -> None:
    """Reject a staged file without deleting it."""
    try:
        result = run_import_reject(project_dir, import_id=import_id, reason=reason, archive=archive)
    except Exception as exc:
        console.print(f"[red]import-reject failed[/red]: {exc}")
        if debug:
            traceback.print_exc()
        raise typer.Exit(code=1) from exc
    table_summary = Table(title="import-reject complete")
    table_summary.add_column("Field")
    table_summary.add_column("Value")
    table_summary.add_row("rejected", str(len(result.rejected)))
    table_summary.add_row("archive_copies", str(len(result.archive_paths)))
    table_summary.add_row("decisions", str(result.decisions_path))
    console.print(table_summary)


@app.command("import-summary")
def import_summary_command(
    project_dir: Annotated[Path, typer.Argument(help="Project directory.")],
    debug: Annotated[bool, DEBUG_OPTION] = False,
) -> None:
    """Summarize staged, ready, imported, rejected, and duplicate files."""
    try:
        result = run_import_summary(project_dir)
    except Exception as exc:
        console.print(f"[red]import-summary failed[/red]: {exc}")
        if debug:
            traceback.print_exc()
        raise typer.Exit(code=1) from exc
    table_summary = Table(title="import-summary complete")
    table_summary.add_column("Field")
    table_summary.add_column("Value")
    for key in [
        "candidate_count",
        "staged_files",
        "ready_files",
        "imported_files",
        "rejected_files",
        "duplicate_files",
        "files_needing_metadata",
        "files_needing_privacy_review",
    ]:
        table_summary.add_row(key, str(result.data.get(key, 0)))
    table_summary.add_row("report", str(result.report_path))
    table_summary.add_row("json", str(result.json_path))
    console.print(table_summary)


@app.command("import-doctor")
def import_doctor_command(
    project_dir: Annotated[Path, typer.Argument(help="Project directory.")],
    debug: Annotated[bool, DEBUG_OPTION] = False,
) -> None:
    """Diagnose import-staging readiness without importing files."""
    try:
        result = run_import_doctor(project_dir)
    except Exception as exc:
        console.print(f"[red]import-doctor failed[/red]: {exc}")
        if debug:
            traceback.print_exc()
        raise typer.Exit(code=1) from exc
    table_summary = Table(title="import-doctor complete")
    table_summary.add_column("Field")
    table_summary.add_column("Value")
    for key in [
        "staging_present",
        "incoming_file_count",
        "candidate_count",
        "files_needing_metadata",
        "files_needing_privacy_review",
        "ready_files",
        "duplicate_files",
        "imported_files",
        "rejected_files",
        "archived_files",
    ]:
        table_summary.add_row(key, str(result.data.get(key, 0)))
    table_summary.add_row("report", str(result.report_path))
    table_summary.add_row("json", str(result.json_path))
    console.print(table_summary)


@app.command("import-preview")
def import_preview_command(
    project_dir: Annotated[Path, typer.Argument(help="Project directory.")],
    import_id: Annotated[str | None, typer.Option("--import-id", help="Import candidate ID to preview.")] = None,
    limit_chars: Annotated[int, typer.Option("--limit-chars", help="Maximum text preview characters.")] = 1200,
    debug: Annotated[bool, DEBUG_OPTION] = False,
) -> None:
    """Show a safe preview of one staged candidate without importing it."""
    try:
        result = run_import_preview(project_dir, import_id=import_id, limit_chars=limit_chars)
    except Exception as exc:
        console.print(f"[red]import-preview failed[/red]: {exc}")
        if debug:
            traceback.print_exc()
        raise typer.Exit(code=1) from exc
    table_summary = Table(title="import-preview complete")
    table_summary.add_column("Field")
    table_summary.add_column("Value")
    for key in [
        "import_id",
        "filename",
        "detected_kind",
        "suggested_destination",
        "duplicate_status",
        "privacy_status",
        "privacy_scan_status",
        "preview_status",
    ]:
        table_summary.add_row(key, str(result.data.get(key, "")))
    table_summary.add_row("report", str(result.report_path))
    console.print(table_summary)
    console.print(str(result.data.get("preview_text", "")))


@app.command("import-create-demo-files")
def import_create_demo_files_command(
    project_dir: Annotated[Path, typer.Argument(help="Project directory.")],
    overwrite: Annotated[bool, typer.Option("--overwrite", help="Overwrite existing demo staged files.")] = False,
    debug: Annotated[bool, DEBUG_OPTION] = False,
) -> None:
    """Create clearly labeled toy files under import staging for command rehearsal."""
    try:
        result = run_import_create_demo_files(project_dir, overwrite=overwrite)
    except Exception as exc:
        console.print(f"[red]import-create-demo-files failed[/red]: {exc}")
        if debug:
            traceback.print_exc()
        raise typer.Exit(code=1) from exc
    table_summary = Table(title="import-create-demo-files complete")
    table_summary.add_column("Field")
    table_summary.add_column("Value")
    table_summary.add_row("created", str(len(result.created)))
    table_summary.add_row("skipped", str(len(result.skipped)))
    table_summary.add_row("overwritten", str(len(result.overwritten)))
    table_summary.add_row("next", f"import-scan {result.project_dir} --write")
    console.print(table_summary)


@app.command("import-rehearsal")
def import_rehearsal_command(
    project_dir: Annotated[Path, typer.Argument(help="Project directory.")],
    create_demo: Annotated[bool, typer.Option("--create-demo", help="Create toy staged files before rehearsal.")] = False,
    debug: Annotated[bool, DEBUG_OPTION] = False,
) -> None:
    """Run a safe import-staging rehearsal without activating staged files."""
    try:
        result = run_import_rehearsal(project_dir, create_demo=create_demo)
    except Exception as exc:
        console.print(f"[red]import-rehearsal failed[/red]: {exc}")
        if debug:
            traceback.print_exc()
        raise typer.Exit(code=1) from exc
    table_summary = Table(title="import-rehearsal complete")
    table_summary.add_column("Field")
    table_summary.add_column("Value")
    table_summary.add_row("created_demo", str(result.data.get("created_demo", False)))
    table_summary.add_row("candidates", str(result.data.get("scan_summary", {}).get("candidate_count", 0)))
    table_summary.add_row("ready", str(result.data.get("plan", {}).get("ready_count", 0)))
    table_summary.add_row("needs_metadata", str(result.data.get("plan", {}).get("needs_metadata_count", 0)))
    table_summary.add_row("validation_ok", str(result.data.get("validation", {}).get("ok", False)))
    table_summary.add_row("report", str(result.report_path))
    table_summary.add_row("json", str(result.json_path))
    console.print(table_summary)


@app.command("import-update-metadata")
def import_update_metadata_command(
    project_dir: Annotated[Path, typer.Argument(help="Project directory.")],
    from_csv: Annotated[Path, typer.Option("--from-csv", help="Filled import metadata CSV template.")],
    debug: Annotated[bool, DEBUG_OPTION] = False,
) -> None:
    """Update staged candidate metadata from a CSV template."""
    try:
        result = run_import_update_metadata(project_dir, from_csv=from_csv)
    except Exception as exc:
        console.print(f"[red]import-update-metadata failed[/red]: {exc}")
        if debug:
            traceback.print_exc()
        raise typer.Exit(code=1) from exc
    table_summary = Table(title="import-update-metadata complete")
    table_summary.add_column("Field")
    table_summary.add_column("Value")
    table_summary.add_row("updated", str(len(result.updated)))
    table_summary.add_row("unmatched_rows", str(len(result.unmatched_rows)))
    table_summary.add_row("report", str(result.report_path))
    table_summary.add_row("json", str(result.json_path))
    table_summary.add_row("decisions", str(project_dir / "planning" / "import_staging" / "manifests" / "import_decisions.jsonl"))
    console.print(table_summary)


@app.command("style-mode-summary")
def style_mode_summary(
    project_dir: Annotated[Path, typer.Argument(help="Project directory.")],
    mode: Annotated[str | None, typer.Option("--mode", help="Style mode to summarize.")] = None,
    debug: Annotated[bool, DEBUG_OPTION] = False,
) -> None:
    """Summarize writing samples, exclusions, and section coverage by style mode."""
    try:
        result = run_style_mode_summary(project_dir, mode=mode, debug=debug)
    except Exception as exc:
        console.print(f"[red]style-mode-summary failed[/red]: {exc}")
        if debug:
            traceback.print_exc()
        raise typer.Exit(code=1) from exc
    table_summary = Table(title="style-mode-summary complete")
    table_summary.add_column("Field")
    table_summary.add_column("Value")
    table_summary.add_row("selected_mode", str(result.data["selected_mode"]))
    table_summary.add_row("samples", str(result.data["selected_sample_count"]))
    table_summary.add_row("warnings", str(len(result.data.get("warnings", []))))
    table_summary.add_row("report", str(result.report_path))
    table_summary.add_row("json", str(result.json_path))
    console.print(table_summary)


@app.command("extract-style-corpus")
def extract_style_corpus_command(
    project_dir: Annotated[Path, typer.Argument(help="Project directory.")],
    mode: Annotated[str | None, typer.Option("--mode", help="Style mode to extract.")] = None,
    force: Annotated[bool, typer.Option("--force", help="Ignore cached extraction records.")] = False,
    debug: Annotated[bool, DEBUG_OPTION] = False,
) -> None:
    """Extract eligible style documents without drafting or profiling."""
    try:
        result = run_extract_style_corpus(project_dir, mode=mode, force=force)
    except Exception as exc:
        console.print(f"[red]extract-style-corpus failed[/red]: {exc}")
        if debug:
            traceback.print_exc()
        raise typer.Exit(code=1) from exc
    summary = result.summary
    table_summary = Table(title="extract-style-corpus complete")
    table_summary.add_column("Field")
    table_summary.add_column("Value")
    table_summary.add_row("mode", str(result.mode or "configured/default"))
    table_summary.add_row("files_considered", str(summary["files_considered"]))
    table_summary.add_row("files_extracted", str(summary["files_extracted"]))
    table_summary.add_row("failed", str(summary["failed"]))
    table_summary.add_row("unsupported", str(summary["unsupported"]))
    table_summary.add_row("likely_scanned", str(summary["likely_scanned_or_unextractable"]))
    table_summary.add_row("manifest", str(result.manifest_path))
    table_summary.add_row("report", str(result.report_path))
    console.print(table_summary)


@app.command("style-corpus-doctor")
def style_corpus_doctor_command(
    project_dir: Annotated[Path, typer.Argument(help="Project directory.")],
    mode: Annotated[str | None, typer.Option("--mode", help="Style mode to diagnose.")] = None,
    debug: Annotated[bool, DEBUG_OPTION] = False,
) -> None:
    """Diagnose style corpus readiness for style profiling."""
    try:
        result = run_style_corpus_doctor(project_dir, mode=mode)
    except Exception as exc:
        console.print(f"[red]style-corpus-doctor failed[/red]: {exc}")
        if debug:
            traceback.print_exc()
        raise typer.Exit(code=1) from exc
    table_summary = Table(title="style-corpus-doctor complete")
    table_summary.add_column("Field")
    table_summary.add_column("Value")
    table_summary.add_row("selected_mode", str(result.data["selected_mode"]))
    table_summary.add_row("files_considered", str(result.data["files_considered"]))
    table_summary.add_row("extracted_files", str(result.data["extracted_files"]))
    table_summary.add_row("recommendations", str(len(result.data.get("recommendations", []))))
    table_summary.add_row("report", str(result.report_path))
    table_summary.add_row("json", str(result.json_path))
    console.print(table_summary)


@app.command("build-style-chunk-registry")
def build_style_chunk_registry_command(
    project_dir: Annotated[Path, typer.Argument(help="Project directory.")],
    mode: Annotated[str | None, typer.Option("--mode", help="Style mode to build chunks for.")] = None,
    force: Annotated[bool, typer.Option("--force", help="Force style text re-extraction before chunking.")] = False,
    debug: Annotated[bool, DEBUG_OPTION] = False,
) -> None:
    """Build the local chunk-level style curation registry."""
    try:
        result = run_build_style_chunk_registry(project_dir, mode=mode, force=force)
    except Exception as exc:
        console.print(f"[red]build-style-chunk-registry failed[/red]: {exc}")
        if debug:
            traceback.print_exc()
        raise typer.Exit(code=1) from exc
    summary = result.summary
    table_summary = Table(title="build-style-chunk-registry complete")
    table_summary.add_column("Field")
    table_summary.add_column("Value")
    table_summary.add_row("chunks", str(summary["total_chunks"]))
    table_summary.add_row("needs_review", str(summary["approval_status"].get("needs_review", 0)))
    table_summary.add_row("excluded", str(summary["approval_status"].get("excluded", 0)))
    table_summary.add_row("by_section", ", ".join(f"{k}={v}" for k, v in sorted(summary["section_distribution"].items())) or "none")
    table_summary.add_row("by_mode", ", ".join(f"{k}={v}" for k, v in sorted(summary["mode_distribution"].items())) or "none")
    table_summary.add_row("warnings", str(summary["warning_count"]))
    table_summary.add_row("registry", str(result.paths["csv"]))
    table_summary.add_row("review", str(result.paths["review"]))
    console.print(table_summary)


@app.command("style-chunk-report")
def style_chunk_report_command(
    project_dir: Annotated[Path, typer.Argument(help="Project directory.")],
    mode: Annotated[str | None, typer.Option("--mode", help="Style mode to report.")] = None,
    section: Annotated[str | None, typer.Option("--section", help="Section type to report.")] = None,
    debug: Annotated[bool, DEBUG_OPTION] = False,
) -> None:
    """Summarize style chunk quality and review needs."""
    try:
        result = run_style_chunk_report(project_dir, mode=mode, section=section)
    except Exception as exc:
        console.print(f"[red]style-chunk-report failed[/red]: {exc}")
        if debug:
            traceback.print_exc()
        raise typer.Exit(code=1) from exc
    table_summary = Table(title="style-chunk-report complete")
    table_summary.add_column("Field")
    table_summary.add_column("Value")
    table_summary.add_row("total_chunks", str(result.data["total_chunks"]))
    table_summary.add_row("approved", str(result.data["approved_chunks"]))
    table_summary.add_row("needs_review", str(result.data["needs_review_chunks"]))
    table_summary.add_row("excluded", str(result.data["excluded_chunks"]))
    table_summary.add_row("report", str(result.report_path))
    table_summary.add_row("json", str(result.json_path))
    table_summary.add_row("xlsx", str(result.xlsx_path))
    console.print(table_summary)


@app.command("style-chunk-approve")
def style_chunk_approve_command(
    project_dir: Annotated[Path, typer.Argument(help="Project directory.")],
    chunk_id: Annotated[str | None, typer.Option("--chunk-id", help="Specific chunk ID to update.")] = None,
    source_file: Annotated[str | None, typer.Option("--source-file", help="All chunks from one source file.")] = None,
    section: Annotated[str | None, typer.Option("--section", help="All chunks matching a section type.")] = None,
    mode: Annotated[str | None, typer.Option("--mode", help="All chunks matching a style mode.")] = None,
    approve: Annotated[bool, typer.Option("--approve", help="Mark matching chunks approved.")] = False,
    exclude: Annotated[bool, typer.Option("--exclude", help="Mark matching chunks excluded.")] = False,
    needs_review: Annotated[bool, typer.Option("--needs-review", help="Mark matching chunks as needing review.")] = False,
    approve_for: Annotated[
        str | None,
        typer.Option("--approve-for", help="style_profile|exemplar_retrieval|benchmark|style_eval"),
    ] = None,
    note: Annotated[str | None, typer.Option("--note", help="Decision note.")] = None,
    debug: Annotated[bool, DEBUG_OPTION] = False,
) -> None:
    """Approve, exclude, or mark style chunks for review."""
    try:
        result = run_style_chunk_approval(
            project_dir,
            chunk_id=chunk_id,
            source_file=source_file,
            section=section,
            mode=mode,
            approve=approve,
            exclude=exclude,
            needs_review=needs_review,
            approve_for=approve_for,
            note=note,
        )
    except Exception as exc:
        console.print(f"[red]style-chunk-approve failed[/red]: {exc}")
        if debug:
            traceback.print_exc()
        raise typer.Exit(code=1) from exc
    table_summary = Table(title="style-chunk-approve complete")
    table_summary.add_column("Field")
    table_summary.add_column("Value")
    table_summary.add_row("matched_chunks", str(len(result.matched_rows)))
    table_summary.add_row("decisions", str(result.decisions_path))
    table_summary.add_row("registry", str(result.registry_paths["csv"]))
    console.print(table_summary)


@app.command("style-coverage-report")
def style_coverage_report_command(
    project_dir: Annotated[Path, typer.Argument(help="Project directory.")],
    mode: Annotated[str | None, typer.Option("--mode", help="Style mode to report.")] = None,
    debug: Annotated[bool, DEBUG_OPTION] = False,
) -> None:
    """Report section-level style coverage and balance."""
    try:
        result = run_style_coverage_report(project_dir, mode=mode)
    except Exception as exc:
        console.print(f"[red]style-coverage-report failed[/red]: {exc}")
        if debug:
            traceback.print_exc()
        raise typer.Exit(code=1) from exc
    table_summary = Table(title="style-coverage-report complete")
    table_summary.add_column("Field")
    table_summary.add_column("Value")
    table_summary.add_row("selected_mode", str(result.data["selected_mode"]))
    table_summary.add_row("sections", str(len(result.data["coverage"])))
    table_summary.add_row("warnings", str(len(result.data.get("warnings", []))))
    table_summary.add_row("report", str(result.report_path))
    table_summary.add_row("json", str(result.json_path))
    table_summary.add_row("matrix", str(result.matrix_path))
    console.print(table_summary)


@app.command("build-style-cards")
def build_style_cards_command(
    project_dir: Annotated[Path, typer.Argument(help="Project directory.")],
    mode: Annotated[str | None, typer.Option("--mode", help="Style mode to summarize.")] = None,
    debug: Annotated[bool, DEBUG_OPTION] = False,
) -> None:
    """Build local style cards by mode and section."""
    try:
        result = run_build_style_cards(project_dir, mode=mode)
    except Exception as exc:
        console.print(f"[red]build-style-cards failed[/red]: {exc}")
        if debug:
            traceback.print_exc()
        raise typer.Exit(code=1) from exc
    table_summary = Table(title="build-style-cards complete")
    table_summary.add_column("Field")
    table_summary.add_column("Value")
    table_summary.add_row("cards", str(len(result.card_paths)))
    table_summary.add_row("cards_dir", str(result.cards_dir))
    table_summary.add_row("manifest", str(result.manifest_path))
    console.print(table_summary)


@app.command("prep-style-modes")
def prep_style_modes_command(
    project_dir: Annotated[Path, typer.Argument(help="Project directory.")],
    include_coursework: Annotated[
        bool,
        typer.Option("--include-coursework/--no-include-coursework", help="Create coursework style folder."),
    ] = True,
    include_journal: Annotated[
        bool,
        typer.Option("--include-journal/--no-include-journal", help="Create journal-related style folders."),
    ] = True,
    debug: Annotated[bool, DEBUG_OPTION] = False,
) -> None:
    """Create named style-mode folders with local README guidance."""
    try:
        result = run_prep_style_modes(
            project_dir,
            include_coursework=include_coursework,
            include_journal=include_journal,
        )
    except Exception as exc:
        console.print(f"[red]prep-style-modes failed[/red]: {exc}")
        if debug:
            traceback.print_exc()
        raise typer.Exit(code=1) from exc
    table_summary = Table(title="prep-style-modes complete")
    table_summary.add_column("Field")
    table_summary.add_column("Value")
    table_summary.add_row("created", str(len(result.created)))
    table_summary.add_row("existing", str(len(result.existing)))
    table_summary.add_row("unclassified_files", str(len(result.unclassified_files)))
    console.print(table_summary)


@app.command("prep-journal-adapter")
def prep_journal_adapter_command(
    project_dir: Annotated[Path, typer.Argument(help="Project directory.")],
    journal: Annotated[str, typer.Option("--journal", help="Journal name for a local manual adapter.")],
    debug: Annotated[bool, DEBUG_OPTION] = False,
) -> None:
    """Create a manual journal-adapter scaffold without web access."""
    try:
        result = run_prep_journal_adapter(project_dir, journal=journal)
    except Exception as exc:
        console.print(f"[red]prep-journal-adapter failed[/red]: {exc}")
        if debug:
            traceback.print_exc()
        raise typer.Exit(code=1) from exc
    table_summary = Table(title="prep-journal-adapter complete")
    table_summary.add_column("Field")
    table_summary.add_column("Value")
    table_summary.add_row("journal", result.journal)
    table_summary.add_row("adapter_dir", str(result.adapter_dir))
    table_summary.add_row("created", str(len(result.created)))
    table_summary.add_row("existing", str(len(result.existing)))
    console.print(table_summary)


@app.command()
def init(project_dir: Annotated[Path, typer.Argument(help="Project directory to create.")]) -> None:
    """Create a ManuscriptForge project skeleton."""
    initialize_project(project_dir)
    console.print(f"[green]Initialized project[/green]: {project_dir}")


@app.command()
def validate(
    project_dir: Annotated[Path, typer.Argument(help="Project directory to validate.")],
) -> None:
    """Validate required inputs and configuration."""
    result = validate_project(project_dir)
    table = Table(title=f"Validation Summary ({validation_summary(result)})")
    table.add_column("Level")
    table.add_column("Message")
    for error in result.errors:
        table.add_row("error", error)
    for warning in result.warnings:
        table.add_row("warning", warning)
    for info in result.infos:
        table.add_row("info", info)
    console.print(table)
    if not result.ok:
        raise typer.Exit(code=1)


@app.command()
def ingest(
    project_dir: Annotated[Path, typer.Argument(help="Project directory.")],
    enrich_sources: Annotated[
        bool,
        typer.Option("--enrich-sources", help="Opt into configured source metadata enrichment."),
    ] = False,
    debug: Annotated[bool, DEBUG_OPTION] = False,
) -> None:
    """Normalize project inputs into intermediate JSON artifacts."""
    _run_or_exit("ingest", run_ingest, project_dir, enrich_sources=enrich_sources, debug=debug)


@app.command()
def profile_style(
    project_dir: Annotated[Path, typer.Argument(help="Project directory.")],
    debug: Annotated[bool, DEBUG_OPTION] = False,
) -> None:
    """Profile the supplied style corpus."""
    _run_or_exit("profile-style", run_profile_style, project_dir, debug=debug)


@app.command()
def build_claims(
    project_dir: Annotated[Path, typer.Argument(help="Project directory.")],
    debug: Annotated[bool, DEBUG_OPTION] = False,
) -> None:
    """Compile an evidence-linked claim registry."""
    _run_or_exit("build-claims", run_build_claims, project_dir, debug=debug)


@app.command()
def draft(
    project_dir: Annotated[Path, typer.Argument(help="Project directory.")],
    debug: Annotated[bool, DEBUG_OPTION] = False,
) -> None:
    """Run the full drafting workflow."""
    _run_or_exit("draft", run_draft, project_dir, debug=debug)


@app.command()
def audit(
    project_dir: Annotated[Path, typer.Argument(help="Project directory.")],
    debug: Annotated[bool, DEBUG_OPTION] = False,
) -> None:
    """Audit the latest generated manuscript."""
    _run_or_exit("audit", run_audit, project_dir, debug=debug)


@app.command()
def ask_style(
    project_dir: Annotated[Path, typer.Argument(help="Project directory.")],
    debug: Annotated[bool, DEBUG_OPTION] = False,
) -> None:
    """Generate style calibration questions without blocking for interactive input."""
    _run_or_exit("ask-style", run_ask_style, project_dir, debug=debug)


@app.command()
def style_summary(
    project_dir: Annotated[Path, typer.Argument(help="Project directory.")],
    debug: Annotated[bool, DEBUG_OPTION] = False,
) -> None:
    """Summarize style corpus coverage, memory, and unresolved style ambiguities."""
    _run_or_exit("style-summary", run_style_summary, project_dir, debug=debug)


@app.command()
def build_style_eval(
    project_dir: Annotated[Path, typer.Argument(help="Project directory.")],
    debug: Annotated[bool, DEBUG_OPTION] = False,
) -> None:
    """Build local style evaluation JSONL datasets without training a model."""
    _run_or_exit("build-style-eval", run_build_style_eval, project_dir, debug=debug)


@app.command()
def style_benchmark(
    project_dir: Annotated[Path, typer.Argument(help="Project directory.")],
    run: Annotated[str | None, typer.Option("--run", help="Run ID or path to benchmark.")] = None,
    compare_run: Annotated[
        str | None,
        typer.Option("--compare-run", help="Run ID/path to compare against, or 'previous'."),
    ] = None,
    section: Annotated[
        str | None,
        typer.Option("--section", help="Limit section-specific scoring to one section type."),
    ] = None,
    output_json: Annotated[
        bool,
        typer.Option("--output-json", help="Print machine-readable benchmark JSON after writing files."),
    ] = False,
    debug: Annotated[bool, DEBUG_OPTION] = False,
) -> None:
    """Run deterministic local style benchmarking for a manuscript run."""
    try:
        run_dir = run_style_benchmark(
            project_dir,
            run_ref=run,
            compare_run_ref=compare_run,
            section_filter=section,
        )
        if output_json:
            console.print_json(data=read_json(run_dir / "style_benchmark_scores.json"))
        else:
            _print_run("style-benchmark", run_dir)
    except Exception as exc:
        console.print(f"[red]style-benchmark failed[/red]: {exc}")
        if debug:
            traceback.print_exc()
        raise typer.Exit(code=1) from exc


@app.command()
def review_plan(
    project_dir: Annotated[Path, typer.Argument(help="Project directory.")],
    run: Annotated[str | None, typer.Option("--run", help="Run ID or path to review.")] = None,
    max_items: Annotated[int, typer.Option("--max-items", help="Maximum review items to select.")] = 30,
    priority: Annotated[
        str,
        typer.Option("--priority", help="all|style|claims|citations|journal|no-invention|benchmark"),
    ] = "all",
    section: Annotated[str | None, typer.Option("--section", help="Limit review items to a section type.")] = None,
    debug: Annotated[bool, DEBUG_OPTION] = False,
) -> None:
    """Create a structured human review plan from audits, source map, and benchmark outputs."""
    _run_or_exit(
        "review-plan",
        run_review_plan,
        project_dir,
        run_ref=run,
        max_items=max_items,
        priority=priority,
        section_filter=section,
        debug=debug,
    )


@app.command()
def generate_variants(
    project_dir: Annotated[Path, typer.Argument(help="Project directory.")],
    run: Annotated[str | None, typer.Option("--run", help="Run ID or path.")] = None,
    review_plan: Annotated[Path | None, typer.Option("--review-plan", help="Review plan JSON path.")] = None,
    max_variants: Annotated[int, typer.Option("--max-variants", help="Maximum variants per item.")] = 4,
    section: Annotated[str | None, typer.Option("--section", help="Limit variants to a section type.")] = None,
    debug: Annotated[bool, DEBUG_OPTION] = False,
) -> None:
    """Generate deterministic rewrite variants for review-plan items."""
    _run_or_exit(
        "generate-variants",
        run_generate_variants,
        project_dir,
        run_ref=run,
        review_plan_path=review_plan,
        max_variants=max_variants,
        section_filter=section,
        debug=debug,
    )


@app.command()
def capture_feedback(
    project_dir: Annotated[Path, typer.Argument(help="Project directory.")],
    run: Annotated[str | None, typer.Option("--run", help="Run ID or path.")] = None,
    variants: Annotated[Path | None, typer.Option("--variants", help="Variants JSONL path.")] = None,
    interactive: Annotated[bool, typer.Option("--interactive", help="Capture feedback in the terminal.")] = False,
    from_file: Annotated[Path | None, typer.Option("--from-file", help="Feedback JSONL file to append.")] = None,
    debug: Annotated[bool, DEBUG_OPTION] = False,
) -> None:
    """Append validated author feedback to style_feedback.jsonl."""
    _run_or_exit(
        "capture-feedback",
        run_capture_feedback,
        project_dir,
        run_ref=run,
        variants_path=variants,
        interactive=interactive,
        from_file=from_file,
        debug=debug,
    )


@app.command()
def apply_feedback(
    project_dir: Annotated[Path, typer.Argument(help="Project directory.")],
    run: Annotated[str | None, typer.Option("--run", help="Run ID or path.")] = None,
    feedback: Annotated[Path | None, typer.Option("--feedback", help="Feedback JSONL path.")] = None,
    mode: Annotated[str, typer.Option("--mode", help="reviewed-copy|patch-plan")] = "reviewed-copy",
    benchmark: Annotated[bool, typer.Option("--benchmark", help="Write a simple feedback benchmark delta.")] = False,
    debug: Annotated[bool, DEBUG_OPTION] = False,
) -> None:
    """Apply accepted feedback safely to a reviewed copy or patch plan."""
    _run_or_exit(
        "apply-feedback",
        run_apply_feedback,
        project_dir,
        run_ref=run,
        feedback_path=feedback,
        mode=mode,
        benchmark=benchmark,
        debug=debug,
    )


@app.command()
def feedback_summary(
    project_dir: Annotated[Path, typer.Argument(help="Project directory.")],
    run: Annotated[str | None, typer.Option("--run", help="Run ID or path.")] = None,
    debug: Annotated[bool, DEBUG_OPTION] = False,
) -> None:
    """Summarize review feedback, accepted rewrites, and collection counts."""
    _run_or_exit("feedback-summary", run_feedback_summary, project_dir, run_ref=run, debug=debug)


@app.command()
def reviewer_sim(
    project_dir: Annotated[Path, typer.Argument(help="Project directory.")],
    debug: Annotated[bool, DEBUG_OPTION] = False,
) -> None:
    """Generate a reviewer-style critique for the latest manuscript."""
    _run_or_exit("reviewer-sim", run_reviewer_sim, project_dir, debug=debug)


@app.command()
def export(
    project_dir: Annotated[Path, typer.Argument(help="Project directory.")],
    debug: Annotated[bool, DEBUG_OPTION] = False,
) -> None:
    """Re-export latest manuscript artifacts."""
    _run_or_exit("export", run_export, project_dir, debug=debug)


@app.command()
def diff(
    project_dir: Annotated[Path, typer.Argument(help="Project directory.")],
    run_a: Annotated[str | None, typer.Option("--run-a", help="Run ID or path for the older run.")] = None,
    run_b: Annotated[str | None, typer.Option("--run-b", help="Run ID or path for the newer run.")] = None,
    debug: Annotated[bool, DEBUG_OPTION] = False,
) -> None:
    """Compare two ManuscriptForge runs."""
    _run_or_exit("diff", run_diff, project_dir, run_a=run_a, run_b=run_b, debug=debug)


@app.command()
def ui(project_dir: Annotated[Path, typer.Argument(help="Project directory.")]) -> None:
    """Launch the optional Streamlit UI."""
    try:
        import streamlit  # noqa: F401
    except Exception as exc:
        console.print("[red]Streamlit is not installed.[/red] Install with: pip install -e .[ui]")
        raise typer.Exit(code=1) from exc
    app_path = Path(__file__).with_name("app.py")
    subprocess.run(
        [sys.executable, "-m", "streamlit", "run", str(app_path), "--", str(project_dir)],
        check=False,
    )


if __name__ == "__main__":
    app()
