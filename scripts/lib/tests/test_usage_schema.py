"""rl_common.usage_schema の Skill / Agent 判別器のテスト（#480）。

usage.jsonl は3スキーマ混在（Skill 呼出 / Agent 呼出 / workflow-conformance）で、
判別ロジックがこれまで production に存在しなかった（唯一の実装は bench 専用の
`measure_467_join.is_skill_usage_record` だった）。本テストは単一ソースを
production の6箇所が経由する前提となる判別契約を固定する。
"""
from __future__ import annotations

import sys
from pathlib import Path

_lib_dir = Path(__file__).resolve().parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

from rl_common.usage_schema import (  # noqa: E402
    bare_skill_name,
    is_agent_skill_label,
    is_agent_usage_record,
    is_skill_usage_record,
)

_SKILL_REC = {
    "skill_name": "evolve",
    "ts": "2026-08-15T10:00:00Z",
    "session_id": "s1",
    "outcome": "success",
}
_AGENT_REC = {
    "skill_name": "Agent:impl-worker",
    "subagent_type": "impl-worker",
    "agent_id": "a1",
    "timestamp": "2026-08-15T10:00:00Z",
    "session_id": "s1",
}
_CONFORMANCE_REC = {
    "skill": "implement",
    "ts": "2026-08-15T10:00:00Z",
    "outcome": "success",
}
# 2026-08-16 実データ調査: 旧スキーマの Skill 行は `timestamp` を使い `outcome` を
# 持たない（261 件実在）。docstring の旧記述「skill_name + timestamp = agent」は誤り
# だったことの回帰対照。
_LEGACY_TIMESTAMP_SKILL_REC = {
    "skill_name": "research-best-practices",
    "timestamp": "2026-05-08T23:23:25.065Z",
    "session_id": "s2",
}
# subagent_type のみ / agent_id のみでも Agent 判定できること（両方揃わない書込経路の
# 将来変化に耐える）。
_AGENT_REC_SUBAGENT_ONLY = {
    "skill_name": "Agent:general-purpose",
    "subagent_type": "general-purpose",
    "timestamp": "2026-08-15T10:00:00Z",
}
_AGENT_REC_AGENT_ID_ONLY = {
    "skill_name": "Agent:general-purpose",
    "agent_id": "a2",
    "timestamp": "2026-08-15T10:00:00Z",
}


def test_is_skill_usage_record_true_for_skill_false_for_agent():
    assert is_skill_usage_record(_SKILL_REC) is True
    assert is_skill_usage_record(_AGENT_REC) is False


def test_is_skill_usage_record_excludes_workflow_conformance_schema():
    assert is_skill_usage_record(_CONFORMANCE_REC) is False


def test_is_skill_usage_record_covers_legacy_timestamp_keyed_skill_rows():
    assert is_skill_usage_record(_LEGACY_TIMESTAMP_SKILL_REC) is True


def test_is_agent_usage_record_true_for_agent_false_for_skill_and_conformance():
    assert is_agent_usage_record(_AGENT_REC) is True
    assert is_agent_usage_record(_SKILL_REC) is False
    assert is_agent_usage_record(_CONFORMANCE_REC) is False


def test_is_agent_usage_record_detects_either_key_alone():
    assert is_agent_usage_record(_AGENT_REC_SUBAGENT_ONLY) is True
    assert is_agent_usage_record(_AGENT_REC_AGENT_ID_ONLY) is True


def test_skill_and_agent_predicates_are_mutually_exclusive_and_exhaustive_for_known_schemas():
    for rec in (_SKILL_REC, _AGENT_REC, _CONFORMANCE_REC, _LEGACY_TIMESTAMP_SKILL_REC):
        assert is_skill_usage_record(rec) != is_agent_usage_record(rec) or (
            is_skill_usage_record(rec) is False and is_agent_usage_record(rec) is False
        )
    # conformance レコードはどちらの述語にも当たらない（第3スキーマとして中立）。
    assert is_skill_usage_record(_CONFORMANCE_REC) is False
    assert is_agent_usage_record(_CONFORMANCE_REC) is False


def test_predicates_survive_duckdb_schema_unification_none_padding():
    """DuckDB read_json_auto はファイル全体で単一スキーマを推論し、その行に無い列を
    ``None`` で埋めて返す（key は必ず存在・値だけ None）。key-presence 判定
    （``"subagent_type" in record``）だとこの None 埋めで全レコードが Agent 誤判定される
    （#480 実データ検証で 1502/1502 件が誤判定される事故を実測）。値の truthiness で
    判定していれば None 埋めは無害。
    """
    duckdb_normalized_skill_rec = {
        "skill_name": "evolve",
        "ts": "2026-08-15T10:00:00Z",
        "session_id": "s1",
        "outcome": "success",
        # DuckDB がファイル内の他行（Agent 行）由来で追加した None 埋め列。
        "subagent_type": None,
        "agent_id": None,
        "timestamp": None,
        "skill": None,
    }
    duckdb_normalized_agent_rec = {
        "skill_name": "Agent:impl-worker",
        "subagent_type": "impl-worker",
        "agent_id": "",  # 実装は "" を書きうる（event に agent_id が無い場合）
        "timestamp": "2026-08-15T10:00:00Z",
        # DuckDB がファイル内の他行（Skill 行）由来で追加した None 埋め列。
        "ts": None,
        "outcome": None,
        "skill": None,
    }
    assert is_skill_usage_record(duckdb_normalized_skill_rec) is True
    assert is_agent_usage_record(duckdb_normalized_skill_rec) is False
    assert is_skill_usage_record(duckdb_normalized_agent_rec) is False
    assert is_agent_usage_record(duckdb_normalized_agent_rec) is True


def test_swap_regression_skill_and_agent_identity_not_just_counts():
    """件数が同数のケースで、件数比較だけの実装だと swap を素通りしてしまうことの回帰対照。

    Skill 3件・Agent 3件の等数フィクスチャで、どのレコードが Skill/Agent かを内容で
    検証する（総数の一致だけでは Skill と Agent を入れ替えても検出できない）。
    """
    skill_recs = [
        {"skill_name": f"s{i}", "ts": "2026-08-15T10:00:00Z", "session_id": "s1"}
        for i in range(3)
    ]
    agent_recs = [
        {
            "skill_name": f"Agent:a{i}",
            "subagent_type": f"a{i}",
            "agent_id": f"id{i}",
            "timestamp": "2026-08-15T10:00:00Z",
        }
        for i in range(3)
    ]
    all_recs = skill_recs + agent_recs

    classified_skill = [r for r in all_recs if is_skill_usage_record(r)]
    classified_agent = [r for r in all_recs if is_agent_usage_record(r)]

    assert classified_skill == skill_recs
    assert classified_agent == agent_recs
    # 件数だけを見る誤実装（len 一致のみ確認）も落ちるように、内容の恒等性を明示的に見る。
    assert len(classified_skill) == len(classified_agent) == 3


def test_is_agent_skill_label_true_for_agent_prefixed_string_value():
    assert is_agent_skill_label("Agent:impl-worker") is True
    assert is_agent_skill_label("Agent:general-purpose") is True


def test_is_agent_skill_label_false_for_plain_skill_or_falsy():
    assert is_agent_skill_label("evolve") is False
    assert is_agent_skill_label("") is False
    assert is_agent_skill_label(None) is False


def test_bare_skill_name_still_excludes_agent_labels_after_refactor():
    """bare_skill_name の Agent: 除外は is_agent_skill_label に委譲後も同じ契約を保つ（#480）。"""
    assert bare_skill_name("Agent:impl-worker") is None
    assert bare_skill_name("evolve-anything:cleanup") == "cleanup"
    assert bare_skill_name("update-config") == "update-config"
    assert bare_skill_name(None) is None
    assert bare_skill_name("") is None
