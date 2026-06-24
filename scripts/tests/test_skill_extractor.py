"""skill_extractor パッケージのユニットテスト。

TDD-first: テストを実装前に書いている。
- test_trajectory_sampler_basic: sessions.jsonl のモックで基本抽出テスト
- test_trajectory_sampler_max_files: max_files 制限のテスト
- test_skill_extractor_groups_by_skill: スキル別グループ化テスト
- test_generalizability_score_range: スコアが 0.0-1.0 の範囲内
"""
import json
import sys
from pathlib import Path
from typing import List
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from skill_extractor.trajectory_sampler import (
    TrajectoryRecord,
    sample_trajectories,
    _parse_jsonl_file,
    _extract_skill_from_turn,
    _find_preceding_user_prompt,
    _is_machinery_prompt,
    _determine_outcome,
    _has_error_tool_result,
)
from skill_extractor.skill_extractor import (
    extract_skill_candidates,
    _group_by_skill,
    _compute_generalizability_score,
)


# ── フィクスチャ ──────────────────────────────────────────


def _make_session_jsonl(turns: List[dict]) -> str:
    """テスト用 sessions.jsonl 文字列を生成する。"""
    return "\n".join(json.dumps(t, ensure_ascii=False) for t in turns)


@pytest.fixture
def simple_session_turns():
    """スキル呼び出し1件を含む最小セッション。"""
    return [
        {
            "type": "user",
            "message": {
                "role": "user",
                "content": "実装をお願いします",
            },
            "sessionId": "sess-001",
            "uuid": "uuid-001",
            "timestamp": "2026-01-01T00:00:00.000Z",
        },
        {
            "type": "user",
            "message": {
                "role": "user",
                "content": "<command-name>/evolve-anything:implement</command-name>\n<command-message>implement</command-message>\n<command-args></command-args>",
            },
            "sessionId": "sess-001",
            "uuid": "uuid-002",
            "timestamp": "2026-01-01T00:01:00.000Z",
        },
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": "実装を開始します。",
            },
            "sessionId": "sess-001",
            "uuid": "uuid-003",
            "timestamp": "2026-01-01T00:02:00.000Z",
        },
    ]


@pytest.fixture
def multi_skill_session_turns():
    """複数スキル呼び出しを含むセッション。"""
    return [
        {
            "type": "user",
            "message": {"role": "user", "content": "コードをレビューして"},
            "sessionId": "sess-002",
            "uuid": "uuid-010",
            "timestamp": "2026-01-02T00:00:00.000Z",
        },
        {
            "type": "user",
            "message": {
                "role": "user",
                "content": "<command-name>/evolve-anything:audit</command-name>\n<command-message>audit</command-message>",
            },
            "sessionId": "sess-002",
            "uuid": "uuid-011",
            "timestamp": "2026-01-02T00:01:00.000Z",
        },
        {
            "type": "assistant",
            "message": {"role": "assistant", "content": "audit 完了しました。スコア: 0.85"},
            "sessionId": "sess-002",
            "uuid": "uuid-012",
            "timestamp": "2026-01-02T00:02:00.000Z",
        },
        {
            "type": "user",
            "message": {"role": "user", "content": "evolve もやって"},
            "sessionId": "sess-002",
            "uuid": "uuid-013",
            "timestamp": "2026-01-02T00:03:00.000Z",
        },
        {
            "type": "user",
            "message": {
                "role": "user",
                "content": "<command-name>/evolve-anything:evolve</command-name>\n<command-message>evolve</command-message>",
            },
            "sessionId": "sess-002",
            "uuid": "uuid-014",
            "timestamp": "2026-01-02T00:04:00.000Z",
        },
        {
            "type": "assistant",
            "message": {"role": "assistant", "content": "evolve 完了しました。"},
            "sessionId": "sess-002",
            "uuid": "uuid-015",
            "timestamp": "2026-01-02T00:05:00.000Z",
        },
    ]


# ── _extract_skill_from_turn ─────────────────────────────


class TestExtractSkillFromTurn:
    def test_extracts_skill_name(self):
        turn = {
            "type": "user",
            "message": {
                "role": "user",
                "content": "<command-name>/evolve-anything:implement</command-name>",
            },
        }
        result = _extract_skill_from_turn(turn)
        assert result == "evolve-anything:implement"

    def test_strips_leading_slash(self):
        turn = {
            "type": "user",
            "message": {
                "role": "user",
                "content": "<command-name>/commit</command-name>",
            },
        }
        result = _extract_skill_from_turn(turn)
        assert result == "commit"

    def test_returns_none_when_no_command_name(self):
        turn = {
            "type": "user",
            "message": {"role": "user", "content": "ただのテキスト"},
        }
        assert _extract_skill_from_turn(turn) is None

    def test_returns_none_for_non_user_turn(self):
        turn = {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": "<command-name>/evolve-anything:implement</command-name>",
            },
        }
        # assistant ターンのコマンド名は無視
        assert _extract_skill_from_turn(turn) is None

    def test_ignores_builtin_commands(self):
        """/compact, /rename などのビルトインコマンドを無視する。"""
        for cmd in ["/compact", "/rename", "/reload-plugins", "/plugin", "/clear"]:
            turn = {
                "type": "user",
                "message": {"role": "user", "content": f"<command-name>{cmd}</command-name>"},
            }
            assert _extract_skill_from_turn(turn) is None, f"{cmd} should be ignored"

    def test_handles_system_subtype_local_command(self):
        """system/local_command ターンも command-name を抽出する。"""
        turn = {
            "type": "system",
            "subtype": "local_command",
            "content": "<command-name>/evolve-anything:audit</command-name>",
        }
        result = _extract_skill_from_turn(turn)
        assert result == "evolve-anything:audit"


# ── _parse_jsonl_file ─────────────────────────────────────


class TestParseJsonlFile:
    def test_basic_extraction(self, tmp_path, simple_session_turns):
        jsonl_path = tmp_path / "session.jsonl"
        jsonl_path.write_text(_make_session_jsonl(simple_session_turns))

        records = _parse_jsonl_file(jsonl_path)

        assert len(records) == 1
        rec = records[0]
        assert isinstance(rec, TrajectoryRecord)
        assert rec.skill_name == "evolve-anything:implement"
        assert rec.user_prompt == "実装をお願いします"
        assert rec.session_id == "sess-001"

    def test_multi_skill_extraction(self, tmp_path, multi_skill_session_turns):
        jsonl_path = tmp_path / "session.jsonl"
        jsonl_path.write_text(_make_session_jsonl(multi_skill_session_turns))

        records = _parse_jsonl_file(jsonl_path)

        assert len(records) == 2
        skill_names = {r.skill_name for r in records}
        assert skill_names == {"evolve-anything:audit", "evolve-anything:evolve"}

    def test_no_command_name_returns_empty(self, tmp_path):
        turns = [
            {
                "type": "user",
                "message": {"role": "user", "content": "ただのプロンプト"},
                "sessionId": "sess-x",
                "uuid": "uuid-x",
                "timestamp": "2026-01-01T00:00:00.000Z",
            }
        ]
        jsonl_path = tmp_path / "session.jsonl"
        jsonl_path.write_text(_make_session_jsonl(turns))

        records = _parse_jsonl_file(jsonl_path)
        assert records == []

    def test_invalid_json_lines_skipped(self, tmp_path):
        content = 'invalid json\n{"type":"user","message":{"role":"user","content":""},"sessionId":"s","uuid":"u","timestamp":"t"}\n'
        jsonl_path = tmp_path / "session.jsonl"
        jsonl_path.write_text(content)
        # Should not raise
        records = _parse_jsonl_file(jsonl_path)
        assert isinstance(records, list)

    def test_outcome_success_when_assistant_responds(self, tmp_path, simple_session_turns):
        jsonl_path = tmp_path / "session.jsonl"
        jsonl_path.write_text(_make_session_jsonl(simple_session_turns))

        records = _parse_jsonl_file(jsonl_path)
        assert len(records) == 1
        assert records[0].outcome == "success"

    def test_outcome_unknown_when_no_assistant_follows(self, tmp_path):
        turns = [
            {
                "type": "user",
                "message": {"role": "user", "content": "お願いします"},
                "sessionId": "sess-z",
                "uuid": "uuid-z1",
                "timestamp": "2026-01-01T00:00:00.000Z",
            },
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": "<command-name>/evolve-anything:evolve</command-name>",
                },
                "sessionId": "sess-z",
                "uuid": "uuid-z2",
                "timestamp": "2026-01-01T00:01:00.000Z",
            },
            # No assistant turn follows
        ]
        jsonl_path = tmp_path / "session.jsonl"
        jsonl_path.write_text(_make_session_jsonl(turns))

        records = _parse_jsonl_file(jsonl_path)
        assert len(records) == 1
        assert records[0].outcome == "unknown"


# ── 機構ターンフィルタ（#387 — routing ノイズの真因）────────────
# compaction サマリ / SKILL.md 注入 / task-notification / system-reminder /
# Stop hook feedback は type=user だがユーザー発話ではない。直前プロンプト探索で
# これらを飛ばし、本物の人間依頼を拾う（飛ばさないとパス・機構語が trigger を汚す）。


class TestMachineryFilter:
    def test_is_machinery_prompt_detects_markers(self):
        machinery = [
            "This session is being continued from a previous conversation. Summary…",
            "Base directory for this skill: /Users/x/.claude/skills/review\n# Review",
            "<system-reminder>\nThe user named this session 'fitness'.\n</system-reminder>",
            "<task-notification>\n<task-id>abc</task-id>\n<tool-use-id>toolu_01</tool-use-id>",
            "Stop hook feedback:\n先送り表現を検出しました",
            "Caveat: The messages below were generated by the user while running local commands",
        ]
        for m in machinery:
            assert _is_machinery_prompt(m) is True, f"機構未検出: {m[:40]!r}"

    def test_is_machinery_prompt_passes_real_prompts(self):
        real = [
            "実装をお願いします",
            "1bやったら、いったんprたてようかなぁ",
            "review this diff and fix bugs",
            "mergeして",
        ]
        for r in real:
            assert _is_machinery_prompt(r) is False, f"実依頼を誤検出: {r!r}"

    def _command_turn(self, skill="evolve-anything:audit", ts="2026-01-01T00:09:00.000Z"):
        return {
            "type": "user",
            "message": {"role": "user", "content": f"<command-name>/{skill}</command-name>"},
            "sessionId": "sess-m", "uuid": "u-cmd", "timestamp": ts,
        }

    def _user_turn(self, content, uuid="u", ts="2026-01-01T00:00:00.000Z"):
        return {
            "type": "user",
            "message": {"role": "user", "content": content},
            "sessionId": "sess-m", "uuid": uuid, "timestamp": ts,
        }

    def test_skips_machinery_and_finds_real_prompt(self):
        """機構ターンを飛ばして、その手前の本物の依頼を拾う。"""
        turns = [
            self._user_turn("バッチ処理を直して", uuid="u1"),
            self._user_turn(
                "Base directory for this skill: /Users/x/.claude/skills/review\n# Review",
                uuid="u2",
            ),
            self._user_turn(
                "<task-notification>\n<tool-use-id>toolu_01abc</tool-use-id>", uuid="u3"
            ),
            self._command_turn(),
        ]
        prompt = _find_preceding_user_prompt(turns, command_index=3)
        assert prompt == "バッチ処理を直して"

    def test_returns_empty_when_only_machinery_precedes(self):
        """直前が機構ターンしか無ければ空文字（機構語で trigger を汚さない）。"""
        turns = [
            self._user_turn(
                "This session is being continued from a previous conversation. Summary…",
                uuid="u1",
            ),
            self._command_turn(),
        ]
        assert _find_preceding_user_prompt(turns, command_index=1) == ""

    def test_parse_jsonl_excludes_machinery_prompt(self, tmp_path):
        """E2E: 機構ターン直後のスキル呼び出しは本物の依頼を user_prompt に持つ。"""
        turns = [
            self._user_turn("認証まわりを整理して", uuid="u1"),
            self._user_turn(
                "<system-reminder>\nbackground reminder\n</system-reminder>", uuid="u2"
            ),
            self._command_turn(),
            {
                "type": "assistant",
                "message": {"role": "assistant", "content": "対応します"},
                "sessionId": "sess-m", "uuid": "u-a", "timestamp": "2026-01-01T00:10:00.000Z",
            },
        ]
        jsonl_path = tmp_path / "s.jsonl"
        jsonl_path.write_text(_make_session_jsonl(turns))
        records = _parse_jsonl_file(jsonl_path)
        assert len(records) == 1
        assert records[0].user_prompt == "認証まわりを整理して"


# ── failure 判定（#27 — 未回復エラーで終わる軌跡のみ failure）──────
# トラジェクトリ末尾が未回復エラーのときだけ failure。エラー後に assistant
# で回復していれば success のまま（一過性エラーを failure 扱いしない FP ガード）。


def _user_tool_result_turn(*, is_error: bool, content_override=None):
    """user 型ターンで content list 内に tool_result block を持つターンを構築する。"""
    if content_override is not None:
        content = content_override
    else:
        content = [
            {
                "type": "tool_result",
                "tool_use_id": "toolu_01",
                "content": "boom" if is_error else "ok",
                "is_error": is_error,
            }
        ]
    return {
        "type": "user",
        "message": {"role": "user", "content": content},
        "sessionId": "sess-f",
        "uuid": "u-tr",
        "timestamp": "2026-01-01T00:00:00.000Z",
    }


def _assistant_text_turn(text="対応します"):
    return {
        "type": "assistant",
        "message": {"role": "assistant", "content": text},
        "sessionId": "sess-f",
        "uuid": "u-a",
        "timestamp": "2026-01-01T00:00:01.000Z",
    }


def _command_turn_f():
    return {
        "type": "user",
        "message": {"role": "user", "content": "<command-name>/evolve-anything:audit</command-name>"},
        "sessionId": "sess-f",
        "uuid": "u-cmd",
        "timestamp": "2026-01-01T00:00:00.000Z",
    }


class TestHasErrorToolResult:
    def test_user_turn_with_error_block_true(self):
        turn = _user_tool_result_turn(is_error=True)
        assert _has_error_tool_result(turn) is True

    def test_user_turn_with_non_error_block_false(self):
        turn = _user_tool_result_turn(is_error=False)
        assert _has_error_tool_result(turn) is False

    def test_content_str_safe_false(self):
        turn = _user_tool_result_turn(is_error=True, content_override="just a string")
        assert _has_error_tool_result(turn) is False

    def test_content_none_safe_false(self):
        turn = {
            "type": "user",
            "message": {"role": "user", "content": None},
            "sessionId": "s", "uuid": "u", "timestamp": "t",
        }
        assert _has_error_tool_result(turn) is False

    def test_content_non_list_dict_safe_false(self):
        turn = {
            "type": "user",
            "message": {"role": "user", "content": {"weird": "shape"}},
            "sessionId": "s", "uuid": "u", "timestamp": "t",
        }
        assert _has_error_tool_result(turn) is False

    def test_no_message_key_safe_false(self):
        assert _has_error_tool_result({"type": "user"}) is False

    def test_assistant_turn_false(self):
        # tool_result block は user ターンにしか現れない。assistant text は False。
        assert _has_error_tool_result(_assistant_text_turn()) is False


class TestDetermineOutcomeFailure:
    def test_unrecovered_error_at_tail_is_failure(self):
        # command の直後にエラー tool_result で終わり、回復 assistant 無し → failure
        turns = [_command_turn_f(), _user_tool_result_turn(is_error=True)]
        assert _determine_outcome(turns, 0) == "failure"

    def test_error_then_assistant_recovery_is_success(self):
        # エラーの後に assistant ターンで回復 → success（FP ガードの回帰ロック）
        turns = [
            _command_turn_f(),
            _user_tool_result_turn(is_error=True),
            _assistant_text_turn("リカバリしました"),
        ]
        assert _determine_outcome(turns, 0) == "success"

    def test_no_error_with_assistant_is_success(self):
        turns = [_command_turn_f(), _assistant_text_turn()]
        assert _determine_outcome(turns, 0) == "success"

    def test_empty_window_is_unknown(self):
        turns = [_command_turn_f()]
        assert _determine_outcome(turns, 0) == "unknown"

    def test_non_error_tool_result_then_no_assistant_is_unknown(self):
        # window 内に assistant も error も無い → unknown
        turns = [_command_turn_f(), _user_tool_result_turn(is_error=False)]
        assert _determine_outcome(turns, 0) == "unknown"


# ── sample_trajectories ───────────────────────────────────


class TestTrajectorySampler:
    def test_trajectory_sampler_basic(self, tmp_path, simple_session_turns, multi_skill_session_turns):
        """sessions.jsonl のモックで基本抽出テスト。"""
        pj_dir = tmp_path / "projects" / "my-project"
        pj_dir.mkdir(parents=True)

        (pj_dir / "sess1.jsonl").write_text(_make_session_jsonl(simple_session_turns))
        (pj_dir / "sess2.jsonl").write_text(_make_session_jsonl(multi_skill_session_turns))

        records = sample_trajectories(projects_root=tmp_path / "projects")

        assert len(records) >= 1
        skill_names = {r.skill_name for r in records}
        # 少なくとも1つスキルが抽出されること
        assert len(skill_names) >= 1
        # 全レコードが TrajectoryRecord であること
        for rec in records:
            assert isinstance(rec, TrajectoryRecord)

    def test_trajectory_sampler_max_files(self, tmp_path):
        """max_files=3 のとき _parse_jsonl_file の呼び出しが 3 回以内であること。"""
        pj_dir = tmp_path / "projects" / "big-project"
        pj_dir.mkdir(parents=True)

        # 10ファイル作成
        for i in range(10):
            turns = [
                {
                    "type": "user",
                    "message": {"role": "user", "content": f"prompt {i}"},
                    "sessionId": f"sess-{i:03d}",
                    "uuid": f"uuid-{i}-1",
                    "timestamp": f"2026-01-{i+1:02d}T00:00:00.000Z",
                },
                {
                    "type": "user",
                    "message": {
                        "role": "user",
                        "content": f"<command-name>/evolve-anything:audit</command-name>",
                    },
                    "sessionId": f"sess-{i:03d}",
                    "uuid": f"uuid-{i}-2",
                    "timestamp": f"2026-01-{i+1:02d}T00:01:00.000Z",
                },
                {
                    "type": "assistant",
                    "message": {"role": "assistant", "content": "done"},
                    "sessionId": f"sess-{i:03d}",
                    "uuid": f"uuid-{i}-3",
                    "timestamp": f"2026-01-{i+1:02d}T00:02:00.000Z",
                },
            ]
            (pj_dir / f"sess{i}.jsonl").write_text(
                "\n".join(json.dumps(t) for t in turns)
            )

        # _parse_jsonl_file の呼び出し回数を記録するラッパーで patch する
        import skill_extractor.trajectory_sampler as _mod

        call_count = {"n": 0}
        original_parse = _mod._parse_jsonl_file

        def _counting_parse(path):
            call_count["n"] += 1
            return original_parse(path)

        with patch.object(_mod, "_parse_jsonl_file", side_effect=_counting_parse):
            sample_trajectories(
                projects_root=tmp_path / "projects",
                max_files=3,
            )

        # 実際に読み込まれたファイル数が max_files=3 を超えていないこと
        assert call_count["n"] <= 3, (
            f"_parse_jsonl_file was called {call_count['n']} times, expected <= 3"
        )

    def test_empty_projects_root_returns_empty(self, tmp_path):
        """存在しない/空のディレクトリは空リストを返す。"""
        records = sample_trajectories(projects_root=tmp_path / "nonexistent")
        assert records == []

    def test_records_have_required_fields(self, tmp_path, simple_session_turns):
        """TrajectoryRecord の必須フィールドが揃っていること。"""
        pj_dir = tmp_path / "projects" / "test-pj"
        pj_dir.mkdir(parents=True)
        (pj_dir / "s.jsonl").write_text(_make_session_jsonl(simple_session_turns))

        records = sample_trajectories(projects_root=tmp_path / "projects")

        assert len(records) >= 1
        rec = records[0]
        assert hasattr(rec, "skill_name")
        assert hasattr(rec, "user_prompt")
        assert hasattr(rec, "outcome")
        assert hasattr(rec, "session_id")
        assert hasattr(rec, "timestamp")


# ── skill_extractor ───────────────────────────────────────


class TestGroupBySkill:
    def test_groups_correctly(self):
        records = [
            TrajectoryRecord(skill_name="audit", user_prompt="p1", outcome="success", session_id="s1", timestamp="t1"),
            TrajectoryRecord(skill_name="audit", user_prompt="p2", outcome="failure", session_id="s2", timestamp="t2"),
            TrajectoryRecord(skill_name="evolve", user_prompt="p3", outcome="success", session_id="s3", timestamp="t3"),
        ]
        grouped = _group_by_skill(records)

        assert "audit" in grouped
        assert "evolve" in grouped
        assert len(grouped["audit"]) == 2
        assert len(grouped["evolve"]) == 1

    def test_empty_input(self):
        assert _group_by_skill([]) == {}


class TestGeneralizabilityScore:
    def test_generalizability_score_range(self):
        """スコアが 0.0-1.0 の範囲内であること。"""
        test_cases = [
            # (cluster_size, success_count, specialization_factor)
            (1, 1, 1.0),
            (5, 4, 1.0),
            (10, 8, 2.0),
            (0, 0, 1.0),
            (100, 50, 0.5),
        ]
        for cluster_size, success_count, spec_factor in test_cases:
            records = [
                TrajectoryRecord(
                    skill_name="test-skill",
                    user_prompt=f"prompt {i}",
                    outcome="success" if i < success_count else "failure",
                    session_id=f"s{i}",
                    timestamp="t",
                )
                for i in range(cluster_size)
            ]
            score = _compute_generalizability_score(records, specialization_factor=spec_factor)
            assert 0.0 <= score <= 1.0, (
                f"Score {score} out of range for cluster_size={cluster_size}, "
                f"success_count={success_count}, spec_factor={spec_factor}"
            )

    def test_higher_success_rate_gives_higher_score(self):
        """成功率が高いほどスコアが高いこと。"""
        high_success = [
            TrajectoryRecord(skill_name="sk", user_prompt=f"p{i}", outcome="success", session_id=f"s{i}", timestamp="t")
            for i in range(10)
        ]
        low_success = [
            TrajectoryRecord(
                skill_name="sk",
                user_prompt=f"p{i}",
                outcome="success" if i < 2 else "failure",
                session_id=f"s{i}",
                timestamp="t",
            )
            for i in range(10)
        ]
        score_high = _compute_generalizability_score(high_success)
        score_low = _compute_generalizability_score(low_success)
        assert score_high > score_low

    def test_empty_records_returns_zero(self):
        assert _compute_generalizability_score([]) == 0.0


class TestExtractSkillCandidates:
    def test_skill_extractor_groups_by_skill(self, tmp_path, simple_session_turns, multi_skill_session_turns):
        """スキル別グループ化テスト。"""
        pj_dir = tmp_path / "projects" / "test-pj"
        pj_dir.mkdir(parents=True)
        (pj_dir / "s1.jsonl").write_text(_make_session_jsonl(simple_session_turns))
        (pj_dir / "s2.jsonl").write_text(_make_session_jsonl(multi_skill_session_turns))

        candidates = extract_skill_candidates(
            projects_root=tmp_path / "projects",
            min_cluster_size=1,
        )

        assert isinstance(candidates, list)
        # 各候補が missed_skills 形式であること
        for cand in candidates:
            assert "skill_name" in cand
            assert "session_count" in cand
            assert "generalizability_score" in cand
            assert "source" in cand
            assert cand["source"] == "codeskill_extraction"

    def test_min_cluster_size_filters(self, tmp_path, simple_session_turns):
        """min_cluster_size よりクラスタが小さい場合はフィルタされること。"""
        pj_dir = tmp_path / "projects" / "test-pj"
        pj_dir.mkdir(parents=True)
        (pj_dir / "s1.jsonl").write_text(_make_session_jsonl(simple_session_turns))
        # implement は 1回しか呼ばれていない

        # min_cluster_size=2 の場合、1回のスキルは除外される
        candidates_strict = extract_skill_candidates(
            projects_root=tmp_path / "projects",
            min_cluster_size=2,
        )
        candidates_loose = extract_skill_candidates(
            projects_root=tmp_path / "projects",
            min_cluster_size=1,
        )

        assert len(candidates_strict) <= len(candidates_loose)

    def test_generalizability_score_in_candidates(self, tmp_path, multi_skill_session_turns):
        """candidates の generalizability_score が 0.0-1.0 であること。"""
        pj_dir = tmp_path / "projects" / "test-pj"
        pj_dir.mkdir(parents=True)
        (pj_dir / "s2.jsonl").write_text(_make_session_jsonl(multi_skill_session_turns))

        candidates = extract_skill_candidates(
            projects_root=tmp_path / "projects",
            min_cluster_size=1,
        )

        for cand in candidates:
            score = cand["generalizability_score"]
            assert 0.0 <= score <= 1.0, f"Score {score} out of range for {cand['skill_name']}"
