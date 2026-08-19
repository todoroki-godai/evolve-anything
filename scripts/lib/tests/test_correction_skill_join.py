"""`correction_skill_join.py`（#478）の単体テスト。

production から使う read-time join の単一ソース。合成 fixture のみ・実 ``~/.claude`` 非依存。
"""
from correction_skill_join import (
    attach_last_skill,
    find_preceding_skill,
    index_skill_usage_by_session,
    parse_iso8601,
    resolve_preceding_skills,
)


def test_parse_iso8601_z_and_offset_suffix_are_same_instant():
    a = parse_iso8601("2026-08-15T15:00:00Z")
    b = parse_iso8601("2026-08-15T15:00:00+00:00")
    assert a == b


def test_find_preceding_skill_picks_latest_before_correction():
    usage_records = [
        {"skill_name": "old-skill", "session_id": "s1", "ts": "2026-08-15T10:00:00Z", "outcome": "success"},
        {"skill_name": "recent-skill", "session_id": "s1", "ts": "2026-08-15T14:00:00Z", "outcome": "success"},
        {"skill_name": "after-correction", "session_id": "s1", "ts": "2026-08-15T16:00:00Z", "outcome": "success"},
    ]
    correction = {"session_id": "s1", "timestamp": "2026-08-15T15:00:00Z"}
    idx = index_skill_usage_by_session(usage_records)
    assert find_preceding_skill(correction, idx) == "recent-skill"


def test_find_preceding_skill_order_reversed_returns_none():
    """陽性対照の裏返し: skill 呼び出しが correction より後ろにしか無ければ None
    （#478 完了条件「順序が逆なら None のまま」）。"""
    usage_records = [
        {"skill_name": "after", "session_id": "s1", "ts": "2026-08-15T20:00:00Z", "outcome": "success"},
    ]
    correction = {"session_id": "s1", "timestamp": "2026-08-15T15:00:00Z"}
    idx = index_skill_usage_by_session(usage_records)
    assert find_preceding_skill(correction, idx) is None


def test_resolve_preceding_skills_order_matches_input():
    corrections = [
        {"session_id": "s1", "timestamp": "2026-08-15T15:00:00Z"},
        {"session_id": "s2", "timestamp": "2026-08-15T15:00:00Z"},
    ]
    usage_records = [
        {"skill_name": "a", "session_id": "s1", "ts": "2026-08-15T10:00:00Z", "outcome": "success"},
    ]
    resolved = resolve_preceding_skills(corrections, usage_records)
    assert resolved == ["a", None]


# --- attach_last_skill（production consumer が呼ぶ入口） ---


def test_attach_last_skill_fills_missing_value_from_join():
    """陽性対照ではなく本丸: last_skill が None の correction を usage.jsonl から埋める。"""
    corrections = [
        {"session_id": "s1", "timestamp": "2026-08-15T15:00:00Z", "last_skill": None},
    ]
    usage_records = [
        {"skill_name": "evolve", "session_id": "s1", "ts": "2026-08-15T14:00:00Z", "outcome": "success"},
    ]
    result = attach_last_skill(corrections, usage_records)
    assert result[0]["last_skill"] == "evolve"
    # 入力を破壊しない（呼び出し側の副作用汚染を避ける）
    assert corrections[0]["last_skill"] is None


def test_attach_last_skill_preserves_existing_truthy_value():
    """陽性対照: すでに last_skill が明示的にセットされている correction は上書きしない
    （旧 hook writer 由来の値を read-time join で潰さない）。"""
    corrections = [
        {"session_id": "s1", "timestamp": "2026-08-15T15:00:00Z", "last_skill": "explicit-skill"},
    ]
    usage_records = [
        {"skill_name": "joined-skill", "session_id": "s1", "ts": "2026-08-15T14:00:00Z", "outcome": "success"},
    ]
    result = attach_last_skill(corrections, usage_records)
    assert result[0]["last_skill"] == "explicit-skill"


def test_attach_last_skill_no_preceding_call_stays_none():
    corrections = [{"session_id": "s1", "timestamp": "2026-08-15T15:00:00Z"}]
    usage_records = []
    result = attach_last_skill(corrections, usage_records)
    assert result[0]["last_skill"] is None


def test_attach_last_skill_missing_session_id_stays_none():
    """陰性試験クラス: session_id 欠落。"""
    corrections = [{"timestamp": "2026-08-15T15:00:00Z"}]
    usage_records = [
        {"skill_name": "evolve", "session_id": "s1", "ts": "2026-08-15T14:00:00Z", "outcome": "success"},
    ]
    result = attach_last_skill(corrections, usage_records)
    assert result[0]["last_skill"] is None


def test_attach_last_skill_excludes_agent_usage_records():
    """陰性試験クラス: 直前の呼び出しが Agent（Skill でない）なら拾わない。"""
    corrections = [{"session_id": "s1", "timestamp": "2026-08-15T15:00:00Z"}]
    usage_records = [
        {
            "skill_name": "Agent:impl-worker",
            "session_id": "s1",
            "ts": "2026-08-15T14:00:00Z",
            "subagent_type": "impl-worker",
            "agent_id": "a1",
        },
    ]
    result = attach_last_skill(corrections, usage_records)
    assert result[0]["last_skill"] is None
