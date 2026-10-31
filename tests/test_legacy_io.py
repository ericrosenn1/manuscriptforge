from __future__ import annotations

import hashlib
import json
import shutil
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
import yaml

from manuscriptforge.config import load_project_config
from manuscriptforge.export import bundle_exporter
from manuscriptforge.pipeline.workflow import (
    run_audit,
    run_draft,
    run_export,
    run_reviewer_sim,
    write_manifest,
)
from manuscriptforge.utils import io
from manuscriptforge.utils.hashing import sha256_file


def make_run(project: Path, text: str = "complete") -> Path:
    run = io.create_run_dir(project)
    io.write_text(run / "value.txt", text)
    io.write_text(run / "nested/second.txt", text)
    return run


def test_run_allocation_is_atomic_under_same_timestamp(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(io, "run_timestamp", lambda: "20261031_120000")
    with ThreadPoolExecutor(max_workers=12) as pool:
        paths = list(pool.map(lambda _: io.create_run_dir(tmp_path), range(64)))
    assert len(set(paths)) == 64
    assert all((path / "intermediate").is_dir() and (path / "logs").is_dir() for path in paths)
    assert len(list((tmp_path / "outputs/runs").iterdir())) == 64
    assert io.latest_run_dir(tmp_path).name == "20261031_120000_64"


@pytest.mark.parametrize(
    "writer,data",
    [
        (io.write_json, {"bad": object()}),
        (io.write_jsonl, [{"first": 1}, {"bad": object()}]),
        (io.write_yaml, {"bad": object()}),
    ],
)
def test_serialization_failure_preserves_existing_file(tmp_path, writer, data) -> None:
    path = tmp_path / "state.txt"
    path.write_text("previous complete value")
    with pytest.raises((TypeError, yaml.YAMLError)):
        writer(path, data)
    assert path.read_text() == "previous complete value"
    assert list(tmp_path.iterdir()) == [path]


def test_fsync_failure_does_not_publish_partial_text(tmp_path, monkeypatch) -> None:
    path = tmp_path / "state.txt"
    path.write_text("previous")

    def fail(_descriptor):
        raise OSError("simulated fsync failure")

    monkeypatch.setattr(io.os, "fsync", fail)
    with pytest.raises(OSError, match="fsync failure"):
        io.write_text(path, "new content")
    assert path.read_text() == "previous"
    assert list(tmp_path.iterdir()) == [path]


def test_successful_atomic_formats_remain_readable(tmp_path) -> None:
    io.write_text(tmp_path / "a.txt", "Unicode α\ntext")
    io.write_json(tmp_path / "a.json", {"a": "α", "b": [1, 2]})
    io.write_jsonl(tmp_path / "a.jsonl", [{"a": 1}, {"b": 2}])
    io.write_yaml(tmp_path / "a.yaml", {"a": [1, 2]})
    assert io.read_text(tmp_path / "a.txt") == "Unicode α\ntext"
    assert io.read_json(tmp_path / "a.json")["a"] == "α"
    assert [json.loads(line) for line in (tmp_path / "a.jsonl").read_text().splitlines()] == [
        {"a": 1},
        {"b": 2},
    ]
    assert io.read_yaml(tmp_path / "a.yaml") == {"a": [1, 2]}


def test_concurrent_publications_do_not_mix_runs_or_touch_engine_pointer(tmp_path) -> None:
    runs = [make_run(tmp_path, str(index)) for index in range(8)]
    engine_pointer = tmp_path / "outputs/latest.json"
    engine_pointer.write_text('{"engine":"preserve"}')
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda run: io.update_latest_copy(tmp_path, run), runs))
    latest = tmp_path / "outputs/latest"
    assert (latest / "value.txt").read_text() == (latest / "nested/second.txt").read_text()
    selected = io.latest_run_dir(tmp_path)
    assert (selected / "value.txt").read_text() == (latest / "value.txt").read_text()
    assert engine_pointer.read_text() == '{"engine":"preserve"}'
    assert not list((tmp_path / "outputs").glob(".latest-staging-*"))
    assert not list((tmp_path / "outputs").glob(".latest-previous-*"))


def test_failed_directory_promotion_rolls_back_previous_latest(tmp_path, monkeypatch) -> None:
    previous = make_run(tmp_path, "previous")
    io.update_latest_copy(tmp_path, previous)
    new = make_run(tmp_path, "new")
    original_replace = io.os.replace

    def failed_replace(source, destination):
        if Path(source).name.startswith(".latest-staging-") and Path(destination).name == "latest":
            raise OSError("simulated promotion failure")
        return original_replace(source, destination)

    monkeypatch.setattr(io.os, "replace", failed_replace)
    with pytest.raises(OSError, match="promotion failure"):
        io.update_latest_copy(tmp_path, new)
    assert (tmp_path / "outputs/latest/value.txt").read_text() == "previous"
    assert not list((tmp_path / "outputs").glob(".latest-previous-*"))


def test_pointer_failure_rolls_back_directory_and_preserves_old_pointer(
    tmp_path, monkeypatch
) -> None:
    previous = make_run(tmp_path, "previous")
    io.update_latest_copy(tmp_path, previous)
    pointer = (tmp_path / "outputs/latest_legacy.json").read_bytes()
    new = make_run(tmp_path, "new")
    original_write = io.write_json

    def fail_pointer(path, data):
        if path.name == "latest_legacy.json":
            raise OSError("simulated pointer failure")
        return original_write(path, data)

    monkeypatch.setattr(io, "write_json", fail_pointer)
    with pytest.raises(OSError, match="pointer failure"):
        io.update_latest_copy(tmp_path, new)
    assert (tmp_path / "outputs/latest/value.txt").read_text() == "previous"
    assert (tmp_path / "outputs/latest_legacy.json").read_bytes() == pointer


def test_publication_rejects_outside_run_and_preserves_unexpected_latest_file(tmp_path) -> None:
    project = tmp_path / "project"
    run = make_run(project)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("preserve")
    with pytest.raises(RuntimeError, match="ordinary run"):
        io.update_latest_copy(project, outside)
    latest = project / "outputs/latest"
    latest.write_text("preserve unexpected file")
    with pytest.raises(RuntimeError, match="ordinary directory"):
        io.update_latest_copy(project, run)
    assert latest.read_text() == "preserve unexpected file"
    assert (outside / "keep.txt").read_text() == "preserve"


def test_manifest_hashes_curation_and_preserves_original_input_lineage(tmp_path) -> None:
    project = tmp_path / "project"
    shutil.copytree(Path("tests/fixtures/synthetic_project"), project)
    authorities = [
        "style_memory.json",
        "style_preferences.jsonl",
        "style_feedback.jsonl",
        "planning/intake/writing_samples_registry.csv",
        "planning/intake/style_chunks/style_chunks_registry.jsonl",
    ]
    for name in authorities:
        path = project / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("first decision")
    run = make_run(project)
    config = load_project_config(project)
    write_manifest(project, run, "draft", config)
    first = io.read_json(run / "run_manifest.json")
    assert all(first["input_hashes"][name] == sha256_file(project / name) for name in authorities)
    (project / authorities[-1]).write_text("later decision")
    write_manifest(project, run, "audit", config)
    second = io.read_json(run / "run_manifest.json")
    assert first["input_hashes"] == second["input_hashes"]
    assert (
        second["current_project_file_hashes"][authorities[-1]]
        != first["input_hashes"][authorities[-1]]
    )
    assert second["created_utc"] == first["created_utc"]
    assert second["initial_command"] == "draft"
    assert [item["command"] for item in second["operation_history"]] == ["draft", "audit"]


def test_failed_copy_keeps_previous_latest_and_pointer(tmp_path, monkeypatch) -> None:
    previous = make_run(tmp_path, "previous")
    io.update_latest_copy(tmp_path, previous)
    pointer = (tmp_path / "outputs/latest_legacy.json").read_bytes()
    new = make_run(tmp_path, "new")

    def failed_copy(source, destination):
        destination.mkdir()
        (destination / "partial.txt").write_text("incomplete")
        raise OSError("simulated copy failure")

    monkeypatch.setattr(io.shutil, "copytree", failed_copy)
    with pytest.raises(OSError, match="copy failure"):
        io.update_latest_copy(tmp_path, new)
    assert (tmp_path / "outputs/latest/value.txt").read_text() == "previous"
    assert (tmp_path / "outputs/latest_legacy.json").read_bytes() == pointer
    assert (new / "value.txt").read_text() == "new"
    assert not list((tmp_path / "outputs").glob(".latest-staging-*"))


def test_same_run_operations_finalize_matching_manifest_inside_bundle(tmp_path) -> None:
    project = tmp_path / "project"
    shutil.copytree(Path("tests/fixtures/synthetic_project"), project)
    run = run_draft(project)
    created = io.read_json(run / "run_manifest.json")["created_utc"]
    for command in (None, run_audit, run_reviewer_sim, run_export):
        if command is not None:
            assert command(project) == run
        manifest_path = run / "run_manifest.json"
        manifest = io.read_json(manifest_path)
        assert manifest["created_utc"] == created
        assert "*.zip" in manifest["hash_policy"]["excluded_outputs"]
        with zipfile.ZipFile(run / "manuscriptforge_bundle.zip") as archive:
            assert archive.testzip() is None
            assert archive.read("run_manifest.json") == manifest_path.read_bytes()
            assert not any(name.lower().endswith(".zip") for name in archive.namelist())
            assert set(archive.namelist()) == set(manifest["files"]) | {"run_manifest.json"}
            for name, digest in manifest["output_hashes"].items():
                assert hashlib.sha256(archive.read(name)).hexdigest() == digest
    assert [item["command"] for item in manifest["operation_history"]] == [
        "draft",
        "audit",
        "reviewer-sim",
        "export",
    ]


def test_bundle_failure_preserves_existing_archive(tmp_path, monkeypatch) -> None:
    (tmp_path / "value.txt").write_text("original")
    path = bundle_exporter.export_bundle(tmp_path)
    previous = path.read_bytes()

    def fail_write(*_args, **_kwargs):
        raise OSError("simulated archive failure")

    monkeypatch.setattr(zipfile.ZipFile, "write", fail_write)
    with pytest.raises(OSError, match="archive failure"):
        bundle_exporter.export_bundle(tmp_path)
    assert path.read_bytes() == previous
    assert not list(tmp_path.glob(".manuscriptforge-bundle-*"))


def test_bundle_rejects_changed_sources_and_never_embeds_previous_zips(
    tmp_path, monkeypatch
) -> None:
    source = tmp_path / "value.txt"
    source.write_text("original")
    (tmp_path / "earlier.zip").write_bytes(b"not an artifact to recursively embed")
    bundle = bundle_exporter.export_bundle(tmp_path)
    with zipfile.ZipFile(bundle) as archive:
        assert archive.namelist() == ["value.txt"]
    previous = bundle.read_bytes()
    original_write = zipfile.ZipFile.write

    def changed_write(self, filename, arcname=None, **kwargs):
        result = original_write(self, filename, arcname, **kwargs)
        source.write_text("changed while building")
        return result

    monkeypatch.setattr(zipfile.ZipFile, "write", changed_write)
    with pytest.raises(ValueError, match="changed during export"):
        bundle_exporter.export_bundle(tmp_path)
    assert bundle.read_bytes() == previous
