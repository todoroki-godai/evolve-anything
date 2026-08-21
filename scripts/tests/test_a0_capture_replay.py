import hashlib

import capture_recall
from capture_recall import evaluate_capture_recall, load_capture_eval_set, wilson_interval
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


@pytest.mark.parametrize(
    "row",
    [
        {"text": "example", "label": "typo"},
        {"text": "example"},
        {"text": None, "label": "TP"},
        {"label": "TP"},
    ],
)
def test_invalid_corpus_rows_are_rejected(row):
    with pytest.raises(ValueError):
        evaluate_capture_recall([row], lambda _text: None)


def test_load_capture_eval_set_validates_hash_and_row_count(monkeypatch, tmp_path):
    path = tmp_path / "eval.jsonl"
    raw = b'{"text":"example","label":"TP"}\n'
    path.write_bytes(raw)
    monkeypatch.setattr(capture_recall, "EXPECTED_EVAL_SHA256", hashlib.sha256(raw).hexdigest())
    monkeypatch.setattr(capture_recall, "EXPECTED_EVAL_ROWS", 1)
    assert load_capture_eval_set(path) == [{"text": "example", "label": "TP"}]
    path.write_bytes(raw + raw)
    with pytest.raises(ValueError, match="hash mismatch"):
        load_capture_eval_set(path)


def test_load_capture_eval_set_rejects_wrong_row_count(monkeypatch, tmp_path):
    path = tmp_path / "eval.jsonl"
    raw = b'{"text":"example","label":"TP"}\n'
    path.write_bytes(raw)
    monkeypatch.setattr(capture_recall, "EXPECTED_EVAL_SHA256", hashlib.sha256(raw).hexdigest())
    monkeypatch.setattr(capture_recall, "EXPECTED_EVAL_ROWS", 2)
    with pytest.raises(ValueError, match="row count mismatch"):
        load_capture_eval_set(path)
