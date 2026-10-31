from __future__ import annotations

import hashlib
import json
from collections import defaultdict

from manuscriptforge.evaluation.models import EvaluationDataset, PublicTask


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def dataset_hash(dataset: EvaluationDataset) -> str:
    return text_hash(
        json.dumps(dataset.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    )


def independent_source_groups(dataset: EvaluationDataset) -> list[list[str]]:
    """Keep document families, authors, duplicate texts, and case sources together."""
    parent = {source.source_id: source.source_id for source in dataset.sources}

    def find(item: str) -> str:
        while parent[item] != item:
            parent[item] = parent[parent[item]]
            item = parent[item]
        return item

    def union(left: str, right: str) -> None:
        parent[find(left)] = find(right)

    seen: dict[tuple[str, str], str] = {}
    for source in dataset.sources:
        keys = [
            ("family", source.document_family),
            ("author", source.author_id),
            ("hash", source.sha256),
            ("normalized", text_hash(" ".join(source.text.casefold().split()))),
        ]
        for key in keys:
            if key in seen:
                union(source.source_id, seen[key])
            else:
                seen[key] = source.source_id
    for case in dataset.cases:
        if not case.source_ids:
            raise ValueError("Each case needs source context, including abstention cases")
        for source_id in case.source_ids[1:]:
            union(case.source_ids[0], source_id)
    groups: dict[str, list[str]] = defaultdict(list)
    for source_id in parent:
        groups[find(source_id)].append(source_id)
    return list(groups.values())


def assign_grouped_splits(dataset: EvaluationDataset) -> EvaluationDataset:
    ordered = sorted(
        independent_source_groups(dataset),
        key=lambda ids: text_hash(f"{dataset.split_seed}:{min(ids)}"),
    )
    if len(ordered) < 3:
        raise ValueError("At least three independent groups are needed for train/dev/test")
    train_count = max(1, int(len(ordered) * 0.6))
    dev_count = max(1, int(len(ordered) * 0.2))
    assignment: dict[str, str] = {}
    for index, group in enumerate(ordered):
        split = (
            "train" if index < train_count else "dev" if index < train_count + dev_count else "test"
        )
        assignment.update(dict.fromkeys(group, split))
    revised = dataset.model_copy(
        update={
            "cases": [
                case.model_copy(update={"split": assignment[case.source_ids[0]]})
                for case in dataset.cases
            ]
        }
    )
    validate_dataset(revised)
    return revised


def public_task(dataset: EvaluationDataset, case_id: str) -> PublicTask:
    case = next(case for case in dataset.cases if case.case_id == case_id)
    selected = set(case.source_ids)
    return PublicTask(
        case_id=case.case_id,
        task_kind=case.task_kind,
        query=case.query,
        sources=[source for source in dataset.sources if source.source_id in selected],
        facts=[fact for fact in dataset.facts if fact.source_id in selected],
    )


def validate_dataset(dataset: EvaluationDataset, require_all_splits: bool = True) -> dict:
    sources = {source.source_id: source for source in dataset.sources}
    facts = {fact.fact_id: fact for fact in dataset.facts}
    cases = {case.case_id: case for case in dataset.cases}
    if any(
        len(items) != len(unique)
        for items, unique in (
            (dataset.sources, sources),
            (dataset.facts, facts),
            (dataset.cases, cases),
        )
    ):
        raise ValueError("Dataset IDs must be unique")
    for source in dataset.sources:
        if dataset.synthetic and not source.synthetic:
            raise ValueError("A synthetic dataset cannot contain unlabeled real sources")
        if not source.author_id or not source.document_family or not source.provenance:
            raise ValueError("Every source needs author, document family, and provenance")
        if text_hash(source.text) != source.sha256:
            raise ValueError(f"Source hash mismatch: {source.source_id}")
    for fact in dataset.facts:
        if fact.source_id not in sources:
            raise ValueError(f"Unknown fact source: {fact.fact_id}")
    partitions: dict[tuple[str, str], set[str]] = defaultdict(set)
    for case in dataset.cases:
        if not case.source_ids or len(case.source_ids) != len(set(case.source_ids)):
            raise ValueError("Cases need nonempty unique source IDs")
        if len(case.expected_fact_ids) != len(set(case.expected_fact_ids)):
            raise ValueError("Expected fact IDs must be unique")
        if not set(case.source_ids) <= sources.keys():
            raise ValueError(f"Unknown case source: {case.case_id}")
        if not set(case.expected_fact_ids) <= facts.keys():
            raise ValueError(f"Unknown expected fact: {case.case_id}")
        if not set(case.relevant_source_ids) <= set(case.source_ids):
            raise ValueError(f"Unavailable relevant source: {case.case_id}")
        if any(facts[key].source_id not in case.source_ids for key in case.expected_fact_ids):
            raise ValueError(f"Unavailable expected fact: {case.case_id}")
        if case.should_abstain and (case.expected_fact_ids or case.relevant_source_ids):
            raise ValueError("Abstention cases cannot require supported answers")
        if case.related_case_id and (
            case.related_case_id not in cases or cases[case.related_case_id].split != case.split
        ):
            raise ValueError("Related counterfactual cases must remain in one split")
        for key in case.source_ids:
            source = sources[key]
            normalized = text_hash(" ".join(source.text.casefold().split()))
            for label, value in (
                ("source", key),
                ("family", source.document_family),
                ("author", source.author_id),
                ("hash", source.sha256),
                ("normalized", normalized),
            ):
                partitions[(label, value)].add(case.split)
    leaks = [f"{kind}:{value}" for (kind, value), splits in partitions.items() if len(splits) > 1]
    if leaks:
        raise ValueError("Cross-split leakage: " + ", ".join(leaks[:5]))
    counts = {
        split: sum(case.split == split for case in dataset.cases)
        for split in ("train", "dev", "test")
    }
    if require_all_splits and any(count == 0 for count in counts.values()):
        raise ValueError("Nonempty train, dev, and test partitions are required")
    return {
        "dataset_sha256": dataset_hash(dataset),
        "case_counts": counts,
        "sources": len(sources),
        "facts": len(facts),
        "leakage_detected": False,
    }
