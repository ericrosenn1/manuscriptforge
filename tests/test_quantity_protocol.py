"""Gold replay checks parser/scorer compatibility; this is not model evaluation."""

import pytest

from manuscriptforge.evaluation.extraction_runner import run_extraction_comparison
from manuscriptforge.evaluation.extraction_scoring import score_extraction_case
from manuscriptforge.evaluation.models import Assertion, CandidatePrediction
from manuscriptforge.llm.extraction import (
    ExtractionSource,
    FactExtractionRequest,
    FactExtractionResponse,
    FactProposal,
    validate_fact_proposals,
)
from scripts.generate_quantity_benchmark import build_quantity_dataset


def test_gold_replay_is_compatible_with_explicit_quantity_protocol():
    dataset = build_quantity_dataset()
    sources = {source.source_id: source for source in dataset.sources}
    facts = {fact.fact_id: fact for fact in dataset.facts}
    for case in dataset.cases:
        request = FactExtractionRequest(
            sources=[
                ExtractionSource(
                    source_id=source_id,
                    text=sources[source_id].text,
                    content_hash=sources[source_id].sha256,
                )
                for source_id in case.source_ids
            ]
        )
        response = FactExtractionResponse(
            facts=[
                FactProposal(
                    source_id=facts[fact_id].source_id,
                    subject=facts[fact_id].subject,
                    predicate=facts[fact_id].predicate,
                    value=facts[fact_id].value,
                    unit=facts[fact_id].unit,
                    quote=sources[facts[fact_id].source_id].text,
                )
                for fact_id in case.expected_fact_ids
            ],
            unresolved_questions=["Fixture abstention."],
        )
        result = validate_fact_proposals(request, response, quantity_protocol="2.1")
        assert result.validation.valid, (case.stratum, result.validation.issues)
        prediction = CandidatePrediction(
            case_id=case.case_id,
            abstained=case.should_abstain,
            assertions=[
                Assertion(
                    fact_id=fact.proposal_id,
                    source_id=fact.source_id,
                    subject=fact.subject,
                    predicate=fact.predicate,
                    value=fact.value,
                    unit=fact.unit,
                )
                for fact in result.facts
            ],
        )
        scored = score_extraction_case(
            case,
            prediction,
            facts,
            extraction_result=result,
            sources=request.sources,
            protocol_version="2.1-quantities",
        )
        assert scored["complete_case_correct"], (case.stratum, scored)


def test_quantity_runner_requires_explicit_21_before_any_candidate_call(tmp_path):
    def forbidden_call(task):
        raise AssertionError("The candidate must not be invoked")

    with pytest.raises(ValueError, match="explicit protocol 2.1"):
        run_extraction_comparison(
            build_quantity_dataset(), forbidden_call, forbidden_call, tmp_path
        )
    assert not list(tmp_path.iterdir())
