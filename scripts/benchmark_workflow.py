"""Measure cold and verified-cache workflow runs on original synthetic inputs.

This measures orchestration and I/O on this machine, not research validity or
model quality. No model API is called. Each repetition starts a new project.
Run from a stable checkout or pass a frozen --source-root while developing.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import statistics
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

import psutil


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--workers", type=int, nargs="+", default=[1, 2, 4])
    parser.add_argument("--repetitions", type=int, default=2)
    args = parser.parse_args()
    if not 1 <= args.repetitions <= 20 or any(not 1 <= value <= 8 for value in args.workers):
        parser.error("Use 1–20 repetitions and worker counts in 1–8")
    source_root = args.source_root.resolve(strict=True)
    sys.path.insert(0, str(source_root))
    from manuscriptforge.research.demo import prepare_research_demo
    from manuscriptforge.research.pipeline import research_engine, run_research
    from manuscriptforge.research.settings import ResearchSettings
    from manuscriptforge.utils.io import write_json

    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    rows = []
    implementation = {
        path.relative_to(source_root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted((source_root / "manuscriptforge").rglob("*"))
        if path.is_file() and path.suffix in {".py", ".yaml", ".json"}
    }
    write_json(
        output / "protocol.json",
        {
            "generated_at": datetime.now(UTC).isoformat(),
            "synthetic": True,
            "source_root": str(source_root),
            "implementation_sha256": implementation,
            "python": sys.version,
            "platform": platform.platform(),
            "logical_cpus": os.cpu_count(),
            "workers": args.workers,
            "repetitions": args.repetitions,
            "provider": "mock",
            "rss_method": "100ms samples of this process and live descendants; approximate peak",
            "interpretation": "Local orchestration and I/O benchmark; no model or scientific validation.",
        },
    )
    for workers in args.workers:
        for repetition in range(args.repetitions):
            project = prepare_research_demo(output / "projects" / f"w{workers}-r{repetition}")
            for mode in ("cold", "warm"):
                stopped = threading.Event()
                peak = [0]

                def sample_memory(stopped=stopped, peak=peak) -> None:
                    parent = psutil.Process()
                    while not stopped.wait(0.1):
                        total = 0
                        for process in [parent, *parent.children(recursive=True)]:
                            try:
                                total += process.memory_info().rss
                            except psutil.Error:
                                continue
                        peak[0] = max(peak[0], total)

                monitor = threading.Thread(target=sample_memory, daemon=True)
                monitor.start()
                started = time.perf_counter()
                try:
                    run = run_research(project, ResearchSettings(max_workers=workers))
                finally:
                    stopped.set()
                    monitor.join(timeout=2)
                elapsed = time.perf_counter() - started
                verified = research_engine(project).verify_run(run.run_id)
                row = {
                    "workers": workers,
                    "repetition": repetition,
                    "mode": mode,
                    "seconds": elapsed,
                    "approximate_peak_rss_bytes": peak[0],
                    "status": run.status,
                    "run_id": run.run_id,
                    "artifact_integrity": verified.model_dump(mode="json"),
                    "node_statuses": {key: value.status for key, value in run.nodes.items()},
                }
                rows.append(row)
                write_json(output / "runs.json", rows)
                print(
                    json.dumps(
                        {
                            key: row[key]
                            for key in ("workers", "repetition", "mode", "seconds", "status")
                        }
                    ),
                    flush=True,
                )
                if run.status != "succeeded" or not verified.valid:
                    raise RuntimeError(
                        f"Workflow benchmark failed; inspect preserved run {run.run_id}"
                    )
                if mode == "warm" and any(node.status != "cached" for node in run.nodes.values()):
                    raise RuntimeError("Warm measurement did not reuse every verified node")
    summary = [
        {
            "workers": workers,
            "mode": mode,
            "repetitions": args.repetitions,
            "median_seconds": statistics.median(
                row["seconds"] for row in rows if row["workers"] == workers and row["mode"] == mode
            ),
            "min_seconds": min(
                row["seconds"] for row in rows if row["workers"] == workers and row["mode"] == mode
            ),
            "max_seconds": max(
                row["seconds"] for row in rows if row["workers"] == workers and row["mode"] == mode
            ),
        }
        for workers in args.workers
        for mode in ("cold", "warm")
    ]
    write_json(output / "summary.json", summary)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
