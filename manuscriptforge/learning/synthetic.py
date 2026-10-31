"""Declared latent-rule mechanism test; these are not human preference labels."""

from __future__ import annotations

import random

from manuscriptforge.evaluation.protocol import text_hash
from manuscriptforge.learning.features import feature_vector
from manuscriptforge.learning.models import PairDataset, StylePair

TEMPLATES = (
    "We may compare record {key}. The pattern could remain uncertain.",
    "Record {key} was assessed through a process that involved several documented stages, and the final report was reviewed before the complete archive was stored.",
    "We inspected record {key}; however, the comparison may depend on context.",
    "Record {key} is available. The entry was reviewed.",
    "Record {key} includes a detailed statement (for later review) which may be useful when the accumulated observations are considered together.",
)


def synthetic_preferences(
    groups: int = 30, pairs_per_group: int = 4, authors: int = 1, seed: int = 20261031
) -> PairDataset:
    if groups < 5 or pairs_per_group < 1 or authors < 1:
        raise ValueError("Use at least five groups, one pair per group, and one author")
    rng = random.Random(seed)
    pairs = []
    for group in range(groups):
        for item in range(pairs_per_group):
            key = f"{group:03d}-{item:03d}"
            while True:
                left = " ".join(
                    rng.choice(TEMPLATES).format(key=key + "L") for _ in range(rng.randint(1, 3))
                )
                right = " ".join(
                    rng.choice(TEMPLATES).format(key=key + "R") for _ in range(rng.randint(1, 3))
                )
                a, b = feature_vector(left), feature_vector(right)
                # A fixed, disclosed style-feature rule generates synthetic labels.
                score_a = 0.03 * a[5] - 0.06 * a[1] + 0.01 * a[6]
                score_b = 0.03 * b[5] - 0.06 * b[1] + 0.01 * b[6]
                if abs(score_a - score_b) > 1e-8:
                    break
            pairs.append(
                StylePair(
                    pair_id=f"pair-{key}",
                    group_id=f"group-{group:03d}",
                    document_family=f"document-family-{group:03d}",
                    author_id=f"synthetic-author-{group % authors}",
                    left_text=left,
                    right_text=right,
                    preferred="left" if score_a > score_b else "right",
                    source_hashes=[text_hash(left), text_hash(right)],
                    provenance="original synthetic latent linear rule v1; labels are generated, not human judgments",
                    synthetic=True,
                )
            )
    return PairDataset(
        dataset_id=f"synthetic-preference-v1-{seed}",
        description="Mechanism-only labels from .03*hedging_rate - .06*mean_sentence_length + .01*first_person_rate; no author-efficacy claim.",
        pairs=pairs,
    )
