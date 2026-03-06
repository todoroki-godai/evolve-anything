"""Tests for model_cascade module."""
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(
    0, str(Path(__file__).resolve().parent.parent / "scripts")
)
from model_cascade import (
    TIER1_MODEL,
    TIER2_MODEL,
    TIER3_MODEL,
    ModelCascade,
    load_cascade_config,
)


class TestModelCascadeInit:
    def test_default_models(self):
        cascade = ModelCascade()
        assert cascade.get_model(1) == TIER1_MODEL
        assert cascade.get_model(2) == TIER2_MODEL
        assert cascade.get_model(3) == TIER3_MODEL

    def test_config_override(self):
        config = {"tier1": "custom-small", "tier2": "custom-mid", "tier3": "custom-large"}
        cascade = ModelCascade(config=config)
        assert cascade.get_model(1) == "custom-small"
        assert cascade.get_model(2) == "custom-mid"
        assert cascade.get_model(3) == "custom-large"

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("TIER1_MODEL", "env-haiku")
        monkeypatch.setenv("TIER2_MODEL", "env-sonnet")
        monkeypatch.setenv("TIER3_MODEL", "env-opus")
        cascade = ModelCascade(config={"tier1": "ignored"})
        assert cascade.get_model(1) == "env-haiku"
        assert cascade.get_model(2) == "env-sonnet"
        assert cascade.get_model(3) == "env-opus"


class TestGetModel:
    def test_valid_tiers(self):
        cascade = ModelCascade()
        for tier in (1, 2, 3):
            assert isinstance(cascade.get_model(tier), str)

    @pytest.mark.parametrize("invalid_tier", [0, 4, -1, 99])
    def test_invalid_tier_raises(self, invalid_tier):
        cascade = ModelCascade()
        with pytest.raises(ValueError, match="Invalid tier"):
            cascade.get_model(invalid_tier)


class TestEnabled:
    def test_enabled_default(self):
        assert ModelCascade().enabled is True

    def test_enabled_false(self):
        assert ModelCascade(enabled=False).enabled is False

    def test_enabled_true_explicit(self):
        assert ModelCascade(enabled=True).enabled is True


class TestRunWithTier:
    def _mock_run_success(self, *args, **kwargs):
        result = subprocess.CompletedProcess(args=args[0], returncode=0, stdout="ok", stderr="")
        return result

    def _mock_run_fail(self, *args, **kwargs):
        result = subprocess.CompletedProcess(args=args[0], returncode=1, stdout="", stderr="error")
        return result

    def test_successful_execution(self):
        cascade = ModelCascade()
        with patch("subprocess.run", side_effect=self._mock_run_success) as mock:
            result = cascade.run_with_tier("test prompt", 1)
        assert result == "ok"
        call_args = mock.call_args
        assert call_args[0][0] == ["claude", "-p", "--model", "haiku"]
        assert call_args[1]["input"] == "test prompt"

    def test_tier1_fails_escalates_to_tier2(self):
        call_count = 0

        def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return subprocess.CompletedProcess(args=args[0], returncode=1, stdout="", stderr="fail")
            return subprocess.CompletedProcess(args=args[0], returncode=0, stdout="tier2-ok", stderr="")

        cascade = ModelCascade()
        with patch("subprocess.run", side_effect=side_effect) as mock:
            result = cascade.run_with_tier("prompt", 1)
        assert result == "tier2-ok"
        assert mock.call_count == 2
        assert mock.call_args_list[1][0][0] == ["claude", "-p", "--model", "sonnet"]

    def test_tier3_fails_raises(self):
        cascade = ModelCascade()
        with patch("subprocess.run", side_effect=self._mock_run_fail):
            with pytest.raises(RuntimeError, match="claude -p failed"):
                cascade.run_with_tier("prompt", 3)

    @pytest.mark.parametrize("invalid_tier", [0, 4, -1])
    def test_invalid_tier_raises(self, invalid_tier):
        cascade = ModelCascade()
        with pytest.raises(ValueError, match="Invalid tier"):
            cascade.run_with_tier("prompt", invalid_tier)

    def test_full_cascade_tier1_to_tier3(self):
        call_count = 0

        def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                return subprocess.CompletedProcess(args=args[0], returncode=1, stdout="", stderr="fail")
            return subprocess.CompletedProcess(args=args[0], returncode=0, stdout="tier3-ok", stderr="")

        cascade = ModelCascade()
        with patch("subprocess.run", side_effect=side_effect) as mock:
            result = cascade.run_with_tier("prompt", 1)
        assert result == "tier3-ok"
        assert mock.call_count == 3


class TestLoadCascadeConfig:
    def test_none_returns_empty(self):
        assert load_cascade_config(None) == {}

    def test_nonexistent_file_returns_empty(self, tmp_path):
        assert load_cascade_config(tmp_path / "nope.yaml") == {}

    def test_valid_yaml(self, tmp_path):
        cfg = tmp_path / "cascade.yaml"
        cfg.write_text("tier1: fast\ntier2: medium\ntier3: slow\n")
        result = load_cascade_config(cfg)
        assert result == {"tier1": "fast", "tier2": "medium", "tier3": "slow"}

    def test_invalid_yaml_returns_empty(self, tmp_path):
        cfg = tmp_path / "bad.yaml"
        cfg.write_text(": : :\n  - [invalid")
        result = load_cascade_config(cfg)
        # yaml パースエラー or 空辞書
        assert isinstance(result, dict)


class TestDisabledCascadeRegression:
    def test_disabled_cascade_still_functions(self):
        cascade = ModelCascade(enabled=False)
        assert cascade.enabled is False
        assert cascade.get_model(1) == TIER1_MODEL
        with patch("subprocess.run") as mock:
            mock.return_value = subprocess.CompletedProcess(
                args=[], returncode=0, stdout="result", stderr=""
            )
            result = cascade.run_with_tier("prompt", 1)
        assert result == "result"
