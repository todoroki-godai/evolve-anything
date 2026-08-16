"""telemetry_query.usage_errors.query_usage_by_skill_session の回帰テスト（#480 おまけ）。

usage.jsonl は "tool_name" キーを持たない（hooks/observe.py は skill_name/subagent_type 系
フィールドのみ書く）ため、旧実装の `rec.get("tool_name") == "Skill"` は常に False で
`_aggregate_skill_sessions` は常に空リストを返す恒久 no-op だった。
`is_skill_usage_record`（#480 単一ソース）で Skill 発火を検出するよう是正した。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_lib_dir = Path(__file__).resolve().parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

from telemetry_query.usage_errors import query_usage_by_skill_session  # noqa: E402


def _write_jsonl(path: Path, records) -> None:
    path.write_text(
        "\n".join(json.dumps(r) for r in records) + "\n",
        encoding="utf-8",
    )


def test_query_usage_by_skill_session_detects_fire_without_tool_name_key(tmp_path, monkeypatch):
    """usage.jsonl の実スキーマ（tool_name キー無し）でも skill 発火セッションを検出できる。

    旧実装は `rec.get("tool_name") == "Skill"` が常に False で `skill_fires` が
    常に空になり、この関数は常に `[]` を返す恒久 no-op だった（#480 おまけ是正）。
    """
    monkeypatch.setattr("telemetry_query.HAS_DUCKDB", False)
    usage_file = tmp_path / "usage.jsonl"
    _write_jsonl(usage_file, [
        {
            "skill_name": "evolve",
            "ts": "2026-08-15T10:00:00Z",
            "session_id": "s1",
            "outcome": "success",
        },
        {
            "skill_name": "audit",
            "ts": "2026-08-15T10:01:00Z",
            "session_id": "s1",
            "outcome": "success",
        },
    ])

    result = query_usage_by_skill_session("evolve", usage_file=usage_file)

    # 旧実装（恒久 no-op）は常に [] を返していた。是正後は発火セッションを検出する。
    assert len(result) == 1
    assert result[0]["session_id"] == "s1"
