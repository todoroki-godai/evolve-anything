"""#467 §1.5.1 実測 join ロジックの単体テスト（合成 fixture・実 ~/.claude 非依存）。

対象: `scripts/lib/measure_467_join.py`。
"""
from pathlib import Path

from measure_467_join import (
    count_skill_usage,
    find_preceding_skill,
    index_skill_usage_by_session,
    is_skill_usage_record,
    load_jsonl,
    parse_iso8601,
    resolve_preceding_skills,
    skill_md_resolves,
    summarize_corrections,
)


def test_parse_iso8601_z_and_offset_suffix_are_same_instant():
    # Z 終端と +00:00 終端は同一 instant を指す（pitfall_iso8601_lexical_compare_tz_suffix）。
    a = parse_iso8601("2026-08-15T15:00:00Z")
    b = parse_iso8601("2026-08-15T15:00:00+00:00")
    assert a == b


def test_parse_iso8601_naive_string_and_invalid():
    assert parse_iso8601(None) is None
    assert parse_iso8601("") is None
    assert parse_iso8601("not-a-timestamp") is None
    # naive（tz 無し）は UTC とみなす
    dt = parse_iso8601("2026-08-15T15:00:00")
    assert dt is not None
    assert dt.tzinfo is not None


def test_find_preceding_skill_lexical_comparison_would_get_wrong_order():
    """tz suffix 混在で辞書順比較が壊れる具体例。

    skill call: 2026-08-15T23:59:00+09:00 = 2026-08-15T14:59:00Z（correction より前）
    correction: 2026-08-15T15:00:00Z

    辞書順（文字列 `<` 比較）では "23:59:00+09:00" > "15:00:00Z" と判定され、
    真の時刻順（14:59 UTC < 15:00 UTC）と逆転する。datetime 経由の比較なら
    正しく「correction より前」と判定できる。
    """
    usage_records = [
        {
            "skill_name": "evolve",
            "session_id": "s1",
            "ts": "2026-08-15T23:59:00+09:00",
            "outcome": "success",
        },
    ]
    correction = {
        "session_id": "s1",
        "timestamp": "2026-08-15T15:00:00Z",
    }
    idx = index_skill_usage_by_session(usage_records)
    assert find_preceding_skill(correction, idx) == "evolve"

    # 素の文字列比較だとこの逆転を検出できないことを併記で示す（回帰用の対照）。
    naive_precedes = usage_records[0]["ts"] < correction["timestamp"]
    assert naive_precedes is False  # 辞書順は「前ではない」と誤判定する


def test_find_preceding_skill_picks_latest_before_correction():
    usage_records = [
        {"skill_name": "old-skill", "session_id": "s1", "ts": "2026-08-15T10:00:00Z", "outcome": "success"},
        {"skill_name": "recent-skill", "session_id": "s1", "ts": "2026-08-15T14:00:00Z", "outcome": "success"},
        {"skill_name": "after-correction", "session_id": "s1", "ts": "2026-08-15T16:00:00Z", "outcome": "success"},
    ]
    correction = {"session_id": "s1", "timestamp": "2026-08-15T15:00:00Z"}
    idx = index_skill_usage_by_session(usage_records)
    assert find_preceding_skill(correction, idx) == "recent-skill"


def test_find_preceding_skill_no_session_match_or_no_preceding_call():
    usage_records = [
        {"skill_name": "s", "session_id": "other", "ts": "2026-08-15T10:00:00Z", "outcome": "success"},
        {"skill_name": "future", "session_id": "s1", "ts": "2026-08-15T20:00:00Z", "outcome": "success"},
    ]
    idx = index_skill_usage_by_session(usage_records)
    assert find_preceding_skill({"session_id": "s1", "timestamp": "2026-08-15T15:00:00Z"}, idx) is None
    assert find_preceding_skill({"session_id": "unknown", "timestamp": "2026-08-15T15:00:00Z"}, idx) is None
    # timestamp 欠落
    assert find_preceding_skill({"session_id": "s1"}, idx) is None


def test_find_preceding_skill_excludes_exact_same_instant():
    """`dt < corr_dt` は境界で厳密に「前」のみを採用する（同時刻は除外）。

    2026-08-16 codex cold review [Should]: 辞書順比較への回帰や `<` を `>` への反転は
    既存ケースで検出できるが、同時刻の境界（`==`）は別途固定する必要がある。
    Skill 呼び出しと correction が完全同一 instant（`Z` と `+00:00` の異表記でも同一）の
    場合、先行 Skill 呼び出しは無し（None）と判定されること。
    """
    usage_records = [
        {"skill_name": "same-instant", "session_id": "s1", "ts": "2026-08-15T15:00:00+00:00", "outcome": "success"},
    ]
    correction = {"session_id": "s1", "timestamp": "2026-08-15T15:00:00Z"}
    idx = index_skill_usage_by_session(usage_records)
    assert find_preceding_skill(correction, idx) is None


def test_find_preceding_skill_finds_legacy_timestamp_keyed_skill_row():
    """旧スキーマ（`ts` でなく `timestamp` を使う Skill 行）も index に載ること。"""
    usage_records = [
        {"skill_name": "legacy-skill", "session_id": "s1", "timestamp": "2026-08-15T10:00:00Z"},
    ]
    correction = {"session_id": "s1", "timestamp": "2026-08-15T15:00:00Z"}
    idx = index_skill_usage_by_session(usage_records)
    assert find_preceding_skill(correction, idx) == "legacy-skill"


def test_is_skill_usage_record_distinguishes_agent_records():
    skill_rec = {"skill_name": "evolve", "ts": "2026-08-15T10:00:00Z", "outcome": "success", "session_id": "s1"}
    agent_rec = {
        "skill_name": "Agent:impl-worker",
        "timestamp": "2026-08-15T10:00:00Z",
        "session_id": "s1",
        "subagent_type": "impl-worker",
        "agent_id": "a1",
    }
    assert is_skill_usage_record(skill_rec) is True
    assert is_skill_usage_record(agent_rec) is False


def test_is_skill_usage_record_covers_legacy_timestamp_keyed_skill_rows():
    """2026-08-16 実データ調査: 旧スキーマの Skill 行は `ts` でなく `timestamp` を使う
    （`outcome` も持たない）。それでも Skill 呼び出しとして数える。"""
    legacy_skill_rec = {
        "skill_name": "evolve",
        "timestamp": "2026-08-15T10:00:00Z",
        "session_id": "s1",
        "project": "evolve-anything",
    }
    assert is_skill_usage_record(legacy_skill_rec) is True


def test_is_skill_usage_record_excludes_workflow_conformance_schema():
    """`skill_name` でなく `skill` を持つ別スキーマ（workflow-conformance 記録）は対象外。"""
    conformance_rec = {"skill": "evolve", "ts": "2026-08-15T10:00:00Z", "outcome": "success"}
    assert is_skill_usage_record(conformance_rec) is False


def test_count_skill_usage_excludes_agent_records():
    records = [
        {"skill_name": "evolve", "ts": "2026-08-15T10:00:00Z", "outcome": "success", "session_id": "s1"},
        {
            "skill_name": "Agent:impl-worker",
            "timestamp": "2026-08-15T10:00:00Z",
            "session_id": "s1",
            "subagent_type": "impl-worker",
            "agent_id": "a1",
        },
        {"skill_name": "audit", "ts": "2026-08-15T11:00:00Z", "outcome": "error", "session_id": "s1"},
    ]
    assert count_skill_usage(records) == 2


def test_summarize_corrections():
    corrections = [
        {"last_skill": "evolve", "source": "hook", "correction_type": "stop"},
        {"last_skill": None, "source": "reflect_confirmed", "correction_type": "semantic_idiom"},
        {"source": "reflect_confirmed", "correction_type": "semantic_idiom"},
    ]
    summary = summarize_corrections(corrections)
    assert summary["total"] == 3
    assert summary["last_skill_truthy"] == 1
    assert summary["source_counts"] == {"hook": 1, "reflect_confirmed": 2}
    assert summary["correction_type_counts"] == {"stop": 1, "semantic_idiom": 2}


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


def test_skill_md_resolves_bare_name(tmp_path):
    home = tmp_path
    skill_dir = home / ".claude" / "skills" / "evolve"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# evolve", encoding="utf-8")

    assert skill_md_resolves("evolve", home) is True
    assert skill_md_resolves("nonexistent", home) is False
    assert skill_md_resolves(None, home) is False
    assert skill_md_resolves("", home) is False


def test_skill_md_resolves_plugin_namespaced_name_never_resolves(tmp_path):
    """runner.py:417 の glob は bare 名前提。plugin:skill 形式は原理的に解決しない
    （§1.5.1 の実測結果 = 28件全件解決不能の根拠と同じ規則）。"""
    home = tmp_path
    (home / ".claude" / "skills" / "spec-keeper").mkdir(parents=True)
    (home / ".claude" / "skills" / "spec-keeper" / "SKILL.md").write_text("x", encoding="utf-8")

    assert skill_md_resolves("evolve-anything:spec-keeper", home) is False


def test_skill_md_resolves_project_local_when_absent_from_global(tmp_path):
    """`discover/runner.py:417-419` は global に加えて project 側
    （`<project_root>/.claude/skills/...`）も探索する。global に無く project にだけ
    存在するケースを解決できること（2026-08-16 codex cold review [Must]1: 修正前は
    global のみを見ており本番と契約不一致だった。project_root 未指定なら見つからず
    False のままであることも併せて確認する）。"""
    home = tmp_path / "home"
    project_root = tmp_path / "project"
    pj_skill_dir = project_root / ".claude" / "skills" / "pj-only-skill"
    pj_skill_dir.mkdir(parents=True)
    (pj_skill_dir / "SKILL.md").write_text("# pj-only-skill", encoding="utf-8")

    assert skill_md_resolves("pj-only-skill", home) is False  # project_root 未指定
    assert skill_md_resolves("pj-only-skill", home, project_root=project_root) is True


def test_skill_md_resolves_prefers_global_over_project(tmp_path):
    """global と project の両方に同名スキルがある場合、global 側が先に解決される
    （runner.py:419 の順序 `skill_dirs + [... not in skill_dirs]` の再現）。"""
    home = tmp_path / "home"
    project_root = tmp_path / "project"
    (home / ".claude" / "skills" / "shared").mkdir(parents=True)
    (home / ".claude" / "skills" / "shared" / "SKILL.md").write_text("global", encoding="utf-8")
    (project_root / ".claude" / "skills" / "shared").mkdir(parents=True)
    (project_root / ".claude" / "skills" / "shared" / "SKILL.md").write_text("pj", encoding="utf-8")

    assert skill_md_resolves("shared", home, project_root=project_root) is True


def test_load_jsonl_skips_malformed_lines(tmp_path):
    p = tmp_path / "corrections.jsonl"
    p.write_text(
        '{"a": 1}\n'
        "\n"
        "not-json\n"
        '{"a": 2}\n',
        encoding="utf-8",
    )
    records = load_jsonl(p)
    assert records == [{"a": 1}, {"a": 2}]


def test_load_jsonl_missing_file_returns_empty(tmp_path):
    assert load_jsonl(tmp_path / "does-not-exist.jsonl") == []
