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
from typing import Any, Dict, Optional


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


def bare_skill_name(key: Optional[str]) -> Optional[str]:
    """起動時のスキル名 ``<plugin>:<skill>`` を bare な skill 名（SKILL.md dir 名）へ正規化する。

    dir 名に ``:`` は含まれないため、修飾形は最後の ``:`` 以降が skill 名
    （``evolve-anything:cleanup`` -> ``cleanup``、``update-config`` -> ``update-config``）。
    ``Agent:*`` は subagent 帰属でありスキルではないため None（集計/join 対象外）。

    3 実装が別々に同じロジックを持っていたのを単一化した（#145・pitfall #40 と同型の
    copied-parse-convention）。旧: coherence.scoring_advanced._bare_used_skill（空値 ""）/
    audit.multiview_eval._bare_skill_name（空値 None）/ audit.predictive_validity._bare
    （空値 None）。空値時の戻り値は呼び出し側で ``bare_skill_name(x) or ""`` 等に吸収する。
    """
    if not key:
        return None
    if key.startswith("Agent:"):
        return None
    return key.rsplit(":", 1)[-1]
