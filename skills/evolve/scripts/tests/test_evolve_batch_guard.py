"""evolve.py batch_guard_trigger sentinel の伝播テスト。"""
import json
import sys
from pathlib import Path
from unittest import mock

import pytest

_plugin_root = Path(__file__).resolve().parent.parent.parent.parent.parent
sys.path.insert(0, str(_plugin_root / "skills" / "evolve" / "scripts"))
sys.path.insert(0, str(_plugin_root / "skills" / "audit" / "scripts"))
sys.path.insert(0, str(_plugin_root / "scripts" / "lib"))
sys.path.insert(0, str(_plugin_root / "scripts"))

from evolve import run_evolve


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    """DATA_DIR を tmp_path に向けてファイル I/O を隔離する。"""
    monkeypatch.setattr("evolve.DATA_DIR", tmp_path)
    monkeypatch.setattr("evolve.EVOLVE_STATE_FILE", tmp_path / "evolve-state.json")
    return tmp_path


class TestSentinelPropagation:
    def test_batch_guard_trigger_stored_in_result(self, tmp_path):
        """batch_guard_trigger sentinel が result["phases"]["skill_evolve"] に格納される。"""
        sentinel = {
            "_meta": "batch_guard_trigger",
            "groups": [{"origin": "custom", "skills": ["s1"], "skill_dirs": ["/x"],
                        "estimated_tokens": 47000, "skill_count": 1}],
            "total_effective": 11,
            "already_denied": [],
        }

        import evolve as _e
        with mock.patch.object(_e, "skill_evolve_assessment", return_value=[sentinel]):
            result = run_evolve(project_dir=str(tmp_path))

        se = result["phases"].get("skill_evolve", {})
        assert se.get("batch_guard_trigger") == sentinel

    def test_batch_guard_none_when_no_sentinel(self, tmp_path):
        """sentinel がない場合は batch_guard_trigger が None になる。"""
        normal_assessment = [
            {"skill_name": "my-skill", "skill_dir": "/x", "already_evolved": False,
             "suitability": "low", "scores": {}, "total_score": 0,
             "anti_patterns": [], "recommendation": "変換非推奨",
             "telemetry_detail": {}, "llm_cached": False}
        ]

        import evolve as _e
        with mock.patch.object(_e, "skill_evolve_assessment", return_value=normal_assessment):
            result = run_evolve(project_dir=str(tmp_path))

        se = result["phases"].get("skill_evolve", {})
        assert se.get("batch_guard_trigger") is None


class TestConfirmedBatchFlag:
    def test_confirmed_batch_bypasses_guard(self, tmp_path):
        """confirmed_batch=True のとき batch_guard_trigger が返らず通常評価が走る。"""
        normal_assessment = [
            {"skill_name": "my-skill", "skill_dir": "/x", "already_evolved": False,
             "suitability": "high", "scores": {}, "total_score": 12,
             "anti_patterns": [], "recommendation": "変換推奨",
             "telemetry_detail": {}, "llm_cached": False}
        ]

        import evolve as _e
        with mock.patch.object(_e, "skill_evolve_assessment", return_value=normal_assessment):
            result = run_evolve(project_dir=str(tmp_path), confirmed_batch=True)

        se = result["phases"].get("skill_evolve", {})
        assert se.get("batch_guard_trigger") is None
        assert se.get("total_skills") == 1

    def test_confirmed_batch_flag_passed_to_assessment(self, tmp_path):
        """confirmed_batch=True が skill_evolve_assessment に正しく伝播する。"""
        import evolve as _e
        captured = {}

        def fake_assessment(*args, **kwargs):
            captured["kwargs"] = kwargs
            return []

        with mock.patch.object(_e, "skill_evolve_assessment", side_effect=fake_assessment):
            run_evolve(project_dir=str(tmp_path), confirmed_batch=True)

        assert captured.get("kwargs", {}).get("confirmed_batch") is True

    def test_confirmed_batch_false_still_fires_guard(self, tmp_path):
        """confirmed_batch=False (default) では batch_guard_trigger が通常通り発火する。"""
        sentinel = {
            "_meta": "batch_guard_trigger",
            "groups": [],
            "total_effective": 11,
            "already_denied": [],
        }

        import evolve as _e
        with mock.patch.object(_e, "skill_evolve_assessment", return_value=[sentinel]):
            result = run_evolve(project_dir=str(tmp_path), confirmed_batch=False)

        se = result["phases"].get("skill_evolve", {})
        assert se.get("batch_guard_trigger") == sentinel


class TestSkipLlmEvolveFlag:
    def test_skip_llm_evolve_passes_flag_to_assessment(self, tmp_path):
        """--skip-llm-evolve で skip_llm_evolve=True が assessment に渡される。"""
        import evolve as _e
        captured = {}

        def fake_assessment(*args, **kwargs):
            captured["kwargs"] = kwargs
            return []

        with mock.patch.object(_e, "skill_evolve_assessment", side_effect=fake_assessment):
            result = run_evolve(project_dir=str(tmp_path), skip_llm_evolve=True)

        assert captured.get("kwargs", {}).get("skip_llm_evolve") is True
        se = result["phases"].get("skill_evolve", {})
        assert se.get("total_skills", 0) == 0
