"""Explicit protocol v1.1 facets on saved outputs; never rerun a provider."""

from __future__ import annotations

import hashlib
import json
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path

from manuscriptforge.evaluation.models import CandidatePrediction, EvaluationDataset
from manuscriptforge.evaluation.protocol import validate_dataset
from manuscriptforge.evaluation.scoring import paired_comparison, score_prediction, summarize

ANSWER_DEFINITION = (
    "Structured answer correctness preserves exact fact/source matching. For required "
    "abstention, an empty assertion set with abstained=true is correct even when known "
    "source IDs are ranked to explain conflicting evidence. Unknown or duplicate "
    "ranked IDs remain invalid. Narrative semantic correctness is not evaluated."
)


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rescore_saved_run(input_run: Path, output_root: Path) -> Path:
    input_run = Path(input_run).resolve(strict=True)
    originals = {
        name: input_run / name
        for name in ("dataset.json", "summary.json", "baseline.jsonl", "candidate.jsonl")
    }
    hashes = {name: _digest(path) for name, path in originals.items()}
    manifest_path = input_run / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    declared = {row["path"]: row for row in manifest["files"]}
    for name, path in originals.items():
        if (
            name not in declared
            or declared[name]["sha256"] != hashes[name]
            or declared[name]["bytes"] != path.stat().st_size
        ):
            raise ValueError(f"Original run manifest mismatch: {name}")
    originals["manifest.json"] = manifest_path
    hashes["manifest.json"] = _digest(manifest_path)
    sidecar = input_run / "campaign_metadata.json"
    if sidecar.is_file():
        originals["campaign_metadata.json"] = sidecar
        hashes["campaign_metadata.json"] = _digest(sidecar)
    dataset = EvaluationDataset.model_validate_json(
        originals["dataset.json"].read_text(encoding="utf-8")
    )
    validation = validate_dataset(dataset)
    original_summary = json.loads(originals["summary.json"].read_text(encoding="utf-8"))
    if original_summary["dataset"]["dataset_sha256"] != validation["dataset_sha256"]:
        raise ValueError("Saved dataset does not match its original summary fingerprint")
    selected = {
        case.case_id: case for case in dataset.cases if case.split == original_summary["split"]
    }
    facts = {fact.fact_id: fact for fact in dataset.facts}
    rescored = {}
    for name in ("baseline", "candidate"):
        rows = [
            json.loads(line)
            for line in originals[f"{name}.jsonl"].read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        ids = [row["case_id"] for row in rows]
        if len(ids) != len(set(ids)) or set(ids) != selected.keys():
            raise ValueError("Saved outputs do not cover exactly the selected partition")
        augmented = []
        for row in rows:
            case = selected[row["case_id"]]
            prediction = CandidatePrediction.model_validate(row["prediction"])
            check = score_prediction(case, prediction, facts)
            answer_correct = check["complete_case_correct"]
            if case.task_kind == "grounded_response" and case.should_abstain:
                answer_correct = bool(
                    prediction.abstained
                    and not prediction.assertions
                    and not check["invalid_ranking"]
                )
            if row.get("execution_error"):
                answer_correct = False
            augmented.append(
                row
                | {
                    "legacy_complete_case_correct": row["complete_case_correct"],
                    "answer_case_correct": answer_correct,
                    "ranking_gold_changed": False,
                }
            )
        rescored[name] = augmented
    if hashes != {name: _digest(path) for name, path in originals.items()}:
        raise ValueError("Input artifacts changed while rescoring")
    return _write_rescore(
        input_run, Path(output_root), originals, hashes, original_summary, validation, rescored
    )


def _write_rescore(input_run, output_root, originals, hashes, original_summary, validation, rows):
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    run = output_root / f"rescore-v1.1-{stamp}-{uuid.uuid4().hex[:8]}"
    (run / "original").mkdir(parents=True)
    for name, path in originals.items():
        shutil.copyfile(path, run / "original" / name)
    for name, records in rows.items():
        (run / f"{name}.jsonl").write_text(
            "".join(json.dumps(row, sort_keys=True) + "\n" for row in records), encoding="utf-8"
        )
    answer_rows = {
        name: [row | {"complete_case_correct": row["answer_case_correct"]} for row in records]
        for name, records in rows.items()
    }
    report = {
        "protocol_version": "1.1-rescore",
        "definition": ANSWER_DEFINITION,
        "input_run": str(input_run),
        "input_hashes": hashes,
        "campaign_metadata": {
            "path": "original/campaign_metadata.json"
            if "campaign_metadata.json" in hashes
            else None,
            "sha256": hashes.get("campaign_metadata.json"),
            "status": "hashed sidecar; not signed or covered by the original runner manifest",
        },
        "dataset": validation,
        "legacy": {name: original_summary[name] for name in ("baseline", "candidate", "paired")},
        "answer_facet": {name: summarize(records) for name, records in answer_rows.items()},
        "paired_answer_facet": paired_comparison(answer_rows["baseline"], answer_rows["candidate"]),
        "original_ranking_labels_preserved": True,
        "model_calls": 0,
        "scientific_validation": "NOT_EVALUATED",
        "interpretation": "Any absolute-score change reflects an explicit evaluator-definition correction, not model improvement. Compare models only within the same facet.",
        "rescore_code_sha256": _digest(Path(__file__)),
    }
    (run / "summary.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    lines = [
        "# Protocol v1.1 rescore of saved outputs",
        "",
        ANSWER_DEFINITION,
        "",
        "No provider was called. Original dataset, labels, outputs, and report are preserved under original/.",
        "",
        "| Facet | Baseline | Candidate |",
        "| --- | ---: | ---: |",
    ]
    for facet, source in (
        ("Legacy strict", report["legacy"]),
        ("Structured answer", report["answer_facet"]),
    ):
        lines.append(
            f"| {facet} | {source['baseline']['complete_case_rate']} | {source['candidate']['complete_case_rate']} |"
        )
    lines.extend(
        [
            "",
            report["interpretation"],
            "",
            "Original source-ranking metrics retain their original labels. Conflict-evidence relevance requires a separate versioned ranking dataset, not retroactive relabeling.",
        ]
    )
    (run / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    payloads = [
        {
            "path": path.relative_to(run).as_posix(),
            "bytes": path.stat().st_size,
            "sha256": _digest(path),
        }
        for path in sorted(run.rglob("*"))
        if path.is_file()
    ]
    (run / "manifest.json").write_text(
        json.dumps({"schema_version": "1.0", "files": payloads}, indent=2) + "\n", encoding="utf-8"
    )
    return run
