from __future__ import annotations

import json
import os
import shutil
import stat
import sys
import tempfile
import time
import uuid
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

from manuscriptforge.utils.dates import run_timestamp
from manuscriptforge.utils.hashing import sha256_file, sha256_text


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


@contextmanager
def _atomic_text_writer(path: Path):
    """Write beside the destination and publish only after a successful fsync."""
    ensure_dir(path.parent)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n",
                                         dir=path.parent, prefix=f".{path.name}.",
                                         suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            yield handle
            handle.flush()
            os.fsync(handle.fileno())
        if path.exists() and not path.is_symlink():
            os.chmod(temporary, stat.S_IMODE(path.stat().st_mode))
        deadline = time.monotonic() + 1
        while True:
            try:
                os.replace(temporary, path)
                break
            except PermissionError:
                if sys.platform != "win32" or time.monotonic() >= deadline:
                    raise
                time.sleep(0.01)
        if sys.platform != "win32":
            descriptor = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def write_text(path: Path, text: str) -> None:
    with _atomic_text_writer(path) as handle:
        handle.write(text)


def read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        loaded = yaml.safe_load(handle) or {}
    if not isinstance(loaded, dict):
        raise ValueError(f"Expected YAML mapping in {path}")
    return loaded


def write_yaml(path: Path, data: dict[str, Any]) -> None:
    with _atomic_text_writer(path) as handle:
        yaml.safe_dump(data, handle, sort_keys=False)


def model_to_data(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, list):
        return [model_to_data(item) for item in value]
    if isinstance(value, dict):
        return {key: model_to_data(item) for key, item in value.items()}
    return value


def write_json(path: Path, data: Any) -> None:
    with _atomic_text_writer(path) as handle:
        json.dump(model_to_data(data), handle, indent=2, sort_keys=True)


def write_jsonl(path: Path, rows: list[Any]) -> None:
    with _atomic_text_writer(path) as handle:
        for row in rows:
            handle.write(json.dumps(model_to_data(row), sort_keys=True) + "\n")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def project_outputs_dir(project_dir: Path) -> Path:
    return ensure_dir(project_dir / "outputs")


def create_run_dir(project_dir: Path) -> Path:
    runs_dir = ensure_dir(project_outputs_dir(project_dir) / "runs")
    base = run_timestamp()
    suffix = 1
    while True:
        run_dir = runs_dir / (base if suffix == 1 else f"{base}_{suffix}")
        try:
            run_dir.mkdir(exist_ok=False)
            break
        except FileExistsError:
            suffix += 1
    ensure_dir(run_dir / "intermediate")
    ensure_dir(run_dir / "logs")
    return run_dir


def latest_run_dir(project_dir: Path) -> Path | None:
    runs_dir = project_dir / "outputs" / "runs"
    if not runs_dir.exists():
        return None
    pointer = project_dir / "outputs" / "latest_legacy.json"
    if pointer.is_file():
        try:
            value = read_json(pointer)
            candidate = runs_dir / value["run_name"]
            if (candidate.parent.resolve() == runs_dir.resolve()
                    and candidate.is_dir() and not _is_link(candidate)):
                return candidate
        except (OSError, ValueError, KeyError, TypeError):
            pass
    def sort_key(path: Path) -> tuple[str, int]:
        name, separator, suffix = path.name.rpartition("_")
        if separator and len(name) > 8 and suffix.isdigit():
            return name, int(suffix)
        return path.name, 1
    candidates = sorted([p for p in runs_dir.iterdir() if p.is_dir() and not _is_link(p)], key=sort_key)
    return candidates[-1] if candidates else None


def _is_link(path: Path) -> bool:
    metadata = path.lstat()
    return path.is_symlink() or bool(
        getattr(metadata, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    )


def _tree_hashes(root: Path) -> dict[str, str]:
    if not root.is_dir() or _is_link(root):
        raise RuntimeError(f"Expected an ordinary directory: {root}")
    result = {}
    pending = [root]
    while pending:
        for path in sorted(pending.pop().iterdir()):
            if _is_link(path):
                raise RuntimeError(f"Refusing a symbolic link or junction in publication: {path}")
            if path.is_dir():
                pending.append(path)
            elif path.is_file():
                result[path.relative_to(root).as_posix()] = sha256_file(path)
            else:
                raise RuntimeError(f"Unsupported publication file type: {path}")
    return result


@contextmanager
def _legacy_publication_lock(path: Path):
    if path.exists() and _is_link(path):
        raise RuntimeError("The publication lock cannot be a symbolic link or junction")
    with path.open("a+b") as handle:
        if os.fstat(handle.fileno()).st_size == 0:
            handle.write(b"0")
            handle.flush()
        deadline = time.monotonic() + 120
        while True:
            try:
                handle.seek(0)
                if sys.platform == "win32":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as exc:
                if time.monotonic() >= deadline:
                    raise TimeoutError("Timed out waiting for the legacy publication lock") from exc
                time.sleep(0.025)
        try:
            yield
        finally:
            handle.seek(0)
            if sys.platform == "win32":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _remove_publication_directory(path: Path, outputs_dir: Path, expected_name: str) -> None:
    """Delete only the exact, validated temporary directory owned by this call."""
    if not path.exists():
        return
    if (path.name != expected_name or not expected_name.startswith((".latest-staging-", ".latest-previous-"))
            or path.parent.resolve() != outputs_dir or path.resolve().parent != outputs_dir):
        raise RuntimeError(f"Refusing to remove unowned publication path: {path}")
    _tree_hashes(path)  # Reject any nested link/junction before recursive deletion.
    shutil.rmtree(path)


def update_latest_copy(project_dir: Path, run_dir: Path) -> Path:
    """Publish a verified copy, retaining the old directory until commit.

    Writers are serialized. ``latest_legacy.json`` is the atomic reader pointer;
    the legacy directory swap uses rollback because Windows cannot atomically
    replace a populated directory. The research engine owns ``latest.json``.
    """
    project = Path(project_dir).resolve()
    outputs_dir = project / "outputs"
    if os.path.lexists(outputs_dir) and _is_link(outputs_dir):
        raise RuntimeError("The outputs directory cannot be a symbolic link or junction")
    ensure_dir(outputs_dir)
    runs_dir = outputs_dir / "runs"
    source = Path(run_dir)
    if (not source.is_dir() or _is_link(source) or not runs_dir.is_dir() or _is_link(runs_dir)
            or source.resolve().parent != runs_dir.resolve()):
        raise RuntimeError("Latest publication requires an ordinary run directly under this project's outputs/runs")
    source = source.resolve()
    latest = outputs_dir / "latest"
    token = uuid.uuid4().hex
    staging = outputs_dir / f".latest-staging-{token}"
    backup = outputs_dir / f".latest-previous-{token}"
    moved_previous = False
    installed = False
    committed = False
    rollback_failed = False
    with _legacy_publication_lock(outputs_dir / ".latest_legacy.lock"):
        if os.path.lexists(latest):
            _tree_hashes(latest)
        expected = _tree_hashes(source)
        try:
            shutil.copytree(source, staging)
            if _tree_hashes(staging) != expected or _tree_hashes(source) != expected:
                raise RuntimeError("Run contents changed during latest publication")
            if latest.exists():
                os.replace(latest, backup)
                moved_previous = True
            os.replace(staging, latest)
            installed = True
            manifest = source / "run_manifest.json"
            write_json(outputs_dir / "latest_legacy.json", {
                "schema_version": 1,
                "run_name": source.name,
                "manifest_sha256": sha256_file(manifest) if manifest.is_file() else None,
                "content_fingerprint": sha256_text(json.dumps(expected, sort_keys=True)),
                "publication_token": token,
            })
            committed = True
        except BaseException as original:
            try:
                if installed and latest.exists():
                    os.replace(latest, staging)
                if moved_previous and backup.exists():
                    os.replace(backup, latest)
            except OSError as rollback_error:
                rollback_failed = True
                raise RuntimeError(f"Publication failed and rollback could not finish; previous data is preserved at {backup}") from rollback_error
            raise original
        finally:
            if not rollback_failed:
                with suppress(OSError):
                    _remove_publication_directory(staging, outputs_dir, staging.name)
                if committed:
                    with suppress(OSError):
                        _remove_publication_directory(backup, outputs_dir, backup.name)
    return latest
