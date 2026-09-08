from __future__ import annotations

import platform
import sys
from importlib import metadata
from pathlib import Path
from typing import Any

from manuscriptforge.audit.citation_audit import audit_citations
from manuscriptforge.audit.claim_audit import audit_claims
from manuscriptforge.audit.journal_audit import audit_journal_profile, write_journal_audit_report
from manuscriptforge.audit.no_invention_audit import (
    audit_no_invention,
    build_source_map,
    write_no_invention_report,
    write_source_map,
)
from manuscriptforge.audit.overclaiming_audit import audit_overclaiming
from manuscriptforge.audit.reproducibility_audit import audit_reproducibility
from manuscriptforge.audit.reviewer_simulator import simulate_reviewer, write_reviewer_critique
from manuscriptforge.audit.style_audit import audit_style_details, write_style_report
from manuscriptforge.config import ValidationResult, load_project_config, validate_project
from manuscriptforge.drafting.abstract_writer import write_abstract
from manuscriptforge.drafting.ai_disclosure import write_ai_disclosure
from manuscriptforge.drafting.discussion_writer import write_discussion
from manuscriptforge.drafting.outline_builder import build_outline
from manuscriptforge.drafting.results_writer import write_results
from manuscriptforge.drafting.revision_questions import (
    generate_revision_question_records,
    write_revision_question_artifacts,
)
from manuscriptforge.drafting.section_writer import write_generic_section
from manuscriptforge.drafting.title_writer import write_title_candidates
from manuscriptforge.evidence.claim_registry import build_claim_registry
from manuscriptforge.export.bundle_exporter import export_bundle
from manuscriptforge.export.docx_exporter import export_docx
from manuscriptforge.export.excel_reporter import (
    write_audit_workbook,
    write_citation_audit_xlsx,
    write_findings_xlsx,
    write_workbook,
)
from manuscriptforge.export.latex_exporter import export_latex
from manuscriptforge.export.markdown_exporter import export_markdown
from manuscriptforge.ingest.project_ingest import ingest_project
from manuscriptforge.llm.base import LLMProvider
from manuscriptforge.llm.mock_provider import MockLLMProvider
from manuscriptforge.llm.openai_provider import OpenAIProvider
from manuscriptforge.models.audit import AuditFinding
from manuscriptforge.models.citation import CitationRecord
from manuscriptforge.models.claim import ScientificClaim
from manuscriptforge.models.manuscript import Manuscript
from manuscriptforge.pipeline.run_context import ManuscriptContext
from manuscriptforge.review.workflow import (
    run_apply_feedback as execute_apply_feedback,
)
from manuscriptforge.review.workflow import (
    run_capture_feedback as execute_capture_feedback,
)
from manuscriptforge.review.workflow import (
    run_feedback_summary as execute_feedback_summary,
)
from manuscriptforge.review.workflow import (
    run_generate_variants as execute_generate_variants,
)
from manuscriptforge.review.workflow import (
    run_review_plan as execute_review_plan,
)
from manuscriptforge.style.benchmark import run_style_benchmark as execute_style_benchmark
from manuscriptforge.style.evaluation import build_style_evaluation_set
from manuscriptforge.style.memory import load_style_memory, update_style_memory
from manuscriptforge.style.preference_trainer import write_style_questions
from manuscriptforge.style.profiler import build_style_profile
from manuscriptforge.style.summary import build_style_summary, render_style_summary
from manuscriptforge.utils.dates import utc_iso
from manuscriptforge.utils.hashing import sha256_file, sha256_text
from manuscriptforge.utils.io import (
    create_run_dir,
    latest_run_dir,
    read_json,
    update_latest_copy,
    write_json,
    write_text,
)


def provider_from_config(config) -> LLMProvider:
    provider = (config.llm.provider or "").strip().lower()
    if not provider or provider == "mock":
        return MockLLMProvider()
    if provider == "openai":
        if config.privacy.local_only:
            return MockLLMProvider()
        return OpenAIProvider(
            model=config.llm.model,
            temperature=config.llm.temperature,
            local_only=config.privacy.local_only,
        )
    raise ValueError(f"Unknown LLM provider: {config.llm.provider}")


def _input_hashes(ingested: dict[str, Any]) -> dict[str, str]:
    hashes = {}
    for _key, value in ingested.get("text_inputs", {}).items():
        if value.get("content_hash"):
            hashes[value["source_path"]] = value["content_hash"]
    for table in ingested.get("tables", []):
        hashes[table["source_path"]] = table["content_hash"]
    for sample in ingested.get("style_corpus", []):
        hashes[sample["source_path"]] = sample["content_hash"]
    for citation in ingested.get("citations", []):
        source_path = citation.source_path if isinstance(citation, CitationRecord) else citation.get("source_path")
        if source_path and source_path not in hashes:
            project_source_hash = ingested.get("source_hashes", {}).get(source_path)
            if project_source_hash:
                hashes[source_path] = project_source_hash
    for evidence in ingested.get("evidence_items", []):
        source_path = evidence.source_path if hasattr(evidence, "source_path") else evidence.get("source_path")
        content_hash = evidence.content_hash if hasattr(evidence, "content_hash") else evidence.get("content_hash")
        if source_path and content_hash and source_path not in hashes:
            hashes[source_path] = content_hash
    return hashes


def _project_file_hashes(project_dir: Path) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for folder_name in ["inputs", "style_corpus"]:
        folder = project_dir / folder_name
        if not folder.exists():
            continue
        for path in sorted(p for p in folder.rglob("*") if p.is_file()):
            hashes[path.relative_to(project_dir).as_posix()] = sha256_file(path)
    config_path = project_dir / "project.yaml"
    if config_path.exists():
        hashes["project.yaml"] = sha256_file(config_path)
    return hashes


def _metadata_cache_hashes(project_dir: Path, config) -> dict[str, str]:
    cache_dir = project_dir / config.sources.metadata_cache_dir
    if not cache_dir.exists():
        return {}
    return {
        path.relative_to(project_dir).as_posix(): sha256_file(path)
        for path in sorted(cache_dir.rglob("*.json"))
        if path.is_file()
    }


def _run_file_hashes(run_dir: Path) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for path in sorted(p for p in run_dir.rglob("*") if p.is_file() and p.name != "run_manifest.json"):
        hashes[path.relative_to(run_dir).as_posix()] = sha256_file(path)
    return hashes


def _software_versions() -> dict[str, str]:
    packages = [
        "manuscriptforge",
        "pandas",
        "pydantic",
        "python-docx",
        "pypdf",
        "openpyxl",
        "typer",
        "rich",
        "bibtexparser",
        "PyYAML",
    ]
    versions = {"python": sys.version.split()[0], "platform": platform.platform()}
    for package in packages:
        try:
            versions[package] = metadata.version(package)
        except metadata.PackageNotFoundError:
            versions[package] = "not installed"
    return versions


def write_manifest(
    project_dir: Path,
    run_dir: Path,
    command: str,
    config,
    ingested: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    files = sorted(
        path.relative_to(run_dir).as_posix()
        for path in run_dir.rglob("*")
        if path.is_file() and path.name != "run_manifest.json"
    )
    manifest = {
        "command": command,
        "created_utc": utc_iso(),
        "completed_utc": utc_iso(),
        "project_dir": str(project_dir),
        "run_dir": str(run_dir),
        "config": config.model_dump(mode="json"),
        "config_hash": sha256_text(config.model_dump_json()),
        "input_hashes": _project_file_hashes(project_dir) | _input_hashes(ingested or {}),
        "metadata_cache_hashes": _metadata_cache_hashes(project_dir, config),
        "output_hashes": _run_file_hashes(run_dir),
        "software_versions": _software_versions(),
        "files": files,
        "extra": extra or {},
    }
    write_json(run_dir / "run_manifest.json", manifest)


def run_ingest(project_dir: Path, enrich_sources: bool = False) -> Path:
    project_dir = Path(project_dir)
    config = load_project_config(project_dir)
    run_dir = create_run_dir(project_dir)
    ingested = ingest_project(project_dir, run_dir, enrich_sources=enrich_sources)
    write_manifest(
        project_dir,
        run_dir,
        "ingest",
        config,
        ingested,
        {
            "files_ingested": len(ingested.get("source_hashes", {})),
            "tables_ingested": len(ingested.get("tables", [])),
            "sources_parsed": len(ingested.get("citations", [])),
            "metadata_cache_files": len(ingested.get("metadata_cache_files", [])),
        },
    )
    update_latest_copy(project_dir, run_dir)
    return run_dir


def run_profile_style(project_dir: Path) -> Path:
    project_dir = Path(project_dir)
    config = load_project_config(project_dir)
    run_dir = create_run_dir(project_dir)
    provider_name = (config.llm.provider or "").strip().lower()
    provider = provider_from_config(config) if provider_name not in {"", "mock"} else None
    profile = build_style_profile(project_dir, run_dir, provider=provider)
    memory = load_style_memory(project_dir)
    if memory:
        write_json(run_dir / "style_memory.json", memory)
    chunk_index = profile.global_features.get("style_chunk_index", {})
    write_manifest(
        project_dir,
        run_dir,
        "profile-style",
        config,
        extra={
            "style_documents": len(profile.corpus_files),
            "style_chunks": chunk_index.get("chunk_count", 0) if isinstance(chunk_index, dict) else 0,
            "style_files": profile.corpus_files,
            "active_style_mode": config.style.active_mode,
            "style_mode_distribution": profile.global_features.get("style_mode_distribution", {}),
            "style_extraction_summary": profile.global_features.get("style_extraction_summary", {}),
        },
    )
    update_latest_copy(project_dir, run_dir)
    return run_dir


def run_build_claims(project_dir: Path) -> Path:
    project_dir = Path(project_dir)
    config = load_project_config(project_dir)
    run_dir = create_run_dir(project_dir)
    ingested = ingest_project(project_dir, run_dir)
    claims = build_claim_registry(project_dir, run_dir, ingested)
    write_manifest(project_dir, run_dir, "build-claims", config, ingested, {"claim_count": len(claims)})
    update_latest_copy(project_dir, run_dir)
    return run_dir


def _load_citations(ingested: dict[str, Any]) -> list[CitationRecord]:
    return [
        item if isinstance(item, CitationRecord) else CitationRecord.model_validate(item)
        for item in ingested.get("citations", [])
    ]


def _write_text_reports(
    run_dir: Path,
    unsupported_claims: list[ScientificClaim],
    overclaiming: list[AuditFinding],
    reproducibility: list[AuditFinding],
) -> None:
    unsupported_lines = [
        "# Unsupported or Review-Needed Claims",
        "",
        "| Claim ID | Strength | Section | Evidence | Citations | Recommended action | Claim |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for claim in unsupported_claims:
        evidence = ", ".join(
            f"{item.evidence_id} ({item.source_label}{', ' + item.locator if item.locator else ''})"
            for item in claim.evidence_items
        ) or "none"
        citations = ", ".join(claim.citation_ids) or "none"
        if claim.support_strength == "unsupported":
            action = "Remove, rewrite as a limitation, or add direct evidence."
        elif claim.needs_citation:
            action = "Map to a supplied citation or mark unsupported."
        else:
            action = "Soften wording and verify evidence scope."
        unsupported_lines.append(
            "| "
            + " | ".join(
                [
                    f"`{claim.claim_id}`",
                    claim.support_strength,
                    claim.section_target,
                    evidence.replace("|", "/"),
                    citations,
                    action,
                    claim.claim_text.replace("|", "/"),
                ]
            )
            + " |"
        )
    if not unsupported_claims:
        unsupported_lines.append("")
        unsupported_lines.append("No unsupported or review-needed claims were detected.")
    write_text(
        run_dir / "unsupported_claims.md",
        "\n".join(unsupported_lines) + "\n",
    )
    write_text(
        run_dir / "overclaiming_report.md",
        "# Overclaiming Report\n\n"
        + "\n".join(f"- {finding.severity}: {finding.message} ({finding.location})" for finding in overclaiming)
        + ("\n" if overclaiming else "No overclaiming findings were detected.\n"),
    )
    write_text(
        run_dir / "reproducibility_report.md",
        "# Reproducibility Report\n\n"
        + "\n".join(
            f"- {finding.severity}: {finding.message} Suggested fix: {finding.suggested_fix or ''}"
            for finding in reproducibility
        )
        + ("\n" if reproducibility else "No reproducibility findings were detected.\n"),
    )


def _audit_and_write(
    project_dir: Path,
    run_dir: Path,
    manuscript: Manuscript,
    claims: list[ScientificClaim],
    citations: list[CitationRecord],
    style_profile,
    ingested: dict[str, Any] | None = None,
    provider_name: str = "unknown",
) -> list[AuditFinding]:
    config = load_project_config(project_dir)
    claim_findings = audit_claims(manuscript, claims)
    citation_findings = audit_citations(manuscript, claims, citations)
    overclaiming_findings = audit_overclaiming(manuscript, claims)
    reproducibility_findings = audit_reproducibility(manuscript, config.field)
    style_memory = load_style_memory(project_dir)
    style_findings, style_report, style_report_data = audit_style_details(
        manuscript, style_profile, style_memory
    )
    journal_findings, journal_report = audit_journal_profile(manuscript, config)
    no_invention_findings, no_invention_report = audit_no_invention(
        manuscript, claims, citations, ingested
    )
    all_findings = (
        claim_findings
        + citation_findings
        + overclaiming_findings
        + reproducibility_findings
        + style_findings
        + journal_findings
        + no_invention_findings
    )
    write_findings_xlsx(run_dir / "claim_audit.xlsx", claim_findings, "claim_audit")
    write_citation_audit_xlsx(run_dir / "citation_audit.xlsx", citation_findings, citations)
    write_findings_xlsx(run_dir / "journal_audit.xlsx", journal_findings, "journal_audit")
    write_findings_xlsx(run_dir / "audit_findings.xlsx", all_findings, "findings")
    write_json(run_dir / "audit_findings.json", all_findings)
    write_json(run_dir / "intermediate" / "audit_findings.json", all_findings)
    write_style_report(run_dir / "style_report.md", style_report)
    write_json(run_dir / "style_report.json", style_report_data)
    write_workbook(
        run_dir / "style_report.xlsx",
        {
            "Style Summary": [style_report_data.get("summary", {})],
            "Style Metrics": style_report_data.get("metrics", []),
            "Style Findings": style_report_data.get("findings", []),
        },
    )
    write_journal_audit_report(run_dir / "journal_audit.md", journal_report)
    write_no_invention_report(run_dir / "no_invention_report.md", no_invention_report)
    source_map = build_source_map(manuscript, provider_name=provider_name)
    write_source_map(run_dir / "source_map.json", source_map)
    _write_text_reports(
        run_dir,
        [claim for claim in claims if claim.support_strength in {"unsupported", "needs_review"} or claim.needs_human_review],
        overclaiming_findings,
        reproducibility_findings,
    )
    revision_records = generate_revision_question_records(claims, all_findings)
    write_revision_question_artifacts(run_dir, revision_records)
    write_audit_workbook(
        run_dir / "manuscriptforge_audit_workbook.xlsx",
        claims=claims,
        citations=citations,
        findings=all_findings,
        revision_questions=revision_records,
        tables=(ingested or {}).get("tables", []),
        source_map=source_map,
        manifest=read_json(run_dir / "run_manifest.json") if (run_dir / "run_manifest.json").exists() else {},
    )
    return all_findings


def run_draft(project_dir: Path) -> Path:
    project_dir = Path(project_dir)
    validation = validate_project(project_dir)
    if not validation.ok:
        raise ValueError(
            "Project validation failed. Fix these issue(s) before drafting:\n- "
            + "\n- ".join(validation.errors)
        )
    config = load_project_config(project_dir)
    provider = provider_from_config(config)
    run_dir = create_run_dir(project_dir)
    ingested = ingest_project(project_dir, run_dir)
    style_profile = build_style_profile(project_dir, run_dir, samples=ingested.get("style_corpus", []))
    style_memory = load_style_memory(project_dir)
    if style_memory:
        write_json(run_dir / "style_memory.json", style_memory)
    claims = build_claim_registry(project_dir, run_dir, ingested)
    citations = _load_citations(ingested)
    context = ManuscriptContext(
        project_dir=project_dir,
        run_dir=run_dir,
        config=config,
        ingested=ingested,
        style_profile=style_profile,
        claims=claims,
        citations=citations,
        provider=provider,
    )
    title_candidates = write_title_candidates(context)
    abstract = write_abstract(context)
    sections = []
    for section_name in build_outline(config):
        if section_name in {"Abstract", "References"}:
            continue
        if section_name == "Results":
            sections.append(write_results(context))
        elif section_name == "Discussion":
            sections.append(write_discussion(context))
        else:
            sections.append(write_generic_section(context, section_name))
    figure_text = ingested["text_inputs"].get("figure_legends", {}).get("text", "")
    figure_legends = [line.strip() for line in figure_text.splitlines() if line.strip()]
    manuscript = Manuscript(
        title=title_candidates[0],
        title_candidates=title_candidates,
        abstract=abstract,
        sections=sections,
        references=citations,
        figure_legends=figure_legends,
        tables=ingested.get("tables", []),
        ai_disclosure=write_ai_disclosure(context),
        metadata={
            "article_type": config.article_type,
            "generated_by": "ManuscriptForge",
            "provider": provider.name,
            "active_style_mode": config.style.active_mode,
            "style_section_controls": config.style.section_controls,
        },
    )
    write_json(run_dir / "manuscript.json", manuscript)
    write_text(run_dir / "ai_disclosure.md", "# AI-Use Disclosure Draft\n\n" + manuscript.ai_disclosure + "\n")
    export_markdown(run_dir / "manuscript.md", manuscript)
    export_docx(run_dir / "manuscript.docx", manuscript)
    export_latex(run_dir / "manuscript.tex", manuscript)
    findings = _audit_and_write(
        project_dir,
        run_dir,
        manuscript,
        claims,
        citations,
        style_profile,
        ingested=ingested,
        provider_name=provider.name,
    )
    critique = simulate_reviewer(manuscript, findings)
    write_reviewer_critique(run_dir / "reviewer_critique.md", critique)
    revision_records = generate_revision_question_records(claims, findings)
    write_revision_question_artifacts(run_dir, revision_records)
    export_bundle(run_dir)
    write_manifest(
        project_dir,
        run_dir,
        "draft",
        config,
        ingested,
        {
            "claim_count": len(claims),
            "citation_count": len(citations),
            "finding_count": len(findings),
            "unsupported_claims": len([claim for claim in claims if claim.needs_human_review]),
            "tables_ingested": len(ingested.get("tables", [])),
            "sources_parsed": len(citations),
            "active_style_mode": config.style.active_mode,
        },
    )
    update_latest_copy(project_dir, run_dir)
    return run_dir


def _load_latest_artifacts(
    project_dir: Path,
) -> tuple[Path, Manuscript, list[ScientificClaim], list[CitationRecord], Any, dict[str, Any]]:
    run_dir = None
    runs_dir = project_dir / "outputs" / "runs"
    if runs_dir.exists():
        for candidate in sorted([p for p in runs_dir.iterdir() if p.is_dir()], reverse=True):
            if (candidate / "manuscript.json").exists():
                run_dir = candidate
                break
    if run_dir is None:
        run_dir = latest_run_dir(project_dir)
    if run_dir is None:
        raise FileNotFoundError("No prior run found. Run `manuscriptforge draft PROJECT_DIR` first.")
    if not (run_dir / "manuscript.json").exists():
        raise FileNotFoundError("No manuscript run found. Run `manuscriptforge draft PROJECT_DIR` first.")
    manuscript = Manuscript.model_validate(read_json(run_dir / "manuscript.json"))
    claims = [ScientificClaim.model_validate(item) for item in read_json(run_dir / "claim_registry.json")]
    citation_path = run_dir / "citation_registry.json"
    citations = [CitationRecord.model_validate(item) for item in read_json(citation_path)] if citation_path.exists() else []
    from manuscriptforge.models.style import StyleProfile

    style_profile = StyleProfile.model_validate(read_json(run_dir / "style_profile.json"))
    ingested_path = run_dir / "intermediate" / "ingested_project.json"
    ingested = read_json(ingested_path) if ingested_path.exists() else {}
    return run_dir, manuscript, claims, citations, style_profile, ingested


def run_audit(project_dir: Path) -> Path:
    project_dir = Path(project_dir)
    config = load_project_config(project_dir)
    run_dir, manuscript, claims, citations, style_profile, ingested = _load_latest_artifacts(project_dir)
    provider_name = str(manuscript.metadata.get("provider", "unknown"))
    findings = _audit_and_write(
        project_dir,
        run_dir,
        manuscript,
        claims,
        citations,
        style_profile,
        ingested=ingested,
        provider_name=provider_name,
    )
    revision_records = generate_revision_question_records(claims, findings)
    write_revision_question_artifacts(run_dir, revision_records)
    critique = simulate_reviewer(manuscript, findings)
    write_reviewer_critique(run_dir / "reviewer_critique.md", critique)
    write_manifest(project_dir, run_dir, "audit", config, extra={"finding_count": len(findings)})
    update_latest_copy(project_dir, run_dir)
    return run_dir


def run_reviewer_sim(project_dir: Path) -> Path:
    project_dir = Path(project_dir)
    run_dir, manuscript, claims, citations, style_profile, ingested = _load_latest_artifacts(project_dir)
    provider_name = str(manuscript.metadata.get("provider", "unknown"))
    findings = _audit_and_write(
        project_dir,
        run_dir,
        manuscript,
        claims,
        citations,
        style_profile,
        ingested=ingested,
        provider_name=provider_name,
    )
    critique = simulate_reviewer(manuscript, findings)
    write_reviewer_critique(run_dir / "reviewer_critique.md", critique)
    update_latest_copy(project_dir, run_dir)
    return run_dir


def run_ask_style(project_dir: Path) -> Path:
    project_dir = Path(project_dir)
    config = load_project_config(project_dir)
    latest_manuscript = None
    latest_findings: list[AuditFinding] = []
    try:
        latest_run, latest_manuscript, _claims, _citations, _style_profile, _ingested = _load_latest_artifacts(project_dir)
        findings_path = latest_run / "audit_findings.json"
        if findings_path.exists():
            latest_findings = [
                AuditFinding.model_validate(item) for item in read_json(findings_path)
            ]
    except FileNotFoundError:
        latest_manuscript = None
        latest_findings = []
    run_dir = create_run_dir(project_dir)
    profile = build_style_profile(project_dir, run_dir)
    updated = write_style_questions(
        project_dir,
        run_dir,
        profile,
        manuscript=latest_manuscript,
        findings=latest_findings,
    )
    write_manifest(
        project_dir,
        run_dir,
        "ask-style",
        config,
        extra={
            "question_count": len(updated.preference_training_examples),
            "style_chunks": updated.global_features.get("style_chunk_index", {}).get("chunk_count", 0),
            "active_style_mode": config.style.active_mode,
        },
    )
    update_latest_copy(project_dir, run_dir)
    return run_dir


def run_style_summary(project_dir: Path) -> Path:
    project_dir = Path(project_dir)
    config = load_project_config(project_dir)
    run_dir = create_run_dir(project_dir)
    profile = build_style_profile(project_dir, run_dir)
    memory = update_style_memory(project_dir, profile)
    summary = build_style_summary(profile, memory)
    write_json(run_dir / "style_summary.json", summary)
    write_text(run_dir / "style_summary.md", render_style_summary(summary))
    write_json(run_dir / "style_memory.json", memory)
    write_manifest(
        project_dir,
        run_dir,
        "style-summary",
        config,
        extra={
            "style_documents": summary["style_documents"],
            "style_chunks": summary["style_chunks"],
            "preference_answers_collected": summary["preference_answers_collected"],
            "active_style_mode": config.style.active_mode,
            "style_extraction_summary": summary.get("style_extraction_summary", {}),
        },
    )
    update_latest_copy(project_dir, run_dir)
    return run_dir


def run_build_style_eval(project_dir: Path) -> Path:
    project_dir = Path(project_dir)
    config = load_project_config(project_dir)
    run_dir = create_run_dir(project_dir)
    profile = build_style_profile(project_dir, run_dir)
    counts = build_style_evaluation_set(project_dir, run_dir)
    write_manifest(
        project_dir,
        run_dir,
        "build-style-eval",
        config,
        extra={
            "style_documents": len(profile.corpus_files),
            "style_chunks": counts["style_chunks"],
            "style_eval_pairs": counts["style_eval_pairs"],
            "style_eval_rewrite_prompts": counts["style_eval_rewrite_prompts"],
            "style_eval_rank_questions": counts["style_eval_rank_questions"],
            "active_style_mode": config.style.active_mode,
        },
    )
    update_latest_copy(project_dir, run_dir)
    return run_dir


def run_style_benchmark(
    project_dir: Path,
    run_ref: str | None = None,
    compare_run_ref: str | None = None,
    section_filter: str | None = None,
) -> Path:
    project_dir = Path(project_dir)
    config = load_project_config(project_dir)
    return execute_style_benchmark(
        project_dir,
        config,
        run_ref=run_ref,
        compare_run_ref=compare_run_ref,
        section_filter=section_filter,
    )


def run_review_plan(
    project_dir: Path,
    run_ref: str | None = None,
    max_items: int = 30,
    priority: str = "all",
    section_filter: str | None = None,
) -> Path:
    return execute_review_plan(
        Path(project_dir),
        run_ref=run_ref,
        max_items=max_items,
        priority=priority,
        section_filter=section_filter,
    )


def run_generate_variants(
    project_dir: Path,
    run_ref: str | None = None,
    review_plan_path: Path | None = None,
    max_variants: int = 4,
    section_filter: str | None = None,
) -> Path:
    return execute_generate_variants(
        Path(project_dir),
        run_ref=run_ref,
        review_plan_path=review_plan_path,
        max_variants=max_variants,
        section_filter=section_filter,
    )


def run_capture_feedback(
    project_dir: Path,
    run_ref: str | None = None,
    variants_path: Path | None = None,
    interactive: bool = False,
    from_file: Path | None = None,
) -> Path:
    return execute_capture_feedback(
        Path(project_dir),
        run_ref=run_ref,
        variants_path=variants_path,
        interactive=interactive,
        from_file=from_file,
    )


def run_apply_feedback(
    project_dir: Path,
    run_ref: str | None = None,
    feedback_path: Path | None = None,
    mode: str = "reviewed-copy",
    benchmark: bool = False,
) -> Path:
    return execute_apply_feedback(
        Path(project_dir),
        run_ref=run_ref,
        feedback_path=feedback_path,
        mode=mode,
        benchmark=benchmark,
    )


def run_feedback_summary(project_dir: Path, run_ref: str | None = None) -> Path:
    return execute_feedback_summary(Path(project_dir), run_ref=run_ref)


def run_export(project_dir: Path) -> Path:
    project_dir = Path(project_dir)
    config = load_project_config(project_dir)
    run_dir, manuscript, claims, citations, style_profile, ingested = _load_latest_artifacts(project_dir)
    del claims, citations, style_profile
    del ingested
    export_markdown(run_dir / "manuscript.md", manuscript)
    export_docx(run_dir / "manuscript.docx", manuscript)
    export_latex(run_dir / "manuscript.tex", manuscript)
    export_bundle(run_dir)
    write_manifest(project_dir, run_dir, "export", config)
    update_latest_copy(project_dir, run_dir)
    return run_dir


def validation_summary(result: ValidationResult) -> str:
    parts = [f"errors={len(result.errors)}", f"warnings={len(result.warnings)}", f"infos={len(result.infos)}"]
    return ", ".join(parts)
