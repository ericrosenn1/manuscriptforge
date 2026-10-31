"""One trusted Python handler per subprocess, with a durable completion receipt."""

from __future__ import annotations

import argparse
import importlib
import inspect
import os
import threading
import time
import traceback
from contextlib import suppress
from pathlib import Path
from typing import Any

import psutil

from manuscriptforge.orchestration.models import WorkerJob, WorkerReceipt
from manuscriptforge.orchestration.storage import (
    atomic_json,
    collect_artifacts,
    connect,
    contained_file,
    hash_file,
    utc_now,
    verify_artifacts,
)


def verify_implementation(job: WorkerJob, target: Any) -> None:
    source = inspect.getsourcefile(target)
    if source is None or hash_file(Path(source)) != job.handler_sha256:
        raise ValueError("Handler implementation changed after its fingerprint was recorded")
    package = Path(__file__).resolve().parents[1]
    for name, digest in job.implementation_hashes.items():
        if hash_file(contained_file(package, name)) != digest:
            raise ValueError(f"Package implementation changed during execution: {name}")


def execute(job_path: Path) -> int:
    job = WorkerJob.model_validate_json(job_path.read_text(encoding="utf-8"))
    # Durable startup handshake: a late child cannot execute an attempt that a
    # recovering scheduler has already cancelled, replaced, or timed out.
    own_pid = os.getpid()
    own_start = psutil.Process(own_pid).create_time()
    with connect(job.database_path) as connection:
        connection.execute(
            "UPDATE attempts SET pid=?,process_start=? WHERE attempt_id=? AND status='running' "
            "AND pid IS NULL AND deadline_epoch>=? "
            "AND EXISTS(SELECT 1 FROM runs WHERE run_id=? AND status='running' AND cancel_requested=0)",
            (own_pid, own_start, job.attempt_id, time.time(), job.context.run_id),
        )
        row = connection.execute(
            "SELECT * FROM attempts WHERE attempt_id=?", (job.attempt_id,)
        ).fetchone()
        if (
            row is None
            or row["status"] != "running"
            or row["pid"] != own_pid
            or row["fingerprint"] != job.fingerprint
            or row["deadline_epoch"] < time.time()
        ):
            return 2
    started = utc_now()

    def expire() -> None:
        """Enforce the deadline even if the parent scheduler has disappeared."""
        receipt = WorkerReceipt(
            attempt_id=job.attempt_id,
            fingerprint=job.fingerprint,
            node_id=job.context.node_id,
            run_id=job.context.run_id,
            status="failed",
            error="Worker exceeded its execution deadline",
            started_at=started,
            completed_at=utc_now(),
        )
        with suppress(OSError):
            atomic_json(job.receipt_path, receipt.model_dump(mode="json"))
        try:
            descendants = psutil.Process().children(recursive=True)
            for process in descendants:
                with suppress(psutil.Error):
                    process.kill()
            psutil.wait_procs(descendants, timeout=1)
        finally:
            os._exit(124)

    watchdog = threading.Timer(max(0, row["deadline_epoch"] - time.time()), expire)
    watchdog.daemon = True
    watchdog.start()
    try:
        verify_artifacts(job.context.project_dir, job.snapshot_manifest)
        target: Any = importlib.import_module(job.callable_module)
        for part in job.callable_name.split("."):
            target = getattr(target, part)
        verify_implementation(job, target)
        result = target(job.context)
        if result is None:
            result = {}
        if not isinstance(result, dict):
            raise TypeError("A workflow handler must return a JSON dictionary or None")
        for name in job.expected_outputs:
            if not contained_file(job.context.work_dir, name).is_file():
                raise ValueError(f"Handler did not produce required output: {name}")
        verify_artifacts(job.context.project_dir, job.snapshot_manifest)
        verify_implementation(job, target)
        receipt = WorkerReceipt(
            attempt_id=job.attempt_id,
            fingerprint=job.fingerprint,
            node_id=job.context.node_id,
            run_id=job.context.run_id,
            status="succeeded",
            artifacts=collect_artifacts(job.context.work_dir),
            metadata=result,
            started_at=started,
            completed_at=utc_now(),
        )
        atomic_json(job.receipt_path, receipt.model_dump(mode="json"))
        return 0
    except BaseException as exc:
        traceback.print_exc()
        receipt = WorkerReceipt(
            attempt_id=job.attempt_id,
            fingerprint=job.fingerprint,
            node_id=job.context.node_id,
            run_id=job.context.run_id,
            status="failed",
            error=f"{type(exc).__name__}: {exc}"[:32000],
            started_at=started,
            completed_at=utc_now(),
        )
        atomic_json(job.receipt_path, receipt.model_dump(mode="json"))
        return 1
    finally:
        watchdog.cancel()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job", required=True, type=Path)
    args = parser.parse_args()
    raise SystemExit(execute(args.job))


if __name__ == "__main__":
    main()
