import pytest

from manuscriptforge.config import load_project_config, write_project_config
from manuscriptforge.models.style import StyleProfile
from manuscriptforge.style.curation import build_style_chunk_registry, update_style_chunk_approval
from manuscriptforge.style.profiler import build_style_profile
from manuscriptforge.style.retrieval import get_style_exemplars


def test_profile_permission_does_not_imply_exemplar_permission(synthetic_project):
    config = load_project_config(synthetic_project)
    config.style.curation.require_chunk_approval = True
    write_project_config(synthetic_project, config)
    registry = build_style_chunk_registry(synthetic_project)
    chunk = next(row for row in registry.rows if row.section_type == "methods")
    update_style_chunk_approval(
        synthetic_project, chunk_id=chunk.chunk_id, approve=True, approve_for="style_profile"
    )
    profile = build_style_profile(synthetic_project)
    assert profile.global_features["style_chunks"]
    assert get_style_exemplars(profile, "Methods", "procedure", project_dir=synthetic_project) == []
    assert get_style_exemplars(profile, "Methods", "procedure") == []
    update_style_chunk_approval(
        synthetic_project, chunk_id=chunk.chunk_id, approve=True, approve_for="exemplar_retrieval"
    )
    assert get_style_exemplars(profile, "Methods", "procedure", project_dir=synthetic_project)
    update_style_chunk_approval(synthetic_project, chunk_id=chunk.chunk_id, exclude=True)
    assert get_style_exemplars(profile, "Methods", "procedure", project_dir=synthetic_project) == []


def test_retrieval_rejects_negative_limits_and_explicit_foreign_modes():
    profile = StyleProfile(
        style_mode="academic_manuscript",
        global_features={
            "style_chunks": [
                {
                    "chunk_id": "foreign",
                    "source_file": "coursework.md",
                    "text": "A familiar method.",
                    "section_type": "methods",
                    "style_mode": "coursework_explanatory",
                },
                {
                    "chunk_id": "legacy",
                    "source_file": "unknown.md",
                    "text": "A documented method.",
                    "section_type": "methods",
                    "style_mode": "unknown",
                },
            ]
        },
    )
    assert [item["chunk_id"] for item in get_style_exemplars(profile, "Methods", "method")] == [
        "legacy"
    ]
    assert not get_style_exemplars(profile, "Methods", "method", max_examples=0)
    with pytest.raises(ValueError, match="nonnegative"):
        get_style_exemplars(profile, "Methods", "method", max_examples=-1)
