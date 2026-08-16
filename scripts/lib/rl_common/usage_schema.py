"""usage.jsonl レコードの skill 名 / timestamp / Skill・Agent 種別を解決する単一ソース
（#139, #480）。

usage.jsonl は 3 スキーマ混在:
- Skill 呼出（現行）        : ``skill_name`` + ``ts``
- Skill 呼出（旧・backfill）: ``skill_name`` + ``timestamp``（``outcome`` 無し。261 件実在。
  2026-08-16 実データ調査で確認。**旧 docstring の「skill_name + timestamp = agent」は
  誤りだった**＝このキー組だけでは Skill/Agent を判別できない）
- Agent 呼出                : ``skill_name`` (=``f"Agent:{subagent_type}"``) + ``ts``/``timestamp``
  に加え ``subagent_type`` / ``agent_id`` のいずれかを必ず持つ（hooks/observe.py の
  tool_name=="Agent" 分岐が両方書く）
- workflow-conformance（implement 専用の別集計）: ``skill_name`` を持たず ``skill`` を持つ

どのレコードも両フィールド名を同時に満たさないため、片側だけを見る弱いパース式は
必ず取りこぼす（skill 欠落 → 空集合 / timestamp 欠落 → 例外 skip）。writer/reader が
同じ解決規則を共有するための単一ソース（copied-parse-convention pitfall #40 の教訓・
`is_noise_agent_type` と同方針）。usage.jsonl を読む全 call site はこの関数を呼ぶ。

Skill/Agent の判別（``is_skill_usage_record`` / ``is_agent_usage_record``）は
``timestamp`` の有無ではなく ``subagent_type`` / ``agent_id`` の有無で行う（#480）。
旧実装は `scripts/lib/measure_467_join.py` の bench 専用関数にのみ存在し、production の
6 箇所（audit/usage.py・audit/reward_ema.py 経由の永続化書込・
telemetry_query/usage_errors.py 等）はこれを経由せず、各所が独自に
``"subagent_type" in rec`` 相当のロジックを書くか、または書いていなかった
（＝ Agent 呼び出しが Skill 集計・reward_ema.jsonl の永続化データに混入していた）。
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


def is_agent_usage_record(record: Dict[str, Any]) -> bool:
    """usage レコードが Agent（subagent）呼び出し由来かを判定する（#480）。

    hooks/observe.py の tool_name=="Agent" 分岐は必ず ``subagent_type`` と ``agent_id``
    の両方を書くが、判定はどちらか一方の有無で十分（将来 Agent 呼び出しの記録項目が
    減っても壊れないよう、両方を要求しない・保守的に広く拾う）。
    """
    return "subagent_type" in record or "agent_id" in record


def is_skill_usage_record(record: Dict[str, Any]) -> bool:
    """usage レコードが Skill 呼び出し（Agent 呼び出しではない）由来かを判定する（#480）。

    Skill 呼び出しは ``skill_name`` を持ち ``subagent_type`` / ``agent_id`` を持たない。
    workflow-conformance 用の別スキーマ（``skill_name`` を持たず ``skill`` のみ）は
    ``skill_name`` 要件で自然に除外される（Skill でも Agent でもない第3スキーマ）。
    """
    return "skill_name" in record and not is_agent_usage_record(record)


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
