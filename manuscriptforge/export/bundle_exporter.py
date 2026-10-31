from __future__ import annotations

import hashlib
import os
import stat
import tempfile
import zipfile
from pathlib import Path

from manuscriptforge.utils.hashing import sha256_file


def _is_link(path: Path) -> bool:
    value = path.lstat()
    return path.is_symlink() or bool(
        getattr(value, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    )


def _bundle_sources(run_dir: Path, bundle_path: Path) -> list[Path]:
    files: list[Path] = []
    pending = [run_dir]
    while pending:
        for path in sorted(pending.pop().iterdir()):
            if _is_link(path):
                raise ValueError(f"Refusing to include a linked file or junction: {path}")
            if path.is_dir():
                pending.append(path)
            elif (path.is_file() and path.resolve() != bundle_path.resolve()
                  and path.suffix.lower() != ".zip"
                  and not path.name.startswith(".manuscriptforge-bundle-")):
                files.append(path)
    return sorted(files)


def export_bundle(run_dir: Path, bundle_path: Path | None = None) -> Path:
    """Atomically publish a verified ZIP; prior ZIPs are never recursively bundled.

    The caller finalizes run_manifest.json before invoking this function. ZIP
    files are excluded from both the workflow hash inventory and this archive.
    """
    run_dir = Path(run_dir)
    bundle_path = bundle_path or run_dir / "manuscriptforge_bundle.zip"
    bundle_path.parent.mkdir(parents=True, exist_ok=True)
    if _is_link(run_dir) or not run_dir.is_dir():
        raise ValueError("The bundle source must be an ordinary directory")
    sources = _bundle_sources(run_dir, bundle_path)
    expected = {path.relative_to(run_dir).as_posix(): sha256_file(path) for path in sources}
    descriptor, temporary_name = tempfile.mkstemp(prefix=".manuscriptforge-bundle-", suffix=".tmp", dir=bundle_path.parent)
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sources:
                archive.write(path, path.relative_to(run_dir).as_posix())
        with zipfile.ZipFile(temporary) as archive:
            if archive.testzip() is not None or set(archive.namelist()) != set(expected):
                raise ValueError("Bundle verification failed")
            for name, digest in expected.items():
                archived_digest = hashlib.sha256()
                with archive.open(name) as member:
                    for block in iter(lambda: member.read(1024 * 1024), b""):
                        archived_digest.update(block)
                if archived_digest.hexdigest() != digest or sha256_file(run_dir / name) != digest:
                    raise ValueError(f"Bundle source changed during export: {name}")
        if {path.relative_to(run_dir).as_posix() for path in _bundle_sources(run_dir, bundle_path)} != set(expected):
            raise ValueError("Bundle source inventory changed during export")
        with temporary.open("r+b") as handle:
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, bundle_path)
    finally:
        if temporary.exists():
            temporary.unlink()
    return bundle_path
