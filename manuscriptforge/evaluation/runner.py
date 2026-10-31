from __future__ import annotations

import hashlib
import json
import platform
import random
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from manuscriptforge.evaluation.models import (
    CandidatePrediction,
    EvaluationDataset,
    PublicTask,
    Split,
)
from manuscriptforge.evaluation.protocol import (
    dataset_hash,
    independent_source_groups,
    public_task,
    validate_dataset,
)
from manuscriptforge.evaluation.scoring import paired_comparison, score_prediction, summarize

Predictor = Callable[[PublicTask], CandidatePrediction]


def _describe(predict: Predictor) -> dict[str, Any]:
    describe = getattr(predict, "describe", None)
    return describe() if callable(describe) else {}


def evaluate_one(dataset: EvaluationDataset, case_id: str, predict: Predictor) -> dict[str, Any]:
    case = next(case for case in dataset.cases if case.case_id == case_id)
    task = public_task(dataset, case_id).model_copy(deep=True)
    started = time.perf_counter()
    failure = None
    try:
        prediction = CandidatePrediction.model_validate(predict(task))
        if prediction.case_id != case_id:
            raise ValueError("Prediction returned the wrong case ID")
    except Exception as exc:
        failure = f"{type(exc).__name__}: {exc}"
        prediction = CandidatePrediction(case_id=case_id)
    elapsed = time.perf_counter() - started
    row = score_prediction(case, prediction, {fact.fact_id: fact for fact in dataset.facts})
    row["independent_group"] = next(
        min(group) for group in independent_source_groups(dataset) if case.source_ids[0] in group
    )
    if failure:
        row["complete_case_correct"] = False
        row["abstention_correct"] = False
        row["errors"].append(failure)
    row.update(
        {
            "elapsed_seconds": elapsed,
            "execution_error": failure,
            "input_tokens": prediction.input_tokens,
            "output_tokens": prediction.output_tokens,
            "cost_usd": prediction.cost_usd,
            "prediction": prediction.model_dump(mode="json"),
        }
    )
    return row


def _write(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_run(
    output_root: Path,
    dataset: EvaluationDataset,
    report: dict[str, Any],
    baseline: list[dict[str, Any]],
    candidate: list[dict[str, Any]],
) -> Path:
    output_root = Path(output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    run = output_root / f"evaluation-{stamp}-{uuid.uuid4().hex[:8]}"
    run.mkdir()
    _write(run / "dataset.json", dataset.model_dump(mode="json"))
    report["generated_at"] = datetime.now(UTC).isoformat()
    report["evaluator_source_sha256"] = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(Path(__file__).parent.glob("*.py"))
    }
    _write(run / "summary.json", report)
    for label, rows in (("baseline", baseline), ("candidate", candidate)):
        (run / f"{label}.jsonl").write_text(
            "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8"
        )
    lines = [
        "# Held-out functional evaluation",
        "",
        f"- Dataset: {dataset.dataset_id} ({dataset.revision}); SHA-256 {dataset_hash(dataset)}",
        f"- Partition: {report['split']}; paired cases: {report['paired']['paired_cases']}",
        f"- Synthetic sources: {dataset.synthetic}",
        f"- Functional suite: {report['functional_suite_status']}",
        "- Scientific validation, narrative entailment, and author preference: NOT EVALUATED.",
        "",
        "| Metric | Baseline | Candidate |",
        "| --- | ---: | ---: |",
    ]
    for key in (
        "complete_case_rate",
        "complete_case_wilson95",
        "factual_precision_micro",
        "fact_recall_micro",
        "assertions_emitted",
        "expected_facts",
    ):
        lines.append(f"| {key} | {report['baseline'][key]} | {report['candidate'][key]} |")
    for key in (
        "reciprocal_rank",
        "recall_at_1",
        "recall_at_3",
        "citation_precision",
        "citation_recall",
        "brier_score",
        "latency_seconds",
        "cost_usd",
    ):
        lines.append(f"| {key} | {report['baseline'][key]} | {report['candidate'][key]} |")
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "Exact assertions are compared as subject/predicate/value/unit/source tuples. Numeric spelling may normalize through Decimal; units are never silently converted. Ranking uses provided relevant-source labels. Zero denominators remain null. Missing usage and cost are not zero.",
            "",
            "Wilson intervals describe the observed complete-case proportion. Paired bootstrap intervals and exact McNemar results are exploratory; small, correlated, or synthetic collections do not establish generalization. Inspect per-stratum and case outputs rather than relying on one aggregate.",
            "",
            "Only PublicTask is passed to candidate callbacks. Expected fact IDs, relevant-source labels, strata, split tags, and abstention labels are withheld. Grouped author/document-family/exact-and-normalized-hash leakage checks run before execution.",
            "",
            "Latency is local elapsed wall time around each callback. Alternated randomized call order reduces ordering bias; this is not a hardware-normalized throughput benchmark. Token/cost fields require provider and pricing provenance.",
        ]
    )
    (run / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    files = [
        {
            "path": path.name,
            "bytes": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        for path in sorted(run.iterdir())
        if path.is_file()
    ]
    _write(run / "manifest.json", {"schema_version": "1.0", "files": files})
    return run


def run_comparison(
    dataset: EvaluationDataset,
    baseline: Predictor,
    candidate: Predictor,
    output_root: Path,
    *,
    split: Split = "test",
    seed: int = 20261031,
    baseline_name: str = "baseline",
    candidate_name: str = "candidate",
) -> Path:
    """A caller supplies predictions; this runner never opens a paid connection."""
    protocol = validate_dataset(dataset)
    fingerprint = dataset_hash(dataset)
    cases = [case for case in dataset.cases if case.split == split]
    if not cases:
        raise ValueError(f"No {split} cases to evaluate")
    rng = random.Random(seed)
    rng.shuffle(cases)
    before: list[dict[str, Any]] = []
    after: list[dict[str, Any]] = []
    call_order = []
    for case in cases:
        providers = [("baseline", baseline, before), ("candidate", candidate, after)]
        rng.shuffle(providers)
        for name, predict, target in providers:
            target.append(evaluate_one(dataset, case.case_id, predict))
            call_order.append({"case_id": case.case_id, "candidate": name})
    if dataset_hash(dataset) != fingerprint:
        raise ValueError("Dataset changed during evaluation")
    before.sort(key=lambda row: row["case_id"])
    after.sort(key=lambda row: row["case_id"])
    report = {
        "schema_version": "1.0",
        "dataset": protocol,
        "split": split,
        "seed": seed,
        "baseline_name": baseline_name,
        "candidate_name": candidate_name,
        "predictor_metadata": {
            "baseline": _describe(baseline),
            "candidate": _describe(candidate),
        },
        "baseline": summarize(before),
        "candidate": summarize(after),
        "paired": paired_comparison(before, after, seed=seed),
        "strata": {
            stratum: {
                "baseline": summarize([r for r in before if r["stratum"] == stratum]),
                "candidate": summarize([r for r in after if r["stratum"] == stratum]),
            }
            for stratum in sorted({case.stratum for case in cases})
        },
        "scientific_validation": "NOT_EVALUATED",
        "qualification_definition": "All declared structured functional cases must pass; no case may regress. Narrative semantics and author preference require separate assessment.",
        "functional_suite_status": "PASS"
        if all(row["complete_case_correct"] for row in after)
        else "FAIL",
        "call_order": call_order,
        "python": platform.python_version(),
    }
    return write_run(output_root, dataset, report, before, after)
