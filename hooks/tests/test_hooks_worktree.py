"""worktree info / observe enrichment / subagent worktree 関連テスト。

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


class TestExtractWorktreeInfo:
    """common.extract_worktree_info() のテスト。"""

    def test_worktree_present(self):
        """worktree フィールドがある event から name/branch を抽出する。"""
        event = {
            "worktree": {
                "name": "feature-branch-wt",
                "path": "/tmp/worktrees/feature",
                "branch": "feature/login",
                "original_repo_dir": "/home/user/project",
            }
        }
        result = common.extract_worktree_info(event)
        assert result == {"name": "feature-branch-wt", "branch": "feature/login"}

    def test_worktree_absent(self):
        """worktree フィールドがない event では None を返す。"""
        event = {"tool_name": "Skill", "session_id": "s1"}
        result = common.extract_worktree_info(event)
        assert result is None

    def test_worktree_not_dict(self):
        """worktree が dict でない場合は None を返す。"""
        event = {"worktree": "not-a-dict"}
        assert common.extract_worktree_info(event) is None

    def test_worktree_empty_dict(self):
        """worktree が空 dict の場合は None を返す。"""
        event = {"worktree": {}}
        assert common.extract_worktree_info(event) is None

    def test_worktree_name_only(self):
        """name のみの場合でも返す。"""
        event = {"worktree": {"name": "my-wt"}}
        result = common.extract_worktree_info(event)
        assert result == {"name": "my-wt", "branch": ""}

    def test_worktree_branch_only(self):
        """branch のみの場合でも返す。"""
        event = {"worktree": {"branch": "main"}}
        result = common.extract_worktree_info(event)
        assert result == {"name": "", "branch": "main"}

    def test_path_excluded(self):
        """path と original_repo_dir は返却に含まれない。"""
        event = {"worktree": {"name": "wt", "branch": "b", "path": "/secret", "original_repo_dir": "/orig"}}
        result = common.extract_worktree_info(event)
        assert "path" not in result
        assert "original_repo_dir" not in result


# --- v2.1.78: observe.py agent_id / worktree テスト ---


class TestObserveEnrichment:
    """observe.py の agent_id / worktree enrichment テスト。"""

    def test_agent_id_recorded(self, patch_data_dir):
        """Agent 記録に agent_id が含まれる。"""
        event = {
            "tool_name": "Agent",
            "tool_input": {"subagent_type": "Explore", "prompt": "test"},
            "tool_result": {},
            "session_id": "sess-enrich-001",
            "agent_id": "agent-abc-123",
        }
        observe.handle_post_tool_use(event)

        usage_file = patch_data_dir / "usage.jsonl"
        record = json.loads(usage_file.read_text().strip())
        assert record["agent_id"] == "agent-abc-123"

    def test_agent_worktree_recorded(self, patch_data_dir):
        """Agent 記録に worktree 情報が含まれる。"""
        event = {
            "tool_name": "Agent",
            "tool_input": {"subagent_type": "Explore", "prompt": "test"},
            "tool_result": {},
            "session_id": "sess-enrich-002",
            "worktree": {"name": "wt-1", "branch": "feat/x", "path": "/tmp/wt"},
        }
        observe.handle_post_tool_use(event)

        usage_file = patch_data_dir / "usage.jsonl"
        record = json.loads(usage_file.read_text().strip())
        assert record["worktree"] == {"name": "wt-1", "branch": "feat/x"}

    def test_agent_no_worktree_key_omitted(self, patch_data_dir):
        """worktree がない場合はキー自体が省略される。"""
        event = {
            "tool_name": "Agent",
            "tool_input": {"subagent_type": "Explore", "prompt": "test"},
            "tool_result": {},
            "session_id": "sess-enrich-003",
        }
        observe.handle_post_tool_use(event)

        usage_file = patch_data_dir / "usage.jsonl"
        record = json.loads(usage_file.read_text().strip())
        assert "worktree" not in record

    def test_skill_worktree_recorded(self, patch_data_dir):
        """Skill 記録にも worktree 情報が含まれる。"""
        event = {
            "tool_name": "Skill",
            "tool_input": {"skill": "my-skill", "args": ""},
            "tool_result": {},
            "session_id": "sess-enrich-004",
            "worktree": {"name": "wt-2", "branch": "feat/y", "path": "/tmp/wt2"},
        }
        observe.handle_post_tool_use(event)

        usage_file = patch_data_dir / "usage.jsonl"
        record = json.loads(usage_file.read_text().strip())
        assert record["worktree"] == {"name": "wt-2", "branch": "feat/y"}

    def test_error_worktree_recorded(self, patch_data_dir):
        """error 記録にも worktree 情報が含まれる。"""
        event = {
            "tool_name": "Bash",
            "tool_input": {"command": "false"},
            "tool_result": {"is_error": True, "content": "failed"},
            "session_id": "sess-enrich-005",
            "worktree": {"name": "wt-3", "branch": "feat/z"},
        }
        observe.handle_post_tool_use(event)

        errors_file = patch_data_dir / "errors.jsonl"
        record = json.loads(errors_file.read_text().strip())
        assert record["worktree"] == {"name": "wt-3", "branch": "feat/z"}


# --- v2.1.78: subagent_observe.py worktree テスト ---


class TestSubagentWorktree:
    """subagent_observe.py の worktree enrichment テスト。"""

    def test_worktree_recorded(self, patch_data_dir):
        """SubagentStop event に worktree 情報が付与される。"""
        event = {
            "agent_type": "Explore",
            "agent_id": "agent-wt-001",
            "last_assistant_message": "done",
            "agent_transcript_path": "/tmp/t.jsonl",
            "session_id": "sess-wt-001",
            "worktree": {"name": "wt-sub", "branch": "feat/sub", "path": "/tmp/wt"},
        }
        subagent_observe.handle_subagent_stop(event)

        subagents_file = patch_data_dir / "subagents.jsonl"
        record = json.loads(subagents_file.read_text().strip())
        assert record["worktree"] == {"name": "wt-sub", "branch": "feat/sub"}

    def test_no_worktree_key_omitted(self, patch_data_dir):
        """worktree がない event ではキーが省略される。"""
        event = {
            "agent_type": "Explore",
            "agent_id": "agent-wt-002",
            "last_assistant_message": "done",
            "agent_transcript_path": "/tmp/t.jsonl",
            "session_id": "sess-wt-002",
        }
        subagent_observe.handle_subagent_stop(event)

        subagents_file = patch_data_dir / "subagents.jsonl"
        record = json.loads(subagents_file.read_text().strip())
        assert "worktree" not in record


class TestProjectFieldWorktreeNormalization:
    """#492: hook 書込側の project フィールドが worktree cwd でも本体 repo 名になる。

    旧実装は素の basename だったため worktree cwd（.../.claude/worktrees/<name>）で
    worktree 名（feedback / bots 等）が project に固定され、読み側で本体 repo 名に
    復元できず当 PJ フィルタから恒久的に漏れていた（#489 レビュー）。
    """

    def test_project_name_from_dir_normalizes_worktree(self):
        """worktree cwd → project は本体 repo 名（切り詰め basename）。"""
        cwd = "/Users/x/tools/evolve-anything/.claude/worktrees/agent-many"
        assert rl_common.project_name_from_dir(cwd) == "evolve-anything"

    def test_project_name_from_dir_main_repo_unchanged(self):
        """本体 repo の cwd は従来どおり basename。"""
        assert rl_common.project_name_from_dir("/Users/x/work/ai-daily-report") == "ai-daily-report"

    def test_observe_writes_normalized_project(self, patch_data_dir, monkeypatch):
        """observe hook の usage.jsonl project が worktree cwd でも本体名で書かれる。"""
        wt_cwd = "/Users/x/tools/evolve-anything/.claude/worktrees/agent-z"
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", wt_cwd)
        event = {
            "tool_name": "Skill",
            "tool_input": {"skill": "evolve", "args": ""},
            "tool_result": {"is_error": False},
            "session_id": "sess-wt-proj",
        }
        observe.handle_post_tool_use(event)

        usage_file = patch_data_dir / "usage.jsonl"
        record = json.loads(usage_file.read_text().strip().splitlines()[0])
        assert record["project"] == "evolve-anything"

    def test_session_summary_writes_normalized_project(self, patch_data_dir, monkeypatch):
        """session_summary の sessions レコード project が worktree cwd でも本体名で書かれる。"""
        wt_cwd = "/Users/x/tools/evolve-anything/.claude/worktrees/agent-z"
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", wt_cwd)
        event = {"session_id": "sess-wt-summary"}
        session_summary.handle_stop(event)

        records = session_store.query()
        assert len(records) == 1
        assert records[0]["project"] == "evolve-anything"

    def test_migration_date_constant_exists(self):
        """移行日定数が公開され #478 と同型で記録されている。"""
        assert rl_common.PJ_SLUG_NORMALIZATION_DATE == "2026-06-12"


class TestProjectPathWorktreeNormalization:
    """#593: hook 書込側の project_path も worktree 安全 slug に正規化する。

    project フィールドは #492 で正規化済みだが、project_path は raw CLAUDE_PROJECT_DIR
    （worktree フルパス）をそのまま stamp していたため、worktree（例
    .../.claude/worktrees/<name>）が幻の別PJ slug として cross-PJ 統計に混入していた。
    project_path の全 consumer は PJ 識別子として扱う（パスとして open/stat しない）ため、
    書込時に project と同じ project_name_from_dir（pj_slug_fast 経由）で slug 化する。
    """

    def test_observe_usage_registry_project_path_normalized(self, patch_data_dir, monkeypatch):
        """observe hook の usage-registry.jsonl project_path が worktree cwd でも本体名。"""
        wt_cwd = "/Users/x/tools/evolve-anything/.claude/worktrees/agent-z"
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", wt_cwd)
        # global スキル判定を真にして usage-registry 経路へ入れる。
        monkeypatch.setattr(observe, "is_global_skill", lambda *a, **k: True)
        event = {
            "tool_name": "Skill",
            "tool_input": {"skill": "evolve", "args": ""},
            "tool_result": {"is_error": False},
            "session_id": "sess-wt-regpath",
        }
        observe.handle_post_tool_use(event)

        reg_file = patch_data_dir / "usage-registry.jsonl"
        record = json.loads(reg_file.read_text().strip().splitlines()[0])
        assert record["project_path"] == "evolve-anything"

    def test_correction_detect_project_path_normalized(self, patch_data_dir, monkeypatch):
        """correction_detect hook の corrections.jsonl project_path が worktree cwd でも本体名。"""
        import correction_detect

        wt_cwd = "/Users/x/tools/evolve-anything/.claude/worktrees/agent-z"
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", wt_cwd)
        event = {
            "session_id": "sess-wt-corrpath",
            "prompt": "いや、そうじゃなくて",  # 修正パターン（iya）を踏ませる
        }
        correction_detect.handle_user_prompt_submit(event)

        corr_file = patch_data_dir / "corrections.jsonl"
        record = json.loads(corr_file.read_text().strip().splitlines()[0])
        assert record["project_path"] == "evolve-anything"

    def test_correction_detect_main_repo_unchanged(self, patch_data_dir, monkeypatch):
        """本体 repo の cwd は project_path も従来どおり basename。"""
        import correction_detect

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", "/Users/x/work/ai-daily-report")
        event = {
            "session_id": "sess-main-corrpath",
            "prompt": "いや、そうじゃなくて",
        }
        correction_detect.handle_user_prompt_submit(event)

        corr_file = patch_data_dir / "corrections.jsonl"
        record = json.loads(corr_file.read_text().strip().splitlines()[0])
        assert record["project_path"] == "ai-daily-report"


# --- v2.1.78: instructions_loaded.py テスト ---

import instructions_loaded


