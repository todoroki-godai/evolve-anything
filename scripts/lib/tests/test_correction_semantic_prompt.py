"""correction_semantic.prompt のテスト（#431 バッチプロンプト + verdict パース）。

プロンプト組み立てとモデル応答(JSON)のパースを検証する。LLM は呼ばない（文字列のみ）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_lib_dir = Path(__file__).resolve().parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

from correction_semantic import prompt as cs_prompt  # noqa: E402


def _utts():
    return [
        {"source_path": "/a.jsonl", "line_no": 1,
         "text": "つむぎにしてほしい、四国めたんじゃなくて", "prev_action": "Edit"},
        {"source_path": "/a.jsonl", "line_no": 2,
         "text": "ありがとう、それで完璧", "prev_action": None},
    ]


def test_build_prompt_contains_all_utterances() -> None:
    p = cs_prompt.build_batch_prompt(_utts())
    assert "四国めたん" in p
    assert "ありがとう" in p
    # 各発話に判定用の index 番号が振られている
    assert "0" in p and "1" in p


def test_build_prompt_asks_for_json() -> None:
    p = cs_prompt.build_batch_prompt(_utts())
    assert "JSON" in p or "json" in p
    # 二値 + 言い回し抽出を要求
    assert "is_correction" in p
    assert "idiom" in p


# ── verdict パース ────────────────────────────────────────────────


def test_parse_verdicts_valid_json() -> None:
    raw = json.dumps({
        "verdicts": [
            {"index": 0, "is_correction": True, "idiom": "四国めたんじゃなくて",
             "reason": "正しい値の後置型"},
            {"index": 1, "is_correction": False, "idiom": None, "reason": ""},
        ]
    }, ensure_ascii=False)
    verdicts = cs_prompt.parse_verdicts(raw)
    assert len(verdicts) == 2
    assert verdicts[0]["is_correction"] is True
    assert verdicts[0]["idiom"] == "四国めたんじゃなくて"
    assert verdicts[1]["is_correction"] is False


def test_parse_verdicts_json_with_codefence() -> None:
    raw = "```json\n" + json.dumps({"verdicts": [
        {"index": 0, "is_correction": True, "idiom": "x", "reason": "y"}]}) + "\n```"
    verdicts = cs_prompt.parse_verdicts(raw)
    assert len(verdicts) == 1
    assert verdicts[0]["is_correction"] is True


def test_parse_verdicts_empty_on_garbage() -> None:
    assert cs_prompt.parse_verdicts("not json at all") == []
    assert cs_prompt.parse_verdicts("") == []
    assert cs_prompt.parse_verdicts(None) == []


def test_parse_verdicts_tolerates_missing_fields() -> None:
    raw = json.dumps({"verdicts": [{"index": 0, "is_correction": True}]})
    verdicts = cs_prompt.parse_verdicts(raw)
    assert verdicts[0]["idiom"] is None  # 欠落 idiom は None に正規化


# ── parse_verdicts_result: 空リストとパース失敗の区別（#273）────────────────


def test_parse_verdicts_result_ok_true_on_valid_json() -> None:
    raw = json.dumps({"verdicts": [
        {"index": 0, "is_correction": True, "idiom": "x", "reason": "y"},
    ]})
    result = cs_prompt.parse_verdicts_result(raw)
    assert result["ok"] is True
    assert len(result["verdicts"]) == 1


def test_parse_verdicts_result_ok_true_on_legitimate_empty_list() -> None:
    """正しい JSON で verdicts が空配列（モデルが「該当なし」と判定）は ok=True。"""
    raw = json.dumps({"verdicts": []})
    result = cs_prompt.parse_verdicts_result(raw)
    assert result["ok"] is True
    assert result["verdicts"] == []


def test_parse_verdicts_result_ok_false_on_malformed_json() -> None:
    """壊れた JSON はパース失敗（ok=False）。空リストと区別できないと #273 の非対称が再発する。"""
    result = cs_prompt.parse_verdicts_result("not json at all {{{")
    assert result["ok"] is False
    assert result["verdicts"] == []


def test_parse_verdicts_result_ok_false_on_missing_response() -> None:
    assert cs_prompt.parse_verdicts_result("")["ok"] is False
    assert cs_prompt.parse_verdicts_result(None)["ok"] is False


def test_parse_verdicts_result_ok_false_on_missing_verdicts_key() -> None:
    """"verdicts" キー自体が無い/リストでない応答は解釈不能として扱う。"""
    assert cs_prompt.parse_verdicts_result(json.dumps({"foo": "bar"}))["ok"] is False
    assert cs_prompt.parse_verdicts_result(json.dumps({"verdicts": "not a list"}))["ok"] is False


def test_parse_verdicts_backward_compat_delegates_to_result() -> None:
    """既存 API `parse_verdicts` は parse_verdicts_result の verdicts をそのまま返す（後方互換）。"""
    raw = json.dumps({"verdicts": [{"index": 0, "is_correction": True, "idiom": "x", "reason": "y"}]})
    assert cs_prompt.parse_verdicts(raw) == cs_prompt.parse_verdicts_result(raw)["verdicts"]
