"""Link/reparse boundaries for standalone release scripts."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from zipfile import ZipFile

import pytest


def script(name):
    path = Path(__file__).resolve().parents[1] / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"release_{name}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def entry(name, payload):
    return {"path": name, "bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}


def benchmark(tmp_path):
    root = tmp_path / "benchmark"
    root.mkdir()
    text = b"A synthetic benchmark artifact.\n"
    (root / "hello.txt").write_bytes(text)
    archived = b"Original synthetic result.\n"
    with ZipFile(root / "run.zip", "w") as archive:
        archive.writestr("result.txt", archived)
        archive.writestr(
            "ARCHIVE_CONTENTS.json", json.dumps({"files": [entry("result.txt", archived)]})
        )
    (root / "manifest.json").write_text(
        json.dumps(
            {"files": [entry("hello.txt", text), entry("run.zip", (root / "run.zip").read_bytes())]}
        ),
        encoding="utf-8",
    )
    (root / "assets").mkdir()
    (root / "verification.json").write_text("{}", encoding="utf-8")
    return root


def site(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "README.md").write_text("# Example\n\n[Config](pyproject.toml)\n", encoding="utf-8")
    (root / "docs" / "assets").mkdir(parents=True)
    (root / "docs" / "intro.md").write_text("# Introduction\n", encoding="utf-8")
    (root / "docs" / "assets" / "logo.svg").write_text("<svg/>", encoding="utf-8")
    destination = root / "build" / "site-source"
    destination.mkdir(parents=True)
    (destination / "sentinel.txt").write_text("Preserve until preflight succeeds", encoding="utf-8")
    return root


def mark_reparse(monkeypatch, path):
    """Simulate the Windows lstat flag without requiring junction privileges."""
    flagged = path.absolute()
    original = Path.lstat

    def lstat(self, *args, **kwargs):
        result = original(self, *args, **kwargs)
        if self.absolute() == flagged:
            return SimpleNamespace(st_mode=result.st_mode, st_file_attributes=0x400)
        return result

    monkeypatch.setattr(Path, "lstat", lstat)


def link_or_skip(path, target, *, directory=False):
    try:
        path.symlink_to(target, target_is_directory=directory)
    except (OSError, NotImplementedError):
        pytest.skip("This host does not grant symlink creation")


def test_verifier_accepts_plain_files_and_preserves_integrity_checks(tmp_path):
    root = benchmark(tmp_path)
    verify = script("verify_benchmarks").verify
    assert verify(root) == {"payload_files": 2, "archives": 1, "original_members": 1}
    (root / "hello.txt").write_bytes(b"changed")
    with pytest.raises(ValueError, match="hash/size"):
        verify(root)


@pytest.mark.parametrize(
    "relative", [".", "..", "manifest.json", "assets", "hello.txt", "verification.json"]
)
def test_verifier_rejects_reparse_root_ancestors_and_every_descendant(
    tmp_path, monkeypatch, relative
):
    root = benchmark(tmp_path)
    flagged = root.parent if relative == ".." else root / relative
    mark_reparse(monkeypatch, flagged)
    with pytest.raises(ValueError, match="Linked"):
        script("verify_benchmarks").verify(root)


def test_verifier_rejects_linked_directory_before_descending(tmp_path, monkeypatch):
    root = benchmark(tmp_path)
    flagged = root / "assets"
    mark_reparse(monkeypatch, flagged)
    original = Path.iterdir

    def iterdir(self):
        assert self.absolute() != flagged.absolute(), "Must not traverse a linked directory"
        return original(self)

    monkeypatch.setattr(Path, "iterdir", iterdir)
    with pytest.raises(ValueError, match="Linked"):
        script("verify_benchmarks").verify(root)


def test_verifier_rejects_real_linked_root_before_resolving(tmp_path):
    root = benchmark(tmp_path)
    alias = tmp_path / "alias"
    link_or_skip(alias, root, directory=True)
    with pytest.raises(ValueError, match="Linked"):
        script("verify_benchmarks").verify(alias)


def test_verifier_rejects_real_linked_manifest(tmp_path):
    root = benchmark(tmp_path)
    manifest = root / "manifest.json"
    outside = tmp_path / "original-manifest.json"
    outside.write_bytes(manifest.read_bytes())
    manifest.unlink()
    link_or_skip(manifest, outside)
    with pytest.raises(ValueError, match="Linked"):
        script("verify_benchmarks").verify(root)


def test_site_plain_inputs_are_copied_and_previous_output_replaced(tmp_path, monkeypatch):
    root = site(tmp_path)
    module = script("prepare_site")
    monkeypatch.setattr(module, "__file__", str(root / "scripts" / "prepare_site.py"))
    module.main()
    destination = root / "build" / "site-source"
    assert not (destination / "sentinel.txt").exists()
    assert "github.com" in (destination / "index.md").read_text(encoding="utf-8")
    assert (destination / "docs" / "intro.md").read_text(encoding="utf-8") == "# Introduction\n"
    assert (destination / "docs" / "assets" / "logo.svg").read_text(encoding="utf-8") == "<svg/>"


@pytest.mark.parametrize(
    "relative",
    [
        ".",
        "build",
        "build/site-source",
        "build/site-source/sentinel.txt",
        "README.md",
        "docs",
        "docs/intro.md",
        "docs/assets",
        "docs/assets/logo.svg",
    ],
)
def test_site_reparse_preflight_preserves_existing_output(tmp_path, monkeypatch, relative):
    root = site(tmp_path)
    module = script("prepare_site")
    monkeypatch.setattr(module, "__file__", str(root / "scripts" / "prepare_site.py"))
    mark_reparse(monkeypatch, root / relative)
    with pytest.raises(ValueError, match="links or reparse"):
        module.main()
    assert (root / "build/site-source/sentinel.txt").exists()


def test_site_does_not_follow_real_linked_source_file(tmp_path, monkeypatch):
    root = site(tmp_path)
    secret = tmp_path / "outside.md"
    secret.write_text("Outside source must not be published", encoding="utf-8")
    link_or_skip(root / "docs" / "outside.md", secret)
    module = script("prepare_site")
    monkeypatch.setattr(module, "__file__", str(root / "scripts" / "prepare_site.py"))
    with pytest.raises(ValueError, match="links or reparse"):
        module.main()
    assert (root / "build/site-source/sentinel.txt").exists()


def test_site_rejects_assets_before_traversal(tmp_path, monkeypatch):
    root = site(tmp_path)
    flagged = root / "docs/assets"
    mark_reparse(monkeypatch, flagged)
    original = Path.iterdir

    def iterdir(self):
        assert self.absolute() != flagged.absolute(), "Must not traverse linked assets"
        return original(self)

    monkeypatch.setattr(Path, "iterdir", iterdir)
    module = script("prepare_site")
    monkeypatch.setattr(module, "__file__", str(root / "scripts" / "prepare_site.py"))
    with pytest.raises(ValueError, match="links or reparse"):
        module.main()
    assert (root / "build/site-source/sentinel.txt").exists()
