"""Demo provenance and idempotence, without paid APIs or network access."""

import json

import pytest

from manuscriptforge.research.demo import MARKER, prepare_research_demo


def test_demo_is_original_valid_and_idempotent(tmp_path):
    root = prepare_research_demo(tmp_path / "demo")
    original = (root / MARKER).read_bytes()
    assert prepare_research_demo(root) == root
    assert (root / MARKER).read_bytes() == original
    manifest = json.loads(original)
    assert manifest["synthetic"] is True
    assert "inputs/source_data/toy_records.csv" in manifest["inputs"]
    (root / "outputs").mkdir(exist_ok=True)
    (root / "outputs" / "new-result.txt").write_text("generated", encoding="utf-8")
    assert prepare_research_demo(root) == root


@pytest.mark.parametrize("mutation", ["edit", "add", "remove"])
def test_demo_preserves_changed_inputs_and_refuses_reinitialization(tmp_path, mutation):
    root = prepare_research_demo(tmp_path / "demo")
    target = root / "inputs" / "methods.md"
    if mutation == "edit":
        target.write_text("User research", encoding="utf-8")
    elif mutation == "add":
        target = root / "inputs" / "additional.md"
        target.write_text("User research", encoding="utf-8")
    else:
        target.unlink()
    before = target.read_bytes() if target.exists() else None
    with pytest.raises(ValueError, match="added, removed, or edited"):
        prepare_research_demo(root)
    assert (target.read_bytes() if target.exists() else None) == before


def test_demo_does_not_replace_unrelated_work(tmp_path):
    (tmp_path / "existing.txt").write_text("keep", encoding="utf-8")
    with pytest.raises(FileExistsError):
        prepare_research_demo(tmp_path)
    assert (tmp_path / "existing.txt").read_text(encoding="utf-8") == "keep"
