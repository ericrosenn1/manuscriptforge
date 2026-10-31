"""Rehash benchmark payloads and their original, unextracted ZIP members."""

from __future__ import annotations

import argparse
import hashlib
import json
import stat
from pathlib import Path, PurePosixPath
from zipfile import ZipFile


def reject_linked_path(path: Path) -> None:
    """Inspect lexical ancestors before resolving or following a reparse point."""
    absolute = path.absolute()
    for current in (absolute, *absolute.parents):
        try:
            info = current.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & getattr(
            stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400
        ):
            raise ValueError("Linked benchmark artifacts are not allowed")


def safe_name(value: str) -> str:
    path = PurePosixPath(value)
    if not value or path.is_absolute() or "\\" in value or ":" in value or ".." in path.parts:
        raise ValueError(f"Unsafe artifact path: {value!r}")
    return path.as_posix()


def check_bytes(payload: bytes, entry: dict) -> None:
    if len(payload) != entry["bytes"] or hashlib.sha256(payload).hexdigest() != entry["sha256"]:
        raise ValueError(f"Artifact hash/size mismatch: {entry['path']}")


def verify(root: Path) -> dict[str, int]:
    reject_linked_path(root)
    root = root.resolve(strict=True)
    manifest_path = root / "manifest.json"
    reject_linked_path(manifest_path)
    manifest = json.loads(manifest_path.read_bytes())
    expected = {safe_name(entry["path"]): entry for entry in manifest["files"]}
    if len(expected) != len(manifest["files"]):
        raise ValueError("Duplicate entries in benchmark manifest")
    excluded = {"manifest.json", "MANIFEST.tsv", "verification.json"}
    actual = set()
    pending = [root]
    while pending:
        for path in pending.pop().iterdir():
            reject_linked_path(path)
            if not path.resolve().is_relative_to(root):
                raise ValueError("Benchmark artifacts must stay inside the selected root")
            if path.is_dir():
                pending.append(path)
            elif path.is_file() and path.relative_to(root).as_posix() not in excluded:
                actual.add(path.relative_to(root).as_posix())
    if actual != expected.keys():
        raise ValueError("Benchmark manifest does not cover the exact payload inventory")
    archives = members = 0
    for name, entry in expected.items():
        path = root / name
        reject_linked_path(path)
        check_bytes(path.read_bytes(), entry)
        if path.suffix != ".zip":
            continue
        archives += 1
        with ZipFile(path) as archive:
            names = archive.namelist()
            if len(names) > 5000 or len(names) != len(set(names)):
                raise ValueError("Excessive or duplicate ZIP entries")
            if sum(item.file_size for item in archive.infolist()) > 256 * 1024 * 1024:
                raise ValueError("Benchmark archive exceeds the verification memory limit")
            if archive.testzip() is not None:
                raise ValueError("ZIP CRC verification failed")
            for item in names:
                safe_name(item)
            inventory = json.loads(archive.read("ARCHIVE_CONTENTS.json"))["files"]
            original = {safe_name(item["path"]): item for item in inventory}
            if len(original) != len(inventory) or set(original) != set(names) - {
                "ARCHIVE_CONTENTS.json"
            }:
                raise ValueError("ZIP original-member inventory mismatch")
            for member, record in original.items():
                check_bytes(archive.read(member), record)
                members += 1
    return {"payload_files": len(expected), "archives": archives, "original_members": members}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    print(json.dumps(verify(args.directory), sort_keys=True))


if __name__ == "__main__":
    main()
