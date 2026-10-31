# Learned style preferences

`manuscriptforge.learning` contains a small, interpretable **trained preference ranker**. It learns from explicitly labeled pairs of passages. It is not foundation-model fine-tuning, an author-identity classifier, or a factual writing generator. Ranking returns the original texts unchanged.

## Labels and splits

A `StylePair` records both passages, a `left` or `right` preference, pair/group/document-family/author IDs, source hashes, provenance, and a synthetic-data flag. Missing labels are rejected rather than guessed. Identical or duplicate pairs require curation.

The default split holds out document groups within an author population. Connected groups keep shared document families, source hashes, and exact or normalized passage text together even when supplied group IDs differ. Optional author holdout joins all passages by the same author; a one-author collection cannot support that split. Reports state the actual scope and train/test author overlap explicitly.

The split seed and data hash are recorded. Training uses only the training partition. Development loss selects the stopping iteration. Test labels and test features are not used to fit coefficients, scaling, or early stopping. Regression tests change test labels and features and verify unchanged fitted parameters.

## Model

The ranker uses 12 transparent English-oriented indicators: word count, sentence length and variation, word length, paragraph length, hedging, first person, transitions, passive-pattern rate, semicolons, parentheses, and type–token ratio. Several are length dependent, and the passive and sentence rules are heuristics.

Feature differences are scaled using training data only. Batch gradient descent fits an L2-regularized logistic model, with deterministic initialization, backtracking, and development-only early stopping. There is no intercept: reversing a pair complements its preference probability. This symmetry prevents a constant positional preference from being mistaken for learned style.

## Python API

The CLI accepts either a supplied, explicitly labeled dataset or the named synthetic mechanism test:

```bash
manuscriptforge research train-style local/style-runs --dataset labeled-pairs.json
# Or, as a separate demonstration:
manuscriptforge research train-style local/synthetic-style --synthetic
manuscriptforge research rank-style TRAINED_RUN/model.json candidate-passages.json --output local/ranking.json
```

Replace `TRAINED_RUN` with the printed training directory. `candidate-passages.json` is a JSON list of nonempty strings. Output paths must be new. The ranker returns the strings unchanged with scores and original indices.

```python
from pathlib import Path
from manuscriptforge.learning import (
    LearningConfig, PairDataset, fit_ranker, load_model,
    rank_texts, save_model, write_training_run,
)

dataset = PairDataset.model_validate_json(Path("labeled-pairs.json").read_text())
model, report = fit_ranker(dataset, LearningConfig(seed=20261031))
run = write_training_run(Path("local/style-runs"), dataset, model, report)
restored = load_model(run / "model.json")
ranked = rank_texts(restored, ["First unchanged passage.", "Second unchanged passage."])
```

`synthetic_preferences()` supplies an explicitly synthetic mechanism test. Its labels come from a disclosed linear combination of hedging, first-person use, and sentence length. Successful performance on this construction demonstrates that training and grouped evaluation work; it is not evidence of real-author efficacy.

## Reports and portability

Each new training directory contains `model.json`, `dataset.json`, `report.json`, `MODEL_CARD.md`, and `manifest.json`. The report includes fitted coefficients, optimization history, baseline accuracy, held-out accuracy, coverage, Brier score, log loss, calibration bins, group-bootstrap intervals, training data hashes, implementation hashes, and the NumPy version.

Model JSON has a versioned feature contract and checksum. Loading rejects changed payloads, unknown features, and nonfinite values. No pickle or executable serialized object is used. Checksums establish integrity, not the authenticity of labels or scientific validity.

The saved dataset contains the labeled passages and may be private. Inspect artifacts before sharing. A practical style model needs representative human preferences, adequate independent held-out groups, and review of calibration and distribution shift. The repository's synthetic mechanism test does not satisfy those requirements for any real author.
