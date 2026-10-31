"""Lexical-index timing with exact legacy-Jaccard ranking checks."""

from __future__ import annotations

import argparse
import random
import statistics
import time

from manuscriptforge.retrieval import LexicalIndex, RetrievalRecord
from manuscriptforge.utils.io import write_json
from manuscriptforge.utils.text import word_tokens


def _word(value: int) -> str:
    result = ""
    while True:
        result = chr(97 + value % 26) + result
        value = value // 26 - 1
        if value < 0:
            return "term" + result


def main() -> None:
    from pathlib import Path

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--records", type=int, default=10000)
    parser.add_argument("--queries", type=int, default=30)
    parser.add_argument("--seed", type=int, default=20261031)
    args = parser.parse_args()
    if not 100 <= args.records <= 100000 or not 4 <= args.queries <= 500:
        parser.error("Use 100–100000 records and 4–500 queries")
    if args.output.exists():
        parser.error("Choose a new output file")
    rng = random.Random(args.seed)
    vocabulary = [_word(value) for value in range(2000)]
    records = [
        RetrievalRecord.from_text(
            f"record-{index:06d}",
            "methods " + " ".join(rng.sample(vocabulary, 40)),
            f"source-{index:06d}",
        )
        for index in range(args.records)
    ]
    queries = [
        ("methods " if index % 2 else "") + " ".join(rng.sample(vocabulary, 4))
        for index in range(args.queries)
    ]
    started = time.perf_counter()
    jaccard = LexicalIndex(records, strategy="jaccard", tokenizer="legacy")
    jaccard_build = time.perf_counter() - started
    started = time.perf_counter()
    bm25 = LexicalIndex(records, strategy="bm25", tokenizer="legacy")
    bm25_build = time.perf_counter() - started
    timings: dict[str, list[float]] = {
        name: [] for name in ("legacy_scan", "indexed_jaccard", "indexed_bm25")
    }
    all_equal = True
    for query in queries:
        query_words = set(word_tokens(query))
        started = time.perf_counter()
        scores = []
        for record in records:
            terms = set(word_tokens(record.text))
            overlap = len(terms & query_words)
            if overlap:
                scores.append((overlap / len(terms | query_words), record.id))
        expected = [
            identity for _, identity in sorted(scores, key=lambda row: (-row[0], row[1]))[:10]
        ]
        timings["legacy_scan"].append(time.perf_counter() - started)
        started = time.perf_counter()
        actual = [hit.record_id for hit in jaccard.search(query, limit=10)]
        timings["indexed_jaccard"].append(time.perf_counter() - started)
        all_equal &= expected == actual
        started = time.perf_counter()
        bm25.search(query, limit=10)
        timings["indexed_bm25"].append(time.perf_counter() - started)
    _report(args, timings, all_equal, jaccard_build, bm25_build)


def _report(args, timings, all_equal, jaccard_build, bm25_build) -> None:
    import hashlib
    import platform
    import sys
    from pathlib import Path

    source = Path(__file__).resolve().parents[1]
    report = {
        "seed": args.seed,
        "records": args.records,
        "queries": args.queries,
        "workload": "40 sampled alphabetic terms per record; half queries include a corpus-wide term",
        "source_sha256": {
            name: hashlib.sha256((source / name).read_bytes()).hexdigest()
            for name in (
                "scripts/benchmark_retrieval.py",
                "manuscriptforge/retrieval/lexical.py",
                "manuscriptforge/utils/text.py",
            )
        },
        "platform": platform.platform(),
        "python": sys.version,
        "build_seconds": {"indexed_jaccard": jaccard_build, "indexed_bm25": bm25_build},
        "timings_seconds": timings,
        "median_seconds": {name: statistics.median(values) for name, values in timings.items()},
        "jaccard_top10_equal": all_equal,
        "relevance_quality": "not evaluated; BM25 timings do not imply improved relevance",
    }
    write_json(args.output, report)
    print(report["median_seconds"], "Jaccard rankings identical:", all_equal)
    if not all_equal:
        raise SystemExit("Jaccard compatibility check failed; report preserved")


if __name__ == "__main__":
    main()
