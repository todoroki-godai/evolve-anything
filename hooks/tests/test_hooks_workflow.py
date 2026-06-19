"""workflow_context / read_workflow_context / classify_prompt 関連テスト。

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


class TestWorkflowContext:
    """workflow_context.py のテスト。"""

    def test_context_file_created(self, tmp_path):
        """Skill 呼び出し時に文脈ファイルが作成される。"""
        with mock.patch.dict(os.environ, {"TMPDIR": str(tmp_path)}):
            event = {
                "tool_name": "Skill",
                "tool_input": {"skill": "opsx:refine", "args": ""},
                "session_id": "sess-wf-001",
            }
            workflow_context.handle_pre_tool_use(event)

            ctx_path = tmp_path / "evolve-anything-workflow-sess-wf-001.json"
            assert ctx_path.exists()
            ctx = json.loads(ctx_path.read_text())
            assert ctx["skill_name"] == "opsx:refine"
            assert ctx["session_id"] == "sess-wf-001"
            assert ctx["workflow_id"].startswith("wf-")
            assert len(ctx["workflow_id"]) == 11  # "wf-" + 8 hex chars
            assert "started_at" in ctx

    def test_context_overwritten_on_new_skill(self, tmp_path):
        """同一セッションで別の Skill が呼ばれると文脈が上書きされる。"""
        with mock.patch.dict(os.environ, {"TMPDIR": str(tmp_path)}):
            event1 = {
                "tool_name": "Skill",
                "tool_input": {"skill": "opsx:refine"},
                "session_id": "sess-wf-002",
            }
            workflow_context.handle_pre_tool_use(event1)

            ctx_path = tmp_path / "evolve-anything-workflow-sess-wf-002.json"
            ctx1 = json.loads(ctx_path.read_text())
            wf_id_1 = ctx1["workflow_id"]

            event2 = {
                "tool_name": "Skill",
                "tool_input": {"skill": "opsx:apply"},
                "session_id": "sess-wf-002",
            }
            workflow_context.handle_pre_tool_use(event2)

            ctx2 = json.loads(ctx_path.read_text())
            assert ctx2["skill_name"] == "opsx:apply"
            assert ctx2["workflow_id"] != wf_id_1

    def test_no_session_id_noop(self, tmp_path):
        """session_id がない場合は何もしない。"""
        with mock.patch.dict(os.environ, {"TMPDIR": str(tmp_path)}):
            event = {
                "tool_name": "Skill",
                "tool_input": {"skill": "test"},
                "session_id": "",
            }
            workflow_context.handle_pre_tool_use(event)
            assert not list(tmp_path.glob("evolve-anything-workflow-*"))

    def test_no_skill_name_noop(self, tmp_path):
        """skill_name がない場合は何もしない。"""
        with mock.patch.dict(os.environ, {"TMPDIR": str(tmp_path)}):
            event = {
                "tool_name": "Skill",
                "tool_input": {},
                "session_id": "sess-wf-003",
            }
            workflow_context.handle_pre_tool_use(event)
            assert not list(tmp_path.glob("evolve-anything-workflow-*"))

    def test_invocation_trigger_top_level(self, tmp_path):
        """最初の Skill 呼び出しは invocation_trigger = 'top-level'。"""
        with mock.patch.dict(os.environ, {"TMPDIR": str(tmp_path)}):
            event = {
                "tool_name": "Skill",
                "tool_input": {"skill": "evolve-anything:audit"},
                "session_id": "sess-wf-trigger-01",
            }
            workflow_context.handle_pre_tool_use(event)
            ctx_path = tmp_path / "evolve-anything-workflow-sess-wf-trigger-01.json"
            ctx = json.loads(ctx_path.read_text())
            assert ctx["invocation_trigger"] == "top-level"

    def test_invocation_trigger_nested(self, tmp_path):
        """コンテキストファイルが存在する状態で呼ばれると invocation_trigger = 'nested-skill'。"""
        with mock.patch.dict(os.environ, {"TMPDIR": str(tmp_path)}):
            # 1回目: top-level
            event1 = {
                "tool_name": "Skill",
                "tool_input": {"skill": "evolve-anything:evolve"},
                "session_id": "sess-wf-trigger-02",
            }
            workflow_context.handle_pre_tool_use(event1)
            # 2回目: nested (コンテキストファイル存在)
            event2 = {
                "tool_name": "Skill",
                "tool_input": {"skill": "evolve-anything:audit"},
                "session_id": "sess-wf-trigger-02",
            }
            workflow_context.handle_pre_tool_use(event2)
            ctx_path = tmp_path / "evolve-anything-workflow-sess-wf-trigger-02.json"
            ctx = json.loads(ctx_path.read_text())
            assert ctx["invocation_trigger"] == "nested-skill"


class TestReadWorkflowContext:
    """common.read_workflow_context() のテスト。"""

    def test_context_exists(self, tmp_path):
        """文脈ファイルが存在する場合、parent_skill と workflow_id を返す。"""
        ctx = {
            "skill_name": "opsx:refine",
            "session_id": "sess-rwc-001",
            "workflow_id": "wf-abc12345",
            "started_at": "2026-03-03T10:00:00+00:00",
        }
        with mock.patch.dict(os.environ, {"TMPDIR": str(tmp_path)}):
            ctx_path = tmp_path / "evolve-anything-workflow-sess-rwc-001.json"
            ctx_path.write_text(json.dumps(ctx))

            result = common.read_workflow_context("sess-rwc-001")
            assert result["parent_skill"] == "opsx:refine"
            assert result["workflow_id"] == "wf-abc12345"

    def test_context_not_exists(self, tmp_path):
        """文脈ファイルが存在しない場合、null を返す。"""
        with mock.patch.dict(os.environ, {"TMPDIR": str(tmp_path)}):
            result = common.read_workflow_context("sess-rwc-002")
            assert result["parent_skill"] is None
            assert result["workflow_id"] is None

    def test_context_corrupted(self, tmp_path):
        """文脈ファイルが破損している場合、null を返す。"""
        with mock.patch.dict(os.environ, {"TMPDIR": str(tmp_path)}):
            ctx_path = tmp_path / "evolve-anything-workflow-sess-rwc-003.json"
            ctx_path.write_text("NOT VALID JSON{{{")

            result = common.read_workflow_context("sess-rwc-003")
            assert result["parent_skill"] is None
            assert result["workflow_id"] is None

    def test_context_expired(self, tmp_path):
        """24時間以上経過した文脈ファイルは無効。"""
        ctx = {
            "skill_name": "opsx:refine",
            "workflow_id": "wf-expired1",
        }
        with mock.patch.dict(os.environ, {"TMPDIR": str(tmp_path)}):
            ctx_path = tmp_path / "evolve-anything-workflow-sess-rwc-004.json"
            ctx_path.write_text(json.dumps(ctx))

            # mtime を25時間前に設定
            old_time = time.time() - (25 * 60 * 60)
            os.utime(ctx_path, (old_time, old_time))

            result = common.read_workflow_context("sess-rwc-004")
            assert result["parent_skill"] is None
            assert result["workflow_id"] is None


class TestClassifyPrompt:
    """common.classify_prompt() のテスト。"""

    # --- 既存カテゴリ（英語） ---

    def test_spec_review(self):
        assert common.classify_prompt("review the spec requirements") == "spec-review"

    def test_code_exploration(self):
        assert common.classify_prompt("explore the codebase structure") == "code-exploration"

    def test_research(self):
        assert common.classify_prompt("research best practice for caching") == "research"

    def test_code_review(self):
        assert common.classify_prompt("review code changes in auth module") == "code-review"

    def test_implementation(self):
        assert common.classify_prompt("implement the new feature") == "implementation"

    def test_other(self):
        assert common.classify_prompt("hello world") == "other"

    def test_case_insensitive(self):
        assert common.classify_prompt("EXPLORE the CODEBASE") == "code-exploration"

    def test_empty_prompt(self):
        assert common.classify_prompt("") == "other"

    # --- 新カテゴリ（英語） ---

    def test_git_ops_merge(self):
        assert common.classify_prompt("merge the feature branch") == "git-ops"

    def test_git_ops_rebase(self):
        assert common.classify_prompt("rebase onto main") == "git-ops"

    def test_deploy_release(self):
        assert common.classify_prompt("deploy to production") == "deploy"

    def test_deploy_staging(self):
        assert common.classify_prompt("release to staging environment") == "deploy"

    def test_debug_fix(self):
        assert common.classify_prompt("fix the login bug") == "debug"

    def test_debug_error(self):
        assert common.classify_prompt("debug the error in auth") == "debug"

    def test_test_pytest(self):
        assert common.classify_prompt("run pytest on the module") == "test"

    def test_test_assert(self):
        assert common.classify_prompt("add assert for edge case") == "test"

    def test_config_setup(self):
        assert common.classify_prompt("update config for database") == "config"

    def test_config_env(self):
        assert common.classify_prompt("setup the env variables") == "config"

    def test_conversation_ok(self):
        assert common.classify_prompt("OK let's do it") == "conversation:approval"

    # --- 既存カテゴリ（日本語） ---

    def test_spec_review_jp(self):
        assert common.classify_prompt("仕様を確認してください") == "spec-review"

    def test_spec_review_jp_requirements(self):
        assert common.classify_prompt("要件を整理して") == "spec-review"

    def test_code_review_jp(self):
        assert common.classify_prompt("コードレビューして") == "code-review"

    def test_code_review_jp_diff(self):
        assert common.classify_prompt("差分を見て") == "code-review"

    def test_code_exploration_jp(self):
        assert common.classify_prompt("ファイル構造を教えて") == "code-exploration"

    def test_code_exploration_jp_read(self):
        assert common.classify_prompt("このファイル読んで") == "code-exploration"

    def test_research_jp(self):
        assert common.classify_prompt("ベストプラクティスを調べて") == "research"

    def test_research_jp_latest(self):
        assert common.classify_prompt("最新の方法を教えて") == "research"

    def test_implementation_jp(self):
        assert common.classify_prompt("この機能を実装して") == "implementation"

    def test_implementation_jp_create(self):
        assert common.classify_prompt("新しいコンポーネントを作って") == "implementation"

    # --- 新カテゴリ（日本語） ---

    def test_git_ops_jp_merge(self):
        assert common.classify_prompt("mainにマージして") == "git-ops"

    def test_git_ops_jp_commit(self):
        assert common.classify_prompt("コミットしてください") == "git-ops"

    def test_git_ops_jp_branch(self):
        assert common.classify_prompt("ブランチを切って") == "git-ops"

    def test_deploy_jp(self):
        assert common.classify_prompt("本番にデプロイして") == "deploy"

    def test_deploy_jp_release(self):
        assert common.classify_prompt("リリースの準備をして") == "deploy"

    def test_debug_jp_fix(self):
        assert common.classify_prompt("このバグを修正して") == "debug"

    def test_debug_jp_investigate(self):
        assert common.classify_prompt("エラーの原因を調査して") == "debug"

    def test_debug_jp_naose(self):
        assert common.classify_prompt("これ直して") == "debug"

    def test_test_jp(self):
        assert common.classify_prompt("テストを実行して") == "test"

    def test_test_jp_verify(self):
        assert common.classify_prompt("動作を検証して") == "test"

    def test_config_jp(self):
        assert common.classify_prompt("設定を変更して") == "config"

    def test_config_jp_readme(self):
        assert common.classify_prompt("README.mdを更新") == "config"

    def test_conversation_jp_please(self):
        assert common.classify_prompt("お願いします") == "conversation:confirmation"

    def test_conversation_jp_continue(self):
        assert common.classify_prompt("続けてください") == "conversation:confirmation"

    def test_conversation_jp_proceed(self):
        assert common.classify_prompt("進めて") == "conversation:confirmation"

    def test_conversation_jp_thanks(self):
        assert common.classify_prompt("ありがとう") == "conversation:thanks"

    # --- 優先順位テスト ---

    def test_priority_spec_over_code_review(self):
        """spec-review は code-review より優先される。"""
        assert common.classify_prompt("review the spec") == "spec-review"

    def test_priority_debug_over_implementation(self):
        """debug は implementation より優先される（'fix' キーワード）。"""
        assert common.classify_prompt("fix this broken feature") == "debug"

    def test_priority_git_ops_over_deploy(self):
        """git-ops は deploy より優先される（'push' キーワード）。"""
        assert common.classify_prompt("push to remote branch") == "git-ops"


