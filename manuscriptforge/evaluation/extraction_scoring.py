"""Post-hoc source-only extraction scoring, independent of proposal/gold IDs.

Reference facts are visible only to this scorer. Model callbacks must receive raw
source text, never these references. Tuple exactness is not semantic entailment.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any, Literal

from manuscriptforge.evaluation.models import CandidatePrediction, EvaluationCase, EvaluationFact
from manuscriptforge.evaluation.scoring import canonical, ratio, wilson_interval
from manuscriptforge.evidence.quantities import canonical_number
from manuscriptforge.llm.extraction import (
    ExtractionSource,
    FactExtractionRequest,
    FactExtractionResponse,
    FactExtractionResult,
    FactProposal,
    validate_fact_proposals,
)
from manuscriptforge.utils.hashing import sha256_text

ExtractionProtocol = Literal["2.0-source-only", "2.1-quantities"]
EXTRACTION_PROTOCOLS = ("2.0-source-only", "2.1-quantities")
_SCALAR = re.compile(r"[+\-−]?\s*(?:\d+(?:,\d{3})*(?:\.\d+)?|\.\d+)(?:[eE][+\-−]?\s*\d+)?")


def validate_extraction_protocol(protocol_version: str) -> None:
    if protocol_version not in EXTRACTION_PROTOCOLS:
        raise ValueError(f"Unsupported extraction protocol: {protocol_version}")


def _value(value: str, protocol_version: ExtractionProtocol) -> str:
    # Only whole scalar values gain numerical equivalence in 2.1. Relations,
    # intervals and textual qualifiers retain the existing literal comparison.
    if protocol_version == "2.1-quantities" and _SCALAR.fullmatch(value.strip()):
        return canonical_number(value)
    return canonical(value)


def _tuple(
    item: Any, protocol_version: ExtractionProtocol = "2.0-source-only"
) -> tuple[str, str, str, str, str]:
    # Source IDs and unit case are exact: mW and MW are not interchangeable.
    unit = " ".join(item.unit.split()) if item.unit is not None else ""
    return (
        canonical(item.subject),
        canonical(item.predicate),
        _value(item.value, protocol_version),
        unit,
        str(item.source_id),
    )


def _source_field(source: Any, field: str) -> Any:
    return source.get(field) if isinstance(source, Mapping) else getattr(source, field, None)


def _quote_grounding(
    prediction: CandidatePrediction,
    result: FactExtractionResult | None,
    sources: Mapping[str, Any] | Sequence[Any] | None,
    protocol_version: ExtractionProtocol,
) -> dict[str, Any]:
    guard = (result.validation.quantity_protocol or "2.0") if result else None
    if not prediction.assertions:
        if result is not None and (result.facts or not result.validation.valid):
            return {"status": "fail", "checked": 0, "failed": 1, "source_reverified": False}
        return {"status": "not_applicable", "checked": 0, "failed": 0, "source_reverified": False}
    if result is None:
        return {"status": "not_evaluated", "checked": 0, "failed": None, "source_reverified": False}
    failures = 0
    predicted = Counter(_tuple(assertion, protocol_version) for assertion in prediction.assertions)
    proposed = Counter(_tuple(fact, protocol_version) for fact in result.facts)
    if predicted != proposed or not result.validation.valid:
        failures += 1
    if sources is None:
        # A prior report is useful provenance but cannot replace independent source checking.
        return {
            "status": "fail" if failures else "not_evaluated",
            "checked": 0,
            "failed": failures,
            "source_reverified": False,
        }
    by_source = (
        sources
        if isinstance(sources, Mapping)
        else {_source_field(source, "source_id"): source for source in sources}
    )
    for fact in result.facts:
        source = by_source.get(fact.source_id)
        text = _source_field(source, "text") if source is not None else None
        digest = (
            (_source_field(source, "content_hash") or _source_field(source, "sha256"))
            if source is not None
            else None
        )
        if not isinstance(text, str) or digest != sha256_text(text) or fact.source_hash != digest:
            failures += 1
            continue
        if (
            not 0 <= fact.quote_start < fact.quote_end <= len(text)
            or text[fact.quote_start : fact.quote_end] != fact.quote
        ):
            failures += 1
    literal_checked = False
    if not failures:
        try:
            rebuilt_sources = [
                ExtractionSource(
                    source_id=str(source_id),
                    text=_source_field(source, "text"),
                    content_hash=_source_field(source, "content_hash")
                    or _source_field(source, "sha256"),
                )
                for source_id, source in by_source.items()
            ]
            rebuilt_facts = [
                FactProposal.model_validate(fact.model_dump(include=set(FactProposal.model_fields)))
                for fact in result.facts
            ]
            verified = validate_fact_proposals(
                FactExtractionRequest(sources=rebuilt_sources, max_facts=500),
                FactExtractionResponse(facts=rebuilt_facts),
                quantity_protocol=guard or "2.0",
            )
            literal_checked = True
            failures += len(verified.validation.issues)
        except (ValueError, TypeError):
            failures += 1
    return {
        "status": "pass" if not failures else "fail",
        "checked": len(result.facts),
        "failed": failures,
        "source_reverified": True,
        "literal_constraints_checked": literal_checked,
        "quantity_protocol": guard,
    }


def score_extraction_case(
    case: EvaluationCase,
    prediction: CandidatePrediction,
    reference_facts: Mapping[str, EvaluationFact] | Sequence[EvaluationFact],
    *,
    execution_error: str | None = None,
    extraction_result: FactExtractionResult | None = None,
    sources: Mapping[str, Any] | Sequence[Any] | None = None,
    protocol_version: ExtractionProtocol = "2.0-source-only",
) -> dict[str, Any]:
    """Align actual returned tuples to private references after model execution.

    Correct proposal IDs need not match gold IDs. Full case success also requires
    independent source quote verification for emitted facts; tuple-only correctness
    is reported separately when quote artifacts or source text are unavailable.
    """
    validate_extraction_protocol(protocol_version)
    if prediction.case_id != case.case_id:
        raise ValueError("Extraction prediction has the wrong case ID")
    reference = (
        dict(reference_facts)
        if isinstance(reference_facts, Mapping)
        else {fact.fact_id: fact for fact in reference_facts}
    )
    if not set(case.expected_fact_ids) <= reference.keys():
        raise ValueError("Expected reference fact IDs are missing from the scorer")
    available = [fact for fact in reference.values() if fact.source_id in case.source_ids]
    known = {_tuple(fact, protocol_version) for fact in available}
    expected = {_tuple(reference[fact_id], protocol_version) for fact_id in case.expected_fact_ids}
    if any(
        reference[fact_id].source_id not in case.source_ids for fact_id in case.expected_fact_ids
    ):
        raise ValueError("Expected fact source is not available in this case")
    correct = 0
    duplicates = 0
    irrelevant = 0
    unsupported = 0
    wrong_source = 0
    wrong_value = 0
    wrong_unit = 0
    seen: set[tuple[str, str, str, str, str]] = set()
    for assertion in prediction.assertions:
        key = _tuple(assertion, protocol_version)
        if key in seen:
            duplicates += 1
            continue
        seen.add(key)
        if key in expected:
            correct += 1
            continue
        if key in known:
            irrelevant += 1
            continue
        unsupported += 1
        peers = [
            _tuple(fact, protocol_version)
            for fact in available
            if canonical(fact.subject) == key[0] and canonical(fact.predicate) == key[1]
        ]
        same_source = [peer for peer in peers if peer[4] == key[4]]
        if assertion.source_id not in case.source_ids or any(
            peer[:4] == key[:4] and peer[4] != key[4] for peer in peers
        ):
            wrong_source += 1
        if same_source and not any(peer[2] == key[2] for peer in same_source):
            wrong_value += 1
        if same_source and not any(peer[3] == key[3] for peer in same_source):
            wrong_unit += 1
    emitted = len(prediction.assertions)
    tuple_correct = (
        (prediction.abstained and emitted == 0)
        if case.should_abstain
        else (not prediction.abstained and seen == expected and emitted == len(expected))
    )
    abstention_correct = prediction.abstained == case.should_abstain and not (
        prediction.abstained and emitted
    )
    quote = _quote_grounding(prediction, extraction_result, sources, protocol_version)
    if execution_error:
        tuple_correct = False
        abstention_correct = False
    complete = bool(
        tuple_correct and quote["status"] in {"pass", "not_applicable"} and not execution_error
    )
    return {
        "protocol_version": protocol_version,
        "case_id": case.case_id,
        "stratum": case.stratum,
        "task_kind": "source_only_extraction",
        "assertions_emitted": emitted,
        "expected_facts": len(expected),
        "responsive_true_positives": correct,
        "responsive_precision": ratio(correct, emitted),
        "responsive_recall": ratio(correct, len(expected)),
        "unsupported_tuples": unsupported,
        "irrelevant_supported_tuples": irrelevant,
        "duplicate_tuples": duplicates,
        "wrong_source": wrong_source,
        "wrong_value": wrong_value,
        "wrong_unit": wrong_unit,
        "should_abstain": case.should_abstain,
        "abstained": prediction.abstained,
        "abstention_correct": bool(abstention_correct),
        "tuple_case_correct": bool(tuple_correct),
        "source_quote_grounding": quote,
        "extraction_guard_version": (
            getattr(extraction_result.validation, "quantity_protocol", None) or "2.0"
            if extraction_result
            else None
        ),
        "complete_case_correct": complete,
        "execution_error": execution_error,
        "retrieval_metrics": "not_applicable",
        "narrative_entailment_evaluated": False,
        "scientific_validation": "NOT_EVALUATED",
    }


def summarize_extraction(rows: list[dict[str, Any]]) -> dict[str, Any]:
    versions = {row.get("protocol_version", "2.0-source-only") for row in rows}
    if len(versions) > 1:
        raise ValueError("Cannot aggregate different extraction scoring protocols")
    count = len(rows)
    correct = sum(row["complete_case_correct"] for row in rows)
    tuple_correct = sum(row["tuple_case_correct"] for row in rows)
    true_positives = sum(row["responsive_true_positives"] for row in rows)
    emitted = sum(row["assertions_emitted"] for row in rows)
    expected = sum(row["expected_facts"] for row in rows)
    required = [row for row in rows if row["should_abstain"]]
    return {
        "protocol_version": next(iter(versions), None),
        "cases": count,
        "complete_case_rate": ratio(correct, count),
        "complete_case_wilson95": wilson_interval(correct, count),
        "tuple_case_rate": ratio(tuple_correct, count),
        "responsive_precision_micro": ratio(true_positives, emitted),
        "responsive_recall_micro": ratio(true_positives, expected),
        "assertions_emitted": emitted,
        "expected_facts": expected,
        "required_abstention_accuracy": ratio(
            sum(row["abstention_correct"] for row in required), len(required)
        ),
        "execution_failures": sum(bool(row["execution_error"]) for row in rows),
        "extraction_guard_versions": dict(
            Counter(row.get("extraction_guard_version") or "unavailable" for row in rows)
        ),
        "quote_grounding_statuses": dict(
            Counter(row["source_quote_grounding"]["status"] for row in rows)
        ),
        "unsupported_tuples": sum(row["unsupported_tuples"] for row in rows),
        "irrelevant_supported_tuples": sum(row["irrelevant_supported_tuples"] for row in rows),
        "wrong_values": sum(row["wrong_value"] for row in rows),
        "wrong_units": sum(row["wrong_unit"] for row in rows),
        "wrong_sources": sum(row["wrong_source"] for row in rows),
        "retrieval_metrics": "not_applicable",
        "scientific_validation": "NOT_EVALUATED",
        "limitation": "Synthetic tuple/source exactness and optional quote consistency; not narrative entailment or scientific acceptance. Case-level intervals assume independent cases.",
    }
