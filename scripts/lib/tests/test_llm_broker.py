"""llm_broker モジュールのテスト（LLM-free、mock 不要）。

broker は claude -p を一切呼ばないため、全テストが決定論で完結する。
"""

import pytest
from llm_broker import (
    FALLBACK_SCORE,
    parse_score,
    passthrough,
    build_requests,
    parse_responses,
)


# --- parse_score: LLM 出力テキスト/数値からスコア抽出 ---


def test_parse_score_extracts_float_from_text():
    assert parse_score("0.75") == 0.75
    assert parse_score("スコアは 0.82 です") == 0.82
    assert parse_score("1.0") == 1.0
    assert parse_score("評価: 0") == 0.0


def test_parse_score_passes_numeric_through():
    assert parse_score(0.6) == 0.6
    assert parse_score(1) == 1.0


def test_parse_score_fallback_on_no_match():
    assert parse_score("評価できません") == FALLBACK_SCORE
    assert parse_score("") == FALLBACK_SCORE
    assert parse_score(None) == FALLBACK_SCORE


def test_parse_score_custom_fallback():
    assert parse_score(None, fallback=0.0) == 0.0


def test_parse_score_bool_is_not_numeric():
    # True/False を 1.0/0.0 に化けさせない（採点値の取り違え防止）
    assert parse_score(True) == FALLBACK_SCORE
    assert parse_score(False) == FALLBACK_SCORE


# --- passthrough: 生成系 consumer 用の素通しパーサ ---


def test_passthrough_returns_raw():
    assert passthrough("生成テキスト") == "生成テキスト"
    assert passthrough({"k": "v"}) == {"k": "v"}


def test_passthrough_fallback_on_none():
    assert passthrough(None) == ""
    assert passthrough(None, fallback="N/A") == "N/A"


# --- build_requests: Phase A（決定論の前処理） ---


def test_build_requests_shape():
    items = [{"id": "a", "axis": "tech"}, {"id": "b", "axis": "domain"}]
    requests = build_requests(items, lambda it: f"prompt for {it['axis']}")
    assert len(requests) == 2
    assert set(requests[0].keys()) == {"id", "prompt", "meta"}
    assert requests[0]["id"] == "a"
    assert requests[0]["prompt"] == "prompt for tech"
    # id 以外のフィールドは meta に保持される
    assert requests[0]["meta"] == {"axis": "tech"}


def test_build_requests_ids_unique_preserved():
    items = [{"id": f"r{i}"} for i in range(3)]
    requests = build_requests(items, lambda it: "p")
    assert [r["id"] for r in requests] == ["r0", "r1", "r2"]


def test_build_requests_requires_id():
    with pytest.raises(ValueError):
        build_requests([{"axis": "tech"}], lambda it: "p")


def test_build_requests_empty():
    assert build_requests([], lambda it: "p") == []


# --- parse_responses: Phase C（決定論のゲート） ---


def test_parse_responses_roundtrip_with_score_parser():
    items = [{"id": "a"}, {"id": "b"}]
    requests = build_requests(items, lambda it: "p")
    responses = {"a": "0.80", "b": 0.60}
    parsed = parse_responses(requests, responses, parser=parse_score)
    assert parsed == {"a": 0.80, "b": 0.60}


def test_parse_responses_missing_id_filled_by_parser_fallback():
    items = [{"id": "a"}, {"id": "b"}]
    requests = build_requests(items, lambda it: "p")
    # responses は b が欠損 → parse_score(None) = FALLBACK_SCORE で穴埋め
    parsed = parse_responses(requests, {"a": "0.9"}, parser=parse_score)
    assert parsed["a"] == 0.9
    assert parsed["b"] == FALLBACK_SCORE


def test_parse_responses_default_passthrough():
    items = [{"id": "a"}]
    requests = build_requests(items, lambda it: "p")
    parsed = parse_responses(requests, {"a": "生成結果"})
    assert parsed == {"a": "生成結果"}


def test_parse_responses_iterates_requests_not_responses():
    # responses に余分な id があっても無視される（requests が単一ソース）
    items = [{"id": "a"}]
    requests = build_requests(items, lambda it: "p")
    parsed = parse_responses(requests, {"a": "0.5", "stray": "0.1"}, parser=parse_score)
    assert set(parsed.keys()) == {"a"}
