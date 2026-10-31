"""Probe a supplied source tree or compare two immutable synthetic probe reports."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory


def probe(source_root: Path, output: Path, revision: str | None) -> None:
    source_root = source_root.resolve(strict=True)
    if output.exists():
        raise FileExistsError(f"Refusing to replace an existing report: {output}")
    sys.path.insert(0, str(source_root))
    import manuscriptforge
    from manuscriptforge.audit.citation_audit import audit_citations
    from manuscriptforge.audit.no_invention_audit import audit_no_invention, build_source_map
    from manuscriptforge.evidence.citation_mapper import map_citations_to_claims
    from manuscriptforge.evidence.claim_extractor import _row_claim_text, _threshold_for_column
    from manuscriptforge.models.citation import CitationRecord
    from manuscriptforge.models.claim import ScientificClaim
    from manuscriptforge.models.manuscript import Manuscript, ManuscriptSection
    from manuscriptforge.models.style import StyleProfile
    from manuscriptforge.style.benchmark import (
        DEFAULT_WEIGHTS,
        _category_scores,
        _global_metrics,
        _overall_score,
        _score_style_eval,
        _section_metric,
        _traceability_metrics,
    )

    if not Path(manuscriptforge.__file__).resolve().is_relative_to(source_root):
        raise RuntimeError("Probe imported a different source tree")
    results = []

    def add(name, value):
        results.append({"case": name, "observed": value})

    profile = StyleProfile(section_features={"methods": {"chunk_count": 1}})
    add(
        "no_numeric_style_features",
        _section_metric("methods", "A generic methods paragraph.", profile).model_dump(mode="json"),
    )
    categories = _category_scores(_global_metrics("", {}, StyleProfile(), {}, {}), [])
    add(
        "empty_manuscript_score",
        {"categories": categories, "overall": _overall_score(categories, DEFAULT_WEIGHTS)},
    )
    with TemporaryDirectory(prefix="manuscriptforge-eval-probe-") as temporary:
        root = Path(temporary)
        run = root / "run"
        run.mkdir()
        fixtures = {
            "style_eval_pairs.jsonl": {
                "example_id": "pair",
                "section_type": "methods",
                "positive": "Alpha beta",
                "negative": "Gamma delta",
            },
            "style_eval_rewrite_prompts.jsonl": {
                "example_id": "rewrite",
                "section_type": "methods",
                "target_user_paragraph": "Alpha beta",
                "neutral_paragraph": "Gamma delta",
            },
            "style_eval_rank_questions.jsonl": {
                "example_id": "classification",
                "task": "section_classification",
                "section_type": "methods",
                "text": "Alpha beta",
            },
        }
        for name, value in fixtures.items():
            (run / name).write_text(json.dumps(value) + "\n", encoding="utf-8")
        add(
            "empty_eval_output",
            [item.model_dump(mode="json") for item in _score_style_eval(root, run, {}, "")],
        )
        add(
            "wrong_nonempty_section",
            [
                item.model_dump(mode="json")
                for item in _score_style_eval(
                    root, run, {"methods": "Unrelated unrelated."}, "Unrelated unrelated."
                )
            ],
        )
    claim = ScientificClaim(
        claim_id="clm_demo",
        claim_text="The device was calibrated.",
        normalized_claim="the device was calibrated",
        section_target="Introduction",
        claim_type="background",
        needs_citation=True,
    )
    citation = CitationRecord(
        citation_id="cit_unrelated", title="The survey was anonymous", year="2024"
    )
    add(
        "stopword_citation_match",
        map_citations_to_claims([claim], [citation])[0].model_dump(mode="json"),
    )

    def manuscript(text, ids=None):
        return Manuscript(
            title="Synthetic adversarial probe",
            sections=[
                ManuscriptSection(
                    section_name="Results",
                    section_type="results",
                    content=text,
                    claim_ids=ids or [],
                )
            ],
        )

    for name, original, changed in [
        (
            "swapped_numbers",
            "Group A had 12 records and group B had 24 records.",
            "Group A had 24 records and group B had 12 records.",
        ),
        (
            "percent_unit_changed",
            "The measured proportion was 5%.",
            "The measured proportion was 5.",
        ),
        (
            "invented_year",
            "The procedure is described here.",
            "The procedure was validated in 2020.",
        ),
    ]:
        findings, _ = audit_no_invention(
            manuscript(changed), [], [], {"text_inputs": {"demo": {"text": original}}}
        )
        add(name, [item.model_dump(mode="json") for item in findings])
    add(
        "unregistered_numeric_citation",
        [
            item.model_dump(mode="json")
            for item in audit_citations(manuscript("A fact is attributed here [99]."), [], [])
        ],
    )
    add(
        "invalid_ids_traceability_score",
        [
            item.model_dump(mode="json")
            for item in _traceability_metrics(
                build_source_map(manuscript("An ungrounded invented claim.", ["clm_fake"]), "mock")
            )
        ],
    )
    add(
        "equal_group_means",
        _row_claim_text(
            "demo",
            {"response_mean": 5, "nonresponse_mean": 5},
            ["response_mean", "nonresponse_mean"],
        ),
    )
    add("zero_configured_threshold", _threshold_for_column("p_value", {"p_value": 0.0}))
    write_probe(source_root, output, revision, results)


def write_probe(root: Path, output: Path, revision: str | None, results: list) -> None:
    if revision is None:
        process = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=False
        )
        revision = (
            process.stdout.strip() if process.returncode == 0 else "unversioned-source-snapshot"
        )
    paths = [
        "style/benchmark.py",
        "style/evaluation.py",
        "evidence/citation_mapper.py",
        "evidence/claim_extractor.py",
        "audit/no_invention_audit.py",
        "audit/citation_audit.py",
    ]
    report = {
        "schema_version": "1.0",
        "revision_label": revision,
        "source_hashes": {
            name: hashlib.sha256((root / "manuscriptforge" / name).read_bytes()).hexdigest()
            for name in paths
        },
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "scope": "Original synthetic functional invariants; no LLM calls or measured scientific results.",
        "provenance_note": "A revision label alone does not prove an unchanged checkout. Source hashes identify the actual files used.",
        "results": results,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), "cases": len(results), "revision": revision}))


def compare(baseline: Path, candidate: Path, output: Path) -> None:
    if output.exists():
        raise FileExistsError(f"Refusing to replace an existing comparison: {output}")
    old = json.loads(baseline.read_text(encoding="utf-8"))
    new = json.loads(candidate.read_text(encoding="utf-8"))
    before = {row["case"]: row["observed"] for row in old["results"]}
    after = {row["case"]: row["observed"] for row in new["results"]}
    if before.keys() != after.keys():
        raise ValueError("Probe case sets differ; they cannot be paired")
    checks = {
        "no_numeric_style_features": lambda value: value["score"] is None,
        "empty_manuscript_score": lambda value: value["overall"] is None,
        "empty_eval_output": lambda value: all(not row["passed"] for row in value),
        "wrong_nonempty_section": lambda value: all(not row["passed"] for row in value),
        "stopword_citation_match": lambda value: (
            not value["citation_ids"] and value["needs_citation"]
        ),
        "swapped_numbers": bool,
        "percent_unit_changed": bool,
        "invented_year": bool,
        "unregistered_numeric_citation": bool,
        "equal_group_means": lambda value: "equal to" in value,
        "zero_configured_threshold": lambda value: value == 0.0,
    }
    rows = [
        {
            "case": name,
            "baseline_pass": check(before[name]),
            "candidate_pass": check(after[name]),
            "baseline": before[name],
            "candidate": after[name],
        }
        for name, check in checks.items()
    ]
    report = {
        "schema_version": "1.0",
        "baseline_report_sha256": hashlib.sha256(baseline.read_bytes()).hexdigest(),
        "candidate_report_sha256": hashlib.sha256(candidate.read_bytes()).hexdigest(),
        "baseline_revision": old.get("revision_label"),
        "candidate_revision": new.get("revision_label"),
        "cases": rows,
        "baseline_passes": sum(row["baseline_pass"] for row in rows),
        "candidate_passes": sum(row["candidate_pass"] for row in rows),
        "invariant_cases": len(rows),
        "separate_limitation": {
            "case": "invalid_ids_traceability_score",
            "baseline": before["invalid_ids_traceability_score"],
            "candidate": after["invalid_ids_traceability_score"],
            "interpretation": "Field-presence completeness remains a diagnostic; it is not referential integrity or semantic validation.",
        },
        "scientific_validation": "NOT_EVALUATED",
        "llm_performance": "NOT_EVALUATED",
        "limitations": "Eleven targeted regression invariants, not a random estimate of defect prevalence or general language-model reliability.",
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "output": str(output),
                "baseline_passes": report["baseline_passes"],
                "candidate_passes": report["candidate_passes"],
                "cases": len(rows),
            }
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("probe")
    run.add_argument("--source-root", type=Path, required=True)
    run.add_argument("--output", type=Path, required=True)
    run.add_argument("--revision")
    diff = commands.add_parser("compare")
    diff.add_argument("--baseline", type=Path, required=True)
    diff.add_argument("--candidate", type=Path, required=True)
    diff.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "probe":
        probe(args.source_root, args.output, args.revision)
    else:
        compare(args.baseline, args.candidate, args.output)


if __name__ == "__main__":
    main()
