"""#407 / #408 のテスト。

#407: `--observe-first` は安価な observe + fitness ゲートだけ算出して early-return し、
      重いフェーズ（discover/audit/skill_evolve/remediation/prune…）を回さない。
#408: result トップレベルに同一性 metadata（slug / project_dir / generated_at /
      env_tier_reason）が必須化される。constitutional None の文言が「失敗」でなく
      「cache stale」になる。CLI 1 行サマリに slug が出る。
"""

import json
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent
_PLUGIN_ROOT = _SCRIPTS.parent.parent.parent
sys.path.insert(0, str(_SCRIPTS))
sys.path.insert(0, str(_PLUGIN_ROOT / "scripts" / "lib"))
sys.path.insert(0, str(_PLUGIN_ROOT / "scripts" / "rl"))

import evolve  # noqa: E402


# --- #407: observe-first early-return ---------------------------------------


@pytest.fixture
def stub_observe(monkeypatch):
    """observe / fitness を安価な固定値に差し替える（実 DATA_DIR を読ませない）。"""

    def _set(action_kwargs):
        suff = {
            "sessions": 0,
            "observations": 0,
            "total_observations": 100,
            "sufficient": True,
            "telemetry_empty": False,
            "backfill_recommended": False,
            "no_new_observations": False,
            "message": "stub",
        }
        suff.update(action_kwargs)
        monkeypatch.setattr(evolve, "check_data_sufficiency", lambda project_dir=None: dict(suff))
        monkeypatch.setattr(
            evolve, "check_fitness_function",
            lambda project_dir=None: {"has_fitness": False, "fitness_functions": []},
        )

    return _set


def test_observe_first_returns_early_without_heavy_phases(stub_observe, monkeypatch, tmp_path):
    """observe_first=True は observe+fitness だけ持ち、重いフェーズを回さない（#407）。"""
    # discover が呼ばれたら検知できるよう sentinel を仕込む。early-return なら呼ばれない。
    # monkeypatch.setitem を使い、テスト後に sys.modules["discover"] を自動復元する
    # （手動 pop は real discover を消して後続テストを汚染する）。
    called = {"discover": False}
    fake = type(sys)("discover")
    fake.run_discover = lambda **k: called.__setitem__("discover", True)
    monkeypatch.setitem(sys.modules, "discover", fake)

    stub_observe({"no_new_observations": True})
    result = evolve.run_evolve(project_dir=str(tmp_path), dry_run=True, observe_first=True)

    assert result["observe_first"] is True
    assert result["skipped_heavy_phases"] is True
    assert called["discover"] is False
    phases = result["phases"]
    assert set(phases.keys()) == {"observe", "fitness"}
    for heavy in ("discover", "audit", "skill_evolve", "remediation", "prune"):
        assert heavy not in phases
    # observe の action は通常フローと同じく算出される（SKILL が分岐に使う）
    assert phases["observe"]["action"] == "lightweight_recommended"


def test_observe_first_includes_identity_metadata(stub_observe, tmp_path):
    """early-return しても metadata は付く（#408 — pre-flight 出力も同一性検証可能に）。"""
    stub_observe({})
    result = evolve.run_evolve(project_dir=str(tmp_path), dry_run=True, observe_first=True)

    assert "slug" in result and isinstance(result["slug"], str) and result["slug"]
    assert result["project_dir"] == str(tmp_path.resolve())
    assert "generated_at" in result
    assert result["generated_at"] == result["timestamp"]
    assert result["dry_run"] is True


# --- #408-E: env_tier 決定根拠 ----------------------------------------------


def test_env_tier_reason_present_and_consistent(stub_observe, tmp_path):
    """env_tier_reason に count/breakdown/thresholds が乗り、tier と整合する（#408-E）。"""
    stub_observe({})
    result = evolve.run_evolve(project_dir=str(tmp_path), dry_run=True, observe_first=True)

    reason = result["env_tier_reason"]
    assert reason["count"] == reason["breakdown"]["total"]
    assert set(reason["breakdown"]) == {"skills_dir", "claude_md_skills", "rules", "total"}
    assert reason["thresholds"] == evolve.ENV_TIER_THRESHOLDS
    # 空 tmp PJ は artifact 0 → small
    assert result["env_tier"] == "small"
    assert reason["count"] == 0


# --- #408-B: CLI 1 行サマリに slug が出る ------------------------------------


def test_cli_summary_surfaces_slug(monkeypatch, tmp_path, capsys):
    fake_result = {
        "slug": "my-proj",
        "project_dir": "/abs/my-proj",
        "generated_at": "2026-06-10T00:00:00+00:00",
        "dry_run": True,
        "env_tier": "small",
        "phases": {"observe": {"action": "ok"}, "fitness": {}},
    }
    monkeypatch.setattr(evolve, "run_evolve", lambda **k: dict(fake_result))
    out = tmp_path / "o.json"
    monkeypatch.setattr(sys, "argv", ["evolve.py", "--dry-run", "--output", str(out)])

    evolve.main()

    summary = json.loads(capsys.readouterr().out)
    assert summary["slug"] == "my-proj"
    assert summary["project_dir"] == "/abs/my-proj"
    assert summary["generated_at"] == fake_result["generated_at"]
    assert summary["dry_run"] is True


# --- #408-D: constitutional None を warnings/observability に surface --------


def _patch_constitutional(monkeypatch, value):
    import fitness.constitutional as con
    monkeypatch.setattr(con, "compute_constitutional_score", lambda p: value)


def test_constitutional_none_surfaced_to_warnings_and_observability(monkeypatch, tmp_path):
    """cache stale/全 miss（None）は observability に「失敗でない」案内で乗る。

    #561: 良性 advisory は warning_sink に積まない（scipy RuntimeWarning 等の真の警告用）。
    """
    _patch_constitutional(monkeypatch, None)
    sink, obs = [], {}
    line = evolve._surface_constitutional_status(tmp_path, sink, obs)

    assert line is not None
    assert "失敗ではない" in line
    assert "refresh" in line or "再生成" in line
    # #561: warning_sink には積まない（良性 advisory を bug 候補として拾わせない）
    assert sink == []
    assert obs["constitutional"] == [line]


def test_constitutional_cache_advisory_not_in_warning_sink(monkeypatch, tmp_path):
    """#561 regression: constitutional_cache カテゴリが warning_sink に入らない。

    warning_sink → result["warnings"] → evolve_introspect._detect_captured_warnings で
    bug 候補として拾われるため、良性 advisory をこのパスに流してはいけない。
    observability にのみ surface する。
    """
    _patch_constitutional(monkeypatch, None)
    sink, obs = [], {}
    evolve._surface_constitutional_status(tmp_path, sink, obs)
    # warning_sink は空のまま
    assert sink == []
    # observability には乗る
    assert "constitutional" in obs
    assert obs["constitutional"]


def test_constitutional_low_coverage_not_in_warning_sink(monkeypatch, tmp_path):
    """#561 regression: low_coverage 良性 advisory も warning_sink に入らない。"""
    _patch_constitutional(monkeypatch, {"overall": None, "skip_reason": "low_coverage"})
    sink, obs = [], {}
    line = evolve._surface_constitutional_status(tmp_path, sink, obs)
    assert "coverage" in line
    # #561: warning_sink には積まない
    assert sink == []
    assert obs["constitutional"] == [line]


def test_constitutional_scored_not_surfaced(monkeypatch, tmp_path):
    """正常算出（overall あり）なら warnings/observability に何も足さない。"""
    _patch_constitutional(monkeypatch, {"overall": 0.8})
    sink, obs = [], {}
    line = evolve._surface_constitutional_status(tmp_path, sink, obs)
    assert line is None
    assert sink == []
    assert "constitutional" not in obs


def test_cli_observe_first_flag_wired(monkeypatch, tmp_path, capsys):
    """--observe-first が run_evolve まで届き、サマリに observe_action が出る。"""
    captured_kwargs = {}

    def fake_run(**kwargs):
        captured_kwargs.update(kwargs)
        return {
            "slug": "p", "env_tier": "small", "observe_first": True,
            "phases": {"observe": {"action": "lightweight_recommended"}, "fitness": {}},
        }

    monkeypatch.setattr(evolve, "run_evolve", fake_run)
    out = tmp_path / "o.json"
    monkeypatch.setattr(
        sys, "argv",
        ["evolve.py", "--dry-run", "--observe-first", "--output", str(out)],
    )

    evolve.main()

    assert captured_kwargs["observe_first"] is True
    summary = json.loads(capsys.readouterr().out)
    assert summary["observe_first"] is True
    assert summary["observe_action"] == "lightweight_recommended"
