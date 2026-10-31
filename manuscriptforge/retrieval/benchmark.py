"""Reproducible local latency measurements, with no hardware-independent claims.

Run: python -m manuscriptforge.retrieval.benchmark --output local/retrieval-benchmarks
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
import random
import statistics
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from manuscriptforge.evidence.evidence_index import EvidenceIndex
from manuscriptforge.models.claim import EvidenceItem
from manuscriptforge.retrieval import LexicalIndex, RetrievalRecord


def synthetic_records(size: int, seed: int) -> list[RetrievalRecord]:
    """Original nonclinical text. Counts are fixture labels, not measurements."""
    rng = random.Random(seed)
    records = []
    for index in range(size):
        topic_number = rng.randrange(100)
        topic = f"topic{chr(97 + topic_number // 26)}{chr(97 + topic_number % 26)}"
        text = (
            f"Archive {index} contains {rng.randrange(10, 1000)} catalog records for {topic}. "
            "A checksum workflow preserves the original document and records each processing step. "
            "The review procedure compares file identifiers, verifies metadata, and reports missing entries. "
            f"This synthetic documentation example belongs to collection{index // 20}."
        )
        records.append(
            RetrievalRecord.from_text(
                f"record-{index:06d}",
                text,
                f"source-{index // 4:06d}",
                {
                    "section": "methods" if index % 2 else "results",
                    "style_mode": "academic_manuscript",
                    "synthetic": True,
                },
            )
        )
    return records


def _measure(search: Any, queries: list[str]) -> dict[str, Any]:
    for query in queries[:3]:
        search(query)
    timings = []
    counts = []
    for query in queries:
        started = time.perf_counter()
        result = search(query)
        timings.append((time.perf_counter() - started) * 1000)
        counts.append(len(result))
    ordered = sorted(timings)
    return {
        "queries": len(queries),
        "median_query_ms": statistics.median(timings),
        "p95_query_ms": ordered[max(0, math.ceil(len(ordered) * 0.95) - 1)],
        "min_query_ms": ordered[0],
        "max_query_ms": ordered[-1],
        "returned_hits": sum(counts),
    }


def run_benchmark(
    output_root: Path,
    *,
    sizes: tuple[int, ...] = (100, 1000, 10000),
    query_count: int = 60,
    seed: int = 20261031,
) -> Path:
    if not sizes or any(size <= 0 for size in sizes) or query_count <= 0:
        raise ValueError("Benchmark sizes and query count must be positive")
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    destination = Path(output_root) / f"retrieval-{stamp}-{uuid.uuid4().hex[:8]}"
    destination.mkdir(parents=True)
    queries = [
        "archive catalog",
        "checksum metadata",
        "topicar",
        "topicbq missing",
        "nonexistent_token",
    ]
    queries = [queries[index % len(queries)] for index in range(query_count)]
    measurements = []
    for size in sizes:
        records = synthetic_records(size, seed)
        legacy = EvidenceIndex(
            [
                EvidenceItem(
                    evidence_id=record.id,
                    evidence_type="manual",
                    source_path=record.source_id,
                    source_label=record.id,
                    text=record.text,
                    content_hash=record.content_hash,
                )
                for record in records
            ]
        )
        measurements.append(
            {
                "records": size,
                "strategy": "legacy_full_scan",
                "tokenizer": "legacy",
                "build_seconds": None,
                **_measure(legacy.search, queries),
            }
        )
        strategies: tuple[tuple[Literal["jaccard", "bm25"], Literal["legacy", "unicode"]], ...] = (
            ("jaccard", "legacy"),
            ("bm25", "legacy"),
            ("bm25", "unicode"),
        )
        for strategy, tokenizer in strategies:
            started = time.perf_counter()
            index = LexicalIndex(records, strategy=strategy, tokenizer=tokenizer)
            build_seconds = time.perf_counter() - started
            measured = _measure(index.search, queries)
            serialized = index.to_dict()
            started = time.perf_counter()
            restored = LexicalIndex.from_dict(serialized)
            reload_seconds = time.perf_counter() - started
            assert [hit.to_dict() for hit in restored.search(queries[0])] == [
                hit.to_dict() for hit in index.search(queries[0])
            ]
            if strategy == "jaccard":
                for query in queries[:5]:
                    assert [hit.record_id for hit in index.search(query)] == [
                        item.evidence_id for item in legacy.search(query)
                    ]
            row = {
                "records": size,
                "strategy": strategy,
                "tokenizer": tokenizer,
                "build_seconds": build_seconds,
                "validated_reload_seconds": reload_seconds,
                "corpus_sha256": index.corpus_sha256,
                "index_sha256": index.index_sha256,
                "vocabulary": len(index.document_frequencies),
                **measured,
            }
            if strategy == "bm25":
                row["mmr"] = _measure(
                    lambda query, current=index: current.search(query, mmr_lambda=0.6),
                    queries[: min(20, query_count)],
                )
            measurements.append(row)
    report: dict[str, Any] = {
        "schema_version": "1.0",
        "seed": seed,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "tokenizer": "specified per measurement row",
        "measurements": measurements,
        "implementation_sha256": {
            "retrieval/lexical.py": hashlib.sha256(
                Path(__file__).with_name("lexical.py").read_bytes()
            ).hexdigest(),
            "retrieval/benchmark.py": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "evidence/evidence_index.py": hashlib.sha256(
                (Path(__file__).parent.parent / "evidence/evidence_index.py").read_bytes()
            ).hexdigest(),
        },
        "limitations": [
            "Local synthetic corpus timing only; no real-data retrieval quality is measured.",
            "Three warm-up queries precede timed queries. Timings include ranking and object construction.",
            "Serialization reload verifies checksums and rebuilds statistics; it is not a trusted binary cache.",
            "No universal performance threshold or semantic relevance claim is inferred.",
        ],
    }
    (destination / "benchmark.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    lines = [
        "# Local lexical retrieval benchmark",
        "",
        f"Python {report['python']}; {report['platform']}; seed {seed}.",
        "",
        "| Records | Strategy | Tokens | Median query (ms) | p95 query (ms) | Build (s) |",
        "| ---: | --- | --- | ---: | ---: | ---: |",
    ]
    for row in measurements:
        build = (
            f"{row['build_seconds']:.4f}" if row["build_seconds"] is not None else "not measured"
        )
        lines.append(
            f"| {row['records']} | {row['strategy']} | {row['tokenizer']} | {row['median_query_ms']:.4f} | {row['p95_query_ms']:.4f} | {build} |"
        )
    lines.extend(["", *[f"- {limitation}" for limitation in report["limitations"]]])
    (destination / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return destination


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("local/retrieval-benchmarks"))
    parser.add_argument("--sizes", type=int, nargs="+", default=[100, 1000, 10000])
    parser.add_argument("--queries", type=int, default=60)
    args = parser.parse_args()
    result = run_benchmark(args.output, sizes=tuple(args.sizes), query_count=args.queries)
    print((result / "REPORT.md").read_text(encoding="utf-8"))
    print(f"Validated benchmark artifacts: {result.resolve()}")


if __name__ == "__main__":
    main()
