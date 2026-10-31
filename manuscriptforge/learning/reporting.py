from __future__ import annotations

import hashlib
import json
import math
import random
import statistics
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from manuscriptforge.evaluation.scoring import wilson_interval
from manuscriptforge.learning.models import PairDataset, StylePair, StyleRanker


def evaluate_pairs(
    model: StyleRanker, pairs: list[StylePair], group_ids: dict[str, str], seed: int
) -> dict:
    from manuscriptforge.learning.ranker import probability_left

    rows: list[dict[str, Any]] = []
    for pair in pairs:
        p = probability_left(model, pair.left_text, pair.right_text)
        predicted = None if abs(p - 0.5) < 1e-12 else "left" if p > 0.5 else "right"
        rows.append(
            {
                "pair_id": pair.pair_id,
                "group": group_ids[pair.pair_id],
                "probability_left": p,
                "confidence": max(p, 1 - p),
                "label_left": pair.preferred == "left",
                "correct": predicted == pair.preferred,
                "abstained": predicted is None,
            }
        )
    if not rows:
        return {"pairs": 0, "accuracy": None, "brier": None, "log_loss": None}
    groups: dict[str, list[int]] = {}
    for row in rows:
        groups.setdefault(row["group"], []).append(int(row["correct"]))
    group_values = list(groups.values())
    rng = random.Random(seed)
    samples = sorted(
        statistics.mean(
            value for group in rng.choices(group_values, k=len(group_values)) for value in group
        )
        for _ in range(1000)
    )
    bins: list[dict[str, Any]] = []
    for index in range(5):
        chosen = [row for row in rows if min(4, int(row["confidence"] * 5)) == index]
        bins.append(
            {
                "lower": index / 5,
                "upper": (index + 1) / 5,
                "count": len(chosen),
                "mean_confidence": statistics.mean(row["confidence"] for row in chosen)
                if chosen
                else None,
                "accuracy": statistics.mean(row["correct"] for row in chosen) if chosen else None,
            }
        )
    correct = sum(row["correct"] for row in rows)
    return {
        "pairs": len(rows),
        "independent_groups": len(groups),
        "correct": correct,
        "accuracy": correct / len(rows),
        "accuracy_wilson95_iid_approximation": wilson_interval(correct, len(rows)),
        "accuracy_group_bootstrap95": [samples[25], samples[975]],
        "coverage": sum(not row["abstained"] for row in rows) / len(rows),
        "brier": statistics.mean(
            (row["probability_left"] - row["label_left"]) ** 2 for row in rows
        ),
        "log_loss": statistics.mean(
            -math.log(
                max(
                    1e-15,
                    row["probability_left"] if row["label_left"] else 1 - row["probability_left"],
                )
            )
            for row in rows
        ),
        "calibration_bins": bins,
        "rows": rows,
    }


def write_training_run(
    output_root: Path, dataset: PairDataset, model: StyleRanker, report: dict
) -> Path:
    from manuscriptforge.learning.ranker import save_model

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    run = Path(output_root) / f"style-training-{stamp}-{uuid.uuid4().hex[:8]}"
    run.mkdir(parents=True)
    save_model(model, run / "model.json")
    (run / "dataset.json").write_text(dataset.model_dump_json(indent=2) + "\n", encoding="utf-8")
    (run / "report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    metrics = report["metrics"]["test"]
    lines = [
        "# Style preference ranker model card",
        "",
        "This is an L2-regularized logistic ranker trained from explicit pairwise preference labels. It ranks existing text; it does not generate or rewrite prose.",
        "",
        f"- Dataset: {dataset.dataset_id}",
        f"- Synthetic training: {model.synthetic_training}",
        f"- Holdout scope: {report['holdout_scope']}",
        f"- Held-out pairs: {metrics['pairs']}; independent groups: {metrics.get('independent_groups')}",
        f"- Held-out accuracy: {metrics['accuracy']}; group bootstrap95: {metrics.get('accuracy_group_bootstrap95')}",
        f"- Held-out Brier score: {metrics['brier']}; log loss: {metrics['log_loss']}",
        f"- Training-majority baseline test accuracy: {report['constant_train_majority_test_accuracy']}",
        f"- Early stopping iteration: {model.best_iteration}",
        "- No test labels are used for gradient updates, scaling, or early stopping.",
        "- Scientific validity and real-author efficacy are NOT ESTABLISHED.",
        "",
        "## Intended use and limitations",
        "",
        "Use only as an interpretable preference signal for the labeled population. Synthetic mechanism checks demonstrate fitting behavior, not a particular author's preferences. English-oriented token and passive-pattern heuristics can misread text. Length-dependent features and corpus shift can alter scores. Inspect per-group uncertainty and calibration before considering any practical use.",
        "",
        "The model uses no topic embeddings and has zero pairwise intercept, so swapping left and right complements the preference probability. Human labels are required for a real corpus; missing labels are never inferred. Document families, source hashes, and normalized texts are kept in one split; author holdout is optional and is reported explicitly.",
        "",
        "## Learned coefficients",
        "",
        "| Feature | Coefficient | Train-only scale |",
        "| --- | ---: | ---: |",
    ]
    lines.extend(
        f"| {name} | {weight:.6g} | {scale:.6g} |"
        for name, weight, scale in zip(
            model.feature_names, model.coefficients, model.scales, strict=True
        )
    )
    (run / "MODEL_CARD.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    files = [
        {
            "path": p.name,
            "bytes": p.stat().st_size,
            "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
        }
        for p in sorted(run.iterdir())
        if p.is_file()
    ]
    (run / "manifest.json").write_text(
        json.dumps({"schema_version": "1.0", "files": files}, indent=2) + "\n", encoding="utf-8"
    )
    return run
