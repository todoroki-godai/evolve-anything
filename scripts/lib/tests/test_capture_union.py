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


def test_named_corpus_rejects_modified_bytes_and_default_a0(tmp_path, monkeypatch):
    rows, _ = fixture()
    raw = "".join(json.dumps(row) + "\n" for row in rows).encode()
    path = tmp_path / "synthetic.jsonl"
    path.write_bytes(raw)
    monkeypatch.setitem(capture_recall.APPROVED_EVAL_SETS, "holdout682", (len(rows), hashlib.sha256(raw).hexdigest()))
    assert capture_recall.identify_eval_set(path) == "holdout682"
    assert capture_recall.load_capture_eval_set(path, name="holdout682") == rows
    monkeypatch.setattr(capture_recall, "EXPECTED_EVAL_ROWS", len(rows))
    with pytest.raises(capture_recall.CaptureEvalIntegrityError):
        capture_recall.load_capture_eval_set(path)
    path.write_bytes(raw.replace(b'"eval_id": "a"', b'"eval_id": "z"', 1))
    with pytest.raises(capture_recall.CaptureEvalIntegrityError):
        capture_recall.identify_eval_set(path)


def test_named_corpus_rejects_wrong_row_count_even_with_approved_hash(tmp_path, monkeypatch):
    path = tmp_path / "synthetic.jsonl"
    raw = b'{"eval_id": "one"}\n'
    path.write_bytes(raw)
    monkeypatch.setitem(capture_recall.APPROVED_EVAL_SETS, "holdout682", (2, hashlib.sha256(raw).hexdigest()))
    with pytest.raises(capture_recall.CaptureEvalIntegrityError, match="row count"):
        capture_recall.identify_eval_set(path)


def test_named_union_rejects_cross_set_meta_and_legacy_only_for_a0():
    rows, results = fixture()
    assert measure(rows, results)["measured"] is True  # old meta, a0
    assert evaluate_capture_union(rows, results, "version", "haiku", 30,
                                  eval_set_name="holdout682")["measured"] is False
    for result in results:
        result["meta"]["eval_set"] = "holdout682"
    assert measure(rows, results)["measured"] is False
    assert evaluate_capture_union(rows, results, "version", "haiku", 30,
                                  eval_set_name="holdout682")["measured"] is True


def test_named_union_reader_rejects_holdout_results_as_a0(tmp_path, monkeypatch):
    import judge_eval
    rows, results = fixture()
    raw = "".join(json.dumps(row) + "\n" for row in rows).encode()
    eval_path = tmp_path / "synthetic.jsonl"
    eval_path.write_bytes(raw)
    result_path = tmp_path / "results.jsonl"
    for result in results:
        result["meta"]["eval_set"] = "holdout682"
    result_path.write_text("".join(json.dumps(row) + "\n" for row in results))
    monkeypatch.setattr(capture_recall, "EXPECTED_EVAL_ROWS", len(rows))
    monkeypatch.setattr(capture_recall, "EXPECTED_EVAL_SHA256", hashlib.sha256(raw).hexdigest())
    monkeypatch.setitem(capture_recall.APPROVED_EVAL_SETS, "holdout682", (len(rows), hashlib.sha256(raw).hexdigest()))
    monkeypatch.setattr(judge_eval, "compute_harness_sha", lambda: "version")
    assert capture_recall.load_capture_union([eval_path], result_path)["measured"] is False
    assert capture_recall.load_capture_union([eval_path], result_path, eval_set_name="holdout682")["measured"] is True


def test_holdout_input_rewrite_and_order_changes_are_detected(tmp_path, monkeypatch):
    """A shuffled valid result remains measurable; content or provenance rewrites do not."""
    import judge_eval
    rows, results = fixture()
    for result in results:
        result["meta"]["eval_set"] = "holdout682"
    raw = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows).encode()
    eval_path = tmp_path / "holdout.jsonl"
    eval_path.write_bytes(raw)
    result_path = tmp_path / "results.jsonl"
    result_path.write_text("".join(json.dumps(row) + "\n" for row in reversed(results)))
    monkeypatch.setitem(capture_recall.APPROVED_EVAL_SETS, "holdout682", (len(rows), hashlib.sha256(raw).hexdigest()))
    monkeypatch.setattr(judge_eval, "compute_harness_sha", lambda: "version")
    assert capture_recall.load_capture_union([eval_path], result_path, eval_set_name="holdout682")["measured"] is True

    rewritten = raw.replace("ありがとう".encode(), "どうも".encode())
    eval_path.write_bytes(rewritten)
    assert capture_recall.load_capture_union([eval_path], result_path, eval_set_name="holdout682")["reason"] == "評価セット不一致"
    eval_path.write_bytes(raw)
    results[0]["meta"]["harness_sha"] = "versioN"
    result_path.write_text("".join(json.dumps(row) + "\n" for row in results))
    out = capture_recall.load_capture_union([eval_path], result_path, eval_set_name="holdout682")
    assert out["measured"] is False
    assert "来歴が現行条件と不一致" in out["reason"]


def test_holdout_alternate_candidate_order_and_zero_hits(tmp_path, monkeypatch):
    """A mismatched first candidate cannot shadow a valid second path."""
    import judge_eval
    rows, results = fixture()
    for result in results:
        result["meta"]["eval_set"] = "holdout682"
    raw = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows).encode()
    bad = tmp_path / "stale.jsonl"
    bad.write_text("{}\n")
    valid = tmp_path / "valid.jsonl"
    valid.write_bytes(raw)
    result_path = tmp_path / "results.jsonl"
    result_path.write_text("".join(json.dumps(row) + "\n" for row in results))
    monkeypatch.setitem(capture_recall.APPROVED_EVAL_SETS, "holdout682", (len(rows), hashlib.sha256(raw).hexdigest()))
    monkeypatch.setattr(judge_eval, "compute_harness_sha", lambda: "version")
    assert capture_recall.load_capture_union([bad, valid], result_path, eval_set_name="holdout682")["measured"] is True
    assert capture_recall.load_capture_union([valid, bad], result_path, eval_set_name="holdout682")["measured"] is True
    for result in results:
        result["meta"]["predicted"] = False
    from rl_common import detection
    monkeypatch.setattr(detection, "_detect_correction", lambda *args, **kwargs: None)
    result_path.write_text("".join(json.dumps(row) + "\n" for row in results))
    out = capture_recall.load_capture_union([valid], result_path, eval_set_name="holdout682")
    assert out["measured"] is False
    assert "合計の分母・検出数が不足" in out["reason"]


def test_holdout_remeasure_instruction_on_validation_failure():
    rows, results = fixture()
    results[0]["meta"]["harness_sha"] = "stale"
    out = evaluate_capture_union(rows, results, "version", "haiku", 30, eval_set_name="holdout682")
    assert out["measured"] is False
    assert "新しい確認用セットを作って測る" in out["reason"]
    assert "baseline" not in out["reason"]
    a0 = evaluate_capture_union(rows, results, "version", "haiku", 30)
    assert "baseline" in a0["reason"]


def test_remeasurement_guidance_covers_every_approved_set():
    assert set(capture_recall._REMEASURE) == set(capture_recall.APPROVED_EVAL_SETS)


def test_holdout_remeasure_instruction_on_missing_results(tmp_path, monkeypatch):
    rows, _ = fixture()
    raw = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows).encode()
    eval_path = tmp_path / "holdout.jsonl"
    eval_path.write_bytes(raw)
    monkeypatch.setitem(capture_recall.APPROVED_EVAL_SETS, "holdout682", (len(rows), hashlib.sha256(raw).hexdigest()))
    out = capture_recall.load_capture_union([eval_path], tmp_path / "missing-results.jsonl", eval_set_name="holdout682")
    assert out["measured"] is False
    assert "新しい確認用セットを作って測る" in out["reason"]
    assert "baseline" not in out["reason"]


def test_advisory_union_valid_positive_control():
    rows, results = fixture()
    out = measure(rows, results)
    assert out["measured"] is True
    assert (out["caught"], out["positives"], out["regex_caught"], out["judge_caught"]) == (2, 2, 1, 1)
    assert out["precision"] == 1
    assert out["generated_at"] == "2026-09-25T00:00:00+00:00"
    assert out["batch_size"] == 30


def test_advisory_union_counts_non_tp_hit_in_precision_positive_control():
    rows, results = fixture()
    results[2]["meta"]["predicted"] = True
    out = measure(rows, results)
    assert out["measured"] is True
    assert (out["caught"], out["hits"], out["precision"]) == (2, 3, 2 / 3)


def test_duplicate_result_with_all_ids_present_is_rejected():
    rows, results = fixture()
    duplicate = copy.deepcopy(results[0])
    duplicate["meta"]["predicted"] = True
    results.append(duplicate)
    assert "重複" in measure(rows, results)["reason"]
    assert measure(rows, results[:3])["measured"] is True


@pytest.mark.parametrize("timestamp", ["2026-09-25T09:00:00+09:00", "2026-09-25T00:00:00"])
def test_non_utc_timestamp_is_rejected(timestamp):
    rows, results = fixture()
    results[0]["meta"]["generated_at"] = timestamp
    assert measure(rows, results)["measured"] is False
    assert measure(*fixture())["measured"] is True


def test_zero_detected_hits_is_unmeasured(monkeypatch):
    rows, results = fixture()
    for result in results:
        result["meta"]["predicted"] = False
    monkeypatch.setattr("rl_common.detection.should_include_message", lambda text: False)
    assert measure(rows, results)["measured"] is False
    results[0]["meta"]["predicted"] = True
    assert measure(rows, results)["measured"] is True


def test_same_text_distinct_ids_are_valid_but_content_swap_is_rejected():
    rows, results = fixture()
    rows[1]["text"] = rows[0]["text"]
    results[1]["meta"]["prompt_sha256"] = results[0]["meta"]["prompt_sha256"]
    assert measure(rows, results)["measured"] is True
    results[1]["meta"]["prompt_sha256"] = hashlib.sha256(b"other").hexdigest()
    assert measure(rows, results)["measured"] is False


def test_json_array_result_row_is_unmeasured(tmp_path, monkeypatch):
    import judge_eval
    rows, results = fixture()
    raw = "".join(json.dumps(row) + "\n" for row in rows).encode()
    eval_path = tmp_path / "a0_eval_set.jsonl"
    eval_path.write_bytes(raw)
    path = tmp_path / "results.jsonl"
    path.write_text("[]\n")
    monkeypatch.setattr(capture_recall, "EXPECTED_EVAL_ROWS", len(rows))
    monkeypatch.setattr(capture_recall, "EXPECTED_EVAL_SHA256", hashlib.sha256(raw).hexdigest())
    assert capture_recall.load_capture_union([eval_path], path)["measured"] is False
    path.write_text("".join(json.dumps(row) + "\n" for row in results))
    monkeypatch.setattr(judge_eval, "compute_harness_sha", lambda: "version")
    assert capture_recall.load_capture_union([eval_path], path)["measured"] is True


def test_absent_and_mismatched_eval_set_have_distinct_reasons(tmp_path):
    results_path = tmp_path / "results.jsonl"
    assert capture_recall.load_capture_union([tmp_path / "absent"], results_path)["reason"] == "評価セットなし"
    bad = tmp_path / "bad.jsonl"
    bad.write_text("{}\n")
    assert capture_recall.load_capture_union([bad], results_path)["reason"] == "評価セット不一致"


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
    assert "柱1 捕捉率（確認用セット・調整に不使用）: 測定不能" in text
    assert "参考: 調整に使った評価セットでの値 2/2 = 100.0%" in text
    assert "到達の根拠にしない" in text


def test_holdout691_is_registered_with_expected_rows_and_hash():
    assert capture_recall.APPROVED_EVAL_SETS["holdout691"] == (
        770, "60d9aebfcce337c58f7543b85c6d0ce6821fff4288b69b48d4223edfeab1fa29",
    )


def test_holdout691_remeasure_no_results_path_says_unused_and_one_time(tmp_path, monkeypatch):
    """load_capture_union's missing-results branch must not say 'used up' before any run."""
    rows = [{"eval_id": "a", "text": "t", "label": "TP"}]
    raw = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows).encode()
    eval_path = tmp_path / "holdout691.jsonl"
    eval_path.write_bytes(raw)
    monkeypatch.setitem(
        capture_recall.APPROVED_EVAL_SETS, "holdout691", (len(rows), hashlib.sha256(raw).hexdigest())
    )
    out = capture_recall.load_capture_union(
        [eval_path], tmp_path / "missing-results.jsonl", eval_set_name="holdout691"
    )
    assert out["measured"] is False
    assert "未使用" in out["reason"]
    assert "1回だけ" in out["reason"]


def test_holdout691_remeasure_has_results_path_says_used_up_not_unused(monkeypatch):
    """evaluate_capture_union's invalid-result branch must not claim the set is still unused."""
    rows, results = fixture()
    monkeypatch.setitem(capture_recall.APPROVED_EVAL_SETS, "holdout691", (len(rows), "irrelevant-for-this-path"))
    results[0]["meta"]["harness_sha"] = "stale"
    out = evaluate_capture_union(rows, results, "version", "haiku", 30, eval_set_name="holdout691")
    assert out["measured"] is False
    assert "未使用" not in out["reason"]
    assert "取り直さない" in out["reason"]
    assert "新しい確認用セットを作って測る" in out["reason"]


def test_holdout691_identify_eval_set_positive_control_and_hash_mismatch(tmp_path, monkeypatch):
    """Byte-level tamper changes the digest, so this only exercises the hash-mismatch branch."""
    rows = [{"eval_id": str(i), "text": f"t{i}", "label": "TP"} for i in range(3)]
    raw = "".join(json.dumps(row) + "\n" for row in rows).encode()
    monkeypatch.setitem(
        capture_recall.APPROVED_EVAL_SETS, "holdout691", (len(rows), hashlib.sha256(raw).hexdigest())
    )
    path = tmp_path / "synthetic691.jsonl"
    path.write_bytes(raw)
    assert capture_recall.identify_eval_set(path) == "holdout691"

    path.write_bytes(raw.replace(b'"eval_id": "0"', b'"eval_id": "9"', 1))
    with pytest.raises(capture_recall.CaptureEvalIntegrityError, match="hash mismatch"):
        capture_recall.identify_eval_set(path)


def test_holdout691_identify_eval_set_rejects_wrong_row_count_even_with_approved_hash(tmp_path, monkeypatch):
    """Mirrors test_named_corpus_rejects_wrong_row_count_even_with_approved_hash for holdout691:
    the digest matches the (mis-)registered hash, so this actually reaches the row-count check."""
    raw = b'{"eval_id": "one"}\n'
    monkeypatch.setitem(capture_recall.APPROVED_EVAL_SETS, "holdout691", (2, hashlib.sha256(raw).hexdigest()))
    path = tmp_path / "wrongcount691.jsonl"
    path.write_bytes(raw)
    with pytest.raises(capture_recall.CaptureEvalIntegrityError, match="row count"):
        capture_recall.identify_eval_set(path)


@pytest.mark.parametrize("change", [
    "missing", "extra", "duplicate", "same_text_different_id", "missing_filled_duplicate",
    "hash", "label", "expected_string", "predicted_string", "status", "rep",
    "missing_provenance", "mixed_provenance", "stale_harness", "wrong_model",
    "wrong_batch", "missing_generated_at",
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
    elif change == "missing_generated_at": del results[0]["meta"]["generated_at"]
    out = measure(rows, results)
    assert out["measured"] is False
    assert "--run --approve-harness --variant" in out["reason"]
    assert "共有 checkout ではなく worktree で実行" in out["reason"]
    assert "新しい baseline/results.jsonl と _state.json を commit して PR" in out["reason"]
    assert "baseline-<YYYYMMDD>/ は commit せず削除" in out["reason"]
    assert "旧結果は git 履歴に残る" in out["reason"]
