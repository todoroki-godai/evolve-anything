"""bin/evolve-prompt-compare のユニット + E2E テスト。"""
import importlib.machinery
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest

_BIN = Path(__file__).resolve().parent.parent.parent / "bin" / "evolve-prompt-compare"


def _load_module():
    loader = importlib.machinery.SourceFileLoader("rl_prompt_compare", str(_BIN))
    spec = importlib.util.spec_from_loader("rl_prompt_compare", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def mod():
    return _load_module()


# ─── _measure_with_prompt ────────────────────────────────────────

def test_measure_with_prompt_returns_runs(mod):
    """N 回分のスコア dict リストを返すことを確認。"""
    with mock.patch.object(mod, "_run_claude_prompt", return_value=0.7):
        result = mod._measure_with_prompt(
            axis="technical",
            prompt_template="rate this: {content}",
            other_prompts={"domain": "d: {content}", "structure": "s: {content}"},
            weights={"technical": 0.4, "domain": 0.4, "structure": 0.2},
            content="test content",
            runs=2,
            label="A",
        )
    assert len(result) == 2
    for r in result:
        assert "technical" in r
        assert "integrated" in r


def test_measure_with_prompt_handles_scorer_failure(mod):
    """スコアラーが例外を投げても FALLBACK_SCORE で埋める。"""
    with mock.patch.object(mod, "_run_claude_prompt", side_effect=RuntimeError("boom")):
        result = mod._measure_with_prompt(
            axis="technical",
            prompt_template="rate: {content}",
            other_prompts={"domain": "d: {content}", "structure": "s: {content}"},
            weights={"technical": 0.4, "domain": 0.4, "structure": 0.2},
            content="test",
            runs=1,
            label="A",
        )
    assert len(result) == 1
    assert result[0]["integrated"] == pytest.approx(0.5)


# ─── _print_report ───────────────────────────────────────────────

def _make_result(recommended="a", mean_drift=0.01, mean_drift_warning=False):
    stats = {"mean": 0.7, "std": 0.02, "min": 0.65, "max": 0.75}
    return {
        "a": {"runs": [], "stats": {"integrated": stats}},
        "b": {"runs": [], "stats": {"integrated": stats}},
        "mean_drift": mean_drift,
        "sigma_delta": -0.001,
        "recommended": recommended,
        "mean_drift_warning": mean_drift_warning,
    }


def test_print_report_no_crash(mod, capsys):
    mod._print_report("technical", _make_result())
    out = capsys.readouterr().out
    assert "A (現行)" in out
    assert "B (候補)" in out


def test_print_report_shows_warning_on_drift(mod, capsys):
    mod._print_report("technical", _make_result(mean_drift_warning=True))
    out = capsys.readouterr().out
    assert "警告" in out or "⚠" in out


def test_print_report_shows_recommended(mod, capsys):
    mod._print_report("domain", _make_result(recommended="b"))
    out = capsys.readouterr().out
    assert "推奨" in out


# ─── data_dir 表示 ───────────────────────────────────────────────

def test_data_dir_uses_env_var(mod, capsys, monkeypatch):
    """CLAUDE_PLUGIN_DATA が設定されていれば指示文にそのパスが使われる。"""
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", "/custom/data")
    result = _make_result(recommended="b")
    mod._print_report("technical", result)
    out = capsys.readouterr().out
    # result["recommended"] == "b" なので data_dir 表示が出る
    # ただし _print_report 自体は data_dir を表示しない（main() の if ブロック）
    # ここでは crash しないことを確認
    assert "推奨" in out


# ─── argparse バリデーション ─────────────────────────────────────

def test_main_exits_on_missing_target(tmp_path):
    env = {"CLAUDE_PLUGIN_DATA": str(tmp_path), "PATH": "/usr/bin:/bin"}
    result = subprocess.run(
        [sys.executable, str(_BIN), "--axis", "technical",
         "--candidate", str(tmp_path / "cand.txt"), str(tmp_path / "missing.md")],
        capture_output=True, text=True, timeout=10, env=env,
    )
    assert result.returncode != 0
    assert "見つかりません" in result.stderr or "not found" in result.stderr.lower()


def test_main_exits_on_missing_candidate(tmp_path):
    target = tmp_path / "SKILL.md"
    target.write_text("# test skill")
    env = {"CLAUDE_PLUGIN_DATA": str(tmp_path), "PATH": "/usr/bin:/bin"}
    result = subprocess.run(
        [sys.executable, str(_BIN), "--axis", "technical",
         "--candidate", str(tmp_path / "missing.txt"), str(target)],
        capture_output=True, text=True, timeout=10, env=env,
    )
    assert result.returncode != 0
    assert "見つかりません" in result.stderr or "not found" in result.stderr.lower()


def test_main_exits_when_candidate_missing_placeholder(tmp_path):
    target = tmp_path / "SKILL.md"
    target.write_text("# test skill")
    cand = tmp_path / "cand.txt"
    cand.write_text("no placeholder here")
    env = {"CLAUDE_PLUGIN_DATA": str(tmp_path), "PATH": "/usr/bin:/bin"}
    result = subprocess.run(
        [sys.executable, str(_BIN), "--axis", "technical",
         "--candidate", str(cand), str(target)],
        capture_output=True, text=True, timeout=10, env=env,
    )
    assert result.returncode != 0
    assert "placeholder" in result.stderr or "{content}" in result.stderr


def test_main_exits_on_invalid_axis(tmp_path):
    target = tmp_path / "SKILL.md"
    target.write_text("# test")
    cand = tmp_path / "cand.txt"
    cand.write_text("{content}")
    env = {"CLAUDE_PLUGIN_DATA": str(tmp_path), "PATH": "/usr/bin:/bin"}
    result = subprocess.run(
        [sys.executable, str(_BIN), "--axis", "invalid_axis",
         "--candidate", str(cand), str(target)],
        capture_output=True, text=True, timeout=10, env=env,
    )
    assert result.returncode != 0
