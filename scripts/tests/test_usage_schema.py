"""usage.jsonl レコードパースの単一ソース（usage_skill_name / usage_timestamp）テスト。

usage.jsonl は 3 スキーマ混在（#139）。writer/reader が同じ解決規則を共有するための
単一ソース関数が、どのスキーマでも skill 名 / timestamp を取りこぼさないことを固定する。
決定論・LLM 非依存。実測スキーマ（issue #139 記載）を忠実に再現する。
"""
import sys
from pathlib import Path

import pytest

_PLUGIN_ROOT = Path(__file__).resolve().parent.parent
_LIB = _PLUGIN_ROOT / "lib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

import rl_common  # noqa: E402


# --- skill 名解決（3スキーマ） ---------------------------------------------------
@pytest.mark.parametrize(
    "record,expected",
    [
        # A) skill_name + ts（通常スキル呼出・3020 件）
        ({"skill_name": "receipt-monthly-classify", "ts": "2026-07-01T00:00:00Z"}, "receipt-monthly-classify"),
        # B) skill + ts（implement スキル専用・36 件）
        ({"skill": "implement", "ts": "2026-07-01T00:00:00Z"}, "implement"),
        # C) skill_name + timestamp（agent/subagent 呼出・2725 件）
        ({"skill_name": "Agent:general-purpose", "timestamp": "2026-07-01T00:00:00+00:00"}, "Agent:general-purpose"),
        # skill_name が空文字なら skill にフォールバック
        ({"skill_name": "", "skill": "implement"}, "implement"),
        # どちらも無ければ ""
        ({"ts": "2026-07-01T00:00:00Z"}, ""),
    ],
)
def test_usage_skill_name_全スキーマで解決(record, expected):
    assert rl_common.usage_skill_name(record) == expected


# --- timestamp 解決（ts / timestamp 両対応） ------------------------------------
@pytest.mark.parametrize(
    "record,expected",
    [
        ({"skill_name": "s", "ts": "2026-07-01T00:00:00Z"}, "2026-07-01T00:00:00Z"),
        ({"skill_name": "s", "timestamp": "2026-07-01T00:00:00+00:00"}, "2026-07-01T00:00:00+00:00"),
        # ts が空文字なら timestamp にフォールバック
        ({"ts": "", "timestamp": "2026-07-01T00:00:00Z"}, "2026-07-01T00:00:00Z"),
        # どちらも無ければ ""
        ({"skill_name": "s"}, ""),
    ],
)
def test_usage_timestamp_両フィールド対応(record, expected):
    assert rl_common.usage_timestamp(record) == expected
