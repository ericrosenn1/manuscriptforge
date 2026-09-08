from manuscriptforge.style.preference_trainer import generate_style_questions
from manuscriptforge.style.profiler import build_style_profile


def test_style_profiler_computes_basic_metrics(tmp_path, synthetic_project) -> None:
    profile = build_style_profile(synthetic_project, tmp_path)
    assert profile.global_features["word_count"] > 0
    assert profile.sentence_length_distribution["count"] > 0
    assert "passive_voice_ratio" in profile.global_features
    assert profile.global_features["limitation_phrases"]
    assert (tmp_path / "style_profile.json").exists()
    assert (tmp_path / "style_guide.md").exists()


def test_style_questions_include_corpus_aware_items(synthetic_project) -> None:
    profile = build_style_profile(synthetic_project)
    questions = generate_style_questions(profile)
    assert len(questions) >= 6
    assert all("question_id" in question for question in questions)
