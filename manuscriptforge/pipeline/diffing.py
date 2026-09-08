from __future__ import annotations

import difflib
from pathlib import Path
from typing import Any

from manuscriptforge.utils.dates import run_timestamp
from manuscriptforge.utils.io import ensure_dir, read_json, write_text


def _runs_dir(project_dir: Path) -> Path:
    return project_dir / "outputs" / "runs"


def list_run_dirs(project_dir: Path) -> list[Path]:
    runs = _runs_dir(project_dir)
    if not runs.exists():
        return []
    return sorted([path for path in runs.iterdir() if path.is_dir()])


def resolve_run(project_dir: Path, value: str | None) -> Path:
    if value:
        path = Path(value)
        if path.exists():
            return path
        candidate = _runs_dir(project_dir) / value
        if candidate.exists():
            return candidate
        raise FileNotFoundError(f"Could not resolve run '{value}' as a path or run ID.")
    raise ValueError("No run value supplied")


def resolve_default_runs(project_dir: Path) -> tuple[Path, Path]:
    runs = [run for run in list_run_dirs(project_dir) if (run / "manuscript.md").exists()]
    if len(runs) < 2:
        raise FileNotFoundError("Need at least two manuscript runs to diff. Run `draft` twice or pass --run-a/--run-b.")
    return runs[-2], runs[-1]


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _read_json_list(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    loaded = read_json(path)
    return loaded if isinstance(loaded, list) else []


def _by_id(rows: list[dict[str, Any]], key: str) -> dict[str, dict[str, Any]]:
    return {str(row.get(key)): row for row in rows if row.get(key)}


def _claim_diff(run_a: Path, run_b: Path) -> list[str]:
    a = _by_id(_read_json_list(run_a / "claim_registry.json"), "claim_id")
    b = _by_id(_read_json_list(run_b / "claim_registry.json"), "claim_id")
    lines = ["## Claim Registry Changes", ""]
    added = sorted(set(b) - set(a))
    removed = sorted(set(a) - set(b))
    changed = []
    for claim_id in sorted(set(a) & set(b)):
        old = a[claim_id]
        new = b[claim_id]
        changes = []
        for field in ["support_strength", "needs_human_review", "citation_ids"]:
            if old.get(field) != new.get(field):
                changes.append(f"{field}: {old.get(field)} -> {new.get(field)}")
        old_evidence = [item.get("evidence_id") for item in old.get("evidence_items", [])]
        new_evidence = [item.get("evidence_id") for item in new.get("evidence_items", [])]
        if old_evidence != new_evidence:
            changes.append(f"evidence_ids: {old_evidence} -> {new_evidence}")
        if changes:
            changed.append(f"- Changed `{claim_id}`: " + "; ".join(changes))
    lines.append(f"- Added claims: {len(added)}")
    lines.extend(f"  - `{claim_id}`" for claim_id in added)
    lines.append(f"- Removed claims: {len(removed)}")
    lines.extend(f"  - `{claim_id}`" for claim_id in removed)
    lines.append(f"- Changed claims: {len(changed)}")
    lines.extend(changed)
    return lines + [""]


def _citation_diff(run_a: Path, run_b: Path) -> list[str]:
    a = _by_id(_read_json_list(run_a / "citation_registry.json"), "citation_id")
    b = _by_id(_read_json_list(run_b / "citation_registry.json"), "citation_id")
    lines = ["## Citation Registry Changes", ""]
    added = sorted(set(b) - set(a))
    removed = sorted(set(a) - set(b))
    status_changed = [
        citation_id
        for citation_id in sorted(set(a) & set(b))
        if a[citation_id].get("metadata_status") != b[citation_id].get("metadata_status")
    ]
    lines.append(f"- Added citations: {len(added)}")
    lines.extend(f"  - `{citation_id}`" for citation_id in added)
    lines.append(f"- Removed citations: {len(removed)}")
    lines.extend(f"  - `{citation_id}`" for citation_id in removed)
    lines.append(f"- Metadata status changes: {len(status_changed)}")
    lines.extend(f"  - `{citation_id}`" for citation_id in status_changed)
    return lines + [""]


def _audit_diff(run_a: Path, run_b: Path) -> list[str]:
    a = _by_id(_read_json_list(run_a / "audit_findings.json"), "finding_id")
    b = _by_id(_read_json_list(run_b / "audit_findings.json"), "finding_id")
    if not a:
        a = _by_id(_read_json_list(run_a / "intermediate" / "audit_findings.json"), "finding_id")
    if not b:
        b = _by_id(_read_json_list(run_b / "intermediate" / "audit_findings.json"), "finding_id")
    new_serious = [fid for fid in sorted(set(b) - set(a)) if b[fid].get("severity") == "serious"]
    resolved_serious = [fid for fid in sorted(set(a) - set(b)) if a[fid].get("severity") == "serious"]
    a_warnings = sum(1 for item in a.values() if item.get("severity") == "warning")
    b_warnings = sum(1 for item in b.values() if item.get("severity") == "warning")
    return [
        "## Audit Finding Changes",
        "",
        f"- New serious findings: {len(new_serious)}",
        *[f"  - `{fid}`" for fid in new_serious],
        f"- Resolved serious findings: {len(resolved_serious)}",
        *[f"  - `{fid}`" for fid in resolved_serious],
        f"- Warning count: {a_warnings} -> {b_warnings}",
        "",
    ]


def _manifest_diff(run_a: Path, run_b: Path) -> list[str]:
    a_manifest = read_json(run_a / "run_manifest.json") if (run_a / "run_manifest.json").exists() else {}
    b_manifest = read_json(run_b / "run_manifest.json") if (run_b / "run_manifest.json").exists() else {}
    return [
        "## Manifest/Config Changes",
        "",
        f"- Config hash: {a_manifest.get('config_hash')} -> {b_manifest.get('config_hash')}",
        f"- Files: {len(a_manifest.get('files', []))} -> {len(b_manifest.get('files', []))}",
        "",
    ]


def run_diff(project_dir: Path, run_a: str | None = None, run_b: str | None = None) -> Path:
    project_dir = Path(project_dir)
    if run_a or run_b:
        if not (run_a and run_b):
            raise ValueError("Provide both --run-a and --run-b, or neither.")
        a_dir = resolve_run(project_dir, run_a)
        b_dir = resolve_run(project_dir, run_b)
    else:
        a_dir, b_dir = resolve_default_runs(project_dir)
    out_dir = ensure_dir(project_dir / "outputs" / "diffs" / run_timestamp())
    a_text = _read_text(a_dir / "manuscript.md").splitlines()
    b_text = _read_text(b_dir / "manuscript.md").splitlines()
    unified = list(
        difflib.unified_diff(
            a_text,
            b_text,
            fromfile=str(a_dir / "manuscript.md"),
            tofile=str(b_dir / "manuscript.md"),
            lineterm="",
        )
    )
    lines = [
        "# ManuscriptForge Run Diff",
        "",
        f"- Run A: {a_dir}",
        f"- Run B: {b_dir}",
        "",
        "## Manuscript Text Diff",
        "",
        "```diff",
        *(unified or ["# No manuscript.md text changes detected."]),
        "```",
        "",
    ]
    lines.extend(_claim_diff(a_dir, b_dir))
    lines.extend(_citation_diff(a_dir, b_dir))
    lines.extend(_audit_diff(a_dir, b_dir))
    lines.extend(_manifest_diff(a_dir, b_dir))
    style_a = read_json(a_dir / "style_profile.json") if (a_dir / "style_profile.json").exists() else {}
    style_b = read_json(b_dir / "style_profile.json") if (b_dir / "style_profile.json").exists() else {}
    lines.extend(
        [
            "## Style Profile Changes",
            "",
            f"- Corpus files: {len(style_a.get('corpus_files', []))} -> {len(style_b.get('corpus_files', []))}",
            "",
        ]
    )
    write_text(out_dir / "diff_report.md", "\n".join(lines).strip() + "\n")
    return out_dir
