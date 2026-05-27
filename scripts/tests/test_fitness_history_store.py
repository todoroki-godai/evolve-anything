"""tests for fitness_history_store — Phase 1 (issue #240)。

DuckDB は実 DB を使う（tmp_path fixture でテスト用 DB パスを差し替え）。
CLAUDE_PLUGIN_DATA 環境変数で token_usage_store と同じパターン。
"""
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


def _make_store(tmp_path: Path):
    """tmp_path を CLAUDE_PLUGIN_DATA に設定して fitness_history_store を再ロードする。"""
    import importlib
    import lib.fitness_history_store as fhs_module

    with mock.patch.dict(os.environ, {"CLAUDE_PLUGIN_DATA": str(tmp_path)}):
        # モジュールレベルの定数を tmp_path に差し替えて再インポート
        importlib.reload(fhs_module)
        yield fhs_module

    # テスト後に本来の状態に戻す
    importlib.reload(fhs_module)


@pytest.fixture()
def fhs(tmp_path):
    """tmp_path を向いた fitness_history_store モジュールを返す fixture。"""
    import importlib
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
    """同 run_id を2回 insert しても重複なし（INSERT OR IGNORE）。"""
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

    # 5 件挿入
    for _ in range(5):
        fhs.record_fitness_run(str(uuid.uuid4()), axis_scores, weights)

    history_3 = fhs.get_axis_history("coherence", limit=3)
    assert len(history_3) == 3

    history_10 = fhs.get_axis_history("coherence", limit=10)
    assert len(history_10) == 5


def test_get_axis_history_returns_newest_first(fhs):
    """新しい順に返る。"""
    import time

    scores = [0.50, 0.60, 0.70]
    run_ids = []
    for s in scores:
        rid = str(uuid.uuid4())
        run_ids.append(rid)
        fhs.record_fitness_run(rid, {"coherence": s}, {"coherence": 1.0})
        time.sleep(0.01)  # timestamp の差をつける

    history = fhs.get_axis_history("coherence", limit=10)
    assert len(history) == 3
    # 最新が先頭（スコア 0.70 の run が最後に挿入された）
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


def test_environment_fitness_records_on_default(tmp_path):
    """compute_environment_fitness() を record=True（デフォルト）で呼ぶと
    record_fitness_run が呼ばれる。

    coherence 軸をモックして axis_scores が空でない状態を保証し、
    record_fitness_run が必ず 1 回呼ばれることを検証する。

    environment.py は `from fitness_history_store import record_fitness_run` で
    関数実行時にインポートする。importlib.util でロードした場合のモジュール名は
    "environment_test" となるため、sys.modules に "fitness_history_store" を
    差し込んでパッチする。
    """
    import importlib
    import importlib.util
    import lib.fitness_history_store as fhs_module

    scripts_dir = Path(__file__).resolve().parent.parent.parent
    rl_fitness_dir = scripts_dir / "scripts" / "rl" / "fitness"

    with mock.patch.dict(os.environ, {"CLAUDE_PLUGIN_DATA": str(tmp_path)}):
        importlib.reload(fhs_module)

        # environment.py を再ロード（sys.path に scripts/ が必要）
        spec = importlib.util.spec_from_file_location(
            "environment_test",
            rl_fitness_dir / "environment.py",
        )
        env_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(env_mod)

        # coherence 軸が計算成功するようにモック → axis_scores が空でない状態を保証
        fake_coherence_result = {"overall": 0.75, "axes": {}}
        fake_coherence_mod = mock.MagicMock()
        fake_coherence_mod.compute_coherence_score.return_value = fake_coherence_result

        mock_record = mock.MagicMock()
        # fhs_module を "fitness_history_store" として sys.modules に差し込み、
        # record_fitness_run をモックに差し替えてパッチする
        patched_fhs = mock.MagicMock()
        patched_fhs.record_fitness_run = mock_record
        with mock.patch.object(env_mod, "_load_sibling", return_value=fake_coherence_mod), \
             mock.patch.dict(
                 __import__("sys").modules,
                 {"fitness_history_store": patched_fhs},
             ):
            # compute を呼ぶ（LLM 呼ばない, skip_llm=True）
            env_mod.compute_environment_fitness(
                tmp_path, days=30, skip_llm=True, record=True
            )

        # axis_scores が空でない（coherence が計算された） → record_fitness_run が呼ばれる
        mock_record.assert_called_once()

    importlib.reload(fhs_module)


def test_environment_fitness_no_record_when_false(tmp_path):
    """record=False で呼ぶと record_fitness_run が呼ばれない。"""
    import importlib.util

    scripts_dir = Path(__file__).resolve().parent.parent.parent
    rl_fitness_dir = scripts_dir / "scripts" / "rl" / "fitness"

    spec = importlib.util.spec_from_file_location(
        "environment_test_norecord",
        rl_fitness_dir / "environment.py",
    )
    env_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(env_mod)

    # coherence 軸が計算成功するようにモック → axis_scores が空でない状態を保証
    fake_coherence_result = {"overall": 0.75, "axes": {}}
    fake_coherence_mod = mock.MagicMock()
    fake_coherence_mod.compute_coherence_score.return_value = fake_coherence_result

    mock_record = mock.MagicMock()
    patched_fhs = mock.MagicMock()
    patched_fhs.record_fitness_run = mock_record
    with mock.patch.object(env_mod, "_load_sibling", return_value=fake_coherence_mod), \
         mock.patch.dict(
             __import__("sys").modules,
             {"fitness_history_store": patched_fhs},
         ):
        env_mod.compute_environment_fitness(
            tmp_path, days=30, skip_llm=True, record=False
        )

    mock_record.assert_not_called()
