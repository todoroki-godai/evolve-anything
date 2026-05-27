"""tests for fitness_history_store — Phase 1 (issue #240)。

DuckDB は実 DB を使う（tmp_path fixture でテスト用 DB パスを差し替え）。
CLAUDE_PLUGIN_DATA 環境変数で token_usage_store と同じパターン。
"""
import importlib
import importlib.util
import os
import sys
import uuid
from pathlib import Path
from unittest import mock

import pytest

# sys.path に scripts/ を追加（conftest.py が追加済みだが念のため）
_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))


@pytest.fixture()
def fhs(tmp_path):
    """tmp_path を向いた fitness_history_store モジュールを返す fixture。"""
    import lib.fitness_history_store as fhs_module

    with mock.patch.dict(os.environ, {"CLAUDE_PLUGIN_DATA": str(tmp_path)}):
        importlib.reload(fhs_module)
        yield fhs_module

    importlib.reload(fhs_module)


# ── テスト群 ──────────────────────────────────────────────────────────────────

def test_record_fitness_run_basic(fhs):
    """スコアが DB に保存される。"""
    run_id = str(uuid.uuid4())
    axis_scores = {"coherence": 0.72, "telemetry": 0.55, "overall": 0.63}
    weights = {"coherence": 0.30, "telemetry": 0.70, "overall": 1.0}

    fhs.record_fitness_run(run_id, axis_scores, weights)

    history = fhs.get_axis_history("coherence", limit=10)
    assert len(history) == 1
    assert history[0]["run_id"] == run_id
    assert abs(history[0]["score"] - 0.72) < 1e-6
    assert abs(history[0]["weight_used"] - 0.30) < 1e-6
    assert history[0]["axis"] == "coherence"
    assert history[0]["source"] == "audit"


def test_record_fitness_run_idempotent(fhs):
    """同 run_id を2回 insert しても重複なし（ON CONFLICT DO NOTHING）。"""
    run_id = str(uuid.uuid4())
    axis_scores = {"telemetry": 0.48}
    weights = {"telemetry": 1.0}

    fhs.record_fitness_run(run_id, axis_scores, weights)
    fhs.record_fitness_run(run_id, axis_scores, weights)  # 2回目

    history = fhs.get_axis_history("telemetry", limit=10)
    assert len(history) == 1, f"Expected 1 row, got {len(history)}"


def test_get_axis_history_limit(fhs):
    """limit パラメータが効く。"""
    axis_scores = {"coherence": 0.80}
    weights = {"coherence": 1.0}

    for _ in range(5):
        fhs.record_fitness_run(str(uuid.uuid4()), axis_scores, weights)

    history_3 = fhs.get_axis_history("coherence", limit=3)
    assert len(history_3) == 3

    history_10 = fhs.get_axis_history("coherence", limit=10)
    assert len(history_10) == 5


def test_get_axis_history_returns_newest_first(fhs):
    """新しい順（id DESC）に返る。"""
    scores = [0.50, 0.60, 0.70]
    run_ids = []
    for s in scores:
        rid = str(uuid.uuid4())
        run_ids.append(rid)
        fhs.record_fitness_run(rid, {"coherence": s}, {"coherence": 1.0})

    history = fhs.get_axis_history("coherence", limit=10)
    assert len(history) == 3
    assert history[0]["run_id"] == run_ids[-1]


def test_get_axis_history_empty_when_no_data(fhs):
    """データなし → 空リストを返す。"""
    history = fhs.get_axis_history("constitutional", limit=10)
    assert history == []


def test_record_fitness_run_custom_source(fhs):
    """source パラメータが保存される。"""
    run_id = str(uuid.uuid4())
    fhs.record_fitness_run(
        run_id,
        {"skill_quality": 0.90},
        {"skill_quality": 1.0},
        source="fleet",
    )
    history = fhs.get_axis_history("skill_quality", limit=5)
    assert len(history) == 1
    assert history[0]["source"] == "fleet"


def test_record_fitness_run_nan_skipped(fhs):
    """NaN を含む axis_scores は記録しない。"""
    import math
    run_id = str(uuid.uuid4())
    fhs.record_fitness_run(run_id, {"coherence": math.nan}, {"coherence": 1.0})
    assert fhs.get_axis_history("coherence", limit=5) == []


def test_record_fitness_run_empty_scores_noop(fhs):
    """axis_scores={} のとき何も書かない。"""
    fhs.record_fitness_run(str(uuid.uuid4()), {}, {})
    assert fhs.get_axis_history("coherence", limit=5) == []


def _load_environment_module(name: str):
    """environment.py を独立したモジュール名でロードして返す。"""
    scripts_dir = Path(__file__).resolve().parent.parent.parent
    rl_fitness_dir = scripts_dir / "scripts" / "rl" / "fitness"
    spec = importlib.util.spec_from_file_location(name, rl_fitness_dir / "environment.py")
    env_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(env_mod)
    return env_mod


def test_environment_fitness_calls_record_when_axis_scores_nonempty(tmp_path, fhs):
    """compute_environment_fitness が axis_scores 非空のとき record_fitness_run を呼ぶ。

    coherence 軸のみをモックして非空の axis_scores を確保し、
    DB に実際に書き込まれることを assert する。
    """
    env_mod = _load_environment_module("env_record_call_test")

    # coherence のみ成功させ、他の軸はスキップ（raise で except 節へ）
    def _mock_load_sibling(name):
        if name == "coherence":
            m = mock.MagicMock()
            m.compute_coherence_score.return_value = {"overall": 0.72}
            return m
        raise RuntimeError(f"skipped: {name}")

    # scripts.lib.fitness_history_store をリロードして tmp_path DB を向かせる
    import lib.fitness_history_store as fhs_mod
    with mock.patch.dict(os.environ, {"CLAUDE_PLUGIN_DATA": str(tmp_path)}):
        importlib.reload(fhs_mod)
        with mock.patch.object(env_mod, "_load_sibling", side_effect=_mock_load_sibling):
            env_mod.compute_environment_fitness(tmp_path, days=30, skip_llm=True, record=True)

    history = fhs.get_axis_history("coherence", limit=10)
    assert len(history) >= 1, "record=True かつ axis_scores 非空なら DB に記録されるべき"


def test_environment_fitness_no_record_when_false(tmp_path):
    """record=False のとき DB に書き込まない。"""
    env_mod = _load_environment_module("env_no_record_test")

    def _mock_load_sibling(name):
        if name == "coherence":
            m = mock.MagicMock()
            m.compute_coherence_score.return_value = {"overall": 0.72}
            return m
        raise RuntimeError(f"skipped: {name}")

    import lib.fitness_history_store as fhs_mod
    with mock.patch.dict(os.environ, {"CLAUDE_PLUGIN_DATA": str(tmp_path)}):
        importlib.reload(fhs_mod)
        with mock.patch.object(env_mod, "_load_sibling", side_effect=_mock_load_sibling):
            env_mod.compute_environment_fitness(tmp_path, days=30, skip_llm=True, record=False)

    # tmp_path の DB は存在しないか空のはず
    history = fhs_mod.get_axis_history("coherence", limit=10)
    assert history == [], "record=False なら DB に書き込まれないはず"
