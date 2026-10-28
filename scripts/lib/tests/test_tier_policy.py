#!/usr/bin/env python3
"""tier_policy.py のテスト — モデルティア正典の config load/set（#193）。

決定論・LLM 非依存。root conftest の autouse HOME 隔離により ``Path.home()`` は
per-test tmp dir を指すため、``tiers_config_path()`` は毎回未使用の隔離先を返す
（実 ~/.claude/model-tiers.json には一切触れない）。
"""
import json
import sys
from pathlib import Path

import pytest

_LIB = Path(__file__).resolve().parent.parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

import tier_policy  # noqa: E402


# --- DEFAULT_TIER_POLICY -----------------------------------------------------


class TestDefaultTierPolicy:
    def test_five_tiers_present(self):
        assert set(tier_policy.DEFAULT_TIER_POLICY) == {
            "HEAD", "HARD", "NORMAL", "MECH", "REVIEW",
        }

    def test_head_is_sonnet_max(self):
        assert tier_policy.DEFAULT_TIER_POLICY["HEAD"]["model"] == "sonnet"
        assert tier_policy.DEFAULT_TIER_POLICY["HEAD"]["effort"] == "max"
        assert tier_policy.DEFAULT_TIER_POLICY["HEAD"]["description"]

    def test_hard_is_sonnet_xhigh(self):
        assert tier_policy.DEFAULT_TIER_POLICY["HARD"]["model"] == "sonnet"
        assert tier_policy.DEFAULT_TIER_POLICY["HARD"]["effort"] == "xhigh"

    def test_mech_has_no_effort(self):
        assert tier_policy.DEFAULT_TIER_POLICY["MECH"]["model"] == "haiku"
        assert tier_policy.DEFAULT_TIER_POLICY["MECH"]["effort"] is None

    def test_review_is_fable_high(self):
        assert tier_policy.DEFAULT_TIER_POLICY["REVIEW"]["model"] == "fable"
        assert tier_policy.DEFAULT_TIER_POLICY["REVIEW"]["effort"] == "high"

    def test_all_tiers_have_description(self):
        for tier, policy in tier_policy.DEFAULT_TIER_POLICY.items():
            assert policy.get("description"), f"{tier} に description が無い"

    def test_matches_current_agent_tier_constant(self):
        # agent_tier.py の TIER_POLICY（既存正典）と model/effort が一致すること。
        import agent_tier

        for tier, policy in agent_tier.TIER_POLICY.items():
            assert tier_policy.DEFAULT_TIER_POLICY[tier]["model"] == policy["model"]
            assert tier_policy.DEFAULT_TIER_POLICY[tier]["effort"] == policy["effort"]


# --- tiers_config_path（call-time 解決）--------------------------------------


class TestTiersConfigPath:
    def test_default_path_under_home_claude(self):
        p = tier_policy.tiers_config_path()
        assert p == Path.home() / ".claude" / "model-tiers.json"

    def test_call_time_not_cached_across_home_changes(self, tmp_path, monkeypatch):
        home1 = tmp_path / "home1"
        home1.mkdir()
        monkeypatch.setenv("HOME", str(home1))
        p1 = tier_policy.tiers_config_path()
        assert p1 == home1 / ".claude" / "model-tiers.json"

        home2 = tmp_path / "home2"
        home2.mkdir()
        monkeypatch.setenv("HOME", str(home2))
        p2 = tier_policy.tiers_config_path()
        assert p2 == home2 / ".claude" / "model-tiers.json"
        assert p1 != p2


# --- load_tiers_config --------------------------------------------------------


class TestLoadTiersConfig:
    def test_missing_file_returns_defaults(self, tmp_path):
        cfg = tier_policy.load_tiers_config(config_path=tmp_path / "nope.json")
        assert cfg["_source"] == "defaults"
        assert cfg["tiers"]["HEAD"]["model"] == "sonnet"
        assert cfg["targets"] == {"agents": [], "settings": [], "routing_rules": []}

    def test_valid_file_overrides_defaults(self, tmp_path):
        path = tmp_path / "model-tiers.json"
        custom = {
            "version": 1,
            "tiers": {
                "HEAD": {"model": "opus", "effort": "xhigh", "description": "custom"},
            },
            "targets": {"agents": [], "settings": [], "routing_rules": []},
        }
        path.write_text(json.dumps(custom), encoding="utf-8")
        cfg = tier_policy.load_tiers_config(config_path=path)
        assert cfg["_source"] == "file"
        assert cfg["tiers"]["HEAD"]["model"] == "opus"

    def test_corrupt_json_fail_open_by_default(self, tmp_path):
        path = tmp_path / "model-tiers.json"
        path.write_text("{ this is not json", encoding="utf-8")
        cfg = tier_policy.load_tiers_config(config_path=path)
        assert cfg["_source"] == "defaults"
        assert cfg["tiers"]["HEAD"]["model"] == "sonnet"
        assert "_load_error" in cfg

    def test_corrupt_json_strict_raises_with_path(self, tmp_path):
        path = tmp_path / "model-tiers.json"
        path.write_text("{ this is not json", encoding="utf-8")
        with pytest.raises(ValueError) as exc_info:
            tier_policy.load_tiers_config(strict=True, config_path=path)
        assert str(path) in str(exc_info.value)

    def test_missing_tiers_key_fail_open(self, tmp_path):
        path = tmp_path / "model-tiers.json"
        path.write_text(json.dumps({"version": 1}), encoding="utf-8")
        cfg = tier_policy.load_tiers_config(config_path=path)
        assert cfg["_source"] == "defaults"
        assert "_load_error" in cfg

    def test_missing_tiers_key_strict_raises(self, tmp_path):
        path = tmp_path / "model-tiers.json"
        path.write_text(json.dumps({"version": 1}), encoding="utf-8")
        with pytest.raises(ValueError):
            tier_policy.load_tiers_config(strict=True, config_path=path)


# --- load_tier_policy（gate 互換形）-------------------------------------------


class TestLoadTierPolicy:
    def test_strips_description(self, tmp_path):
        policy = tier_policy.load_tier_policy(config_path=tmp_path / "nope.json")
        assert set(policy["HEAD"]) == {"model", "effort"}

    def test_default_values(self, tmp_path):
        policy = tier_policy.load_tier_policy(config_path=tmp_path / "nope.json")
        assert policy["HEAD"] == {"model": "sonnet", "effort": "max"}
        assert policy["MECH"] == {"model": "haiku", "effort": None}

    def test_reflects_file_override(self, tmp_path):
        path = tmp_path / "model-tiers.json"
        custom = {
            "tiers": {"HEAD": {"model": "opus", "effort": "xhigh"}},
        }
        path.write_text(json.dumps(custom), encoding="utf-8")
        policy = tier_policy.load_tier_policy(config_path=path)
        assert policy["HEAD"] == {"model": "opus", "effort": "xhigh"}


# --- set_tier ------------------------------------------------------------------


class TestSetTier:
    def test_creates_config_when_missing(self, tmp_path):
        path = tmp_path / "model-tiers.json"
        assert not path.exists()
        result = tier_policy.set_tier("HEAD", "opus", "xhigh", config_path=path)
        assert path.is_file()
        assert result["tier"] == "HEAD"
        assert result["new"]["model"] == "opus"
        assert result["new"]["effort"] == "xhigh"
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["tiers"]["HEAD"]["model"] == "opus"
        # 他 tier は DEFAULT のまま埋まっていること
        assert data["tiers"]["NORMAL"]["model"] == "sonnet"

    def test_updates_existing_tier_and_returns_old_new(self, tmp_path):
        path = tmp_path / "model-tiers.json"
        tier_policy.set_tier("HARD", "sonnet", "xhigh", config_path=path)
        result = tier_policy.set_tier("HARD", "opus", "high", config_path=path)
        assert result["old"]["model"] == "sonnet"
        assert result["new"]["model"] == "opus"
        assert result["new"]["effort"] == "high"

    def test_none_effort_allowed_for_non_haiku_model(self, tmp_path):
        path = tmp_path / "model-tiers.json"
        result = tier_policy.set_tier("NORMAL", "sonnet", None, config_path=path)
        assert result["new"]["effort"] is None

    def test_unknown_tier_raises(self, tmp_path):
        with pytest.raises(ValueError):
            tier_policy.set_tier("TURBO", "sonnet", "high", config_path=tmp_path / "x.json")

    def test_unknown_model_alias_raises(self, tmp_path):
        with pytest.raises(ValueError):
            tier_policy.set_tier("HEAD", "gpt5", "high", config_path=tmp_path / "x.json")

    def test_exact_model_id_rejected(self, tmp_path):
        with pytest.raises(ValueError) as exc_info:
            tier_policy.set_tier(
                "HEAD", "claude-sonnet-5", "max", config_path=tmp_path / "x.json"
            )
        assert "exact ID" in str(exc_info.value) or "alias" in str(exc_info.value).lower()

    def test_inherit_model_rejected(self, tmp_path):
        with pytest.raises(ValueError):
            tier_policy.set_tier("HEAD", "inherit", "max", config_path=tmp_path / "x.json")

    def test_invalid_effort_raises(self, tmp_path):
        with pytest.raises(ValueError):
            tier_policy.set_tier(
                "NORMAL", "sonnet", "super-high", config_path=tmp_path / "x.json"
            )

    @pytest.mark.parametrize("effort", ["low", "medium", "high", "xhigh", "max", " LOW "])
    def test_haiku_with_effort_saved(self, tmp_path, effort):
        path = tmp_path / "x.json"
        result = tier_policy.set_tier("MECH", "haiku", effort, config_path=path)
        expected = {**tier_policy.DEFAULT_TIER_POLICY["MECH"], "effort": effort.strip().lower()}
        assert result["new"] == expected
        assert json.loads(path.read_text())["tiers"]["MECH"] == expected

    @pytest.mark.parametrize("description", ["機械作業の説明"])
    def test_description_replaced(self, tmp_path, description):
        path = tmp_path / "x.json"
        config = tier_policy._default_config()
        config["tiers"]["MECH"]["description"] = "古い説明、effort 非対応"
        path.write_text(json.dumps(config))
        result = tier_policy.set_tier(
            "MECH", "haiku", "low", description=description, config_path=path
        )
        assert result["old"]["description"] == "古い説明、effort 非対応"
        assert result["new"]["description"] == description
        data = json.loads(path.read_text())
        assert data["tiers"]["MECH"]["description"] == description
        for tier in tier_policy.TIER_ORDER:
            if tier != "MECH":
                assert data["tiers"][tier] == config["tiers"][tier]

    @pytest.mark.parametrize("description", ["", "  ", "a\nb", "a\rb", "a\r\nb"])
    def test_invalid_description_does_not_write(self, tmp_path, description):
        path = tmp_path / "x.json"
        tier_policy.init_config(config_path=path)
        before = path.read_bytes()
        with pytest.raises(ValueError, match="説明文"):
            tier_policy.set_tier(
                "MECH", "haiku", "low", description=description, config_path=path
            )
        assert path.read_bytes() == before

    def test_description_stripped_before_saving(self, tmp_path):
        path = tmp_path / "x.json"
        result = tier_policy.set_tier(
            "MECH", "haiku", "low", description="  説明  ", config_path=path
        )
        assert result["new"]["description"] == "説明"
        assert json.loads(path.read_text())["tiers"]["MECH"]["description"] == "説明"

    def test_description_omitted_preserves_existing(self, tmp_path):
        path = tmp_path / "x.json"
        config = tier_policy._default_config()
        config["tiers"]["MECH"]["description"] = "カスタム説明"
        path.write_text(json.dumps(config))
        result = tier_policy.set_tier("MECH", "haiku", "low", config_path=path)
        assert result["new"]["description"] == "カスタム説明"
        assert json.loads(path.read_text())["tiers"]["MECH"]["description"] == "カスタム説明"

    def test_haiku_without_effort_ok(self, tmp_path):
        result = tier_policy.set_tier(
            "MECH", "haiku", None, config_path=tmp_path / "x.json"
        )
        assert result["new"]["model"] == "haiku"
        assert result["new"]["effort"] is None

    def test_atomic_write_leaves_no_tmp_file(self, tmp_path):
        path = tmp_path / "model-tiers.json"
        tier_policy.set_tier("HEAD", "opus", "xhigh", config_path=path)
        leftovers = list(tmp_path.glob("*.tmp"))
        assert leftovers == []

    def test_corrupt_config_rejected_not_overwritten(self, tmp_path):
        # 手編集 typo で壊れた config を fail-open の defaults で黙って上書きすると
        # targets manifest が silent 消失する。set は strict で明示エラーにし
        # ファイルを無傷で残すこと。
        path = tmp_path / "model-tiers.json"
        corrupt = '{"tiers": {'
        path.write_text(corrupt, encoding="utf-8")
        with pytest.raises(ValueError):
            tier_policy.set_tier("HEAD", "sonnet", "max", config_path=path)
        assert path.read_text(encoding="utf-8") == corrupt


# --- init_config -----------------------------------------------------------


class TestInitConfig:
    def test_creates_default_config(self, tmp_path):
        path = tmp_path / "model-tiers.json"
        result_path = tier_policy.init_config(config_path=path)
        assert result_path == path
        assert path.is_file()
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["tiers"]["HEAD"]["model"] == "sonnet"
        assert data["targets"] == {"agents": [], "settings": [], "routing_rules": []}

    def test_refuses_when_already_exists(self, tmp_path):
        path = tmp_path / "model-tiers.json"
        path.write_text("{}", encoding="utf-8")
        with pytest.raises(FileExistsError):
            tier_policy.init_config(config_path=path)
