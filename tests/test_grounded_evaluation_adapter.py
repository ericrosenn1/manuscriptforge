import pytest

from manuscriptforge.evaluation.models import EvaluationFact, EvaluationSource, PublicTask
from manuscriptforge.evaluation.protocol import text_hash
from manuscriptforge.llm.evaluation_adapter import GroundedTaskCandidate
from manuscriptforge.llm.grounded import GroundingValidationError
from manuscriptforge.llm.mock_provider import MockLLMProvider


def task():
    text = "Archive A has 12 pages."
    return PublicTask(
        case_id="case-a",
        query="How many pages does Archive A have?",
        sources=[
            EvaluationSource(
                source_id="source-a",
                document_family="a",
                author_id="synthetic-a",
                text=text,
                sha256=text_hash(text),
                provenance="Synthetic fixture",
            )
        ],
        facts=[
            EvaluationFact(
                fact_id="fact-a",
                subject="Archive A",
                predicate="page_count",
                value="12",
                unit="pages",
                source_id="source-a",
                locator="sentence 1",
            )
        ],
    )


def response(value="12"):
    return {
        "section_name": "Evidence response",
        "paragraphs": [{"text": "Archive A has 12 pages.", "claim_ids": ["fact-a"]}],
        "assertions": [
            {
                "claim_id": "fact-a",
                "evidence_id": "source-a",
                "subject": "Archive A",
                "predicate": "page_count",
                "value": value,
                "unit": "pages",
            }
        ],
        "ranked_evidence_ids": ["source-a"],
    }


def test_adapter_uses_actual_returned_assertions():
    prediction = GroundedTaskCandidate(MockLLMProvider(json_responses=[response()]))(task())
    assert prediction.assertions[0].value == "12"
    assert prediction.assertions[0].source_id == "source-a"
    assert prediction.ranked_source_ids == ["source-a"]
    assert prediction.input_tokens is None
    assert prediction.usage_source == "unavailable"


def test_changed_returned_value_is_an_error_not_replaced_with_source_value():
    with pytest.raises(GroundingValidationError, match="changes a supplied fact"):
        GroundedTaskCandidate(MockLLMProvider(json_responses=[response("99")]))(task())


def test_explicit_abstention_remains_distinct_from_generation_failure():
    fixture = {
        "section_name": "Evidence response",
        "paragraphs": [],
        "assertions": [],
        "unresolved_questions": ["The source does not resolve the query."],
    }
    prediction = GroundedTaskCandidate(MockLLMProvider(json_responses=[fixture]))(task())
    assert prediction.abstained and not prediction.assertions
