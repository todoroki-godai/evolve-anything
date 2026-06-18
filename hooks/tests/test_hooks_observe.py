"""observe / subagent_observe 関連テスト。

PR-A: hooks/tests/test_hooks.py から機能別に分割。
共有 fixture (tmp_data_dir, patch_data_dir) は conftest.py を参照。
"""
import json
import os
import time
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import pytest


def _recent_ts(minutes_ago: int = 0) -> str:
    """現在時刻から minutes_ago 分前の ISO timestamp を返す（window テスト用）。"""
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()

import common
import rl_common
import session_store
import observe
import session_summary
import save_state
import restore_state
import post_compact
import subagent_observe
import workflow_context


class TestObserve:
    """observe.py のテスト。"""

    def test_skill_usage_recorded(self, patch_data_dir):
        event = {
            "tool_name": "Skill",
            "tool_input": {"skill": "my-skill", "args": "test.py"},
            "tool_result": {},
            "session_id": "sess-001",
        }
        observe.handle_post_tool_use(event)

        usage_file = patch_data_dir / "usage.jsonl"
        assert usage_file.exists()
        record = json.loads(usage_file.read_text().strip())
        assert record["skill_name"] == "my-skill"
        assert record["session_id"] == "sess-001"

    def test_skill_outcome_success(self, patch_data_dir):
        """Skill 呼び出し成功時は outcome="success" が記録される。"""
        event = {
            "tool_name": "Skill",
            "tool_input": {"skill": "my-skill", "args": ""},
            "tool_result": {"is_error": False},
            "session_id": "sess-out-001",
        }
        observe.handle_post_tool_use(event)
        record = json.loads((patch_data_dir / "usage.jsonl").read_text().strip())
        assert record["outcome"] == "success"

    def test_skill_outcome_error(self, patch_data_dir):
        """Skill 呼び出しでエラー時は outcome="error" が記録される。"""
        event = {
            "tool_name": "Skill",
            "tool_input": {"skill": "bad-skill", "args": ""},
            "tool_result": {"is_error": True, "content": "skill failed"},
            "session_id": "sess-out-002",
        }
        observe.handle_post_tool_use(event)
        record = json.loads((patch_data_dir / "usage.jsonl").read_text().strip())
        assert record["outcome"] == "error"

    def test_skill_outcome_non_dict_tool_result(self, patch_data_dir):
        """tool_result が dict でない場合は outcome="success" にフォールバック。"""
        event = {
            "tool_name": "Skill",
            "tool_input": {"skill": "my-skill", "args": ""},
            "tool_result": None,
            "session_id": "sess-out-003",
        }
        observe.handle_post_tool_use(event)
        record = json.loads((patch_data_dir / "usage.jsonl").read_text().strip())
        assert record["outcome"] == "success"

    def test_skill_usage_project_field(self, patch_data_dir):
        """Skill usage にプロジェクト名が記録される。"""
        with mock.patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": "/Users/foo/atlas-breeaders"}):
            event = {
                "tool_name": "Skill",
                "tool_input": {"skill": "my-skill", "args": ""},
                "tool_result": {},
                "session_id": "sess-proj-001",
            }
            observe.handle_post_tool_use(event)

        usage_file = patch_data_dir / "usage.jsonl"
        record = json.loads(usage_file.read_text().strip())
        assert record["project"] == "atlas-breeaders"

    def test_skill_usage_project_null_when_unset(self, patch_data_dir):
        """CLAUDE_PROJECT_DIR 未設定時は project が null。"""
        # clear=True で環境を空にすると autouse の TMPDIR 隔離も消え last_skill が
        # 実 /tmp に漏れるため、隔離 TMPDIR は保持する（#495）。
        preserved = {"TMPDIR": os.environ["TMPDIR"]} if os.environ.get("TMPDIR") else {}
        with mock.patch.dict(os.environ, preserved, clear=True):
            # CLAUDE_PROJECT_DIR を確実に削除
            os.environ.pop("CLAUDE_PROJECT_DIR", None)
            event = {
                "tool_name": "Skill",
                "tool_input": {"skill": "my-skill", "args": ""},
                "tool_result": {},
                "session_id": "sess-proj-002",
            }
            observe.handle_post_tool_use(event)

        usage_file = patch_data_dir / "usage.jsonl"
        record = json.loads(usage_file.read_text().strip())
        assert record["project"] is None

    def test_skill_usage_project_null_when_empty(self, patch_data_dir):
        """CLAUDE_PROJECT_DIR が空文字列の場合は project が null。"""
        with mock.patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": ""}):
            event = {
                "tool_name": "Skill",
                "tool_input": {"skill": "my-skill", "args": ""},
                "tool_result": {},
                "session_id": "sess-proj-003",
            }
            observe.handle_post_tool_use(event)

        usage_file = patch_data_dir / "usage.jsonl"
        record = json.loads(usage_file.read_text().strip())
        assert record["project"] is None

    def test_global_skill_registers_bare_name(self, patch_data_dir, monkeypatch, tmp_path):
        """CC が渡す bare 名（"commit" 等）の global スキルが usage-registry.jsonl に記録される。

        実 CC は skill=<bare名> を渡す（パス形式ではない）。
        bare 名の場合 is_global_skill は ~/.claude/skills/<name>/SKILL.md の存在チェックで判定する。
        (#485 — パス前置判定が永久 False だったバグの回帰テスト)
        """
        # HOME を tmp_path に差し替えて、実 ~/.claude を触らない
        fake_home = tmp_path / "fake_home"
        fake_home.mkdir()
        monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))

        # fake global skill SKILL.md を設置
        skill_dir = fake_home / ".claude" / "skills" / "commit"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("# commit skill")

        event = {
            "tool_name": "Skill",
            "tool_input": {"skill": "commit"},
            "tool_result": {},
            "session_id": "sess-bare-001",
        }
        observe.handle_post_tool_use(event)

        registry_file = patch_data_dir / "usage-registry.jsonl"
        assert registry_file.exists(), "bare 名の global スキルが usage-registry.jsonl に記録されていない"
        record = json.loads(registry_file.read_text().strip())
        assert record["skill_name"] == "commit"
        assert "project_path" in record

    def test_global_skill_registers_bare_name_non_global(self, patch_data_dir, monkeypatch, tmp_path):
        """bare 名でも ~/.claude/skills/ に存在しない PJ スキルは usage-registry に記録されない。"""
        # HOME を tmp_path に差し替えて、実 ~/.claude を触らない
        fake_home = tmp_path / "fake_home"
        fake_home.mkdir()
        monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))
        # global skills ディレクトリは作るが "local-skill" は置かない
        (fake_home / ".claude" / "skills").mkdir(parents=True)

        event = {
            "tool_name": "Skill",
            "tool_input": {"skill": "local-skill"},
            "tool_result": {},
            "session_id": "sess-bare-002",
        }
        observe.handle_post_tool_use(event)

        registry_file = patch_data_dir / "usage-registry.jsonl"
        assert not registry_file.exists(), "PJ スキルが usage-registry に誤記録された"

    def test_global_skill_registers_path_form_backward_compat(self, patch_data_dir, monkeypatch, tmp_path):
        """パス形式（後方互換）でも usage-registry.jsonl に記録される。

        将来 CC がパス形式で渡す可能性に備えて、既存の動作を維持する。
        """
        # HOME を tmp_path に差し替えて、実 ~/.claude を触らない
        fake_home = tmp_path / "fake_home"
        fake_home.mkdir()
        monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))

        # パス形式で global prefix が一致する場合
        global_prefix = str(fake_home / ".claude" / "skills")
        event = {
            "tool_name": "Skill",
            "tool_input": {"skill": f"{global_prefix}/my-global"},
            "tool_result": {},
            "session_id": "sess-path-001",
        }
        observe.handle_post_tool_use(event)

        registry_file = patch_data_dir / "usage-registry.jsonl"
        assert registry_file.exists()
        record = json.loads(registry_file.read_text().strip())
        assert "project_path" in record

    def test_observe_registered_for_skill_matcher(self):
        """observe.py が PostToolUse の Skill matcher に登録されている (#478)。

        Skill 発火が usage.jsonl に記録される唯一の経路は observe.py の
        handle_post_tool_use の Skill 分岐。これが Agent matcher にしか
        登録されていないと Skill 発火が usage registry に乗らず、
        telemetry の usage_count が構造的に 0 になり prune zero_invocation /
        skill_evolve insufficient_usage が FP 化する。
        """
        hooks_json = Path(__file__).resolve().parent.parent / "hooks.json"
        config = json.loads(hooks_json.read_text(encoding="utf-8"))
        post = config["hooks"]["PostToolUse"]

        def _commands_for(matcher: str) -> list[str]:
            cmds: list[str] = []
            for group in post:
                if group.get("matcher") == matcher:
                    cmds.extend(h.get("command", "") for h in group.get("hooks", []))
            return cmds

        skill_cmds = _commands_for("Skill")
        assert any("observe.py" in c for c in skill_cmds), (
            "observe.py must be registered for the PostToolUse Skill matcher so that "
            "Skill firings are recorded to usage.jsonl"
        )

    def test_error_recorded(self, patch_data_dir):
        event = {
            "tool_name": "Bash",
            "tool_input": {"command": "false"},
            "tool_result": {"is_error": True, "content": "exit code 1"},
            "session_id": "sess-003",
        }
        observe.handle_post_tool_use(event)

        errors_file = patch_data_dir / "errors.jsonl"
        assert errors_file.exists()
        record = json.loads(errors_file.read_text().strip())
        assert record["tool_name"] == "Bash"

    def test_error_last_skill_name(self, patch_data_dir, tmp_path):
        """エラーレコードに last_skill_name が含まれる。"""
        with mock.patch.dict(os.environ, {"TMPDIR": str(tmp_path)}):
            common.write_last_skill("sess-lsn-001", "evolve")
            event = {
                "tool_name": "Bash",
                "tool_input": {"command": "false"},
                "tool_result": {"is_error": True, "content": "exit code 1"},
                "session_id": "sess-lsn-001",
            }
            observe.handle_post_tool_use(event)

        errors_file = patch_data_dir / "errors.jsonl"
        record = json.loads(errors_file.read_text().strip())
        assert record["last_skill_name"] == "evolve"

    def test_error_last_skill_name_empty_when_no_skill(self, patch_data_dir, tmp_path):
        """last_skill がない場合は last_skill_name が空文字列。"""
        with mock.patch.dict(os.environ, {"TMPDIR": str(tmp_path)}):
            event = {
                "tool_name": "Bash",
                "tool_input": {"command": "false"},
                "tool_result": {"is_error": True, "content": "fail"},
                "session_id": "sess-lsn-none",
            }
            observe.handle_post_tool_use(event)

        errors_file = patch_data_dir / "errors.jsonl"
        record = json.loads(errors_file.read_text().strip())
        assert record["last_skill_name"] == ""

    def test_error_project_field(self, patch_data_dir):
        """errors にプロジェクト名が記録される。"""
        with mock.patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": "/Users/foo/error-proj"}):
            event = {
                "tool_name": "Bash",
                "tool_input": {"command": "false"},
                "tool_result": {"is_error": True, "content": "fail"},
                "session_id": "sess-proj-003",
            }
            observe.handle_post_tool_use(event)

        errors_file = patch_data_dir / "errors.jsonl"
        record = json.loads(errors_file.read_text().strip())
        assert record["project"] == "error-proj"

    def test_non_skill_tool_no_usage(self, patch_data_dir):
        event = {
            "tool_name": "Read",
            "tool_input": {"file_path": "/tmp/x"},
            "tool_result": {},
            "session_id": "sess-004",
        }
        observe.handle_post_tool_use(event)

        usage_file = patch_data_dir / "usage.jsonl"
        assert not usage_file.exists()

    def test_write_failure_silent(self, tmp_data_dir):
        """JSONL 書き込み失敗時、例外を投げない。"""
        bad_dir = tmp_data_dir / "nonexistent_sub" / "deep"
        with mock.patch.object(common, "DATA_DIR", bad_dir):
            with mock.patch.object(common, "ensure_data_dir"):
                # ensure_data_dir を無効化して書き込み先を壊す
                event = {
                    "tool_name": "Skill",
                    "tool_input": {"skill": "test"},
                    "tool_result": {},
                    "session_id": "sess-005",
                }
                # 例外が出ないことを確認
                observe.handle_post_tool_use(event)

    def test_directory_auto_created(self, tmp_path):
        """ディレクトリが存在しない場合 MUST 自動作成する。"""
        new_dir = tmp_path / "new-rl-anything"
        with mock.patch.object(common, "DATA_DIR", new_dir), \
             mock.patch.object(rl_common, "DATA_DIR", new_dir), \
             mock.patch.object(rl_common, "CHECKPOINTS_DIR", new_dir / "checkpoints"):
            event = {
                "tool_name": "Skill",
                "tool_input": {"skill": "test"},
                "tool_result": {},
                "session_id": "sess-006",
            }
            observe.handle_post_tool_use(event)

        assert new_dir.exists()

    def test_agent_tool_usage_recorded(self, patch_data_dir):
        """Agent ツール呼び出しが usage.jsonl に記録される。"""
        event = {
            "tool_name": "Agent",
            "tool_input": {
                "subagent_type": "Explore",
                "prompt": "codebase を探索してください",
            },
            "tool_result": {},
            "session_id": "sess-100",
        }
        observe.handle_post_tool_use(event)

        usage_file = patch_data_dir / "usage.jsonl"
        assert usage_file.exists()
        record = json.loads(usage_file.read_text().strip())
        assert record["skill_name"] == "Agent:Explore"
        assert record["subagent_type"] == "Explore"
        assert record["prompt"] == "codebase を探索してください"
        assert record["session_id"] == "sess-100"

    def test_agent_usage_project_field(self, patch_data_dir):
        """Agent usage にプロジェクト名が記録される。"""
        with mock.patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": "/Users/foo/my-project"}):
            event = {
                "tool_name": "Agent",
                "tool_input": {"subagent_type": "Explore", "prompt": "explore"},
                "tool_result": {},
                "session_id": "sess-proj-100",
            }
            observe.handle_post_tool_use(event)

        usage_file = patch_data_dir / "usage.jsonl"
        record = json.loads(usage_file.read_text().strip())
        assert record["project"] == "my-project"

    def test_agent_tool_prompt_truncated(self, patch_data_dir):
        """Agent ツールの prompt が 200 文字に切り詰められる。"""
        long_prompt = "あ" * 300
        event = {
            "tool_name": "Agent",
            "tool_input": {
                "subagent_type": "general-purpose",
                "prompt": long_prompt,
            },
            "tool_result": {},
            "session_id": "sess-101",
        }
        observe.handle_post_tool_use(event)

        usage_file = patch_data_dir / "usage.jsonl"
        record = json.loads(usage_file.read_text().strip())
        assert len(record["prompt"]) == 200

    def test_agent_tool_empty_prompt(self, patch_data_dir):
        """Agent ツールの prompt が空の場合、空文字列として記録される。"""
        event = {
            "tool_name": "Agent",
            "tool_input": {
                "subagent_type": "Explore",
                "prompt": "",
            },
            "tool_result": {},
            "session_id": "sess-102",
        }
        observe.handle_post_tool_use(event)

        usage_file = patch_data_dir / "usage.jsonl"
        record = json.loads(usage_file.read_text().strip())
        assert record["prompt"] == ""

    def test_agent_tool_missing_subagent_type(self, patch_data_dir):
        """Agent ツールの subagent_type が未指定の場合、'unknown' として記録される。"""
        event = {
            "tool_name": "Agent",
            "tool_input": {
                "prompt": "何かやって",
            },
            "tool_result": {},
            "session_id": "sess-103",
        }
        observe.handle_post_tool_use(event)

        usage_file = patch_data_dir / "usage.jsonl"
        record = json.loads(usage_file.read_text().strip())
        assert record["skill_name"] == "Agent:unknown"
        assert record["subagent_type"] == "unknown"

    def test_agent_tool_null_subagent_type(self, patch_data_dir):
        """Agent ツールの subagent_type が None の場合、'unknown' として記録される。"""
        event = {
            "tool_name": "Agent",
            "tool_input": {
                "subagent_type": None,
                "prompt": "何かやって",
            },
            "tool_result": {},
            "session_id": "sess-104",
        }
        observe.handle_post_tool_use(event)

        usage_file = patch_data_dir / "usage.jsonl"
        record = json.loads(usage_file.read_text().strip())
        assert record["subagent_type"] == "unknown"

    def test_agent_with_parent_skill(self, patch_data_dir, tmp_path):
        """ワークフロー文脈がある場合、parent_skill と workflow_id が付与される。"""
        ctx = {
            "skill_name": "opsx:refine",
            "session_id": "sess-110",
            "workflow_id": "wf-test1234",
            "started_at": "2026-03-03T10:00:00+00:00",
        }
        with mock.patch.dict(os.environ, {"TMPDIR": str(tmp_path)}):
            ctx_path = tmp_path / "rl-anything-workflow-sess-110.json"
            ctx_path.write_text(json.dumps(ctx))

            event = {
                "tool_name": "Agent",
                "tool_input": {"subagent_type": "Explore", "prompt": "explore"},
                "tool_result": {},
                "session_id": "sess-110",
            }
            observe.handle_post_tool_use(event)

        usage_file = patch_data_dir / "usage.jsonl"
        record = json.loads(usage_file.read_text().strip())
        assert record["parent_skill"] == "opsx:refine"
        assert record["workflow_id"] == "wf-test1234"

    def test_agent_without_parent_skill(self, patch_data_dir, tmp_path):
        """ワークフロー文脈がない場合、parent_skill と workflow_id は null。"""
        with mock.patch.dict(os.environ, {"TMPDIR": str(tmp_path)}):
            event = {
                "tool_name": "Agent",
                "tool_input": {"subagent_type": "Explore", "prompt": "explore"},
                "tool_result": {},
                "session_id": "sess-111",
            }
            observe.handle_post_tool_use(event)

        usage_file = patch_data_dir / "usage.jsonl"
        record = json.loads(usage_file.read_text().strip())
        assert record["parent_skill"] is None
        assert record["workflow_id"] is None


class TestSubagentObserve:
    """subagent_observe.py のテスト。"""

    def test_subagent_stop_recorded(self, patch_data_dir):
        """SubagentStop イベントが subagents.jsonl に記録される。"""
        event = {
            "agent_type": "Explore",
            "agent_id": "agent-001",
            "last_assistant_message": "探索が完了しました",
            "agent_transcript_path": "/tmp/transcript.jsonl",
            "session_id": "sess-200",
        }
        subagent_observe.handle_subagent_stop(event)

        subagents_file = patch_data_dir / "subagents.jsonl"
        assert subagents_file.exists()
        record = json.loads(subagents_file.read_text().strip())
        assert record["agent_type"] == "Explore"
        assert record["agent_id"] == "agent-001"
        assert record["last_assistant_message"] == "探索が完了しました"
        assert record["agent_transcript_path"] == "/tmp/transcript.jsonl"
        assert record["session_id"] == "sess-200"
        assert "timestamp" in record

    def test_last_message_truncated(self, patch_data_dir):
        """last_assistant_message が 500 文字に切り詰められる。"""
        long_message = "あ" * 600
        event = {
            "agent_type": "general-purpose",
            "agent_id": "agent-002",
            "last_assistant_message": long_message,
            "agent_transcript_path": "/tmp/t.jsonl",
            "session_id": "sess-201",
        }
        subagent_observe.handle_subagent_stop(event)

        subagents_file = patch_data_dir / "subagents.jsonl"
        record = json.loads(subagents_file.read_text().strip())
        assert len(record["last_assistant_message"]) == 500

    def test_empty_last_message(self, patch_data_dir):
        """last_assistant_message が空の場合、空文字列として記録される。"""
        event = {
            "agent_type": "Explore",
            "agent_id": "agent-003",
            "last_assistant_message": "",
            "agent_transcript_path": "/tmp/t.jsonl",
            "session_id": "sess-202",
        }
        subagent_observe.handle_subagent_stop(event)

        subagents_file = patch_data_dir / "subagents.jsonl"
        record = json.loads(subagents_file.read_text().strip())
        assert record["last_assistant_message"] == ""

    def test_null_last_message(self, patch_data_dir):
        """last_assistant_message が null の場合、空文字列として記録される。"""
        event = {
            "agent_type": "Explore",
            "agent_id": "agent-004",
            "last_assistant_message": None,
            "agent_transcript_path": "/tmp/t.jsonl",
            "session_id": "sess-203",
        }
        subagent_observe.handle_subagent_stop(event)

        subagents_file = patch_data_dir / "subagents.jsonl"
        record = json.loads(subagents_file.read_text().strip())
        assert record["last_assistant_message"] == ""

    def test_nonexistent_transcript_path(self, patch_data_dir):
        """agent_transcript_path が存在しないパスでもパスのみ記録する。"""
        event = {
            "agent_type": "Explore",
            "agent_id": "agent-005",
            "last_assistant_message": "done",
            "agent_transcript_path": "/nonexistent/path/transcript.jsonl",
            "session_id": "sess-204",
        }
        subagent_observe.handle_subagent_stop(event)

        subagents_file = patch_data_dir / "subagents.jsonl"
        record = json.loads(subagents_file.read_text().strip())
        assert record["agent_transcript_path"] == "/nonexistent/path/transcript.jsonl"

    def test_write_failure_silent(self, tmp_data_dir):
        """書き込み失敗時、例外を投げない（MUST NOT block session）。"""
        bad_dir = tmp_data_dir / "nonexistent_sub" / "deep"
        with mock.patch.object(common, "DATA_DIR", bad_dir):
            with mock.patch.object(common, "ensure_data_dir"):
                event = {
                    "agent_type": "Explore",
                    "agent_id": "agent-006",
                    "last_assistant_message": "test",
                    "agent_transcript_path": "/tmp/t.jsonl",
                    "session_id": "sess-205",
                }
                subagent_observe.handle_subagent_stop(event)

    def test_parent_skill_attached(self, patch_data_dir, tmp_path):
        """ワークフロー文脈がある場合、parent_skill が subagents.jsonl に付与される。"""
        ctx = {
            "skill_name": "opsx:refine",
            "session_id": "sess-210",
            "workflow_id": "wf-sub12345",
        }
        with mock.patch.dict(os.environ, {"TMPDIR": str(tmp_path)}):
            ctx_path = tmp_path / "rl-anything-workflow-sess-210.json"
            ctx_path.write_text(json.dumps(ctx))

            event = {
                "agent_type": "Explore",
                "agent_id": "agent-010",
                "last_assistant_message": "done",
                "agent_transcript_path": "/tmp/t.jsonl",
                "session_id": "sess-210",
            }
            subagent_observe.handle_subagent_stop(event)

        subagents_file = patch_data_dir / "subagents.jsonl"
        record = json.loads(subagents_file.read_text().strip())
        assert record["parent_skill"] == "opsx:refine"
        assert record["workflow_id"] == "wf-sub12345"

    def test_no_parent_skill(self, patch_data_dir, tmp_path):
        """ワークフロー文脈がない場合、parent_skill は null。"""
        with mock.patch.dict(os.environ, {"TMPDIR": str(tmp_path)}):
            event = {
                "agent_type": "Explore",
                "agent_id": "agent-011",
                "last_assistant_message": "done",
                "agent_transcript_path": "/tmp/t.jsonl",
                "session_id": "sess-211",
            }
            subagent_observe.handle_subagent_stop(event)

        subagents_file = patch_data_dir / "subagents.jsonl"
        record = json.loads(subagents_file.read_text().strip())
        assert record["parent_skill"] is None
        assert record["workflow_id"] is None

    def test_warning_emitted_when_threshold_exceeded(self, patch_data_dir, tmp_path, capsys):
        """直近 window 内の subagent 数が閾値に達したら systemMessage を stdout に出力する。"""
        session_id = "sess-warn-001"
        # 既存の subagents.jsonl に同一 session の「直近」記録を threshold-1 件追加
        recent = _recent_ts(minutes_ago=1)
        for i in range(4):
            common.append_jsonl(
                patch_data_dir / "subagents.jsonl",
                {"session_id": session_id, "agent_type": "Explore", "timestamp": recent},
            )

        # 5件目（閾値 = デフォルト 5）を追加 → 警告が出る
        with mock.patch.dict(os.environ, {"TMPDIR": str(tmp_path)}):
            event = {
                "agent_type": "Explore",
                "agent_id": "agent-warn-01",
                "last_assistant_message": "done",
                "agent_transcript_path": "/tmp/t.jsonl",
                "session_id": session_id,
            }
            subagent_observe.handle_subagent_stop(event)

        out = capsys.readouterr().out
        output = json.loads(out)
        assert "systemMessage" in output
        assert "5" in output["systemMessage"]

    def test_no_warning_when_subagents_outside_window(self, patch_data_dir, tmp_path, capsys):
        """累積では閾値超過でも、古い記録が window 外なら警告しない（長時間セッションの誤検知防止）。"""
        session_id = "sess-window-old-001"
        # window (5分) より前の記録を 10 件 → 累積では閾値超過だが window 外
        old = _recent_ts(minutes_ago=30)
        for i in range(10):
            common.append_jsonl(
                patch_data_dir / "subagents.jsonl",
                {"session_id": session_id, "agent_type": "Explore", "timestamp": old},
            )

        # 直近の 1 件目を追加 → window 内は 1 件のみなので警告なし
        with mock.patch.dict(os.environ, {"TMPDIR": str(tmp_path)}):
            event = {
                "agent_type": "Explore",
                "agent_id": "agent-window-01",
                "last_assistant_message": "done",
                "agent_transcript_path": "/tmp/t.jsonl",
                "session_id": session_id,
            }
            subagent_observe.handle_subagent_stop(event)

        out = capsys.readouterr().out
        assert out.strip() == ""

    def test_window_minutes_configurable(self, patch_data_dir, tmp_path, capsys):
        """subagent_window_minutes を広げると window 外だった記録も計上され警告する。"""
        session_id = "sess-window-cfg-001"
        # 10分前の記録を 4 件（デフォルト window 5 分なら圏外、60 分なら圏内）
        old = _recent_ts(minutes_ago=10)
        for i in range(4):
            common.append_jsonl(
                patch_data_dir / "subagents.jsonl",
                {"session_id": session_id, "agent_type": "Explore", "timestamp": old},
            )

        with mock.patch.dict(
            os.environ,
            {"TMPDIR": str(tmp_path), "CLAUDE_PLUGIN_OPTION_subagent_window_minutes": "60"},
        ):
            event = {
                "agent_type": "Explore",
                "agent_id": "agent-window-cfg-01",
                "last_assistant_message": "done",
                "agent_transcript_path": "/tmp/t.jsonl",
                "session_id": session_id,
            }
            subagent_observe.handle_subagent_stop(event)

        out = capsys.readouterr().out
        output = json.loads(out)
        assert "systemMessage" in output

    def test_warning_includes_additional_context_for_claude(self, patch_data_dir, tmp_path, capsys):
        """閾値超過時、Claude が読んで行動できる hookSpecificOutput.additionalContext を出力する。

        systemMessage は user UI 向けで Claude には届かない。subagent-guard.md の
        「閾値超過警告が出たら作業を一時停止してユーザーに現状説明」を実際にエンフォース
        するには Claude が読める additionalContext が必要（CC v2.1.163）。
        """
        session_id = "sess-warn-ac-001"
        for _ in range(4):
            common.append_jsonl(
                patch_data_dir / "subagents.jsonl",
                {"session_id": session_id, "agent_type": "Explore", "timestamp": _recent_ts(minutes_ago=1)},
            )

        with mock.patch.dict(os.environ, {"TMPDIR": str(tmp_path)}):
            event = {
                "agent_type": "Explore",
                "agent_id": "agent-ac-01",
                "last_assistant_message": "done",
                "agent_transcript_path": "/tmp/t.jsonl",
                "session_id": session_id,
            }
            subagent_observe.handle_subagent_stop(event)

        out = capsys.readouterr().out
        output = json.loads(out)
        hso = output["hookSpecificOutput"]
        assert hso["hookEventName"] == "SubagentStop"
        assert "5" in hso["additionalContext"]
        # subagent-guard の行動指示（一時停止）が文面に含まれる
        assert "一時停止" in hso["additionalContext"]

    def test_no_warning_below_threshold(self, patch_data_dir, tmp_path, capsys):
        """セッション内 subagent 数が閾値未満なら stdout は空。"""
        session_id = "sess-no-warn-001"
        # 4件追加（閾値 5 未満）
        for i in range(3):
            common.append_jsonl(
                patch_data_dir / "subagents.jsonl",
                {"session_id": session_id, "agent_type": "Explore", "timestamp": _recent_ts(minutes_ago=1)},
            )

        with mock.patch.dict(os.environ, {"TMPDIR": str(tmp_path)}):
            event = {
                "agent_type": "Explore",
                "agent_id": "agent-no-warn-01",
                "last_assistant_message": "done",
                "agent_transcript_path": "/tmp/t.jsonl",
                "session_id": session_id,
            }
            subagent_observe.handle_subagent_stop(event)

        out = capsys.readouterr().out
        assert out.strip() == ""

    def test_warning_threshold_configurable(self, patch_data_dir, tmp_path, capsys):
        """userConfig で閾値を変更できる。"""
        session_id = "sess-warn-custom-001"
        # 2件追加（カスタム閾値 = 3 に設定）
        for i in range(2):
            common.append_jsonl(
                patch_data_dir / "subagents.jsonl",
                {"session_id": session_id, "agent_type": "Explore", "timestamp": _recent_ts(minutes_ago=1)},
            )

        with mock.patch.dict(
            os.environ,
            {"TMPDIR": str(tmp_path), "CLAUDE_PLUGIN_OPTION_subagent_warning_threshold": "3"},
        ):
            event = {
                "agent_type": "Explore",
                "agent_id": "agent-cust-01",
                "last_assistant_message": "done",
                "agent_transcript_path": "/tmp/t.jsonl",
                "session_id": session_id,
            }
            subagent_observe.handle_subagent_stop(event)

        out = capsys.readouterr().out
        output = json.loads(out)
        assert "systemMessage" in output


class TestCountDistinctAgents:
    """_count_recent_session_subagents は SubagentStop 記録数でなく distinct agent を数える（#574）。

    長命 background worker は idle のたびに SubagentStop を再発火し、同一 agent_id が
    複数行 append される。記録行数を数えると 1 個のワーカーが窓内で何度 idle になったかまで
    加算され、distinct な subagent 数を水増しして偽の暴走警告を出す。
    """

    def _append(self, data_dir, session_id, agent_id=None, agent_type="Explore", minutes_ago=1):
        rec = {
            "session_id": session_id,
            "agent_type": agent_type,
            "timestamp": _recent_ts(minutes_ago=minutes_ago),
        }
        if agent_id is not None:
            rec["agent_id"] = agent_id
        common.append_jsonl(data_dir / "subagents.jsonl", rec)

    def test_same_agent_id_repeated_counts_one(self, patch_data_dir):
        """同一 agent_id の SubagentStop が N 行あっても distinct は 1。"""
        sid = "sess-distinct-001"
        for _ in range(18):
            self._append(patch_data_dir, sid, agent_id="agent-aaa")
        assert subagent_observe._count_recent_session_subagents(sid, 5) == 1

    def test_distinct_agent_ids_counted_each(self, patch_data_dir):
        """異なる agent_id は各 1 として数える。"""
        sid = "sess-distinct-002"
        for i in range(4):
            # 各 agent_id を複数回 append しても distinct 数は agent 数に一致
            for _ in range(3):
                self._append(patch_data_dir, sid, agent_id=f"agent-{i}")
        assert subagent_observe._count_recent_session_subagents(sid, 5) == 4

    def test_records_without_agent_id_counted_individually(self, patch_data_dir):
        """agent_id 欠落レコードは個別カウント（識別子なしを 1 に潰すと暴走を見逃すため保守側）。"""
        sid = "sess-distinct-003"
        for _ in range(5):
            self._append(patch_data_dir, sid, agent_id=None)
        assert subagent_observe._count_recent_session_subagents(sid, 5) == 5

    def test_outside_window_excluded(self, patch_data_dir):
        """窓外の記録は distinct カウントからも除外する。"""
        sid = "sess-distinct-004"
        for _ in range(10):
            self._append(patch_data_dir, sid, agent_id="agent-old", minutes_ago=30)
        self._append(patch_data_dir, sid, agent_id="agent-new", minutes_ago=1)
        assert subagent_observe._count_recent_session_subagents(sid, 5) == 1

    def test_idle_reemit_does_not_trigger_false_warning(self, patch_data_dir, tmp_path, capsys):
        """2 個のワーカーが窓内で何度 idle 再発火しても閾値 5 に達しない（#574 の偽警告再現）。"""
        sid = "sess-distinct-005"
        # worker A/B がそれぞれ idle 再発火で 10 回ずつ記録（記録数 20 だが distinct は 2）
        for _ in range(10):
            self._append(patch_data_dir, sid, agent_id="worker-A")
            self._append(patch_data_dir, sid, agent_id="worker-B")

        with mock.patch.dict(os.environ, {"TMPDIR": str(tmp_path)}):
            event = {
                "agent_type": "Explore",
                "agent_id": "worker-A",  # 既存ワーカーの再発火
                "last_assistant_message": "done",
                "agent_transcript_path": "/tmp/t.jsonl",
                "session_id": sid,
            }
            subagent_observe.handle_subagent_stop(event)

        # distinct は worker-A / worker-B の 2 個 → 閾値 5 未満 → 警告なし
        assert capsys.readouterr().out.strip() == ""


