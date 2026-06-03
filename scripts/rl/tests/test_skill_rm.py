#!/usr/bin/env python3
"""skill_rm.py (Skill-RM) のテスト。

Skill-RM (arXiv:2606.03980): スキルごとに異なる成功条件（異種基準）を
共通軸（structure / success / validity）に射影し、単一の報酬で横断評価する。

決定論モジュールのため LLM mock は不要。telemetry_query の DATA_DIR のみ差し替える。
"""

import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import pytest

_test_dir = Path(__file__).resolve().parent
_rl_dir = _test_dir.parent
_plugin_root = _rl_dir.parent.parent
sys.path.insert(0, str(_rl_dir))
sys.path.insert(0, str(_plugin_root / "scripts" / "lib"))
sys.path.insert(0, str(_plugin_root / "scripts"))

_rm_path = _rl_dir / "fitness" / "skill_rm.py"
_spec = importlib.util.spec_from_file_location("skill_rm", _rm_path)
skill_rm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(skill_rm)

_env_path = _rl_dir / "fitness" / "environment.py"
_espec = importlib.util.spec_from_file_location("environment", _env_path)
environment = importlib.util.module_from_spec(_espec)
_espec.loader.exec_module(environment)


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _days_ago_iso(days):
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def _write_jsonl(path, records):
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n")


def _make_skill(project_dir, name, *, description="A skill. Use when needed. Trigger: x", body_lines=60):
    skill_dir = project_dir / ".claude" / "skills" / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    fm = f"---\nname: {name}\ndescription: {description}\n---\n"
    body = "\n".join(f"Step {i}" for i in range(body_lines))
    (skill_dir / "SKILL.md").write_text(fm + "\n# " + name + "\n\n" + body)
    return skill_dir


# ── 共通軸の単一報酬 ─────────────────────────────────────────


class TestComputeSkillRewards:
    def test_no_skills_returns_empty(self, tmp_path):
        result = skill_rm.compute_skill_rewards(tmp_path, days=30)
        assert result["skills"] == {}
        assert result["mean_reward"] is None
        assert result["skill_count"] == 0

    def test_single_skill_reward_in_range(self, tmp_path):
        _make_skill(tmp_path, "alpha")
        data_dir = tmp_path / "_data"
        data_dir.mkdir()
        _write_jsonl(data_dir / "usage.jsonl", [
            {"skill_name": "alpha", "session_id": "s1", "ts": _days_ago_iso(1)},
            {"skill_name": "alpha", "session_id": "s2", "ts": _days_ago_iso(2)},
        ])
        _write_jsonl(data_dir / "errors.jsonl", [])
        _write_jsonl(data_dir / "corrections.jsonl", [])

        with mock.patch("telemetry_query.DATA_DIR", data_dir), \
             mock.patch("telemetry_query.HAS_DUCKDB", False):
            result = skill_rm.compute_skill_rewards(tmp_path, days=30)

        assert "alpha" in result["skills"]
        reward = result["skills"]["alpha"]["reward"]
        assert 0.0 <= reward <= 1.0
        assert result["skill_count"] == 1

    def test_reward_uses_normalize_weights_sot(self, tmp_path):
        """報酬合成は environment._normalize_weights を単一ソースとして使う。"""
        _make_skill(tmp_path, "alpha")
        data_dir = tmp_path / "_data"
        data_dir.mkdir()
        _write_jsonl(data_dir / "usage.jsonl", [
            {"skill_name": "alpha", "session_id": "s1", "ts": _days_ago_iso(1)},
        ])
        _write_jsonl(data_dir / "errors.jsonl", [])
        _write_jsonl(data_dir / "corrections.jsonl", [])

        with mock.patch("telemetry_query.DATA_DIR", data_dir), \
             mock.patch("telemetry_query.HAS_DUCKDB", False):
            result = skill_rm.compute_skill_rewards(tmp_path, days=30)

        entry = result["skills"]["alpha"]
        axes = entry["axes"]
        weights = entry["weights"]
        # weights は available axes のみで正規化され合計1.0
        assert abs(sum(weights.values()) - 1.0) < 1e-6
        # reward は axes・weights の内積に一致（数式単一ソース）
        expected = sum(axes[a] * weights[a] for a in weights)
        assert abs(entry["reward"] - round(expected, 4)) < 1e-4

    def test_weights_match_environment_normalize(self, tmp_path):
        """skill_rm の weights は environment._normalize_weights と同一の正規化規則。"""
        _make_skill(tmp_path, "alpha")
        data_dir = tmp_path / "_data"
        data_dir.mkdir()
        _write_jsonl(data_dir / "usage.jsonl", [
            {"skill_name": "alpha", "session_id": "s1", "ts": _days_ago_iso(1)},
        ])
        _write_jsonl(data_dir / "errors.jsonl", [])
        _write_jsonl(data_dir / "corrections.jsonl", [])

        with mock.patch("telemetry_query.DATA_DIR", data_dir), \
             mock.patch("telemetry_query.HAS_DUCKDB", False):
            result = skill_rm.compute_skill_rewards(tmp_path, days=30)

        entry = result["skills"]["alpha"]
        axes_present = list(entry["axes"].keys())
        # _normalize_weights は environment が SoT。skill_rm は同関数を SKILL_RM_BASE_WEIGHTS で再利用する
        reweighted = skill_rm._normalize_skill_axes(axes_present)
        assert entry["weights"] == reweighted
        # environment._normalize_weights を直接 base 指定で呼んでも同結果（数式単一ソース）
        direct = environment._normalize_weights(axes_present, skill_rm.SKILL_RM_BASE_WEIGHTS)
        assert entry["weights"] == direct

    def test_error_lowers_validity(self, tmp_path):
        """エラーが多いスキルは validity 軸が下がり reward も下がる。"""
        _make_skill(tmp_path, "alpha")
        _make_skill(tmp_path, "beta")
        data_dir = tmp_path / "_data"
        data_dir.mkdir()
        _write_jsonl(data_dir / "usage.jsonl", [
            {"skill_name": "alpha", "session_id": "s1", "ts": _days_ago_iso(1)},
            {"skill_name": "alpha", "session_id": "s2", "ts": _days_ago_iso(2)},
            {"skill_name": "beta", "session_id": "s3", "ts": _days_ago_iso(1)},
            {"skill_name": "beta", "session_id": "s4", "ts": _days_ago_iso(2)},
        ])
        # beta だけエラー多発
        _write_jsonl(data_dir / "errors.jsonl", [
            {"skill_name": "beta", "session_id": "s3", "timestamp": _days_ago_iso(1)},
            {"skill_name": "beta", "session_id": "s4", "timestamp": _days_ago_iso(2)},
        ])
        _write_jsonl(data_dir / "corrections.jsonl", [])

        with mock.patch("telemetry_query.DATA_DIR", data_dir), \
             mock.patch("telemetry_query.HAS_DUCKDB", False):
            result = skill_rm.compute_skill_rewards(tmp_path, days=30)

        assert result["skills"]["beta"]["axes"]["validity"] < result["skills"]["alpha"]["axes"]["validity"]
        assert result["skills"]["beta"]["reward"] < result["skills"]["alpha"]["reward"]

    def test_correction_lowers_success(self, tmp_path):
        """invoke 直後に correction があるスキルは success 軸が下がる。"""
        _make_skill(tmp_path, "alpha")
        data_dir = tmp_path / "_data"
        data_dir.mkdir()
        base = datetime.now(timezone.utc) - timedelta(days=1)
        _write_jsonl(data_dir / "usage.jsonl", [
            {"skill_name": "alpha", "session_id": "s1", "ts": base.isoformat()},
        ])
        _write_jsonl(data_dir / "errors.jsonl", [])
        _write_jsonl(data_dir / "corrections.jsonl", [
            {"session_id": "s1", "timestamp": (base + timedelta(seconds=10)).isoformat()},
        ])

        with mock.patch("telemetry_query.DATA_DIR", data_dir), \
             mock.patch("telemetry_query.HAS_DUCKDB", False):
            result = skill_rm.compute_skill_rewards(tmp_path, days=30)

        assert result["skills"]["alpha"]["axes"]["success"] == 0.0

    def test_dispersion_and_mean(self, tmp_path):
        """複数スキルで mean_reward と reward_spread が算出される。"""
        _make_skill(tmp_path, "alpha")
        _make_skill(tmp_path, "beta")
        data_dir = tmp_path / "_data"
        data_dir.mkdir()
        _write_jsonl(data_dir / "usage.jsonl", [
            {"skill_name": "alpha", "session_id": "s1", "ts": _days_ago_iso(1)},
            {"skill_name": "beta", "session_id": "s2", "ts": _days_ago_iso(1)},
        ])
        _write_jsonl(data_dir / "errors.jsonl", [
            {"skill_name": "beta", "session_id": "s2", "timestamp": _days_ago_iso(1)},
        ])
        _write_jsonl(data_dir / "corrections.jsonl", [])

        with mock.patch("telemetry_query.DATA_DIR", data_dir), \
             mock.patch("telemetry_query.HAS_DUCKDB", False):
            result = skill_rm.compute_skill_rewards(tmp_path, days=30)

        assert result["skill_count"] == 2
        assert result["mean_reward"] is not None
        assert result["reward_spread"] is not None
        assert result["reward_spread"] >= 0.0
        # outlier (worst) スキルが特定される
        assert result["worst_skill"] == "beta"


# ── 軸正規化のSoT共有 ─────────────────────────────────────────


class TestNormalizeSkillAxes:
    def test_full_axes_sum_to_one(self):
        w = skill_rm._normalize_skill_axes(["structure", "success", "validity"])
        assert abs(sum(w.values()) - 1.0) < 1e-6

    def test_subset_renormalizes(self):
        w_full = skill_rm._normalize_skill_axes(["structure", "success", "validity"])
        w_sub = skill_rm._normalize_skill_axes(["structure", "validity"])
        assert abs(sum(w_sub.values()) - 1.0) < 1e-6
        assert "success" not in w_sub
        # 部分集合でも比率は保たれる
        ratio_full = w_full["structure"] / w_full["validity"]
        ratio_sub = w_sub["structure"] / w_sub["validity"]
        assert abs(ratio_full - ratio_sub) < 1e-4

    def test_empty_axes(self):
        assert skill_rm._normalize_skill_axes([]) == {}

    def test_delegates_to_environment_normalize(self):
        """skill_rm._normalize_skill_axes は environment._normalize_weights を内部利用する。"""
        axes = ["structure", "success", "validity"]
        result = skill_rm._normalize_skill_axes(axes)
        assert set(result.keys()) == set(axes)
        # environment._normalize_weights に base 渡した結果と完全一致
        assert result == environment._normalize_weights(axes, skill_rm.SKILL_RM_BASE_WEIGHTS)


# ── レポート整形 ─────────────────────────────────────────


class TestFormatReport:
    def test_format_empty(self):
        result = {"skills": {}, "mean_reward": None, "reward_spread": None,
                  "skill_count": 0, "worst_skill": None}
        lines = skill_rm.format_skill_rm_report(result)
        assert any("Skill-RM" in ln for ln in lines)
        # 評価したが対象なしの行が残る（silence != evaluated）
        assert any("対象" in ln or "no skills" in ln.lower() for ln in lines)

    def test_format_with_skills(self):
        result = {
            "skills": {
                "alpha": {"reward": 0.8, "axes": {"structure": 0.7, "success": 1.0, "validity": 1.0},
                          "weights": {"structure": 0.3, "success": 0.4, "validity": 0.3}},
                "beta": {"reward": 0.4, "axes": {"structure": 0.5, "success": 0.0, "validity": 0.5},
                         "weights": {"structure": 0.3, "success": 0.4, "validity": 0.3}},
            },
            "mean_reward": 0.6, "reward_spread": 0.2, "skill_count": 2, "worst_skill": "beta",
        }
        lines = skill_rm.format_skill_rm_report(result)
        text = "\n".join(lines)
        assert "Skill-RM" in text
        assert "alpha" in text
        assert "beta" in text
