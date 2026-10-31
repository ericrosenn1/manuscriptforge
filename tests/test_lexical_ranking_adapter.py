import pytest

from manuscriptforge.evaluation.models import EvaluationFact, EvaluationSource, PublicTask
from manuscriptforge.retrieval import text_sha256
from manuscriptforge.retrieval.evaluation import LexicalRankingCandidate


def task():
    return PublicTask(
        case_id="ranking-case",
        task_kind="source_ranking",
        query="alpha archive",
        sources=[
            EvaluationSource(
                source_id=name,
                text=text,
                sha256=text_sha256(text),
                document_family=name,
                author_id=name,
                provenance="original synthetic fixture",
            )
            for name, text in [("a", "Alpha archive records"), ("b", "Beta archive records")]
        ],
        facts=[
            EvaluationFact(
                fact_id="misleading",
                subject="alpha",
                predicate="source",
                value="b",
                source_id="b",
                locator="synthetic",
            )
        ],
    )


def test_ranking_uses_public_source_text_and_does_not_synthesize_assertions():
    query = task()
    candidate = LexicalRankingCandidate("bm25")
    first = candidate(query)
    assert first.ranked_source_ids[0] == "a"
    assert first.assertions == []
    assert candidate(query.model_copy(update={"facts": []})).model_dump() == first.model_dump()
    metadata = candidate.describe()
    assert metadata["strategy"] == "bm25"
    assert len(metadata["implementation_sha256"]["lexical.py"]) == 64


def test_ranking_only_adapter_rejects_factual_generation_task():
    with pytest.raises(ValueError, match="source_ranking"):
        LexicalRankingCandidate()(task().model_copy(update={"task_kind": "grounded_response"}))
