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
