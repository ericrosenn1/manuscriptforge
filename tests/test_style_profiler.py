from manuscriptforge.style.preference_trainer import generate_style_questions
from manuscriptforge.style.profiler import build_style_profile
from manuscriptforge.style.style_guide import render_style_guide


def test_style_profiler_computes_basic_metrics(tmp_path, synthetic_project) -> None:
    profile = build_style_profile(synthetic_project, tmp_path)
    assert profile.global_features["word_count"] > 0
    assert profile.sentence_length_distribution["count"] > 0
    assert "passive_voice_ratio" in profile.global_features
    assert profile.global_features["limitation_phrases"]
    assert (tmp_path / "style_profile.json").exists()
    assert (tmp_path / "style_guide.md").exists()


def test_style_guide_uses_first_person_usage_and_clear_phrase_labels(tmp_path, synthetic_project) -> None:
    profile = build_style_profile(synthetic_project)
    profile.global_features["first_person_usage"] = 3
    profile.preferred_phrases = []
    guide = render_style_guide(profile)
    assert "- First-person pronouns: 3" in guide
    assert "## Common Three-Word Sequences" in guide
    assert "- No recurring three-word sequences detected." in guide
    assert "## Default Phrases to Review" in guide


def test_style_questions_include_corpus_aware_items(synthetic_project) -> None:
    profile = build_style_profile(synthetic_project)
    questions = generate_style_questions(profile)
    assert len(questions) >= 6
    assert all("question_id" in question for question in questions)
