#!/usr/bin/env python3
"""classify_artifact_origin と prune プラグインスキル除外のテスト。"""
import json
import os
import sys
from pathlib import Path
from unittest import mock

import pytest

_plugin_root = Path(__file__).resolve().parent.parent.parent.parent.parent
sys.path.insert(0, str(_plugin_root / "scripts"))
sys.path.insert(0, str(_plugin_root / "skills" / "prune" / "scripts"))

import audit
import prune


class TestClassifyArtifactOrigin:
    """classify_artifact_origin のユニットテスト。"""

    def test_plugin_origin(self):
        """プラグインキャッシュ配下のスキルは plugin と判定される。"""
        path = Path.home() / ".claude" / "plugins" / "cache" / "rl-anything" / "rl-anything" / "0.4.0" / ".claude" / "skills" / "optimize" / "SKILL.md"
        assert audit.classify_artifact_origin(path) == "plugin"

    def test_plugin_origin_with_tilde(self):
        """チルダ付きパスも正しく展開されて plugin と判定される。"""
        path = Path("~/.claude/plugins/cache/rl-anything/rl-anything/0.4.0/.claude/skills/optimize/SKILL.md")
        assert audit.classify_artifact_origin(path) == "plugin"

    def test_global_origin(self):
        """グローバルスキルは global と判定される。"""
        path = Path.home() / ".claude" / "skills" / "my-skill" / "SKILL.md"
        assert audit.classify_artifact_origin(path) == "global"

    def test_custom_origin(self):
        """プロジェクトローカルのスキルは custom と判定される。"""
        path = Path("/Users/user/project/.claude/skills/my-skill/SKILL.md")
        assert audit.classify_artifact_origin(path) == "custom"

    def test_rules_always_custom(self):
        """ルールは常に custom と判定される。"""
        path = Path("/Users/user/project/.claude/rules/my-rule.md")
        assert audit.classify_artifact_origin(path) == "custom"

    def test_env_override(self):
        """CLAUDE_PLUGINS_DIR 環境変数でプラグインパスをオーバーライドできる。"""
        with mock.patch.dict(os.environ, {"CLAUDE_PLUGINS_DIR": "/custom/plugins"}):
            path = Path("/custom/plugins/rl-anything/SKILL.md")
            assert audit.classify_artifact_origin(path) == "plugin"

    def test_env_override_does_not_match_default(self):
        """環境変数設定時、デフォルトのプラグインパスは plugin と判定されない。"""
        with mock.patch.dict(os.environ, {"CLAUDE_PLUGINS_DIR": "/custom/plugins"}):
            path = Path.home() / ".claude" / "plugins" / "cache" / "test" / "SKILL.md"
            assert audit.classify_artifact_origin(path) == "custom"


class TestPrunePluginExclusion:
    """プラグインスキルが淘汰対象から除外されるテスト。"""

    @pytest.fixture
    def patch_data_dir(self, tmp_path):
        """テスト用の DATA_DIR を作成。"""
        data_dir = tmp_path / "rl-anything"
        data_dir.mkdir()
        with mock.patch.object(audit, "DATA_DIR", data_dir):
            yield data_dir

    def test_plugin_skill_excluded_from_zero_invocations(self, patch_data_dir):
        """プラグイン由来スキルは zero_invocations に含まれない。"""
        plugin_path = Path.home() / ".claude" / "plugins" / "cache" / "test-plugin" / "v1" / ".claude" / "skills" / "my-plugin-skill" / "SKILL.md"
        custom_path = Path("/Users/user/project/.claude/skills/my-custom-skill/SKILL.md")

        artifacts = {
            "skills": [plugin_path, custom_path],
            "rules": [],
        }

        # 空の usage.jsonl を作成（両方とも未使用）
        usage_file = patch_data_dir / "usage.jsonl"
        usage_file.write_text("")

        zero, plugin_unused = prune.detect_zero_invocations(artifacts, days=30)

        # カスタムスキルは zero_invocations に含まれる
        zero_names = [z["skill_name"] for z in zero]
        assert "my-custom-skill" in zero_names

        # プラグインスキルは zero_invocations に含まれない
        assert "my-plugin-skill" not in zero_names

        # プラグインスキルは plugin_unused に含まれる
        plugin_names = [p["skill_name"] for p in plugin_unused]
        assert "my-plugin-skill" in plugin_names

    def test_run_prune_has_plugin_unused_key(self, patch_data_dir, tmp_path):
        """run_prune の戻り値に plugin_unused キーが存在する。"""
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        (project_dir / ".claude" / "skills").mkdir(parents=True)
        (project_dir / ".claude" / "rules").mkdir(parents=True)

        usage_file = patch_data_dir / "usage.jsonl"
        usage_file.write_text("")

        result = prune.run_prune(str(project_dir))
        assert "plugin_unused" in result
        assert isinstance(result["plugin_unused"], list)
