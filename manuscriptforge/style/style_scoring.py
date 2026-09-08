from __future__ import annotations

from manuscriptforge.models.style import StyleProfile
from manuscriptforge.utils.text import distribution, split_sentences, word_tokens


def score_style_deviation(text: str, profile: StyleProfile) -> dict[str, object]:
    manuscript_lengths = [len(word_tokens(sentence)) for sentence in split_sentences(text)]
    manuscript_distribution = distribution(manuscript_lengths)
    target_mean = profile.sentence_length_distribution.get("mean")
    observed_mean = manuscript_distribution.get("mean")
    if isinstance(target_mean, (int, float)) and isinstance(observed_mean, (int, float)):
        delta = round(float(observed_mean) - float(target_mean), 2)
    else:
        delta = None
    severity = "info"
    if delta is not None and abs(delta) > 12:
        severity = "warning"
    return {
        "manuscript_sentence_lengths": manuscript_distribution,
        "target_sentence_lengths": profile.sentence_length_distribution,
        "mean_sentence_delta": delta,
        "severity": severity,
    }
