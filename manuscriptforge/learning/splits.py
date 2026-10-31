from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable

from manuscriptforge.evaluation.protocol import text_hash
from manuscriptforge.learning.models import LearningConfig, PairDataset, StylePair


def normalized_hash(text: str) -> str:
    return text_hash(" ".join(text.casefold().split()))


def split_pairs(
    dataset: PairDataset, config: LearningConfig
) -> tuple[dict[str, list[StylePair]], dict[str, str]]:
    ids = [pair.pair_id for pair in dataset.pairs]
    if len(ids) != len(set(ids)):
        raise ValueError("Pair IDs must be unique")
    parent = dict(zip(ids, ids, strict=True))

    def find(key: str) -> str:
        while parent[key] != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key

    def union(a: str, b: str) -> None:
        parent[find(a)] = find(b)

    seen: dict[tuple[str, str], str] = {}
    pair_labels: set[tuple[str, str]] = set()
    for pair in dataset.pairs:
        left, right = normalized_hash(pair.left_text), normalized_hash(pair.right_text)
        if left == right:
            raise ValueError("Normalized-identical choices cannot encode a strict preference")
        key = (min(left, right), max(left, right))
        if key in pair_labels:
            raise ValueError("Duplicate preference pair; curate duplicates explicitly")
        pair_labels.add(key)
        keys = [
            ("group", pair.group_id),
            ("family", pair.document_family),
            ("text", left),
            ("text", right),
        ]
        keys.extend(("source", digest) for digest in pair.source_hashes)
        if config.group_by_author:
            keys.append(("author", pair.author_id))
        for item in keys:
            if item in seen:
                union(pair.pair_id, seen[item])
            else:
                seen[item] = pair.pair_id
    return _partition(dataset, config, parent, find)


def _partition(
    dataset: PairDataset, config: LearningConfig, parent: dict[str, str], find: Callable[[str], str]
) -> tuple[dict[str, list[StylePair]], dict[str, str]]:
    groups: dict[str, list[str]] = defaultdict(list)
    for key in parent:
        groups[find(key)].append(key)
    ordered = sorted(groups.values(), key=lambda group: text_hash(f"{config.seed}:{min(group)}"))
    if len(ordered) < 3:
        raise ValueError(
            "At least three independent groups are required; author holdout may be impossible for one author"
        )
    train_count, dev_count = max(1, int(len(ordered) * 0.6)), max(1, int(len(ordered) * 0.2))
    assignments, group_ids = {}, {}
    for index, group in enumerate(ordered):
        split = (
            "train" if index < train_count else "dev" if index < train_count + dev_count else "test"
        )
        assignments.update(dict.fromkeys(group, split))
        group_ids.update(dict.fromkeys(group, min(group)))
    partitions = {
        split: [pair for pair in dataset.pairs if assignments[pair.pair_id] == split]
        for split in ("train", "dev", "test")
    }
    return partitions, group_ids
