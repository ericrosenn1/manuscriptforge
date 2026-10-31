import pytest

from manuscriptforge.evaluation.extraction_scoring import (
    score_extraction_case,
    summarize_extraction,
)
from manuscriptforge.evaluation.models import (
    Assertion,
    CandidatePrediction,
    EvaluationCase,
    EvaluationFact,
)
from manuscriptforge.llm.extraction import (
    ExtractionSource,
    FactExtractionRequest,
    extract_fact_proposals,
)
from manuscriptforge.llm.mock_provider import MockLLMProvider
from manuscriptforge.utils.hashing import sha256_text

TEXT = "Shelf A holds 12 books and 14 maps."


def references():
    return {
        "gold-book": EvaluationFact(
            fact_id="gold-book",
            subject="Shelf A",
            predicate="book_count",
            value="12",
            unit="books",
            source_id="source-a",
            locator="sentence 1",
        ),
        "gold-map": EvaluationFact(
            fact_id="gold-map",
            subject="Shelf A",
            predicate="map_count",
            value="14",
            unit="maps",
            source_id="source-a",
            locator="sentence 1",
        ),
    }


def case(abstain=False):
    return EvaluationCase(
        case_id="case-a",
        query="Count the books.",
        source_ids=["source-a", "source-b"],
        split="test",
        stratum="synthetic",
        expected_fact_ids=[] if abstain else ["gold-book"],
        relevant_source_ids=[] if abstain else ["source-a"],
        should_abstain=abstain,
    )


def assertion(**changes):
    return Assertion(
        **(
            {
                "fact_id": "new-generated-proposal",
                "subject": "Shelf A",
                "predicate": "book_count",
                "value": "12",
                "unit": "books",
                "source_id": "source-a",
            }
            | changes
        )
    )


def prediction(assertions=None, abstain=False):
    return CandidatePrediction(
        case_id="case-a",
        assertions=[assertion()] if assertions is None else assertions,
        abstained=abstain,
    )


def anchored():
    source = ExtractionSource(source_id="source-a", text=TEXT, content_hash=sha256_text(TEXT))
    result = extract_fact_proposals(
        FactExtractionRequest(sources=[source]),
        MockLLMProvider(
            json_responses=[
                {
                    "facts": [
                        {
                            "source_id": "source-a",
                            "subject": "Shelf A",
                            "predicate": "book_count",
                            "value": "12",
                            "unit": "books",
                            "quote": TEXT,
                        }
                    ]
                }
            ]
        ),
    )
    return source, result


def test_new_proposal_id_is_accepted_by_tuple_and_full_quote_verified_independently():
    source, result = anchored()
    scored = score_extraction_case(
        case(), prediction(), references(), extraction_result=result, sources=[source]
    )
    assert scored["tuple_case_correct"] and scored["complete_case_correct"]
    assert scored["responsive_precision"] == scored["responsive_recall"] == 1
    assert scored["source_quote_grounding"]["status"] == "pass"
    assert scored["retrieval_metrics"] == "not_applicable"


@pytest.mark.parametrize(
    "changes,diagnostic",
    [
        ({"value": "99"}, "wrong_value"),
        ({"unit": "maps"}, "wrong_unit"),
        ({"source_id": "source-b"}, "wrong_source"),
    ],
)
def test_wrong_value_unit_and_source_are_errors(changes, diagnostic):
    scored = score_extraction_case(case(), prediction([assertion(**changes)]), references())
    assert not scored["tuple_case_correct"]
    assert scored[diagnostic] == 1
    assert scored["responsive_precision"] == 0


def test_extra_true_but_irrelevant_fact_lowers_responsive_precision():
    extra = assertion(fact_id="another-new-id", predicate="map_count", value="14", unit="maps")
    scored = score_extraction_case(case(), prediction([assertion(), extra]), references())
    assert scored["responsive_precision"] == 0.5
    assert scored["responsive_recall"] == 1
    assert scored["irrelevant_supported_tuples"] == 1
    assert not scored["tuple_case_correct"]


def test_duplicates_do_not_inflate_recall():
    scored = score_extraction_case(
        case(), prediction([assertion(), assertion(fact_id="duplicate-new-id")]), references()
    )
    assert scored["responsive_precision"] == 0.5 and scored["responsive_recall"] == 1
    assert scored["duplicate_tuples"] == 1


def test_refusal_or_execution_error_is_not_successful_abstention():
    empty = prediction([], abstain=True)
    good = score_extraction_case(case(True), empty, references())
    failed = score_extraction_case(case(True), empty, references(), execution_error="ModelRefusal")
    assert good["complete_case_correct"] and good["abstention_correct"]
    assert not failed["complete_case_correct"] and not failed["abstention_correct"]
    assert failed["execution_error"] == "ModelRefusal"


def test_missing_quotes_never_count_as_fully_verified_source_extraction():
    scored = score_extraction_case(case(), prediction(), references())
    assert scored["tuple_case_correct"]
    assert not scored["complete_case_correct"]
    assert scored["source_quote_grounding"]["status"] == "not_evaluated"


def test_forged_validation_flag_cannot_substitute_unrelated_verbatim_quote():
    source, result = anchored()
    fact = result.facts[0]
    fact.quote = "maps."
    fact.quote_start = TEXT.index("maps.")
    fact.quote_end = len(TEXT)
    assert result.validation.valid
    scored = score_extraction_case(
        case(), prediction(), references(), extraction_result=result, sources=[source]
    )
    assert scored["tuple_case_correct"]
    assert not scored["complete_case_correct"]
    assert scored["source_quote_grounding"]["status"] == "fail"
    assert scored["source_quote_grounding"]["literal_constraints_checked"]


def test_modified_source_quote_and_unit_case_do_not_pass():
    source, result = anchored()
    source.text = TEXT + " Modified."
    scored = score_extraction_case(
        case(), prediction(), references(), extraction_result=result, sources=[source]
    )
    assert scored["source_quote_grounding"]["status"] == "fail"
    refs = {
        "power": EvaluationFact(
            fact_id="power",
            subject="Gauge A",
            predicate="power",
            value="12",
            unit="mW",
            source_id="source-a",
            locator="1",
        )
    }
    power_case = case().model_copy(update={"expected_fact_ids": ["power"]})
    proposed = prediction([assertion(subject="Gauge A", predicate="power", unit="MW")])
    assert not score_extraction_case(power_case, proposed, refs)["tuple_case_correct"]


def test_summary_preserves_null_denominators_and_unverified_quotes():
    row = score_extraction_case(case(True), prediction([], abstain=True), references())
    summary = summarize_extraction([row])
    assert summary["responsive_precision_micro"] is None
    assert summary["responsive_recall_micro"] is None
    assert summary["required_abstention_accuracy"] == 1
    assert summary["scientific_validation"] == "NOT_EVALUATED"


@pytest.mark.parametrize("returned", ["−0.625", "- 0.625", "-6.25e-1"])
def test_quantity_21_normalizes_only_explicit_whole_scalars(returned):
    refs = references()
    refs["gold-book"].value = "-0.625"
    proposed = prediction([assertion(value=returned)])
    old = score_extraction_case(case(), proposed, refs)
    new = score_extraction_case(case(), proposed, refs, protocol_version="2.1-quantities")
    assert new["tuple_case_correct"]
    assert new["protocol_version"] == "2.1-quantities"
    assert old["tuple_case_correct"] == (returned == "-6.25e-1")


@pytest.mark.parametrize(
    "expected,returned",
    [
        ("<= 12", "12"),
        ("approximately 12", "12"),
        ("[12, 14]", "12"),
        ("-12", "12"),
        ("[−12, 14]", "[-12, 14]"),
    ],
)
def test_quantity_21_does_not_erase_relations_qualifiers_or_sign(expected, returned):
    refs = references()
    refs["gold-book"].value = expected
    scored = score_extraction_case(
        case(),
        prediction([assertion(value=returned)]),
        refs,
        protocol_version="2.1-quantities",
    )
    assert not scored["tuple_case_correct"]


def test_revalidation_records_and_uses_the_actual_extraction_guard_version():
    text = "Shelf A holds approximately 12 books."
    source = ExtractionSource(source_id="source-a", text=text, content_hash=sha256_text(text))
    response = {
        "facts": [
            {
                "source_id": "source-a",
                "subject": "Shelf A",
                "predicate": "book_count",
                "value": "12",
                "unit": "books",
                "quote": text,
            }
        ]
    }
    # The historical guard omitted approximation scope. It remains identifiable
    # when replaying the old protocol, rather than silently relabeling its result.
    result = extract_fact_proposals(
        FactExtractionRequest(sources=[source]),
        MockLLMProvider(json_responses=[response]),
        quantity_protocol="2.0",
    )
    scored = score_extraction_case(
        case(), prediction(), references(), extraction_result=result, sources=[source]
    )
    assert scored["extraction_guard_version"] == "2.0"
    assert scored["source_quote_grounding"]["quantity_protocol"] == "2.0"
    assert scored["complete_case_correct"]


def test_aggregation_refuses_to_combine_different_scoring_protocols():
    old = score_extraction_case(case(True), prediction([], abstain=True), references())
    new = score_extraction_case(
        case(True), prediction([], abstain=True), references(), protocol_version="2.1-quantities"
    )
    with pytest.raises(ValueError, match="different extraction"):
        summarize_extraction([old, new])
