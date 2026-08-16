"""audit.usage.aggregate_usage のユニットテスト（#480）。

旧実装は Skill/Agent の判別を行わず、``skill_name`` フィールドの有無だけで数えていた。
_BUILTIN_TOOLS は既知の builtin agent 名（Explore/Plan/general-purpose）だけを除外するため、
custom agent（impl-worker 等）の Agent 呼び出しがそのまま "スキル" として集計に混入し、
かつ workflow-conformance レコード（``skill`` キーのみ）が実 Skill 呼び出しと合算されて
二重計上されていた（実データで "implement" が 22 → 59 件に膨張・2026-08-16 実測）。
"""
from __future__ import annotations

import sys
from pathlib import Path

_lib_dir = Path(__file__).resolve().parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

from audit.usage import aggregate_usage  # noqa: E402


def test_custom_agent_calls_are_excluded_not_just_known_builtins():
    """builtin ではない custom agent（impl-worker）も Skill 集計から除外される。"""
    records = [
        {"skill_name": "evolve", "ts": "2026-08-15T10:00:00Z", "outcome": "success"},
        {
            "skill_name": "Agent:impl-worker",
            "subagent_type": "impl-worker",
            "agent_id": "a1",
            "timestamp": "2026-08-15T10:00:00Z",
        },
        {
            "skill_name": "Agent:general-purpose",  # builtin agent（従来から除外対象）
            "subagent_type": "general-purpose",
            "agent_id": "a2",
            "timestamp": "2026-08-15T10:00:00Z",
        },
    ]
    counts = aggregate_usage(records)
    assert counts == {"evolve": 1}
    assert "Agent:impl-worker" not in counts
    assert "Agent:general-purpose" not in counts


def test_workflow_conformance_records_not_double_counted_with_real_skill_calls():
    """workflow-conformance レコード（``skill`` キーのみ）は実 Skill 呼び出しと合算されない。"""
    records = [
        # 実 Skill 呼び出し（implement を2回起動）
        {"skill_name": "implement", "ts": "2026-08-15T10:00:00Z", "outcome": "success"},
        {"skill_name": "implement", "ts": "2026-08-15T11:00:00Z", "outcome": "success"},
        # workflow-conformance レコード（別スキーマ・skill_name を持たない）
        {"skill": "implement", "ts": "2026-08-15T10:05:00Z", "outcome": "success", "conformance_rate": 0.91},
    ]
    counts = aggregate_usage(records)
    assert counts == {"implement": 2}
