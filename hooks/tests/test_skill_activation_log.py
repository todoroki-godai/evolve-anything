"""skill_activation_log.py のテスト。"""
import json
import os
import sys
from pathlib import Path
from unittest import mock

import pytest

_hooks = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_hooks))
sys.path.insert(0, str(_hooks.parent / "scripts" / "lib"))

import common
import rl_common
import skill_activation_log


@pytest.fixture
def tmp_data_dir(tmp_path):
    data_dir = tmp_path / "evolve-anything"
    data_dir.mkdir()
    return data_dir


@pytest.fixture
def patch_env(tmp_path, tmp_data_dir):
    with mock.patch.object(common, "DATA_DIR", tmp_data_dir), \
         mock.patch.object(rl_common, "DATA_DIR", tmp_data_dir), \
         mock.patch.dict(os.environ, {"TMPDIR": str(tmp_path), "CLAUDE_PROJECT_DIR": "/proj/test"}):
        yield tmp_path, tmp_data_dir


class TestSkillActivationLog:
    def test_appends_to_skill_activations_jsonl(self, patch_env):
        tmp_path, tmp_data_dir = patch_env
        event = {
            "tool_name": "Skill",
            "tool_input": {"skill": "evolve-anything:audit"},
            "session_id": "sess-sal-001",
        }
        skill_activation_log.handle_post_tool_use(event)

        out_file = tmp_data_dir / "skill_activations.jsonl"
        assert out_file.exists()
        rec = json.loads(out_file.read_text().strip())
        assert rec["skill"] == "evolve-anything:audit"
        assert rec["session_id"] == "sess-sal-001"
        assert "ts" in rec
        assert rec["project"] == "test"

    def test_reads_invocation_trigger_from_context(self, patch_env):
        tmp_path, tmp_data_dir = patch_env
        # Write context file with invocation_trigger
        ctx = {
            "skill_name": "evolve-anything:audit",
            "session_id": "sess-sal-002",
            "workflow_id": "wf-abc12345",
            "started_at": "2026-05-06T00:00:00+00:00",
            "invocation_trigger": "nested-skill",
        }
        ctx_path = tmp_path / "evolve-anything-workflow-sess-sal-002.json"
        ctx_path.write_text(json.dumps(ctx))

        event = {
            "tool_name": "Skill",
            "tool_input": {"skill": "evolve-anything:audit"},
            "session_id": "sess-sal-002",
        }
        skill_activation_log.handle_post_tool_use(event)

        out_file = tmp_data_dir / "skill_activations.jsonl"
        rec = json.loads(out_file.read_text().strip())
        assert rec["invocation_trigger"] == "nested-skill"

    def test_unknown_trigger_when_no_context(self, patch_env):
        """コンテキストファイルが存在しない場合は invocation_trigger = 'unknown'。"""
        tmp_path, tmp_data_dir = patch_env
        event = {
            "tool_name": "Skill",
            "tool_input": {"skill": "evolve-anything:reflect"},
            "session_id": "sess-sal-003",
        }
        skill_activation_log.handle_post_tool_use(event)

        out_file = tmp_data_dir / "skill_activations.jsonl"
        rec = json.loads(out_file.read_text().strip())
        assert rec["invocation_trigger"] == "unknown"

    def test_no_skill_name_noop(self, patch_env):
        tmp_path, tmp_data_dir = patch_env
        event = {
            "tool_name": "Skill",
            "tool_input": {},
            "session_id": "sess-sal-004",
        }
        skill_activation_log.handle_post_tool_use(event)
        assert not (tmp_data_dir / "skill_activations.jsonl").exists()

    def test_no_session_id_noop(self, patch_env):
        tmp_path, tmp_data_dir = patch_env
        event = {
            "tool_name": "Skill",
            "tool_input": {"skill": "evolve-anything:audit"},
            "session_id": "",
        }
        skill_activation_log.handle_post_tool_use(event)
        assert not (tmp_data_dir / "skill_activations.jsonl").exists()
