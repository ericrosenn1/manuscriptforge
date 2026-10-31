from __future__ import annotations

import hashlib
import json

import numpy as np
import pytest

from manuscriptforge.learning.features import FEATURE_NAMES
from manuscriptforge.learning.models import LearningConfig, PairDataset
from manuscriptforge.learning.ranker import (
    fit_ranker,
    load_model,
    probability_left,
    rank_texts,
    save_model,
)
from manuscriptforge.learning.reporting import write_training_run
from manuscriptforge.learning.splits import split_pairs
from manuscriptforge.learning.synthetic import synthetic_preferences


def test_logistic_model_really_learns_heldout_synthetic_preferences():
    dataset = synthetic_preferences()
    model, report = fit_ranker(dataset)
    assert np.linalg.norm(model.coefficients) > 0
    assert model.feature_names == FEATURE_NAMES
    assert report["metrics"]["test"]["accuracy"] >= 0.8
    assert report["metrics"]["test"]["accuracy"] > report["constant_train_majority_test_accuracy"]
    assert report["metrics"]["test"]["brier"] < 0.25
    assert (
        report["optimization_history"][model.best_iteration - 1]["dev_log_loss"]
        < report["initial_dev_log_loss"]
    )
    assert report["holdout_scope"] == "within-author document groups"
    assert report["real_author_efficacy"] == "NOT_ESTABLISHED"
    assert report["test_labels_used_for_fit"] is False


def test_swapping_order_complements_probability_and_text_is_unchanged():
    dataset = synthetic_preferences(groups=10)
    model, _ = fit_ranker(dataset)
    pair = dataset.pairs[0]
    left = probability_left(model, pair.left_text, pair.right_text)
    right = probability_left(model, pair.right_text, pair.left_text)
    assert left + right == pytest.approx(1)
    values = [pair.left_text, pair.right_text, "An unchanged third option."]
    result = rank_texts(model, values)
    assert {row["index"]: row["text"] for row in result} == dict(enumerate(values))
    assert probability_left(model, pair.left_text, pair.left_text) == 0.5


def test_test_labels_do_not_change_training_or_early_stopping():
    dataset = synthetic_preferences(groups=10)
    model, _ = fit_ranker(dataset)
    partitions, _ = split_pairs(dataset, LearningConfig())
    test_ids = {pair.pair_id for pair in partitions["test"]}
    changed = dataset.model_copy(deep=True)
    for pair in changed.pairs:
        if pair.pair_id in test_ids:
            pair.preferred = "right" if pair.preferred == "left" else "left"
    other, _ = fit_ranker(changed)
    assert model.coefficients == other.coefficients
    assert model.scales == other.scales
    assert model.best_iteration == other.best_iteration
    assert model.training_dataset_sha256 == other.training_dataset_sha256


def test_test_features_do_not_fit_scaling_or_coefficients():
    dataset = synthetic_preferences(groups=10)
    model, _ = fit_ranker(dataset)
    partitions, _ = split_pairs(dataset, LearningConfig())
    chosen = partitions["test"][0].pair_id
    changed = dataset.model_copy(deep=True)
    pair = next(pair for pair in changed.pairs if pair.pair_id == chosen)
    pair.left_text += " A uniquely extended held-out sentence." * 100
    pair.source_hashes = [
        hashlib.sha256(text.encode()).hexdigest() for text in (pair.left_text, pair.right_text)
    ]
    other, _ = fit_ranker(changed)
    assert model.scales == other.scales
    assert model.coefficients == other.coefficients


def test_grouped_splits_prevent_duplicate_and_author_leakage():
    dataset = synthetic_preferences(groups=10)
    duplicate = dataset.pairs[0].model_copy(
        update={"pair_id": "copy", "group_id": "other", "document_family": "other"}
    )
    with pytest.raises(ValueError, match="Duplicate preference pair"):
        split_pairs(
            dataset.model_copy(update={"pairs": [*dataset.pairs, duplicate]}), LearningConfig()
        )
    with pytest.raises(ValueError, match="At least three"):
        split_pairs(dataset, LearningConfig(group_by_author=True))
    partitions, _ = split_pairs(
        synthetic_preferences(groups=15, authors=5), LearningConfig(group_by_author=True)
    )
    authors = {name: {pair.author_id for pair in rows} for name, rows in partitions.items()}
    assert not authors["train"] & authors["test"]
    assert not authors["dev"] & authors["test"]


def test_missing_labels_are_rejected():
    raw = synthetic_preferences(groups=5).model_dump()
    del raw["pairs"][0]["preferred"]
    with pytest.raises(ValueError):
        PairDataset.model_validate(raw)


def test_normalized_shared_passage_stays_in_one_partition():
    dataset = synthetic_preferences(groups=10)
    first = dataset.pairs[0]
    left, right = (
        first.left_text.upper() + "  ",
        "A distinct comparison passage for the explicit label.",
    )
    added = first.model_copy(
        update={
            "pair_id": "shared-choice",
            "group_id": "new",
            "document_family": "new",
            "author_id": "new",
            "left_text": left,
            "right_text": right,
            "source_hashes": [hashlib.sha256(text.encode()).hexdigest() for text in (left, right)],
        }
    )
    dataset.pairs.append(added)
    partitions, groups = split_pairs(dataset, LearningConfig())
    assert groups[first.pair_id] == groups[added.pair_id]
    allocation = {pair.pair_id: name for name, pairs in partitions.items() for pair in pairs}
    assert allocation[first.pair_id] == allocation[added.pair_id]


def test_json_model_roundtrip_and_hash_validation(tmp_path):
    dataset = synthetic_preferences(groups=10)
    model, _ = fit_ranker(dataset)
    path = tmp_path / "model.json"
    save_model(model, path)
    loaded = load_model(path)
    assert loaded == model
    with pytest.raises(FileExistsError):
        save_model(model, path)
    raw = json.loads(path.read_text())
    raw["model"]["coefficients"][0] += 1
    path.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="checksum"):
        load_model(path)
    path.write_text("[]")
    with pytest.raises(ValueError, match="checksum"):
        load_model(path)


def test_training_artifacts_have_matching_manifest_and_honest_model_card(tmp_path):
    dataset = synthetic_preferences(groups=10)
    model, report = fit_ranker(dataset)
    output = write_training_run(tmp_path, dataset, model, report)
    card = (output / "MODEL_CARD.md").read_text()
    assert "real-author efficacy are NOT ESTABLISHED" in card
    manifest = json.loads((output / "manifest.json").read_text())
    assert {entry["path"] for entry in manifest["files"]} == {
        "model.json",
        "dataset.json",
        "report.json",
        "MODEL_CARD.md",
    }
    for item in manifest["files"]:
        payload = (output / item["path"]).read_bytes()
        assert len(payload) == item["bytes"]
        assert hashlib.sha256(payload).hexdigest() == item["sha256"]
