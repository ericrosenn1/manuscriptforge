"""Atomic persistence, path checks, and artifact verification."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
import sys
import time
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from manuscriptforge.orchestration.models import ArtifactRecord, relative_path


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def canonical_json(value: Any) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def digest_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def hash_file(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def is_link(path: Path) -> bool:
    info = path.lstat()
    return path.is_symlink() or bool(
        getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    )


def contained_file(root: Path, value: str) -> Path:
    path = root / relative_path(value)
    root = root.resolve()
    if not path.resolve().is_relative_to(root):
        raise ValueError(f"Path leaves artifact directory: {value}")
    current = path
    while current != root:
        if current.exists() and is_link(current):
            raise ValueError(f"Symbolic links and junctions are not artifact files: {value}")
        current = current.parent
    return path


def collect_artifacts(root: Path) -> list[ArtifactRecord]:
    result: list[ArtifactRecord] = []
    if not root.is_dir() or is_link(root):
        raise ValueError(f"Missing or linked artifact directory: {root}")
    pending = [root]
    while pending:
        for path in sorted(pending.pop().iterdir()):
            if is_link(path):
                raise ValueError(f"Symbolic links and junctions are not supported: {path.name}")
            if path.is_dir():
                pending.append(path)
            elif path.is_file():
                result.append(
                    ArtifactRecord(
                        path=path.relative_to(root).as_posix(),
                        sha256=hash_file(path),
                        bytes=path.stat().st_size,
                    )
                )
            else:
                raise ValueError(f"Unsupported filesystem object: {path.name}")
    return sorted(result, key=lambda item: item.path)


def verify_artifacts(root: Path, artifacts: list[ArtifactRecord]) -> None:
    if len({item.path for item in artifacts}) != len(artifacts):
        raise ValueError("Duplicate artifact paths in manifest")
    if collect_artifacts(root) != sorted(artifacts, key=lambda item: item.path):
        raise ValueError(f"Artifact contents differ from their verified manifest: {root}")


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(canonical_json(value) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        if os.name != "nt":
            descriptor = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    finally:
        if temporary.exists():
            temporary.unlink()


class RunLockedError(RuntimeError):
    """Another live scheduler owns the selected run."""


@contextmanager
def exclusive_lock(path: Path, *, wait_seconds: float = 0):
    """OS locks are released on process exit, including scheduler crashes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and is_link(path):
        raise ValueError("A scheduler lock cannot be a symbolic link or junction")
    handle = path.open("a+b")
    if os.fstat(handle.fileno()).st_size == 0:
        handle.write(b"0")
        handle.flush()
    deadline = time.monotonic() + wait_seconds
    locked = False
    try:
        while not locked:
            try:
                handle.seek(0)
                if sys.platform == "win32":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked = True
            except OSError as exc:
                if time.monotonic() >= deadline:
                    raise RunLockedError(f"Another scheduler owns {path.parent.name}") from exc
                time.sleep(0.025)
        yield
    finally:
        if locked:
            handle.seek(0)
            if sys.platform == "win32":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
 run_id TEXT PRIMARY KEY, pipeline_id TEXT NOT NULL, status TEXT NOT NULL,
 run_dir TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 snapshot_fingerprint TEXT NOT NULL, spec_fingerprint TEXT NOT NULL,
 snapshot_dir TEXT NOT NULL, snapshot_json TEXT NOT NULL, spec_json TEXT NOT NULL,
 cancel_requested INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS nodes (
 run_id TEXT NOT NULL, node_id TEXT NOT NULL, status TEXT NOT NULL,
 fingerprint TEXT NOT NULL DEFAULT '', attempts INTEGER NOT NULL DEFAULT 0,
 serial INTEGER NOT NULL DEFAULT 0, artifact_dir TEXT, artifacts_json TEXT NOT NULL DEFAULT '[]',
 metadata_json TEXT NOT NULL DEFAULT '{}', error TEXT, reused_from TEXT, completed_at TEXT,
 PRIMARY KEY (run_id, node_id), FOREIGN KEY(run_id) REFERENCES runs(run_id)
);
CREATE TABLE IF NOT EXISTS attempts (
 attempt_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, node_id TEXT NOT NULL,
 number INTEGER NOT NULL, fingerprint TEXT NOT NULL, status TEXT NOT NULL,
 attempt_dir TEXT NOT NULL, job_path TEXT NOT NULL, receipt_path TEXT NOT NULL,
 pid INTEGER, process_start REAL, launcher_pid INTEGER, launcher_start REAL,
 started_epoch REAL NOT NULL, deadline_epoch REAL NOT NULL,
 started_at TEXT NOT NULL, completed_at TEXT, returncode INTEGER, error TEXT,
 FOREIGN KEY(run_id,node_id) REFERENCES nodes(run_id,node_id)
);
CREATE TABLE IF NOT EXISTS cache (
 fingerprint TEXT PRIMARY KEY, artifact_dir TEXT NOT NULL, artifacts_json TEXT NOT NULL,
 metadata_json TEXT NOT NULL, origin TEXT NOT NULL, created_at TEXT NOT NULL, valid INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
 sequence INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, node_id TEXT,
 event TEXT NOT NULL, created_at TEXT NOT NULL, payload_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS attempts_by_run ON attempts(run_id,status);
CREATE INDEX IF NOT EXISTS events_by_run ON events(run_id,sequence);
"""


@contextmanager
def connect(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=FULL")
    connection.execute("PRAGMA foreign_keys=ON")
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def initialize_database(path: Path) -> None:
    with connect(path) as connection:
        connection.executescript(SCHEMA)
        columns = {row[1] for row in connection.execute("PRAGMA table_info(attempts)")}
        for name, datatype in (("launcher_pid", "INTEGER"), ("launcher_start", "REAL")):
            if name not in columns:
                connection.execute(f"ALTER TABLE attempts ADD COLUMN {name} {datatype}")


def event(
    connection: sqlite3.Connection,
    run_id: str,
    kind: str,
    *,
    node_id: str | None = None,
    **payload: Any,
) -> None:
    connection.execute(
        "INSERT INTO events(run_id,node_id,event,created_at,payload_json) VALUES(?,?,?,?,?)",
        (run_id, node_id, kind, utc_now(), canonical_json(payload)),
    )
