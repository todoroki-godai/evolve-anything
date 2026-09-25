"""#679 advisory: only known reconciliation conditions are detected."""
import copy
import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "bench"))
import capture_recall
from capture_recall import evaluate_capture_union


def fixture():
    """Synthetic utterances only; no frozen corpus text."""
    rows = [
        {"eval_id": "a", "text": "ここを直して", "label": "TP"},
        {"eval_id": "b", "text": "仕様が違います", "label": "TP"},
        {"eval_id": "c", "text": "ありがとう", "label": "not_TP"},
    ]
    results = []
    for row, predicted in zip(rows, (False, True, False)):
        results.append({
            "prompt_id": row["eval_id"], "status": "ok",
            "meta": {"rep": 0, "expected": row["label"] == "TP", "predicted": predicted,
                     "prompt_sha256": hashlib.sha256(row["text"].encode()).hexdigest(),
                     "harness_sha": "version", "model": "haiku", "batch_size_config": 30,
                     "generated_at": "2026-09-25T00:00:00+00:00"},
        })
    return rows, results


def measure(rows, results):
    return evaluate_capture_union(rows, results, "version", "haiku", 30)


def test_advisory_union_valid_positive_control():
    rows, results = fixture()
    out = measure(rows, results)
    assert out["measured"] is True
    assert (out["caught"], out["positives"], out["regex_caught"], out["judge_caught"]) == (2, 2, 1, 1)
    assert out["precision"] == 1
    assert out["generated_at"] == "2026-09-25T00:00:00+00:00"


def test_advisory_union_counts_non_tp_hit_in_precision_positive_control():
    rows, results = fixture()
    results[2]["meta"]["predicted"] = True
    out = measure(rows, results)
    assert out["measured"] is True
    assert (out["caught"], out["hits"], out["precision"]) == (2, 3, 2 / 3)


def test_advisory_union_shared_data_dir_only_positive_control(tmp_path, monkeypatch):
    """A valid shared candidate works when the checkout candidate is absent."""
    import judge_eval
    rows, results = fixture()
    for result in results: result["meta"]["harness_sha"] = "current"
    shared = tmp_path / "data" / "bench" / "a0_eval_set.jsonl"
    shared.parent.mkdir(parents=True)
    raw = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows).encode()
    shared.write_bytes(raw)
    result_path = tmp_path / "results.jsonl"
    result_path.write_text("".join(json.dumps(row) + "\n" for row in results))
    monkeypatch.setattr(capture_recall, "EXPECTED_EVAL_ROWS", len(rows))
    monkeypatch.setattr(capture_recall, "EXPECTED_EVAL_SHA256", hashlib.sha256(raw).hexdigest())
    monkeypatch.setattr(judge_eval, "compute_harness_sha", lambda: "current")
    monkeypatch.setattr(judge_eval, "DEFAULT_BATCH_SIZE", 30)
    out = capture_recall.load_capture_union([tmp_path / "missing", shared], result_path)
    assert out["measured"] is True
    assert out["caught"] == 2
    from results_board import render_results_board
    board = {"slug": "fixture", "decisions": {"accepted": 0, "rejected": 0, "pending": 0, "excluded": 0},
             "accepted_list": [], "withdrawal_candidates": [], "capture_union": out}
    text = "\n".join(render_results_board(board))
    assert "柱1 捕捉率（評価セット・2経路）: 2/2 = 100.0%" in text
    assert "正規表現 1/2・AI 候補（未確認）1/2" in text


@pytest.mark.parametrize("change", [
    "missing", "extra", "duplicate", "same_text_different_id", "missing_filled_duplicate",
    "hash", "label", "expected_string", "predicted_string", "status", "rep",
    "missing_provenance", "mixed_provenance", "stale_harness", "wrong_model",
    "wrong_batch", "same_text_conflicting_prediction", "missing_generated_at",
])
def test_advisory_union_rejects_known_invalid_classes(change):
    rows, results = fixture()
    if change == "missing": results.pop()
    elif change == "extra": results.append(copy.deepcopy(results[0])); results[-1]["prompt_id"] = "z"
    elif change == "duplicate": results[1] = copy.deepcopy(results[0])
    elif change == "same_text_different_id": results[0]["prompt_id"] = "z"
    elif change == "missing_filled_duplicate": results[1] = copy.deepcopy(results[0])
    elif change == "hash": results[0]["meta"]["prompt_sha256"] = "0" * 64
    elif change == "label": results[0]["meta"]["expected"] = False
    elif change == "expected_string": results[0]["meta"]["expected"] = "false"
    elif change == "predicted_string": results[0]["meta"]["predicted"] = "false"
    elif change == "status": results[0]["status"] = "error"
    elif change == "rep": results[0]["meta"]["rep"] = 1
    elif change == "missing_provenance": del results[0]["meta"]["harness_sha"]
    elif change == "mixed_provenance": results[0]["meta"]["harness_sha"] = "other"
    elif change == "stale_harness":
        for result in results: result["meta"]["harness_sha"] = "old"
    elif change == "wrong_model": results[0]["meta"]["model"] = "sonnet"
    elif change == "wrong_batch": results[0]["meta"]["batch_size_config"] = 12
    elif change == "same_text_conflicting_prediction":
        results[1] = copy.deepcopy(results[0]); results[1]["meta"]["predicted"] = True
    elif change == "missing_generated_at": del results[0]["meta"]["generated_at"]
    out = measure(rows, results)
    assert out["measured"] is False
    assert "--run --approve-harness --variant" in out["reason"]
