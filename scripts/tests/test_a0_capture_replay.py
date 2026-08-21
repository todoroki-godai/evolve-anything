from capture_recall import evaluate_capture_recall, wilson_interval
import pytest


def test_evaluate_capture_recall_counts_recall_precision_and_ci():
    rows = [
        {"text": "caught", "label": "TP"},
        {"text": "missed", "label": "TP"},
        {"text": "false-positive", "label": "not_TP"},
        {"text": "negative", "label": "not_TP"},
    ]
    result = evaluate_capture_recall(
        rows, lambda text: "hit" if text in {"caught", "false-positive"} else None
    )
    assert result["positives"] == 2
    assert result["caught"] == 1
    assert result["hits"] == 2
    assert result["recall"] == 0.5
    assert result["precision"] == 0.5
    assert result["recall_ci"] == wilson_interval(1, 2)


def test_include_message_filters_hits_but_not_ground_truth_denominator():
    rows = [{"text": "excluded", "label": "TP"}, {"text": "included", "label": "TP"}]
    result = evaluate_capture_recall(
        rows, lambda _text: True, include_message=lambda text: text == "included"
    )
    assert result["caught"] == 1
    assert result["positives"] == 2
    assert result["recall"] == 0.5


def test_empty_denominators_are_explicit():
    result = evaluate_capture_recall([], lambda _text: False)
    assert result["recall"] is None
    assert result["precision"] is None
    assert result["recall_ci"] == (0.0, 0.0)
    assert result["precision_ci"] == (0.0, 0.0)


def test_wilson_interval_matches_known_value():
    low, high = wilson_interval(21, 47)
    assert low == pytest.approx(0.314, abs=0.001)
    assert high == pytest.approx(0.588, abs=0.001)
