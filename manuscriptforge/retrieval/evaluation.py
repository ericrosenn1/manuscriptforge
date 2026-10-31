"""Source-ranking adapters for the common held-out evaluation protocol."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from manuscriptforge.evaluation.models import (
    CandidatePrediction,
    EvaluationDataset,
    PublicTask,
    Split,
)
from manuscriptforge.retrieval.lexical import LexicalIndex, RetrievalRecord


def rank_sources(
    task: PublicTask, *, strategy: Literal["jaccard", "bm25"], limit: int = 5
) -> list[str]:
    """Read only public query/source text; neither facts nor labels affect ranking."""
    records = [
        RetrievalRecord(
            source.source_id,
            source.text,
            source.source_id,
            source.sha256,
            {"provenance": source.provenance, "synthetic": source.synthetic},
        )
        for source in task.sources
    ]
    index = LexicalIndex(records, strategy=strategy, tokenizer="unicode")
    return [hit.source_id for hit in index.search(task.query, limit=limit)]


@dataclass(frozen=True)
class LexicalRankingCandidate:
    strategy: Literal["jaccard", "bm25"] = "jaccard"
    limit: int = 5

    def __call__(self, task: PublicTask) -> CandidatePrediction:
        if task.task_kind != "source_ranking":
            raise ValueError("LexicalRankingCandidate requires an explicit source_ranking task")
        ranked = rank_sources(task, strategy=self.strategy, limit=self.limit)
        return CandidatePrediction(
            case_id=task.case_id,
            ranked_source_ids=ranked,
            abstained=not ranked,
            model=f"lexical-{self.strategy}",
            provider="deterministic",
        )

    def describe(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy,
            "limit": self.limit,
            "tokenizer": "unicode",
            "k1": 1.5,
            "b": 0.75,
            "mmr": None,
            "implementation_sha256": {
                "lexical.py": hashlib.sha256(
                    Path(__file__).with_name("lexical.py").read_bytes()
                ).hexdigest(),
                "adapter.py": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            },
            "assertion_generation": "not performed",
        }


def run_ranking_comparison(
    dataset: EvaluationDataset, output_root: Path, *, split: Split = "test", limit: int = 5
) -> Path:
    """Use ranking-specific scoring in the shared evaluator, never factual metrics."""
    from manuscriptforge.evaluation.runner import run_comparison
    from manuscriptforge.evaluation.synthetic import as_retrieval_dataset

    return run_comparison(
        as_retrieval_dataset(dataset),
        LexicalRankingCandidate("jaccard", limit),
        LexicalRankingCandidate("bm25", limit),
        output_root,
        split=split,
        baseline_name="indexed-jaccard",
        candidate_name="indexed-bm25",
    )
