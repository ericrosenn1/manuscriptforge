"""Interpretable preference learning from explicit labels, without rewriting text."""

from manuscriptforge.learning.models import LearningConfig, PairDataset, StylePair, StyleRanker
from manuscriptforge.learning.ranker import (
    fit_ranker,
    load_model,
    probability_left,
    rank_texts,
    save_model,
)
from manuscriptforge.learning.reporting import write_training_run
from manuscriptforge.learning.synthetic import synthetic_preferences

__all__ = [
    "LearningConfig",
    "PairDataset",
    "StylePair",
    "StyleRanker",
    "fit_ranker",
    "load_model",
    "probability_left",
    "rank_texts",
    "save_model",
    "write_training_run",
    "synthetic_preferences",
]
