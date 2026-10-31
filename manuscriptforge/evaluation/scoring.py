from __future__ import annotations

import math
import random
import statistics
from decimal import Decimal, InvalidOperation
from typing import Any

from manuscriptforge.evaluation.models import CandidatePrediction, EvaluationCase, EvaluationFact


def canonical(value: str | None) -> str:
    if value is None:
        return ""
    normalized = " ".join(value.casefold().split())
    try:
        number = Decimal(normalized)
        if number.is_finite():
            return str(number.normalize())
    except InvalidOperation:
        pass
    return normalized


def wilson_interval(successes: int, total: int, z: float = 1.959963984540054) -> list[float] | None:
    if total == 0:
        return None
    p = successes / total
    denominator = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denominator
    margin = z * math.sqrt((p * (1 - p) + z * z / (4 * total)) / total) / denominator
    return [max(0.0, center - margin), min(1.0, center + margin)]


def ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def score_prediction(
    case: EvaluationCase, prediction: CandidatePrediction, facts: dict[str, EvaluationFact]
) -> dict[str, Any]:
    if prediction.case_id != case.case_id:
        raise ValueError("Prediction and case IDs differ")
    supported: set[str] = set()
    unsupported = 0
    seen: set[str] = set()
    errors: list[str] = []
    for assertion in prediction.assertions:
        fact = facts.get(assertion.fact_id)
        duplicate = assertion.fact_id in seen
        seen.add(assertion.fact_id)
        matches = (
            fact is not None
            and fact.source_id in case.source_ids
            and assertion.source_id == fact.source_id
        )
        if matches and fact is not None:
            matches = all(
                canonical(getattr(assertion, name)) == canonical(getattr(fact, name))
                for name in ("subject", "predicate", "value", "unit", "source_id")
            )
        if matches and not duplicate:
            supported.add(assertion.fact_id)
        else:
            unsupported += 1
            errors.append(
                f"{'duplicate' if duplicate else 'unsupported'} assertion: {assertion.fact_id}"
            )
    expected = set(case.expected_fact_ids)
    correct = supported & expected
    ranked = prediction.ranked_source_ids
    invalid_rank = len(ranked) != len(set(ranked)) or not set(ranked) <= set(case.source_ids)
    if invalid_rank:
        errors.append("Ranking contains duplicate or unknown source IDs")
    relevant = set(case.relevant_source_ids)
    reciprocal_rank = (
        next((1 / (index + 1) for index, source in enumerate(ranked) if source in relevant), 0.0)
        if relevant
        else None
    )
    exact = (
        (prediction.abstained and not prediction.assertions and not ranked)
        if case.should_abstain
        else (
            not prediction.abstained
            and bool(expected)
            and supported == expected
            and not unsupported
        )
    )
    exact = bool(exact and not invalid_rank)
    if case.task_kind == "source_ranking":
        exact = bool(
            not invalid_rank
            and not prediction.assertions
            and (
                (case.should_abstain and prediction.abstained and not ranked)
                or (
                    not case.should_abstain
                    and not prediction.abstained
                    and ranked
                    and ranked[0] in relevant
                )
            )
        )
    return {
        "case_id": case.case_id,
        "task_kind": case.task_kind,
        "split": case.split,
        "stratum": case.stratum,
        "complete_case_correct": exact,
        "assertions_emitted": len(prediction.assertions),
        "supported_assertions": len(supported),
        "unsupported_assertions": unsupported,
        "relevant_facts_recovered": len(correct),
        "expected_facts": len(expected),
        "factual_precision": ratio(len(supported), len(prediction.assertions)),
        "relevant_fact_precision": ratio(len(correct), len(prediction.assertions)),
        "fact_recall": ratio(len(correct), len(expected)),
        "reciprocal_rank": None if invalid_rank else reciprocal_rank,
        "recall_at_1": None
        if invalid_rank
        else ratio(len(set(ranked[:1]) & relevant), len(relevant)),
        "recall_at_3": None
        if invalid_rank
        else ratio(len(set(ranked[:3]) & relevant), len(relevant)),
        "citation_precision": ratio(len(supported), len(prediction.assertions)),
        "citation_recall": ratio(
            len({facts[item].source_id for item in correct} & relevant), len(relevant)
        ),
        "abstained": prediction.abstained,
        "should_abstain": case.should_abstain,
        "abstention_correct": prediction.abstained == case.should_abstain,
        "confidence": prediction.confidence,
        "errors": errors,
        "invalid_ranking": invalid_rank,
        "narrative_entailment_evaluated": False,
    }


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Keep missing denominators visible; never turn missing metrics into successes."""
    n = len(rows)
    successes = sum(bool(row["complete_case_correct"]) for row in rows)
    emitted = sum(row["assertions_emitted"] for row in rows)
    supported = sum(row["supported_assertions"] for row in rows)
    expected = sum(row["expected_facts"] for row in rows)
    recovered = sum(row["relevant_facts_recovered"] for row in rows)
    result: dict[str, Any] = {
        "cases": n,
        "complete_case_successes": successes,
        "complete_case_rate": ratio(successes, n),
        "complete_case_wilson95": wilson_interval(successes, n),
        "execution_failures": sum(bool(row.get("execution_error")) for row in rows),
        "invalid_rankings": sum(bool(row.get("invalid_ranking")) for row in rows),
        "wilson_assumption": "Per-case IID approximation; use clustered paired interval for related cases.",
        "assertions_emitted": emitted,
        "assertions_supported": supported,
        "factual_precision_micro": ratio(supported, emitted),
        "expected_facts": expected,
        "facts_recovered": recovered,
        "fact_recall_micro": ratio(recovered, expected),
        "abstentions": sum(row["abstained"] for row in rows),
        "required_abstentions": sum(row["should_abstain"] for row in rows),
        "abstention_decisions_correct": sum(row["abstention_correct"] for row in rows),
        "narrative_entailment_evaluated": False,
    }
    for field in (
        "reciprocal_rank",
        "recall_at_1",
        "recall_at_3",
        "citation_precision",
        "citation_recall",
    ):
        observed = [row[field] for row in rows if row.get(field) is not None]
        result[field] = {
            "mean": statistics.mean(observed) if observed else None,
            "denominator": len(observed),
        }
    calibrated = [row for row in rows if row.get("confidence") is not None]
    result["brier_score"] = {
        "mean": statistics.mean(
            (row["confidence"] - int(row["complete_case_correct"])) ** 2 for row in calibrated
        )
        if calibrated
        else None,
        "denominator": len(calibrated),
    }
    bins: list[dict[str, Any]] = []
    for index in range(5):
        subset = [row for row in calibrated if min(4, int(row["confidence"] * 5)) == index]
        bins.append(
            {
                "lower": index / 5,
                "upper": (index + 1) / 5,
                "count": len(subset),
                "mean_confidence": statistics.mean(row["confidence"] for row in subset)
                if subset
                else None,
                "accuracy": statistics.mean(row["complete_case_correct"] for row in subset)
                if subset
                else None,
            }
        )
    result["calibration_bins"] = bins
    result["expected_calibration_error"] = (
        sum(
            item["count"] * abs(item["mean_confidence"] - item["accuracy"])
            for item in bins
            if item["count"]
        )
        / len(calibrated)
        if calibrated
        else None
    )
    timings = [row["elapsed_seconds"] for row in rows if row.get("elapsed_seconds") is not None]
    result["latency_seconds"] = {
        "median": statistics.median(timings) if timings else None,
        "max": max(timings) if timings else None,
        "denominator": len(timings),
    }
    for field in ("input_tokens", "output_tokens", "cost_usd"):
        observed = [row[field] for row in rows if row.get(field) is not None]
        result[field] = {
            "known_total": sum(observed) if observed else None,
            "observed_cases": len(observed),
            "missing_cases": n - len(observed),
        }
    return result


def paired_comparison(
    baseline: list[dict[str, Any]],
    candidate: list[dict[str, Any]],
    seed: int = 20261031,
    repetitions: int = 2000,
) -> dict[str, Any]:
    before = {row["case_id"]: row for row in baseline}
    after = {row["case_id"]: row for row in candidate}
    if (
        before.keys() != after.keys()
        or len(before) != len(baseline)
        or len(after) != len(candidate)
    ):
        raise ValueError("Paired comparisons require exactly the same unique case IDs")
    keys = sorted(before)
    changes = [
        int(after[key]["complete_case_correct"]) - int(before[key]["complete_case_correct"])
        for key in keys
    ]
    if not changes:
        return {"paired_cases": 0, "delta": None, "bootstrap95": None, "mcnemar_exact_p": None}
    if repetitions < 100:
        raise ValueError("At least 100 bootstrap repetitions are required")
    improved, regressed = changes.count(1), changes.count(-1)
    rng = random.Random(seed)
    grouped: dict[str, list[int]] = {}
    for key, change in zip(keys, changes, strict=True):
        group = str(after[key].get("independent_group", key))
        grouped.setdefault(group, []).append(change)
    cluster_values = list(grouped.values())
    samples = sorted(
        statistics.mean(
            value
            for cluster in rng.choices(cluster_values, k=len(cluster_values))
            for value in cluster
        )
        for _ in range(repetitions)
    )
    discordant = improved + regressed
    exact_p = (
        min(
            1.0,
            2
            * sum(math.comb(discordant, k) for k in range(min(improved, regressed) + 1))
            / 2**discordant,
        )
        if discordant
        else 1.0
    )
    return {
        "paired_cases": len(changes),
        "improved": improved,
        "regressed": regressed,
        "unchanged": changes.count(0),
        "delta": statistics.mean(changes),
        "bootstrap95": [
            samples[int(repetitions * 0.025)],
            samples[min(repetitions - 1, int(repetitions * 0.975))],
        ],
        "bootstrap_seed": seed,
        "bootstrap_repetitions": repetitions,
        "bootstrap_unit": "independent source group",
        "independent_groups": len(grouped),
        "mcnemar_exact_p": exact_p,
        "interpretation": "Paired functional case correctness; synthetic results do not establish scientific or author-style validity.",
    }
