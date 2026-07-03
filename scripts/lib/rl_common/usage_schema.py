"""usage.jsonl レコードの skill 名 / timestamp を解決する単一ソース（#139）。

usage.jsonl は 3 スキーマ混在:
- ``skill_name`` + ``ts``        （通常スキル呼出）
- ``skill``      + ``ts``        （implement スキル専用）
- ``skill_name`` + ``timestamp`` （agent/subagent 呼出）

どのレコードも両フィールド名を同時に満たさないため、片側だけを見る弱いパース式は
必ず取りこぼす（skill 欠落 → 空集合 / timestamp 欠落 → 例外 skip）。writer/reader が
同じ解決規則を共有するための単一ソース（copied-parse-convention pitfall #40 の教訓・
`is_noise_agent_type` と同方針）。usage.jsonl を読む全 call site はこの関数を呼ぶ。
"""
from typing import Any, Dict


def usage_skill_name(record: Dict[str, Any]) -> str:
    """usage レコードからスキル名を解決する（``skill_name`` 優先・``skill`` フォールバック）。

    3 スキーマのいずれでも拾えるよう両フィールド名を見る。どちらも空なら ""。
    呼び出し側が "unknown" 等の既定値を要る場合は ``usage_skill_name(rec) or "unknown"``。
    """
    return record.get("skill_name") or record.get("skill") or ""


def usage_timestamp(record: Dict[str, Any]) -> str:
    """usage レコードから timestamp 文字列を解決する（``ts`` 優先・``timestamp`` フォールバック）。

    ISO8601 文字列をそのまま返す。tz suffix（``Z`` vs ``+00:00``）の正規化は行わないため、
    比較する側は datetime へパースして比べる（辞書順比較の tz suffix 罠 pitfall 参照）。
    どちらも無ければ ""。
    """
    return record.get("ts") or record.get("timestamp") or ""
