"""observe / subagent_observe 関連テスト。

PR-A: hooks/tests/test_hooks.py から機能別に分割。
共有 fixture (tmp_data_dir, patch_data_dir) は conftest.py を参照。
"""
import json
import os
import time
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import pytest

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
        with mock.patch.dict(os.environ, {}, clear=True):
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

    def test_global_skill_registers(self, patch_data_dir):
        global_prefix = str(Path.home() / ".claude" / "skills")
        event = {
            "tool_name": "Skill",
            "tool_input": {"skill": f"{global_prefix}/my-global"},
            "tool_result": {},
            "session_id": "sess-002",
        }
        observe.handle_post_tool_use(event)

        registry_file = patch_data_dir / "usage-registry.jsonl"
        assert registry_file.exists()
        record = json.loads(registry_file.read_text().strip())
        assert "project_path" in record

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
        """セッション内 subagent 数が閾値を超えたら systemMessage を stdout に出力する。"""
        session_id = "sess-warn-001"
        # 既存の subagents.jsonl に同一 session の記録を threshold-1 件追加
        subagents_file = patch_data_dir / "subagents.jsonl"
        for i in range(4):
            subagents_file.write_text(
                subagents_file.read_text() if subagents_file.exists() else ""
            )
            common.append_jsonl(
                patch_data_dir / "subagents.jsonl",
                {"session_id": session_id, "agent_type": "Explore", "timestamp": "2026-01-01T00:00:00+00:00"},
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

    def test_no_warning_below_threshold(self, patch_data_dir, tmp_path, capsys):
        """セッション内 subagent 数が閾値未満なら stdout は空。"""
        session_id = "sess-no-warn-001"
        # 4件追加（閾値 5 未満）
        for i in range(3):
            common.append_jsonl(
                patch_data_dir / "subagents.jsonl",
                {"session_id": session_id, "agent_type": "Explore", "timestamp": "2026-01-01T00:00:00+00:00"},
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
                {"session_id": session_id, "agent_type": "Explore", "timestamp": "2026-01-01T00:00:00+00:00"},
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


