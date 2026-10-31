"""Bounded DAG execution with immutable attempts and verified artifact reuse.

This is a local trusted-code executor, not a sandbox. Node specifications name
registry keys, never arbitrary commands or import paths.
"""

from __future__ import annotations

import importlib
import inspect
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from contextlib import suppress
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path
from typing import Any

import psutil

from manuscriptforge.orchestration.models import (
    ArtifactRecord,
    HandlerResult,
    NodeRecord,
    NodeSpec,
    PipelineSpec,
    RunRecord,
    VerificationReport,
    WorkerContext,
    WorkerJob,
    WorkerReceipt,
    safe_id,
)
from manuscriptforge.orchestration.storage import (
    atomic_json,
    canonical_json,
    connect,
    contained_file,
    digest_json,
    event,
    exclusive_lock,
    hash_file,
    initialize_database,
    is_link,
    utc_now,
    verify_artifacts,
)

Handler = Callable[[WorkerContext], HandlerResult]
SUCCESS = {"succeeded", "cached"}
TERMINAL = SUCCESS | {"failed", "cancelled", "blocked"}


def _owned_process(pid: int | None, started: float | None, job_path: str) -> psutil.Process | None:
    if pid is None or started is None:
        return None
    try:
        process = psutil.Process(pid)
        if abs(process.create_time() - started) > 0.01 or not process.is_running():
            return None
        if process.status() == psutil.STATUS_ZOMBIE:
            return None
        if job_path not in process.cmdline():
            return None
        return process
    except (psutil.Error, OSError):
        return None


def _terminate_process(pid: int | None, started: float | None, job_path: str) -> bool:
    process = _owned_process(pid, started, job_path)
    if process is None:
        return False
    try:
        children = process.children(recursive=True)
    except psutil.Error:
        children = []
    owned = [*reversed(children), process]
    for child in owned:
        with suppress(psutil.Error):
            child.terminate()
    _, alive = psutil.wait_procs(owned, timeout=1)
    for child in alive:
        with suppress(psutil.Error):
            child.kill()
    psutil.wait_procs(alive, timeout=2)
    return True


def _kill_attempt(attempt: sqlite3.Row) -> None:
    _terminate_process(attempt["pid"], attempt["process_start"], attempt["job_path"])
    _terminate_process(attempt["launcher_pid"], attempt["launcher_start"], attempt["job_path"])


def _callable_identity(handler: Handler) -> dict[str, str]:
    module_name = getattr(handler, "__module__", "")
    name = getattr(handler, "__qualname__", "")
    if not module_name or module_name == "__main__" or not name or "<" in name:
        raise ValueError("Handlers must be importable module-level Python callables")
    target: Any = importlib.import_module(module_name)
    for part in name.split("."):
        target = getattr(target, part)
    if target is not handler:
        raise ValueError("Handler registry must reference its importable callable directly")
    source = inspect.getsourcefile(handler)
    if source is None:
        raise ValueError("Handlers must have an inspectable implementation file")
    return {"module": module_name, "name": name, "source_sha256": hash_file(Path(source))}


def _software_identity() -> dict[str, Any]:
    package = Path(__file__).resolve().parents[1]
    code = {
        path.relative_to(package).as_posix(): hash_file(path)
        for path in sorted(package.rglob("*"))
        if path.is_file()
        and path.suffix in {".py", ".json", ".yaml", ".yml", ".j2"}
        and "__pycache__" not in path.parts
    }
    versions = sorted(
        {
            (str(item.metadata["Name"]), item.version)
            for item in metadata.distributions()
            if item.metadata["Name"]
        }
    )
    return {
        "python": sys.version,
        "platform": sys.platform,
        "packages": versions,
        "implementation": code,
    }


class Orchestrator:
    def __init__(
        self,
        project_dir: Path,
        handlers: Mapping[str, Handler],
        *,
        state_dir: Path | None = None,
        software_fingerprint: Mapping[str, Any] | None = None,
    ) -> None:
        self.project_dir = Path(project_dir).resolve()
        if not self.project_dir.is_dir():
            raise ValueError("The explicitly selected project directory must exist")
        self.state_dir = Path(state_dir or self.project_dir / "outputs" / "engine").resolve()
        if self.state_dir == self.project_dir:
            raise ValueError("Engine state cannot replace the project directory")
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.state_dir / "state.sqlite"
        self.handlers = dict(handlers)
        self.identities = {
            safe_id(key): _callable_identity(value) for key, value in handlers.items()
        }
        self.software = (
            dict(software_fingerprint) if software_fingerprint is not None else _software_identity()
        )
        self._custom_software = software_fingerprint is not None
        self._processes: dict[str, subprocess.Popen] = {}
        initialize_database(self.db_path)

    def _run_root(self, run_id: str) -> Path:
        path = self.state_dir / "runs" / safe_id(run_id)
        if path.exists() and is_link(path):
            raise ValueError("A run directory cannot be a symbolic link or junction")
        return path

    def _input_inventory(self, spec: PipelineSpec) -> dict[str, Any]:
        required = {path for node in spec.nodes for path in node.input_paths}
        selectors = required | {path for node in spec.nodes for path in node.optional_input_paths}
        selected: dict[str, Any] = {}
        files: dict[str, ArtifactRecord] = {}
        for selector in sorted(selectors):
            source = contained_file(self.project_dir, selector)
            if not source.exists():
                if selector in required:
                    raise FileNotFoundError(f"Required pipeline input is missing: {selector}")
                selected[selector] = {"kind": "missing", "files": []}
                continue
            paths: list[Path] = []
            pending = [source]
            while pending:
                path = pending.pop()
                if is_link(path):
                    raise ValueError(f"Input symlinks and junctions are not supported: {path}")
                if path.resolve().is_relative_to(self.state_dir):
                    raise ValueError("Declared inputs must not include engine state")
                if path.is_dir():
                    pending.extend(sorted(path.iterdir(), reverse=True))
                elif path.is_file():
                    paths.append(path)
                else:
                    raise ValueError(f"Unsupported input type: {path}")
            names = []
            for path in paths:
                name = path.relative_to(self.project_dir).as_posix()
                names.append(name)
                files[name] = ArtifactRecord(
                    path=name, sha256=hash_file(path), bytes=path.stat().st_size
                )
            selected[selector] = {
                "kind": "directory" if source.is_dir() else "file",
                "files": sorted(names),
            }
        return {
            "selectors": selected,
            "files": [files[name].model_dump() for name in sorted(files)],
        }

    def _snapshot(self, spec: PipelineSpec, root: Path) -> tuple[Path, dict[str, Any], str]:
        inventory = self._input_inventory(spec)
        fingerprint = digest_json(inventory)
        destination = root / "snapshots" / fingerprint
        records = [ArtifactRecord.model_validate(item) for item in inventory["files"]]
        if destination.exists():
            verify_artifacts(destination, records)
            return destination, inventory, fingerprint
        temporary = root / "snapshots" / f".{fingerprint}.{uuid.uuid4().hex}.staging"
        temporary.mkdir(parents=True)
        for name, selection in inventory["selectors"].items():
            if selection["kind"] == "directory":
                (temporary / name).mkdir(parents=True, exist_ok=True)
        for record in records:
            target = temporary / record.path
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(contained_file(self.project_dir, record.path), target)
        verify_artifacts(temporary, records)
        if self._input_inventory(spec) != inventory:
            raise RuntimeError("Project inputs changed while their immutable snapshot was copied")
        os.rename(temporary, destination)
        return destination, inventory, fingerprint

    def _prepare(self, spec: PipelineSpec, run_id: str, *, resume: bool) -> None:
        unknown = {node.handler for node in spec.nodes} - self.handlers.keys()
        if unknown:
            raise ValueError(f"Handlers are not in the trusted registry: {sorted(unknown)}")
        root = self._run_root(run_id)
        root.mkdir(parents=True, exist_ok=True)
        with connect(self.db_path) as connection:
            previous = connection.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
            if previous and not resume:
                raise ValueError(f"Run {run_id} already exists; request resume explicitly")
            if resume and previous is None:
                raise ValueError(f"Cannot resume unknown run: {run_id}")
            if previous and previous["pipeline_id"] != spec.id:
                raise ValueError("A run ID cannot be reused for a different pipeline")
        snapshot, inventory, fingerprint = self._snapshot(spec, root)
        now = utc_now()
        spec_json = canonical_json(spec.model_dump(mode="json"))
        with connect(self.db_path) as connection:
            if previous is None:
                connection.execute(
                    "INSERT INTO runs VALUES(?,?,?,?,?,?,?,?,?,?,?,0)",
                    (
                        run_id,
                        spec.id,
                        "running",
                        str(root),
                        now,
                        now,
                        fingerprint,
                        digest_json(json.loads(spec_json)),
                        str(snapshot),
                        canonical_json(inventory),
                        spec_json,
                    ),
                )
            else:
                connection.execute(
                    "UPDATE runs SET status='running',updated_at=?,snapshot_fingerprint=?,"
                    "spec_fingerprint=?,snapshot_dir=?,snapshot_json=?,spec_json=?,cancel_requested=0 WHERE run_id=?",
                    (
                        now,
                        fingerprint,
                        digest_json(json.loads(spec_json)),
                        str(snapshot),
                        canonical_json(inventory),
                        spec_json,
                        run_id,
                    ),
                )
            for node in spec.nodes:
                connection.execute(
                    "INSERT OR IGNORE INTO nodes(run_id,node_id,status) VALUES(?,?,'pending')",
                    (run_id, node.id),
                )
            event(connection, run_id, "resumed" if previous else "created", snapshot=fingerprint)

    def status(self, run_id: str) -> RunRecord:
        with connect(self.db_path) as connection:
            row = connection.execute(
                "SELECT * FROM runs WHERE run_id=?", (safe_id(run_id),)
            ).fetchone()
            if row is None:
                raise KeyError(f"Unknown run: {run_id}")
            spec = PipelineSpec.model_validate_json(row["spec_json"])
            requested = {node.id for node in spec.nodes}
            nodes = {}
            for node in connection.execute("SELECT * FROM nodes WHERE run_id=?", (run_id,)):
                if node["node_id"] in requested:
                    nodes[node["node_id"]] = NodeRecord(
                        id=node["node_id"],
                        status=node["status"],
                        fingerprint=node["fingerprint"],
                        attempts=node["attempts"],
                        artifact_dir=node["artifact_dir"],
                        provenance_path=str(Path(node["artifact_dir"]).parent / "provenance.json")
                        if node["artifact_dir"]
                        else None,
                        artifacts=json.loads(node["artifacts_json"]),
                        metadata=json.loads(node["metadata_json"]),
                        error=node["error"],
                        reused_from=node["reused_from"],
                        completed_at=node["completed_at"],
                    )
            return RunRecord(
                run_id=run_id,
                pipeline_id=row["pipeline_id"],
                status=row["status"],
                run_dir=row["run_dir"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
                snapshot_fingerprint=row["snapshot_fingerprint"],
                spec_fingerprint=row["spec_fingerprint"],
                nodes=nodes,
            )

    def _save_state(self, run_id: str) -> RunRecord:
        with connect(self.db_path) as connection:
            connection.execute(
                "UPDATE runs SET updated_at=? WHERE run_id=? AND status='running'",
                (utc_now(), run_id),
            )
        record = self.status(run_id)
        atomic_json(self._run_root(run_id) / "run.json", record.model_dump(mode="json"))
        return record

    def _fingerprint_payload(
        self, node: NodeSpec, snapshot: dict[str, Any], dependencies: dict[str, NodeRecord]
    ) -> dict[str, Any]:
        selectors = {
            name: snapshot["selectors"][name]
            for name in sorted(set(node.input_paths + node.optional_input_paths))
        }
        selected_files = {name for value in selectors.values() for name in value["files"]}
        inputs = [item for item in snapshot["files"] if item["path"] in selected_files]
        return {
            "schema_version": 1,
            "node": node.id,
            "handler": self.identities[node.handler],
            "version": node.version,
            "parameters": node.parameters,
            "expected_outputs": sorted(node.expected_outputs),
            "selectors": selectors,
            "inputs": inputs,
            "dependencies": {
                key: {
                    "fingerprint": item.fingerprint,
                    "artifacts": [artifact.model_dump() for artifact in item.artifacts],
                    "metadata": item.metadata,
                }
                for key, item in sorted(dependencies.items())
            },
            "software": digest_json(self.software),
        }

    def _fingerprint(
        self, node: NodeSpec, snapshot: dict[str, Any], dependencies: dict[str, NodeRecord]
    ) -> str:
        return digest_json(self._fingerprint_payload(node, snapshot, dependencies))

    def _verify_node(self, node: NodeRecord, expected: list[str]) -> None:
        if not node.artifact_dir:
            raise ValueError(f"Node {node.id} has no artifact directory")
        root = Path(node.artifact_dir)
        if not root.resolve().is_relative_to(self.state_dir):
            raise ValueError("Artifact directory leaves the engine state directory")
        verify_artifacts(root, node.artifacts)
        provenance = json.loads((root.parent / "provenance.json").read_text(encoding="utf-8"))
        if (
            not isinstance(provenance, dict)
            or digest_json(provenance.get("semantic_recipe")) != node.fingerprint
        ):
            raise ValueError(f"Node provenance does not match its fingerprint: {node.id}")
        for name in expected:
            if not contained_file(root, name).is_file():
                raise ValueError(f"Missing expected artifact: {node.id}/{name}")

    def verify_run(self, run_id: str) -> VerificationReport:
        record = self.status(run_id)
        errors = []
        checked = 0
        with connect(self.db_path) as connection:
            row = connection.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
        assert row is not None
        spec = PipelineSpec.model_validate_json(row["spec_json"])
        try:
            snapshot = json.loads(row["snapshot_json"])
            verify_artifacts(
                Path(row["snapshot_dir"]),
                [ArtifactRecord.model_validate(item) for item in snapshot["files"]],
            )
        except (OSError, ValueError) as exc:
            errors.append(f"Input snapshot: {exc}")
        for node in spec.nodes:
            current = record.nodes[node.id]
            if current.status not in SUCCESS:
                errors.append(f"Node {node.id} is {current.status}, not complete")
                continue
            try:
                self._verify_node(current, node.expected_outputs)
                checked += len(current.artifacts)
            except (OSError, ValueError) as exc:
                errors.append(f"Node {node.id}: {exc}")
        if record.status != "succeeded":
            errors.append(f"Run status is {record.status}")
        return VerificationReport(
            run_id=run_id,
            valid=not errors,
            checked_nodes=len(spec.nodes),
            checked_artifacts=checked,
            errors=errors,
        )

    def _invalidate_rows(
        self,
        connection: sqlite3.Connection,
        run_id: str,
        node_ids: set[str],
        reason: str,
        *,
        discard_cache: bool = False,
    ) -> None:
        for node_id in sorted(node_ids):
            if discard_cache:
                connection.execute(
                    "UPDATE cache SET valid=0 WHERE fingerprint IN "
                    "(SELECT fingerprint FROM nodes WHERE run_id=? AND node_id=?)",
                    (run_id, node_id),
                )
            connection.execute(
                "UPDATE nodes SET status='pending',fingerprint='',attempts=0,artifact_dir=NULL,"
                "artifacts_json='[]',metadata_json='{}',error=NULL,reused_from=NULL,completed_at=NULL "
                "WHERE run_id=? AND node_id=?",
                (run_id, node_id),
            )
            event(connection, run_id, "invalidated", node_id=node_id, reason=reason)

    def invalidate(self, run_id: str, node_ids: list[str], reason: str) -> None:
        if not reason.strip():
            raise ValueError("Invalidation requires a recorded reason")
        with exclusive_lock(self._run_root(run_id) / ".scheduler.lock"):
            with connect(self.db_path) as connection:
                row = connection.execute(
                    "SELECT spec_json FROM runs WHERE run_id=?", (run_id,)
                ).fetchone()
                if row is None:
                    raise KeyError(run_id)
                spec = PipelineSpec.model_validate_json(row["spec_json"])
                requested = set(node_ids)
                known = {node.id for node in spec.nodes}
                if not requested <= known:
                    raise ValueError("Cannot invalidate unknown nodes")
                while True:
                    expanded = requested | {
                        node.id for node in spec.nodes if requested.intersection(node.depends_on)
                    }
                    if expanded == requested:
                        break
                    requested = expanded
                self._invalidate_rows(connection, run_id, requested, reason, discard_cache=True)
                connection.execute(
                    "UPDATE runs SET status='pending',updated_at=? WHERE run_id=?",
                    (utc_now(), run_id),
                )
            self._save_state(run_id)

    def _cache_hit(self, run_id: str, node: NodeSpec, fingerprint: str) -> bool:
        if not node.cacheable:
            return False
        with connect(self.db_path) as connection:
            row = connection.execute(
                "SELECT * FROM cache WHERE fingerprint=? AND valid=1", (fingerprint,)
            ).fetchone()
        if row is None:
            return False
        cached = NodeRecord(
            id=node.id,
            status="cached",
            fingerprint=fingerprint,
            artifact_dir=row["artifact_dir"],
            artifacts=json.loads(row["artifacts_json"]),
            metadata=json.loads(row["metadata_json"]),
        )
        try:
            self._verify_node(cached, node.expected_outputs)
        except (OSError, ValueError) as exc:
            with connect(self.db_path) as connection:
                connection.execute("UPDATE cache SET valid=0 WHERE fingerprint=?", (fingerprint,))
                event(connection, run_id, "cache_rejected", node_id=node.id, error=str(exc))
            return False
        with connect(self.db_path) as connection:
            connection.execute(
                "UPDATE nodes SET status='cached',fingerprint=?,artifact_dir=?,artifacts_json=?,"
                "metadata_json=?,error=NULL,reused_from=?,completed_at=? WHERE run_id=? AND node_id=?",
                (
                    fingerprint,
                    row["artifact_dir"],
                    row["artifacts_json"],
                    row["metadata_json"],
                    row["origin"],
                    utc_now(),
                    run_id,
                    node.id,
                ),
            )
            event(
                connection,
                run_id,
                "cache_hit",
                node_id=node.id,
                fingerprint=fingerprint,
                origin=row["origin"],
            )
        return True

    def _launch(
        self,
        run_id: str,
        node: NodeSpec,
        fingerprint: str,
        snapshot_dir: Path,
        snapshot: dict[str, Any],
        dependencies: dict[str, NodeRecord],
    ) -> None:
        root = self._run_root(run_id)
        attempt_id = uuid.uuid4().hex
        with connect(self.db_path) as connection:
            current = connection.execute(
                "SELECT * FROM nodes WHERE run_id=? AND node_id=?", (run_id, node.id)
            ).fetchone()
            assert current is not None
            serial = current["serial"] + 1
            number = current["attempts"] + 1
        directory = root / "attempts" / node.id / f"{serial:04d}-{attempt_id}"
        work = directory / "artifacts"
        work.mkdir(parents=True)
        inputs: dict[str, Path] = {}
        for selector in sorted(set(node.input_paths + node.optional_input_paths)):
            selected = snapshot["selectors"][selector]
            if selected["kind"] != "missing":
                inputs[selector] = snapshot_dir / selector
            inputs.update({name: snapshot_dir / name for name in selected["files"]})
        dependency_paths = {
            key: {
                artifact.path: Path(value.artifact_dir or "") / artifact.path
                for artifact in value.artifacts
            }
            for key, value in dependencies.items()
        }
        identity = self.identities[node.handler]
        job_path = directory / "job.json"
        atomic_json(
            directory / "provenance.json",
            {
                "schema_version": 1,
                "run_id": run_id,
                "node_id": node.id,
                "attempt_id": attempt_id,
                "semantic_fingerprint": fingerprint,
                "semantic_recipe": self._fingerprint_payload(node, snapshot, dependencies),
                "execution_policy": {
                    "timeout_seconds": node.timeout_seconds,
                    "max_attempts": node.max_attempts,
                },
                "software_identity": self.software,
            },
        )
        job = WorkerJob(
            attempt_id=attempt_id,
            fingerprint=fingerprint,
            callable_module=identity["module"],
            callable_name=identity["name"],
            context=WorkerContext(
                project_dir=snapshot_dir,
                work_dir=work,
                inputs=inputs,
                dependencies=dependency_paths,
                parameters=node.parameters,
                node_id=node.id,
                run_id=run_id,
                attempt=number,
            ),
            expected_outputs=node.expected_outputs,
            snapshot_manifest=[ArtifactRecord.model_validate(item) for item in snapshot["files"]],
            receipt_path=directory / "receipt.json",
            database_path=self.db_path,
            handler_sha256=identity["source_sha256"],
            implementation_hashes=self.software.get("implementation", {}),
        )
        atomic_json(job_path, job.model_dump(mode="json"))
        now = time.time()
        with connect(self.db_path) as connection:
            connection.execute(
                "INSERT INTO attempts(attempt_id,run_id,node_id,number,fingerprint,status,attempt_dir,job_path,"
                "receipt_path,started_epoch,deadline_epoch,started_at) VALUES(?,?,?,?,?,'running',?,?,?,?,?,?)",
                (
                    attempt_id,
                    run_id,
                    node.id,
                    serial,
                    fingerprint,
                    str(directory),
                    str(job_path),
                    str(job.receipt_path),
                    now,
                    now + node.timeout_seconds,
                    utc_now(),
                ),
            )
            connection.execute(
                "UPDATE nodes SET status='running',fingerprint=?,attempts=?,serial=?,error=NULL WHERE run_id=? AND node_id=?",
                (fingerprint, number, serial, run_id, node.id),
            )
            event(
                connection,
                run_id,
                "started",
                node_id=node.id,
                attempt=serial,
                fingerprint=fingerprint,
            )
        environment = os.environ.copy()
        environment["PYTHONPATH"] = os.pathsep.join(
            str(Path(path or os.getcwd()).resolve()) for path in sys.path if isinstance(path, str)
        )
        environment["PYTHONUNBUFFERED"] = "1"
        command = [
            sys.executable,
            "-m",
            "manuscriptforge.orchestration.worker",
            "--job",
            str(job_path),
        ]
        try:
            with (
                (directory / "stdout.log").open("wb") as stdout,
                (directory / "stderr.log").open("wb") as stderr,
            ):
                process = subprocess.Popen(
                    command,
                    stdout=stdout,
                    stderr=stderr,
                    env=environment,
                    cwd=work,
                    start_new_session=os.name != "nt",
                    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
                )
            self._processes[attempt_id] = process
            try:
                started = psutil.Process(process.pid).create_time()
            except psutil.Error:
                started = None
            with connect(self.db_path) as connection:
                connection.execute(
                    "UPDATE attempts SET launcher_pid=?,launcher_start=? WHERE attempt_id=?",
                    (process.pid, started, attempt_id),
                )
        except OSError as exc:
            with connect(self.db_path) as connection:
                attempt = connection.execute(
                    "SELECT * FROM attempts WHERE attempt_id=?", (attempt_id,)
                ).fetchone()
            assert attempt is not None
            self._failed_attempt(attempt, node, f"Worker could not start: {exc}", kind="failed")

    def _failed_attempt(
        self,
        attempt: sqlite3.Row,
        node: NodeSpec,
        error: str,
        *,
        kind: str,
        returncode: int | None = None,
    ) -> bool:
        with connect(self.db_path) as connection:
            current = connection.execute(
                "SELECT * FROM attempts WHERE attempt_id=?", (attempt["attempt_id"],)
            ).fetchone()
            if current is None or current["status"] != "running":
                return False
            if attempt["pid"] is None and current["pid"] is not None:
                return False  # The child completed its startup handshake meanwhile.
            row = connection.execute(
                "SELECT attempts FROM nodes WHERE run_id=? AND node_id=?",
                (attempt["run_id"], node.id),
            ).fetchone()
            used = row["attempts"]
            if kind in {"interrupted", "cancelled"}:
                used = max(0, used - 1)
            status = (
                "cancelled"
                if kind == "cancelled"
                else "pending"
                if used < node.max_attempts
                else "failed"
            )
            connection.execute(
                "UPDATE attempts SET status=?,error=?,returncode=?,completed_at=? WHERE attempt_id=?",
                (kind, error[:32000], returncode, utc_now(), attempt["attempt_id"]),
            )
            connection.execute(
                "UPDATE nodes SET status=?,attempts=?,error=? WHERE run_id=? AND node_id=?",
                (status, used, error[:32000], attempt["run_id"], node.id),
            )
            event(
                connection,
                attempt["run_id"],
                kind,
                node_id=node.id,
                attempt_id=attempt["attempt_id"],
                error=error[:32000],
            )
        process = self._processes.pop(attempt["attempt_id"], None)
        if process is not None:
            with suppress(subprocess.TimeoutExpired):
                process.wait(timeout=2)
        return True

    def _accept(self, attempt: sqlite3.Row, node: NodeSpec, returncode: int | None) -> None:
        try:
            job = WorkerJob.model_validate_json(
                Path(attempt["job_path"]).read_text(encoding="utf-8")
            )
            receipt = WorkerReceipt.model_validate_json(
                Path(attempt["receipt_path"]).read_text(encoding="utf-8")
            )
            if (
                receipt.attempt_id != attempt["attempt_id"]
                or receipt.fingerprint != attempt["fingerprint"]
                or receipt.node_id != node.id
                or receipt.run_id != attempt["run_id"]
            ):
                raise ValueError("Completion receipt identity does not match its attempt")
            if receipt.status != "succeeded":
                raise ValueError(receipt.error or "Worker reported failure")
            if returncode not in {0, None}:
                raise ValueError(f"Worker exited with code {returncode} after writing a receipt")
            if datetime.fromisoformat(receipt.completed_at).timestamp() > attempt["deadline_epoch"]:
                raise ValueError("Worker completed after its deadline")
            verify_artifacts(job.context.project_dir, job.snapshot_manifest)
            self._verify_node(
                NodeRecord(
                    id=node.id,
                    status="succeeded",
                    fingerprint=receipt.fingerprint,
                    artifact_dir=str(job.context.work_dir),
                    artifacts=receipt.artifacts,
                ),
                node.expected_outputs,
            )
            state = self.status(attempt["run_id"])
            for dependency in node.depends_on:
                self._verify_node(state.nodes[dependency], [])
        except (OSError, ValueError, KeyError) as exc:
            self._failed_attempt(
                attempt,
                node,
                f"Completion validation failed: {exc}",
                kind="failed",
                returncode=returncode,
            )
            return
        artifacts_json = canonical_json([item.model_dump() for item in receipt.artifacts])
        metadata_json = canonical_json(receipt.metadata)
        with connect(self.db_path) as connection:
            connection.execute(
                "UPDATE attempts SET status='succeeded',completed_at=?,returncode=? WHERE attempt_id=?",
                (receipt.completed_at, returncode, attempt["attempt_id"]),
            )
            connection.execute(
                "UPDATE nodes SET status='succeeded',artifact_dir=?,artifacts_json=?,metadata_json=?,"
                "error=NULL,reused_from=NULL,completed_at=? WHERE run_id=? AND node_id=?",
                (
                    str(job.context.work_dir),
                    artifacts_json,
                    metadata_json,
                    receipt.completed_at,
                    attempt["run_id"],
                    node.id,
                ),
            )
            if node.cacheable:
                connection.execute(
                    "INSERT OR REPLACE INTO cache VALUES(?,?,?,?,?,?,1)",
                    (
                        attempt["fingerprint"],
                        str(job.context.work_dir),
                        artifacts_json,
                        metadata_json,
                        f"{attempt['run_id']}/{node.id}/{attempt['number']}",
                        receipt.completed_at,
                    ),
                )
            event(
                connection,
                attempt["run_id"],
                "succeeded",
                node_id=node.id,
                attempt_id=attempt["attempt_id"],
                receipt_sha256=hash_file(Path(attempt["receipt_path"])),
            )
        process = self._processes.pop(attempt["attempt_id"], None)
        if process is not None:
            process.wait(timeout=2)

    def _poll(self, run_id: str, specs: dict[str, NodeSpec], *, cancelled: bool = False) -> bool:
        progress = False
        with connect(self.db_path) as connection:
            attempts = connection.execute(
                "SELECT * FROM attempts WHERE run_id=? AND status='running'", (run_id,)
            ).fetchall()
        for attempt in attempts:
            node = specs.get(attempt["node_id"])
            if node is None:
                _kill_attempt(attempt)
                removed = NodeSpec(id=attempt["node_id"], handler="removed")
                progress |= self._failed_attempt(
                    attempt, removed, "Node was removed from the resumed graph", kind="cancelled"
                )
                continue
            process = self._processes.get(attempt["attempt_id"])
            returncode = process.poll() if process is not None else None
            owned = _owned_process(attempt["pid"], attempt["process_start"], attempt["job_path"])
            launcher = _owned_process(
                attempt["launcher_pid"], attempt["launcher_start"], attempt["job_path"]
            )
            alive = (
                owned is not None
                or launcher is not None
                or (process is not None and returncode is None)
            )
            if cancelled or (alive and time.time() >= attempt["deadline_epoch"]):
                _kill_attempt(attempt)
                kind = "cancelled" if cancelled else "timed_out"
                progress |= self._failed_attempt(
                    attempt,
                    node,
                    "Cancellation requested"
                    if cancelled
                    else f"Node exceeded its {node.timeout_seconds:g} second deadline",
                    kind=kind,
                )
            elif alive:
                continue
            elif Path(attempt["receipt_path"]).is_file():
                self._accept(attempt, node, returncode)
                progress = True
            elif time.time() >= attempt["deadline_epoch"]:
                progress |= self._failed_attempt(
                    attempt, node, "Worker failed to start before its deadline", kind="timed_out"
                )
            elif attempt["pid"] is None and time.time() - attempt["started_epoch"] < 3:
                continue  # Allow bounded subprocess interpreter startup after a crash.
            else:
                progress |= self._failed_attempt(
                    attempt,
                    node,
                    "Worker ended without a completion receipt",
                    kind="failed" if process is not None else "interrupted",
                    returncode=returncode,
                )
        return progress

    def cancel(self, run_id: str) -> None:
        """Request cancellation and terminate only verified, owned subprocesses."""
        with connect(self.db_path) as connection:
            row = connection.execute(
                "SELECT status FROM runs WHERE run_id=?", (safe_id(run_id),)
            ).fetchone()
            if row is None:
                raise KeyError(run_id)
            if row["status"] not in {"running", "pending"}:
                return
            connection.execute("UPDATE runs SET cancel_requested=1 WHERE run_id=?", (run_id,))
            event(connection, run_id, "cancellation_requested")
            attempts = connection.execute(
                "SELECT * FROM attempts WHERE run_id=? AND status='running'", (run_id,)
            ).fetchall()
        for attempt in attempts:
            _kill_attempt(attempt)

    def _cancelled(self, run_id: str) -> bool:
        with connect(self.db_path) as connection:
            row = connection.execute(
                "SELECT cancel_requested FROM runs WHERE run_id=?", (run_id,)
            ).fetchone()
            return bool(row["cancel_requested"])

    def _ready_nodes(
        self, run_id: str, spec: PipelineSpec, snapshot_dir: Path, inventory: dict[str, Any]
    ) -> bool:
        progress = False
        for node in spec.ordered_nodes():
            state = self.status(run_id)
            current = state.nodes[node.id]
            if current.status == "running":
                continue
            dependencies = {key: state.nodes[key] for key in node.depends_on}
            if any(
                value.status in {"failed", "blocked", "cancelled"}
                for value in dependencies.values()
            ):
                if current.status != "blocked":
                    with connect(self.db_path) as connection:
                        connection.execute(
                            "UPDATE nodes SET status='blocked',error=? WHERE run_id=? AND node_id=?",
                            ("A required dependency did not succeed", run_id, node.id),
                        )
                        event(connection, run_id, "blocked", node_id=node.id)
                    progress = True
                continue
            if any(value.status not in SUCCESS for value in dependencies.values()):
                continue
            fingerprint = self._fingerprint(node, inventory, dependencies)
            if current.status in SUCCESS:
                try:
                    if current.fingerprint != fingerprint:
                        raise ValueError("Declared inputs, implementation, or dependencies changed")
                    self._verify_node(current, node.expected_outputs)
                    continue
                except (OSError, ValueError) as exc:
                    with connect(self.db_path) as connection:
                        self._invalidate_rows(connection, run_id, {node.id}, str(exc))
                    current = self.status(run_id).nodes[node.id]
                    progress = True
            if current.status in {"blocked", "cancelled"} or (
                current.fingerprint and current.fingerprint != fingerprint
            ):
                with connect(self.db_path) as connection:
                    self._invalidate_rows(
                        connection,
                        run_id,
                        {node.id},
                        "Node is ready under the current input fingerprint",
                    )
                current = self.status(run_id).nodes[node.id]
            if current.status == "failed" and current.attempts >= node.max_attempts:
                continue
            if self._cache_hit(run_id, node, fingerprint):
                progress = True
                continue
            with connect(self.db_path) as connection:
                active = connection.execute(
                    "SELECT COUNT(*) FROM attempts WHERE run_id=? AND status='running'", (run_id,)
                ).fetchone()[0]
            if active >= spec.max_workers:
                break
            self._launch(run_id, node, fingerprint, snapshot_dir, inventory, dependencies)
            progress = True
        return progress

    def _publish_latest(self, run_id: str) -> None:
        record = self.status(run_id)
        verified = self.verify_run(run_id)
        if not verified.valid:
            raise ValueError("Cannot publish invalid run: " + "; ".join(verified.errors))
        outputs = self.project_dir / "outputs"
        outputs.mkdir(parents=True, exist_ok=True)
        with exclusive_lock(outputs / ".latest.lock", wait_seconds=10):
            pointer = outputs / "latest.json"
            if pointer.exists():
                try:
                    previous = json.loads(pointer.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    previous = {}
                if (previous.get("completed_at", ""), previous.get("run_id", "")) > (
                    record.updated_at,
                    run_id,
                ):
                    return
            # Final manifests are immutable: resuming this run cannot turn the
            # already published pointer into a running or failed manifest.
            payload = record.model_dump(mode="json")
            manifest = self._run_root(run_id) / "finalized" / f"{digest_json(payload)}.json"
            atomic_json(manifest, payload)
            atomic_json(
                pointer,
                {
                    "schema_version": 1,
                    "run_id": run_id,
                    "pipeline_id": record.pipeline_id,
                    "run_dir": str(self._run_root(run_id)),
                    "manifest_path": str(manifest),
                    "manifest_sha256": hash_file(manifest),
                    "completed_at": record.updated_at,
                },
            )

    def run(
        self,
        spec: PipelineSpec,
        *,
        run_id: str | None = None,
        resume: bool = False,
        cancel_event: threading.Event | None = None,
    ) -> RunRecord:
        """Run or resume a static DAG; failure returns a failed record, never success.

        Completed artifacts are immutable and may be reused only after their
        hashes, required outputs, and full dependency fingerprint are verified.
        """
        spec = PipelineSpec.model_validate(spec.model_dump())
        run_id = safe_id(run_id or f"{datetime.now(UTC):%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:12]}")
        with exclusive_lock(self._run_root(run_id) / ".scheduler.lock"):
            self.identities = {
                key: _callable_identity(value) for key, value in self.handlers.items()
            }
            if not self._custom_software:
                self.software = _software_identity()
            self._prepare(spec, run_id, resume=resume)
            with connect(self.db_path) as connection:
                row = connection.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
            assert row is not None
            snapshot_dir = Path(row["snapshot_dir"])
            inventory = json.loads(row["snapshot_json"])
            specs = {node.id: node for node in spec.nodes}
            self._save_state(run_id)
            cancelled = False
            try:
                while True:
                    if cancel_event is not None and cancel_event.is_set():
                        self.cancel(run_id)
                    cancelled = self._cancelled(run_id)
                    progress = self._poll(run_id, specs, cancelled=cancelled)
                    if cancelled:
                        with connect(self.db_path) as connection:
                            connection.execute(
                                "UPDATE nodes SET status='cancelled',error='Cancellation requested' "
                                "WHERE run_id=? AND status NOT IN ('succeeded','cached')",
                                (run_id,),
                            )
                        break
                    progress |= self._ready_nodes(run_id, spec, snapshot_dir, inventory)
                    state = self.status(run_id)
                    if all(node.status in TERMINAL for node in state.nodes.values()):
                        break
                    if progress:
                        self._save_state(run_id)
                    time.sleep(0.025)
            except BaseException:
                self.cancel(run_id)
                self._poll(run_id, specs, cancelled=True)
                with connect(self.db_path) as connection:
                    connection.execute(
                        "UPDATE runs SET status='cancelled',updated_at=? WHERE run_id=?",
                        (utc_now(), run_id),
                    )
                self._save_state(run_id)
                raise
            state = self.status(run_id)
            status = (
                "cancelled"
                if cancelled
                else "succeeded"
                if all(node.status in SUCCESS for node in state.nodes.values())
                else "failed"
            )
            with connect(self.db_path) as connection:
                connection.execute(
                    "UPDATE runs SET status=?,updated_at=? WHERE run_id=?",
                    (status, utc_now(), run_id),
                )
                event(connection, run_id, "finalized", status=status)
            result = self._save_state(run_id)
            if status == "succeeded":
                try:
                    self._publish_latest(run_id)
                except (OSError, ValueError) as exc:
                    with connect(self.db_path) as connection:
                        connection.execute(
                            "UPDATE runs SET status='failed',updated_at=? WHERE run_id=?",
                            (utc_now(), run_id),
                        )
                        event(connection, run_id, "final_verification_failed", error=str(exc))
                    result = self._save_state(run_id)
            return result


Engine = Orchestrator
