"""load_user_config() のユニットテスト。"""
import os
import sys
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import common


class TestLoadUserConfig:
    """load_user_config() のテスト。"""

    def test_defaults_when_no_env_vars(self):
        """環境変数なしでデフォルト値を返す。"""
        with mock.patch.dict(os.environ, {}, clear=True):
            config = common.load_user_config()
        assert config["auto_trigger"] is True
        assert config["evolve_interval_days"] == 7
        assert config["audit_interval_days"] == 30
        assert config["min_sessions"] == 10
        assert config["cooldown_hours"] == 24
        assert config["language"] == "ja"

    def test_valid_overrides(self):
        """全項目を環境変数でオーバーライド。"""
        env = {
            "CLAUDE_PLUGIN_OPTION_auto_trigger": "false",
            "CLAUDE_PLUGIN_OPTION_evolve_interval_days": "14",
            "CLAUDE_PLUGIN_OPTION_audit_interval_days": "60",
            "CLAUDE_PLUGIN_OPTION_min_sessions": "5",
            "CLAUDE_PLUGIN_OPTION_cooldown_hours": "12",
            "CLAUDE_PLUGIN_OPTION_language": "en",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            config = common.load_user_config()
        assert config["auto_trigger"] is False
        assert config["evolve_interval_days"] == 14
        assert config["audit_interval_days"] == 60
        assert config["min_sessions"] == 5
        assert config["cooldown_hours"] == 12
        assert config["language"] == "en"

    def test_invalid_number_falls_back_to_default(self):
        """非数値が渡された場合はデフォルトにフォールバック。"""
        env = {
            "CLAUDE_PLUGIN_OPTION_evolve_interval_days": "not_a_number",
            "CLAUDE_PLUGIN_OPTION_min_sessions": "",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            config = common.load_user_config()
        assert config["evolve_interval_days"] == 7  # default
        assert config["min_sessions"] == 10  # default

    def test_boolean_parsing(self):
        """文字列の true/false が正しく bool にパースされる。"""
        for true_val in ("true", "True", "TRUE", "1", "yes"):
            with mock.patch.dict(os.environ, {"CLAUDE_PLUGIN_OPTION_auto_trigger": true_val}):
                config = common.load_user_config()
                assert config["auto_trigger"] is True, f"Expected True for '{true_val}'"

        for false_val in ("false", "False", "FALSE", "0", "no"):
            with mock.patch.dict(os.environ, {"CLAUDE_PLUGIN_OPTION_auto_trigger": false_val}):
                config = common.load_user_config()
                assert config["auto_trigger"] is False, f"Expected False for '{false_val}'"

    def test_partial_override(self):
        """一部の項目のみオーバーライドし、残りはデフォルト。"""
        env = {"CLAUDE_PLUGIN_OPTION_cooldown_hours": "48"}
        with mock.patch.dict(os.environ, env, clear=False):
            config = common.load_user_config()
        assert config["cooldown_hours"] == 48
        assert config["auto_trigger"] is True  # default
        assert config["language"] == "ja"  # default

    def test_cleanup_tmp_prefixes_default(self):
        """cleanup_tmp_prefixes はデフォルトで "evolve-anything-" 単独（安全側）。

        PR #70 dogfood で検出した Claude Code runtime (`/tmp/claude-<uid>`) や
        MCP bridge (`/tmp/claude-mcp-*`) を削除候補化する wide prefix バグの
        再発防止として、デフォルトは evolve-anything 名前空間のみに絞る。
        """
        with mock.patch.dict(os.environ, {}, clear=True):
            config = common.load_user_config()
        assert config["cleanup_tmp_prefixes"] == "evolve-anything-"

    def test_cleanup_tmp_prefixes_override(self):
        """cleanup_tmp_prefixes は環境変数で上書き可能（カンマ区切りで複数指定）。"""
        env = {
            "CLAUDE_PLUGIN_OPTION_cleanup_tmp_prefixes": "evolve-anything-,claude-sandbox-,gstack-scratch-",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            config = common.load_user_config()
        assert config["cleanup_tmp_prefixes"] == "evolve-anything-,claude-sandbox-,gstack-scratch-"

    def test_subagent_warning_threshold_default(self):
        """subagent_warning_threshold のデフォルトは 5。"""
        with mock.patch.dict(os.environ, {}, clear=True):
            config = common.load_user_config()
        assert config["subagent_warning_threshold"] == 5

    def test_subagent_warning_threshold_override(self):
        """subagent_warning_threshold は環境変数で上書き可能。"""
        env = {"CLAUDE_PLUGIN_OPTION_subagent_warning_threshold": "10"}
        with mock.patch.dict(os.environ, env, clear=False):
            config = common.load_user_config()
        assert config["subagent_warning_threshold"] == 10

    def test_subagent_window_minutes_default(self):
        """subagent_window_minutes のデフォルトは 5。"""
        with mock.patch.dict(os.environ, {}, clear=True):
            config = common.load_user_config()
        assert config["subagent_window_minutes"] == 5

    def test_subagent_window_minutes_override(self):
        """subagent_window_minutes は環境変数で上書き可能。"""
        env = {"CLAUDE_PLUGIN_OPTION_subagent_window_minutes": "30"}
        with mock.patch.dict(os.environ, env, clear=False):
            config = common.load_user_config()
        assert config["subagent_window_minutes"] == 30

    def test_empty_string_overrides_default_for_string_keys(self):
        """空文字 env var は string 型キーを空文字で上書きする（#77）。

        cleanup_tmp_prefixes="" で category 4 無効化を意図するユーザーが
        silently 無視されないことを保証する。
        """
        env = {"CLAUDE_PLUGIN_OPTION_cleanup_tmp_prefixes": ""}
        with mock.patch.dict(os.environ, env, clear=False):
            config = common.load_user_config()
        assert config["cleanup_tmp_prefixes"] == ""

    def test_empty_string_does_not_override_int_key(self):
        """空文字 env var は int 型キーに対してデフォルトを維持する（#77: 非 string 型は空文字を未設定として扱う）。"""
        env = {"CLAUDE_PLUGIN_OPTION_min_sessions": ""}
        with mock.patch.dict(os.environ, env, clear=False):
            config = common.load_user_config()
        assert config["min_sessions"] == 10  # default

    def test_empty_string_does_not_override_bool_key(self):
        """空文字 env var は bool 型キーに対してデフォルトを維持する（#77）。

        _parse_bool("") → False になるため、空文字を「未設定」として扱わないと
        CLAUDE_PLUGIN_OPTION_auto_trigger="" で auto_trigger が silently 無効化される。
        """
        env = {"CLAUDE_PLUGIN_OPTION_auto_trigger": ""}
        with mock.patch.dict(os.environ, env, clear=False):
            config = common.load_user_config()
        assert config["auto_trigger"] is True  # default

    def test_is_user_config_explicit_with_empty_string(self):
        """空文字でセットした場合も is_user_config_explicit は True を返す（#77）。"""
        env = {"CLAUDE_PLUGIN_OPTION_cleanup_tmp_prefixes": ""}
        with mock.patch.dict(os.environ, env, clear=False):
            assert common.is_user_config_explicit("cleanup_tmp_prefixes") is True

    def test_is_user_config_explicit_when_unset(self):
        """未設定の場合は is_user_config_explicit は False を返す。"""
        with mock.patch.dict(os.environ, {}, clear=True):
            assert common.is_user_config_explicit("cleanup_tmp_prefixes") is False

    def test_icebox_review_threshold_days_default(self):
        """icebox_review_threshold_days のデフォルトは 30。"""
        with mock.patch.dict(os.environ, {}, clear=True):
            config = common.load_user_config()
        assert config["icebox_review_threshold_days"] == 30

    def test_icebox_review_threshold_days_override(self):
        """icebox_review_threshold_days は環境変数で上書き可能。"""
        env = {"CLAUDE_PLUGIN_OPTION_icebox_review_threshold_days": "14"}
        with mock.patch.dict(os.environ, env, clear=False):
            config = common.load_user_config()
        assert config["icebox_review_threshold_days"] == 14
