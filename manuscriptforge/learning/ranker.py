from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

import numpy as np

from manuscriptforge.learning.features import FEATURE_NAMES, feature_vector
from manuscriptforge.learning.models import LearningConfig, PairDataset, StylePair, StyleRanker
from manuscriptforge.learning.splits import split_pairs


def _sigmoid(values):
    return 1.0 / (1.0 + np.exp(-np.clip(values, -50, 50)))


def _matrix(pairs: list[StylePair]) -> tuple[np.ndarray, np.ndarray]:
    return np.asarray(
        [feature_vector(pair.left_text) - feature_vector(pair.right_text) for pair in pairs]
    ), np.asarray([pair.preferred == "left" for pair in pairs], dtype=float)


def _loss(x: np.ndarray, y: np.ndarray, weights: np.ndarray, l2: float = 0.0) -> float:
    values = x @ weights
    return float(
        np.mean(np.logaddexp(0.0, values) - y * values) + 0.5 * l2 * np.dot(weights, weights)
    )


def probability_left(model: StyleRanker, left: str, right: str) -> float:
    if model.feature_names != FEATURE_NAMES:
        raise ValueError("Unknown feature order or feature version")
    difference = (feature_vector(left) - feature_vector(right)) / np.asarray(model.scales)
    return float(_sigmoid(difference @ np.asarray(model.coefficients)))


def rank_texts(model: StyleRanker, texts: list[str]) -> list[dict[str, Any]]:
    if model.feature_names != FEATURE_NAMES:
        raise ValueError("Unknown feature order or feature version")
    weights = np.asarray(model.coefficients) / np.asarray(model.scales)
    result = [
        {"index": index, "text": text, "score": float(feature_vector(text) @ weights)}
        for index, text in enumerate(texts)
    ]
    return sorted(result, key=lambda item: (-item["score"], item["index"]))


def fit_ranker(
    dataset: PairDataset, config: LearningConfig | None = None
) -> tuple[StyleRanker, dict[str, Any]]:
    config = config or LearningConfig()
    started = time.perf_counter()
    splits, group_ids = split_pairs(dataset, config)
    matrices = {name: _matrix(pairs) for name, pairs in splits.items()}
    raw_train, train_y = matrices["train"]
    scales = np.sqrt(np.mean(raw_train * raw_train, axis=0))
    scales[scales < 1e-8] = 1.0
    train_x = raw_train / scales
    dev_x, dev_y = matrices["dev"][0] / scales, matrices["dev"][1]
    weights = np.zeros(len(FEATURE_NAMES))
    best_weights = weights.copy()
    best_loss = _loss(dev_x, dev_y, weights)
    initial_loss = best_loss
    best_iteration = 0
    stale = 0
    history = []
    stop_reason = "iteration_limit"
    for iteration in range(1, config.max_iterations + 1):
        gradient = (
            train_x.T @ (_sigmoid(train_x @ weights) - train_y) / len(train_y) + config.l2 * weights
        )
        before = _loss(train_x, train_y, weights, config.l2)
        step = config.learning_rate
        for _ in range(30):
            candidate = weights - step * gradient
            if _loss(train_x, train_y, candidate, config.l2) <= before:
                break
            step *= 0.5
        weights = candidate
        dev_loss = _loss(dev_x, dev_y, weights)
        history.append(
            {
                "iteration": iteration,
                "train_objective": _loss(train_x, train_y, weights, config.l2),
                "dev_log_loss": dev_loss,
            }
        )
        if dev_loss < best_loss - config.min_delta:
            best_loss, best_weights, best_iteration, stale = dev_loss, weights.copy(), iteration, 0
        else:
            stale += 1
        if stale >= config.patience or float(np.linalg.norm(gradient)) < 1e-8:
            stop_reason = "dev_early_stop" if stale >= config.patience else "gradient_tolerance"
            break
    return _finish_fit(
        dataset,
        config,
        splits,
        group_ids,
        scales,
        best_weights,
        best_iteration,
        initial_loss,
        history,
        stop_reason,
        started,
    )


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _finish_fit(
    dataset,
    config,
    splits,
    group_ids,
    scales,
    weights,
    best_iteration,
    initial_loss,
    history,
    stop_reason,
    started,
):
    from manuscriptforge.learning.reporting import evaluate_pairs

    model = StyleRanker(
        feature_names=FEATURE_NAMES,
        coefficients=tuple(weights.tolist()),
        scales=tuple(scales.tolist()),
        training_pair_ids=tuple(pair.pair_id for pair in splits["train"]),
        training_dataset_sha256=_digest([pair.model_dump(mode="json") for pair in splits["train"]]),
        config=config,
        best_iteration=best_iteration,
        synthetic_training=all(pair.synthetic for pair in splits["train"]),
    )
    train_authors = {pair.author_id for pair in splits["train"]}
    dev_authors = {pair.author_id for pair in splits["dev"]}
    test_authors = {pair.author_id for pair in splits["test"]}
    majority = sum(pair.preferred == "left" for pair in splits["train"]) >= len(splits["train"]) / 2
    report = {
        "schema_version": "1.0",
        "dataset_id": dataset.dataset_id,
        "dataset_sha256": _digest(dataset.model_dump(mode="json")),
        "training_dataset_sha256": model.training_dataset_sha256,
        "method": "L2 logistic preference ranker on train-scaled stylometric differences; zero intercept",
        "numpy_version": np.__version__,
        "scientific_validation": "NOT_EVALUATED",
        "real_author_efficacy": "NOT_ESTABLISHED",
        "synthetic_data_only": all(pair.synthetic for pair in dataset.pairs),
        "author_holdout_requested": config.group_by_author,
        "author_overlap_train_test": sorted(train_authors & test_authors),
        "author_overlap_dev_test": sorted(dev_authors & test_authors),
        "author_overlap_train_dev": sorted(train_authors & dev_authors),
        "holdout_scope": "author and document groups"
        if config.group_by_author
        else "within-author document groups",
        "partitions": {name: [pair.pair_id for pair in pairs] for name, pairs in splits.items()},
        "independent_groups": {
            name: len({group_ids[pair.pair_id] for pair in pairs}) for name, pairs in splits.items()
        },
        "metrics": {
            name: evaluate_pairs(model, pairs, group_ids, config.seed)
            for name, pairs in splits.items()
        },
        "constant_train_majority_test_accuracy": sum(
            (pair.preferred == "left") == majority for pair in splits["test"]
        )
        / len(splits["test"]),
        "initial_dev_log_loss": initial_loss,
        "best_iteration": best_iteration,
        "iterations": len(history),
        "stop_reason": stop_reason,
        "optimization_history": history,
        "elapsed_seconds": time.perf_counter() - started,
        "config": config.model_dump(mode="json"),
        "test_labels_used_for_fit": False,
        "intercept": 0.0,
        "implementation_sha256": {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(Path(__file__).parent.glob("*.py"))
        },
    }
    return model, report


def save_model(model: StyleRanker, path: Path) -> None:
    path = Path(path)
    if path.exists():
        raise FileExistsError(f"Model already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = model.model_dump(mode="json")
    path.write_text(
        json.dumps({"model": payload, "sha256": _digest(payload)}, indent=2) + "\n",
        encoding="utf-8",
    )


def load_model(path: Path) -> StyleRanker:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if (
        not isinstance(raw, dict)
        or set(raw) != {"model", "sha256"}
        or _digest(raw["model"]) != raw["sha256"]
    ):
        raise ValueError("Model serialization checksum mismatch")
    model = StyleRanker.model_validate(raw["model"])
    if model.feature_names != FEATURE_NAMES:
        raise ValueError("Unknown feature names or ordering")
    return model
