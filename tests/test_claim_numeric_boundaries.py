import pytest

from manuscriptforge.evidence.claim_extractor import _row_claim_text, _threshold_for_column


@pytest.mark.parametrize(
    "thresholds", [{"p_value": 0.0}, {"custom_p": 0.0, "p_value": 0.05}, {"adjusted_p_value": 0.0}]
)
def test_zero_threshold_is_preserved(thresholds):
    assert _threshold_for_column("custom_p", thresholds) == 0.0


def test_row_comparison_preserves_equality_and_handles_reverse_column_order():
    row = {"nonresponse_mean": 4.0, "response_mean": 4.0, "p_value": 0.01}
    text = _row_claim_text("Table A", row, list(row), thresholds={"p_value": 0.0})
    assert "response_mean was equal to nonresponse_mean" in text
    assert "lower than" not in text
    assert "not meeting the <= 0.0 threshold" in text


@pytest.mark.parametrize("value,phrase", [(5, "higher than"), (3, "lower than")])
def test_unequal_comparison_direction_is_unchanged(value, phrase):
    row = {"response_mean": value, "nonresponse_mean": 4.0}
    assert f"response_mean was {phrase} nonresponse_mean" in _row_claim_text(
        "Table A", row, list(row)
    )
