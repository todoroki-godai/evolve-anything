#!/usr/bin/env python3
"""semantic_detector.py のユニットテスト。

[ADR-037] Phase 1d-i: claude -p 全廃後のテスト。
- subprocess mock は不要（LLM を一切呼ばない）。
- emit/ingest の決定論 2相と決定論フォールバックを検証する。
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib.semantic_detector import (
    ANALYSIS_PROMPT,
    BATCH_SIZE,
    CONTRADICTION_PROMPT,
    _extract_json_array,
    detect_contradictions,
    emit_contradiction_request,
    emit_validation_requests,
    ingest_contradictions,
    ingest_validation_results,
    validate_corrections,
)


# --- _extract_json_array ---


def test_extract_json_direct():
    text = '[{"index": 0, "is_learning": true}]'
    result = _extract_json_array(text)
    assert result == [{"index": 0, "is_learning": True}]


def test_extract_json_in_code_block():
    text = '```json\n[{"index": 0, "is_learning": false}]\n```'
    result = _extract_json_array(text)
    assert result == [{"index": 0, "is_learning": False}]


def test_extract_json_with_surrounding_text():
    text = 'Here is the result:\n[{"index": 0, "is_learning": true}]\nDone.'
    result = _extract_json_array(text)
    assert result == [{"index": 0, "is_learning": True}]


def test_extract_json_invalid():
    result = _extract_json_array("not json at all")
    assert result is None


# --- BATCH_SIZE ---


def test_batch_size_is_20():
    assert BATCH_SIZE == 20


# --- validate_corrections (決定論フォールバック) ---


def _make_corrections(n):
    return [{"message": f"correction {i}"} for i in range(n)]


def test_validate_corrections_empty():
    """空入力で空リスト。"""
    assert validate_corrections([]) == []


def test_validate_corrections_returns_all_true():
    """入力に関わらず全件 is_learning=True / extracted_learning=None。"""
    corrections = _make_corrections(5)
    results = validate_corrections(corrections)
    assert len(results) == 5
    assert all(r["is_learning"] is True for r in results)
    assert all(r["extracted_learning"] is None for r in results)


def test_validate_corrections_model_arg_ignored():
    """model 引数を渡しても動作する（後方互換）。"""
    corrections = _make_corrections(3)
    results = validate_corrections(corrections, model="haiku")
    assert len(results) == 3
    assert all(r["is_learning"] is True for r in results)


# --- detect_contradictions (決定論フォールバック) ---


def test_detect_contradictions_empty():
    """空リストで空リスト返却。"""
    assert detect_contradictions([]) == []


def test_detect_contradictions_single():
    """1件で空リスト返却。"""
    assert detect_contradictions(_make_corrections(1)) == []


def test_detect_contradictions_multiple_always_empty():
    """2件以上でも決定論フォールバックは空リスト。"""
    corrections = [
        {"message": "日本語で応答して"},
        {"message": "英語で応答して"},
    ]
    assert detect_contradictions(corrections) == []


def test_detect_contradictions_model_arg_ignored():
    """model 引数を渡しても動作する（後方互換）。"""
    assert detect_contradictions(_make_corrections(3), model="opus") == []


# --- emit_validation_requests ---


def test_emit_validation_requests_empty():
    """空 corrections → requests 空。"""
    result = emit_validation_requests([])
    assert result == {"requests": []}


def test_emit_validation_requests_single_batch():
    """20件以下 → 1リクエスト。"""
    corrections = _make_corrections(5)
    result = emit_validation_requests(corrections)
    requests = result["requests"]
    assert len(requests) == 1
    assert requests[0]["id"] == "validate:0"
    assert requests[0]["meta"]["offset"] == 0
    assert requests[0]["meta"]["size"] == 5


def test_emit_validation_requests_two_batches():
    """25件 → 2リクエスト（offset 0 と 20、size 20 と 5）。"""
    corrections = _make_corrections(25)
    result = emit_validation_requests(corrections)
    requests = result["requests"]
    assert len(requests) == 2

    req0 = requests[0]
    assert req0["id"] == "validate:0"
    assert req0["meta"]["offset"] == 0
    assert req0["meta"]["size"] == 20

    req1 = requests[1]
    assert req1["id"] == "validate:20"
    assert req1["meta"]["offset"] == 20
    assert req1["meta"]["size"] == 5


def test_emit_validation_requests_prompt_contains_analysis_prompt():
    """prompt に ANALYSIS_PROMPT 由来の文字列が含まれる。"""
    corrections = _make_corrections(3)
    result = emit_validation_requests(corrections)
    prompt = result["requests"][0]["prompt"]
    # ANALYSIS_PROMPT の一部を確認
    assert "is_learning" in prompt
    assert "corrections" in prompt


def test_emit_validation_requests_no_temp_fields_in_meta():
    """meta に一時フィールド (_batch_items 等) が残っていない。"""
    corrections = _make_corrections(5)
    result = emit_validation_requests(corrections)
    for req in result["requests"]:
        assert "_batch_items" not in req["meta"]
        assert "_items" not in req["meta"]


def test_emit_validation_requests_meta_has_offset_and_size():
    """meta に offset と size が含まれる。"""
    corrections = _make_corrections(5)
    result = emit_validation_requests(corrections)
    meta = result["requests"][0]["meta"]
    assert "offset" in meta
    assert "size" in meta


# --- ingest_validation_results ---


def _make_requests_for(corrections):
    """テスト用: emit の返り値の requests を取得する。"""
    return emit_validation_requests(corrections)["requests"]


def test_ingest_validation_results_full_response():
    """全件マッチする応答 → is_learning を正しくパース。"""
    corrections = _make_corrections(3)
    requests = _make_requests_for(corrections)
    raw = json.dumps([
        {"index": 0, "is_learning": True, "extracted_learning": "learn 0"},
        {"index": 1, "is_learning": False, "extracted_learning": None},
        {"index": 2, "is_learning": True, "extracted_learning": "learn 2"},
    ])
    responses = {"validate:0": raw}
    result = ingest_validation_results(corrections, requests, responses)
    assert len(result) == 3
    assert result[0] == {"is_learning": True, "extracted_learning": "learn 0"}
    assert result[1] == {"is_learning": False, "extracted_learning": None}
    assert result[2] == {"is_learning": True, "extracted_learning": "learn 2"}


def test_ingest_validation_results_partial_response():
    """一部の index が欠落 → 欠落分は is_learning=True のまま。"""
    corrections = _make_corrections(3)
    requests = _make_requests_for(corrections)
    # index 1 が欠落
    raw = json.dumps([
        {"index": 0, "is_learning": False, "extracted_learning": None},
        {"index": 2, "is_learning": False, "extracted_learning": None},
    ])
    responses = {"validate:0": raw}
    result = ingest_validation_results(corrections, requests, responses)
    assert len(result) == 3
    assert result[0]["is_learning"] is False
    assert result[1]["is_learning"] is True   # 欠落 → 既定値
    assert result[2]["is_learning"] is False


def test_ingest_validation_results_empty_array_response():
    """応答が空配列 [] → 全件既定値。"""
    corrections = _make_corrections(3)
    requests = _make_requests_for(corrections)
    responses = {"validate:0": "[]"}
    result = ingest_validation_results(corrections, requests, responses)
    assert len(result) == 3
    assert all(r["is_learning"] is True for r in result)
    assert all(r["extracted_learning"] is None for r in result)


def test_ingest_validation_results_missing_response():
    """request に対応する response が欠損 → 全件既定値。"""
    corrections = _make_corrections(3)
    requests = _make_requests_for(corrections)
    responses = {}  # 欠損
    result = ingest_validation_results(corrections, requests, responses)
    assert len(result) == 3
    assert all(r["is_learning"] is True for r in result)


def test_ingest_validation_results_same_length_as_corrections():
    """結果は corrections と同数・同順。"""
    corrections = _make_corrections(25)
    requests = _make_requests_for(corrections)
    # 両バッチに応答
    raw0 = json.dumps([
        {"index": i, "is_learning": True, "extracted_learning": None}
        for i in range(20)
    ])
    raw1 = json.dumps([
        {"index": i, "is_learning": True, "extracted_learning": None}
        for i in range(5)
    ])
    responses = {"validate:0": raw0, "validate:20": raw1}
    result = ingest_validation_results(corrections, requests, responses)
    assert len(result) == 25


def test_ingest_validation_results_parse_failure():
    """パース不能な応答 → 対象バッチは全件既定値。"""
    corrections = _make_corrections(3)
    requests = _make_requests_for(corrections)
    responses = {"validate:0": "not json at all"}
    result = ingest_validation_results(corrections, requests, responses)
    assert len(result) == 3
    assert all(r["is_learning"] is True for r in result)


# --- emit_contradiction_request ---


def test_emit_contradiction_request_empty():
    """0件 → requests 空。"""
    result = emit_contradiction_request([])
    assert result == {"requests": []}


def test_emit_contradiction_request_single():
    """1件 → requests 空。"""
    result = emit_contradiction_request(_make_corrections(1))
    assert result == {"requests": []}


def test_emit_contradiction_request_two_or_more():
    """2件以上 → 単一リクエスト id=contradictions。"""
    corrections = [
        {"message": "日本語で応答して"},
        {"message": "英語で応答して"},
    ]
    result = emit_contradiction_request(corrections)
    requests = result["requests"]
    assert len(requests) == 1
    assert requests[0]["id"] == "contradictions"


def test_emit_contradiction_request_prompt_content():
    """prompt に CONTRADICTION_PROMPT 由来の文字列が含まれる。"""
    corrections = _make_corrections(3)
    result = emit_contradiction_request(corrections)
    prompt = result["requests"][0]["prompt"]
    assert "矛盾" in prompt or "pair" in prompt


def test_emit_contradiction_request_no_temp_fields_in_meta():
    """meta に一時フィールドが残っていない。"""
    corrections = _make_corrections(3)
    result = emit_contradiction_request(corrections)
    for req in result["requests"]:
        assert "_items" not in req["meta"]
        assert "_batch_items" not in req["meta"]


# --- ingest_contradictions ---


def test_ingest_contradictions_empty_requests():
    """requests 空 → []。"""
    assert ingest_contradictions([], {}) == []


def test_ingest_contradictions_valid_pairs():
    """正常な pair を抽出する。"""
    corrections = [
        {"message": "日本語で応答して"},
        {"message": "英語で応答して"},
        {"message": "コード例を多めに"},
    ]
    requests = emit_contradiction_request(corrections)["requests"]
    raw = json.dumps([
        {"pair": [0, 1], "reason": "言語指定が矛盾している"},
    ])
    responses = {"contradictions": raw}
    result = ingest_contradictions(requests, responses)
    assert len(result) == 1
    assert result[0]["pair"] == [0, 1]
    assert result[0]["reason"] == "言語指定が矛盾している"


def test_ingest_contradictions_invalid_pair_wrong_length():
    """pair の要素数が2でないエントリは除外。"""
    corrections = _make_corrections(3)
    requests = emit_contradiction_request(corrections)["requests"]
    raw = json.dumps([
        {"pair": [0, 1, 2], "reason": "3要素は不正"},
        {"pair": [0, 1], "reason": "正常"},
    ])
    responses = {"contradictions": raw}
    result = ingest_contradictions(requests, responses)
    assert len(result) == 1
    assert result[0]["pair"] == [0, 1]


def test_ingest_contradictions_invalid_pair_non_int():
    """pair の要素が int でないエントリは除外。"""
    corrections = _make_corrections(3)
    requests = emit_contradiction_request(corrections)["requests"]
    raw = json.dumps([
        {"pair": ["a", "b"], "reason": "非int"},
        {"pair": [0, 2], "reason": "正常"},
    ])
    responses = {"contradictions": raw}
    result = ingest_contradictions(requests, responses)
    assert len(result) == 1
    assert result[0]["pair"] == [0, 2]


def test_ingest_contradictions_missing_response():
    """response 欠損 → []。"""
    corrections = _make_corrections(3)
    requests = emit_contradiction_request(corrections)["requests"]
    result = ingest_contradictions(requests, {})
    assert result == []


def test_ingest_contradictions_empty_array_response():
    """応答が空配列 → []。"""
    corrections = _make_corrections(3)
    requests = emit_contradiction_request(corrections)["requests"]
    responses = {"contradictions": "[]"}
    result = ingest_contradictions(requests, responses)
    assert result == []


def test_ingest_contradictions_parse_failure():
    """パース不能な応答 → []。"""
    corrections = _make_corrections(3)
    requests = emit_contradiction_request(corrections)["requests"]
    responses = {"contradictions": "invalid json"}
    result = ingest_contradictions(requests, responses)
    assert result == []
