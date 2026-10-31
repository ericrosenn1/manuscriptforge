"""Trusted research nodes; source documents are data, never executable plans."""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path
from typing import Any
from zipfile import ZIP_DEFLATED, ZipFile

from manuscriptforge.config import load_project_config, validate_project
from manuscriptforge.evidence.claim_registry import build_claim_registry
from manuscriptforge.export.docx_exporter import export_docx
from manuscriptforge.export.latex_exporter import export_latex
from manuscriptforge.export.markdown_exporter import export_markdown
from manuscriptforge.ingest.project_ingest import ingest_project
from manuscriptforge.llm.grounded import (
    draft_grounded_section,
    mock_grounded_provider,
    request_from_context,
    validate_grounded_response,
)
from manuscriptforge.models.citation import CitationRecord
from manuscriptforge.models.claim import ScientificClaim
from manuscriptforge.models.manuscript import Manuscript, ManuscriptSection
from manuscriptforge.models.style import StyleProfile
from manuscriptforge.orchestration import WorkerContext
from manuscriptforge.orchestration.models import relative_path
from manuscriptforge.orchestration.storage import is_link
from manuscriptforge.pipeline.run_context import ManuscriptContext
from manuscriptforge.research.providers import research_provider
from manuscriptforge.research.settings import ResearchSettings
from manuscriptforge.style.profiler import build_style_profile
from manuscriptforge.utils.io import read_json, write_json, write_text


def _settings(context: WorkerContext) -> ResearchSettings:
    keys = ResearchSettings.model_fields
    return ResearchSettings.model_validate(
        {key: value for key, value in context.parameters.items() if key in keys}
    )


def _project_copy(context: WorkerContext, *, full: bool = False) -> Path:
    """Legacy helpers may write extraction caches only inside isolated scratch."""
    config = load_project_config(context.project_dir)
    cache_relative = relative_path(config.sources.metadata_cache_dir)
    cache = context.project_dir / cache_relative
    if not cache.resolve().is_relative_to(context.project_dir.resolve()):
        raise ValueError("Metadata cache must remain inside the immutable project snapshot")
    current = context.project_dir
    for component in cache_relative.split("/"):
        current = current / component
        if current.exists() and is_link(current):
            raise ValueError("Linked metadata cache paths are not allowed")
    destination = context.scratch_dir / "project"
    if full:
        shutil.copytree(context.project_dir, destination)
    else:
        destination.mkdir(parents=True)
        for name in (
            "project.yaml",
            "style_memory.json",
            "style_preferences.jsonl",
            "planning/intake",
        ):
            source = context.project_dir / name
            target = destination / name
            if source.is_file():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
            elif source.is_dir():
                shutil.copytree(source, target)
    return destination


def validate_node(context: WorkerContext) -> dict[str, Any]:
    result = validate_project(context.project_dir)
    write_json(
        context.output_path("validation.json"),
        {
            "valid": result.ok,
            "errors": result.errors,
            "warnings": result.warnings,
            "infos": result.infos,
            "scientific_validation": False,
        },
    )
    if not result.ok:
        raise ValueError("Project validation failed: " + "; ".join(result.errors))
    return {"validated_inputs": len(context.inputs), "warning_count": len(result.warnings)}


def ingest_node(context: WorkerContext) -> dict[str, Any]:
    from manuscriptforge.research.search import build_evidence_index

    project = _project_copy(context, full=True)
    data = ingest_project(project, context.work_dir, enrich_sources=False)
    write_json(context.output_path("project_config.json"), load_project_config(project))
    index_metadata = build_evidence_index(data, context.output_path("evidence_index.json"))
    return {
        "evidence_count": len(data["evidence_items"]),
        "table_count": len(data["tables"]),
        "citation_count": len(data["citations"]),
        "style_samples": len(data["style_corpus"]),
    } | index_metadata


def profile_node(context: WorkerContext) -> dict[str, Any]:
    project = _project_copy(context)
    data = read_json(context.dependency_path("ingest", "intermediate/ingested_project.json"))
    profile = build_style_profile(project, context.work_dir, samples=data["style_corpus"])
    return {
        "style_documents": len(profile.corpus_files),
        "word_count": profile.global_features.get("word_count", 0),
    }


def claims_node(context: WorkerContext) -> dict[str, Any]:
    project = _project_copy(context)
    data = read_json(context.dependency_path("ingest", "intermediate/ingested_project.json"))
    claims = build_claim_registry(project, context.work_dir, data)
    return {
        "claim_count": len(claims),
        "requires_review": sum(claim.needs_human_review for claim in claims),
    }


def _loaded_context(context: WorkerContext, project: Path, *, generate: bool = False):
    data = read_json(context.dependency_path("ingest", "intermediate/ingested_project.json"))
    claims = [
        ScientificClaim.model_validate(item)
        for item in read_json(context.dependency_path("claims", "claim_registry.json"))
    ]
    citations = [CitationRecord.model_validate(item) for item in data["citations"]]
    profile = StyleProfile.model_validate(
        read_json(context.dependency_path("profile", "style_profile.json"))
    )
    provider = research_provider(_settings(context)) if generate else mock_grounded_provider()
    return ManuscriptContext(
        project,
        context.work_dir,
        load_project_config(project),
        data,
        profile,
        claims,
        citations,
        provider,
    )


def section_node(context: WorkerContext) -> dict[str, Any]:
    settings = _settings(context)
    project = _project_copy(context)
    loaded = _loaded_context(context, project, generate=True)
    if settings.provider == "openai" and loaded.config.privacy.local_only:
        raise ValueError("The immutable project configuration forbids network generation")
    section_name = str(context.parameters["section_name"])
    if section_name in {"Abstract", "Conclusion"}:
        loaded.claims = [
            claim.model_copy(update={"section_target": section_name}) for claim in loaded.claims
        ]
    request = request_from_context(loaded, section_name, max_words=settings.max_words_per_section)
    response = draft_grounded_section(request, loaded.provider)
    validation = validate_grounded_response(request, response)
    section = ManuscriptSection(
        section_name=section_name,
        section_type=section_name.lower().replace(" ", "_"),
        content="\n\n".join(
            paragraph.text
            + "".join(
                f" [{citation}]"
                for citation in paragraph.citation_ids
                if f"[{citation}]" not in paragraph.text
            )
            for paragraph in response.paragraphs
        ),
        claim_ids=list(
            dict.fromkeys(
                value for paragraph in response.paragraphs for value in paragraph.claim_ids
            )
        ),
        citation_ids=list(
            dict.fromkeys(
                value for paragraph in response.paragraphs for value in paragraph.citation_ids
            )
        ),
        style_chunk_ids=list(
            dict.fromkeys(
                value for paragraph in response.paragraphs for value in paragraph.style_chunk_ids
            )
        ),
        unresolved_flags=response.unresolved_questions,
        revision_notes=[
            "Candidate requires human review; structural grounding checks do not establish entailment."
        ],
        style_mode=loaded.style_profile.style_mode,
    )
    write_json(context.output_path("section.json"), section)
    write_json(context.output_path("candidate.json"), response)
    write_json(context.output_path("grounding.json"), validation)
    usage = getattr(loaded.provider, "last_record", None)
    if usage:
        write_json(context.output_path("provider_usage.json"), usage)
    return {
        "section_name": section_name,
        "provider": settings.provider,
        "grounding_consistent": validation.valid,
        "requires_human_review": True,
        "word_count": len(section.content.split()),
        "claim_count": len(section.claim_ids),
    }


def assemble_node(context: WorkerContext) -> dict[str, Any]:
    settings = _settings(context)
    project = _project_copy(context)
    loaded = _loaded_context(context, project)
    section_nodes = context.parameters["section_nodes"]
    if not isinstance(section_nodes, list) or not all(
        isinstance(node, str) for node in section_nodes
    ):
        raise ValueError("Assembly requires a list of section node IDs")
    sections = [
        ManuscriptSection.model_validate(
            read_json(context.dependency_path(str(node), "section.json"))
        )
        for node in section_nodes
    ]
    abstract = next(
        (section.content for section in sections if section.section_name == "Abstract"), ""
    )
    abstract_section = next(
        (section for section in sections if section.section_name == "Abstract"), None
    )
    manuscript = Manuscript(
        title=loaded.config.project_name,
        title_candidates=[loaded.config.project_name],
        abstract=abstract,
        sections=[section for section in sections if section.section_name != "Abstract"],
        references=loaded.citations,
        tables=loaded.ingested["tables"],
        ai_disclosure=(
            f"This candidate draft was assembled using ManuscriptForge with provider {settings.provider}. "
            "All source selections, findings, and scientific interpretations require author review. "
            "The software does not establish scientific validity or verify semantic entailment."
        ),
        metadata={
            "provider": settings.provider,
            "model": settings.model if settings.provider == "openai" else None,
            "workflow": "research-manuscript-v1",
            "requires_human_review": True,
            "abstract_provenance": abstract_section.model_dump(mode="json")
            if abstract_section
            else None,
        },
    )
    write_json(context.output_path("manuscript.json"), manuscript)
    export_markdown(context.output_path("manuscript.md"), manuscript)
    export_docx(context.output_path("manuscript.docx"), manuscript)
    export_latex(context.output_path("manuscript.tex"), manuscript)
    return {
        "section_count": len(sections),
        "word_count": len((abstract + " ".join(s.content for s in manuscript.sections)).split()),
        "requires_human_review": True,
    }


def audit_node(context: WorkerContext) -> dict[str, Any]:
    from manuscriptforge.audit.reviewer_simulator import simulate_reviewer, write_reviewer_critique
    from manuscriptforge.pipeline.workflow import _audit_and_write

    project = _project_copy(context)
    loaded = _loaded_context(context, project)
    manuscript = Manuscript.model_validate(
        read_json(context.dependency_path("assemble", "manuscript.json"))
    )
    abstract_record = manuscript.metadata.get("abstract_provenance")
    if isinstance(abstract_record, dict):
        # Legacy audits consume sections. Include Abstract once in their audit
        # view while retaining its separate rendered manuscript field.
        manuscript = manuscript.model_copy(
            update={
                "abstract": "",
                "sections": [
                    ManuscriptSection.model_validate(abstract_record),
                    *manuscript.sections,
                ],
            }
        )
    write_json(
        context.output_path("run_manifest.json"),
        {
            "manifest_kind": "audit_context",
            "workflow": "research-manuscript-v1",
            "input_hashes": loaded.ingested.get("source_hashes", {}),
            "config_hash": hashlib.sha256(loaded.config.model_dump_json().encode()).hexdigest(),
            "execution_provenance": "Authoritative records are engine run.json and node provenance receipts.",
        },
    )
    findings = _audit_and_write(
        project,
        context.work_dir,
        manuscript,
        loaded.claims,
        loaded.citations,
        loaded.style_profile,
        ingested=loaded.ingested,
        provider_name=_settings(context).provider,
    )
    write_reviewer_critique(
        context.output_path("reviewer_critique.md"), simulate_reviewer(manuscript, findings)
    )
    summary = {
        "computational_status": "completed",
        "scientific_validation": "not_established",
        "requires_human_review": True,
        "finding_count": len(findings),
        "finding_severities": {
            level: sum(item.severity == level for item in findings)
            for level in sorted({item.severity for item in findings})
        },
    }
    write_json(context.output_path("qualification.json"), summary)
    return summary


def export_node(context: WorkerContext) -> dict[str, Any]:
    from manuscriptforge.research.provenance import copy_stage_provenance

    package = context.output_path("package/README.md").parent
    for source in sorted(context.project_dir.rglob("*")):
        if source.is_file():
            target = package / "project" / source.relative_to(context.project_dir)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    write_json(
        package / "research_settings.json",
        {key: value for key, value in context.parameters.items() if key != "budget_ledger_path"},
    )
    for node, artifacts in sorted(context.dependencies.items()):
        for relative, source in sorted(artifacts.items()):
            target = package / "stages" / node / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    for name in ("manuscript.md", "manuscript.docx", "manuscript.tex", "manuscript.json"):
        shutil.copy2(context.dependency_path("assemble", name), package / name)
    write_text(
        package / "README.md",
        "# Research manuscript candidate\n\n"
        "The manuscript is a reviewable candidate, not a scientifically validated result. "
        "Inspect stages/audit/qualification.json and all source/claim records before use. "
        "Stage artifacts preserve provenance. Manifest checksums cover every payload file.\n",
    )
    provenance_metadata = copy_stage_provenance(context, package)
    files: list[dict[str, Any]] = []
    for path in sorted(package.rglob("*")):
        if path.is_file():
            files.append(
                {
                    "path": path.relative_to(package).as_posix(),
                    "bytes": path.stat().st_size,
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                }
            )
    manifest = {
        "schema_version": 1,
        "artifact_type": "research_candidate_bundle",
        "files": files,
        "scientific_validation": "not_established",
        "requires_human_review": True,
    }
    write_json(package / "manifest.json", manifest)
    archive = context.output_path("research_bundle.zip")
    with ZipFile(archive, "w", ZIP_DEFLATED) as bundle:
        for path in sorted(package.rglob("*")):
            if path.is_file():
                bundle.write(path, path.relative_to(package).as_posix())
    with ZipFile(archive) as bundle:
        if bundle.testzip() is not None:
            raise ValueError("Export ZIP failed its CRC validation")
        for item in files:
            payload = bundle.read(item["path"])
            if (
                len(payload) != item["bytes"]
                or hashlib.sha256(payload).hexdigest() != item["sha256"]
            ):
                raise ValueError(f"Export integrity mismatch: {item['path']}")
    return {
        "verified_files": len(files),
        "archive_bytes": archive.stat().st_size,
        "requires_human_review": True,
    } | provenance_metadata


HANDLERS = {
    "validate": validate_node,
    "ingest": ingest_node,
    "profile": profile_node,
    "claims": claims_node,
    "section": section_node,
    "assemble": assemble_node,
    "audit": audit_node,
    "export": export_node,
}
