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


# ── provenance（#316） ──────────────────────────────────────────────────────

_OLD_SCHEMA_SQL = """
CREATE SEQUENCE IF NOT EXISTS fitness_history_id_seq;
CREATE TABLE IF NOT EXISTS fitness_history (
    id        BIGINT DEFAULT nextval('fitness_history_id_seq'),
    run_id    TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    axis      TEXT NOT NULL,
    score     REAL NOT NULL,
    weight_used REAL,
    source    TEXT DEFAULT 'audit',
    UNIQUE (run_id, axis)
);
"""


def test_record_fitness_run_stores_provenance_json(fhs):
    """axis 別 provenance を JSON で保存し、get_axis_history が dict として返す。"""
    run_id = str(uuid.uuid4())
    provenance = {
        "coherence": {
            "schema_version": 1,
            "evaluation_kind": "deterministic",
            "producer": "environment.coherence",
        },
    }
    fhs.record_fitness_run(
        run_id, {"coherence": 0.5}, {"coherence": 1.0}, provenance=provenance
    )
    history = fhs.get_axis_history("coherence", limit=1)
    prov = history[0]["provenance"]
    assert prov["evaluation_kind"] == "deterministic"
    assert prov["producer"] == "environment.coherence"
    assert "judge" not in prov  # 決定論評価に judge キーは持たせない契約


def test_record_fitness_run_provenance_defaults_to_unknown_when_missing(fhs):
    """provenance 未指定の axis は unknown envelope になる（fabricate しない）。"""
    run_id = str(uuid.uuid4())
    fhs.record_fitness_run(run_id, {"telemetry": 0.4}, {"telemetry": 1.0})
    history = fhs.get_axis_history("telemetry", limit=1)
    prov = history[0]["provenance"]
    assert prov is not None
    assert prov["evaluation_kind"] == "unknown"
    assert prov["producer"] is None


def test_record_fitness_run_llm_judge_provenance_keeps_judge_key(fhs):
    """constitutional 等 LLM judge axis は judge キー（model 等）を保持したまま保存される。"""
    run_id = str(uuid.uuid4())
    provenance = {
        "constitutional": {
            "schema_version": 1,
            "evaluation_kind": "llm_judge_aggregate",
            "producer": "constitutional",
            "judge": {
                "model": None,
                "effort": None,
                "tool_policy": {"mode": "unknown", "allowed_tools": None},
            },
            "judge_models": ["sonnet"],
        },
    }
    fhs.record_fitness_run(
        run_id, {"constitutional": 0.8}, {"constitutional": 1.0}, provenance=provenance
    )
    history = fhs.get_axis_history("constitutional", limit=1)
    prov = history[0]["provenance"]
    assert prov["judge_models"] == ["sonnet"]
    assert "judge" in prov


def test_old_schema_db_migrates_and_keeps_existing_rows(tmp_path):
    """provenance 列の無い旧スキーマ DB を新コードで開いても既存行が読める（migration）。

    遡及埋めはしない前提なので、旧行の provenance は None のまま。
    """
    import duckdb
    import lib.fitness_history_store as fhs_mod

    with mock.patch.dict(os.environ, {"CLAUDE_PLUGIN_DATA": str(tmp_path)}):
        importlib.reload(fhs_mod)

        con = duckdb.connect(str(fhs_mod.USAGE_DB))
        con.execute(_OLD_SCHEMA_SQL)
        old_run_id = str(uuid.uuid4())
        con.execute(
            "INSERT INTO fitness_history (run_id, timestamp, axis, score, weight_used, source) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [old_run_id, "2026-01-01T00:00:00+00:00", "coherence", 0.6, 1.0, "audit"],
        )
        con.close()

        new_run_id = str(uuid.uuid4())
        fhs_mod.record_fitness_run(new_run_id, {"coherence": 0.7}, {"coherence": 1.0})

        history = fhs_mod.get_axis_history("coherence", limit=10)
        assert len(history) == 2
        by_run_id = {h["run_id"]: h for h in history}
        assert old_run_id in by_run_id
        assert new_run_id in by_run_id
        assert by_run_id[old_run_id]["provenance"] is None
        assert by_run_id[new_run_id]["provenance"] is not None

    importlib.reload(fhs_mod)


def test_migration_preserves_unique_constraint(tmp_path):
    """ALTER TABLE ADD COLUMN 後も UNIQUE (run_id, axis) が有効（同 run_id 二重 insert は弾かれる）。"""
    import duckdb
    import lib.fitness_history_store as fhs_mod

    with mock.patch.dict(os.environ, {"CLAUDE_PLUGIN_DATA": str(tmp_path)}):
        importlib.reload(fhs_mod)

        con = duckdb.connect(str(fhs_mod.USAGE_DB))
        con.execute(_OLD_SCHEMA_SQL)
        con.close()

        run_id = str(uuid.uuid4())
        fhs_mod.record_fitness_run(run_id, {"coherence": 0.5}, {"coherence": 1.0})
        fhs_mod.record_fitness_run(run_id, {"coherence": 0.9}, {"coherence": 1.0})  # 同 run_id 二重

        history = fhs_mod.get_axis_history("coherence", limit=10)
        assert len(history) == 1, "UNIQUE(run_id, axis) が生きていれば二重 insert は弾かれる"

    importlib.reload(fhs_mod)


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


# ── environment.py の provenance 組立（#316） ───────────────────────────────

def test_environment_fitness_provenance_deterministic_axes_no_judge_key(tmp_path):
    """coherence/overall は deterministic axis として judge キー無しで provenance が渡る。"""
    import types

    env_mod = _load_environment_module("env_provenance_det_test")

    def _mock_load_sibling(name):
        if name == "coherence":
            m = mock.MagicMock()
            m.compute_coherence_score.return_value = {"overall": 0.72}
            return m
        raise RuntimeError(f"skipped: {name}")

    captured: dict = {}

    def _fake_record(run_id, axis_scores, weights, source="audit", provenance=None):
        captured["provenance"] = provenance

    fake_store = types.ModuleType("fitness_history_store")
    fake_store.record_fitness_run = _fake_record

    with mock.patch.object(env_mod, "_load_sibling", side_effect=_mock_load_sibling):
        with mock.patch.dict(sys.modules, {"fitness_history_store": fake_store}):
            env_mod.compute_environment_fitness(tmp_path, days=30, skip_llm=True, record=True)

    prov = captured["provenance"]
    assert prov["coherence"]["evaluation_kind"] == "deterministic"
    assert prov["coherence"]["producer"] == "environment.coherence"
    assert "judge" not in prov["coherence"]
    assert prov["overall"]["evaluation_kind"] == "deterministic"
    assert "judge" not in prov["overall"]


def test_environment_fitness_provenance_constitutional_reuses_layer_aggregate(tmp_path):
    """constitutional 軸は constitutional_result['provenance']（llm_judge_aggregate）を再利用する。

    environment 側で別の provenance を作り直すと、別時点の値を偽って合成することになる。
    """
    import types

    env_mod = _load_environment_module("env_provenance_con_test")

    con_provenance = {
        "schema_version": 1,
        "evaluation_kind": "llm_judge_aggregate",
        "producer": "constitutional",
        "judge_models": ["sonnet"],
    }

    def _mock_load_sibling(name):
        if name == "coherence":
            m = mock.MagicMock()
            m.compute_coherence_score.return_value = {"overall": 0.72}
            return m
        if name == "constitutional":
            m = mock.MagicMock()
            m.compute_constitutional_score.return_value = {
                "overall": 0.5,
                "provenance": con_provenance,
            }
            return m
        raise RuntimeError(f"skipped: {name}")

    captured: dict = {}

    def _fake_record(run_id, axis_scores, weights, source="audit", provenance=None):
        captured["provenance"] = provenance

    fake_store = types.ModuleType("fitness_history_store")
    fake_store.record_fitness_run = _fake_record

    with mock.patch.object(env_mod, "_load_sibling", side_effect=_mock_load_sibling):
        with mock.patch.dict(sys.modules, {"fitness_history_store": fake_store}):
            env_mod.compute_environment_fitness(tmp_path, days=30, skip_llm=False, record=True)

    prov = captured["provenance"]
    assert prov["constitutional"] == con_provenance


def test_environment_fitness_provenance_missing_axis_not_fabricated(tmp_path):
    """axis_results に provenance が無ければ environment 側で捏造しない（キー自体を作らない）。"""
    import types

    env_mod = _load_environment_module("env_provenance_missing_test")

    def _mock_load_sibling(name):
        if name == "constitutional":
            m = mock.MagicMock()
            # provenance キー無しの旧形式レスポンスを模す
            m.compute_constitutional_score.return_value = {"overall": 0.5}
            return m
        raise RuntimeError(f"skipped: {name}")

    captured: dict = {}

    def _fake_record(run_id, axis_scores, weights, source="audit", provenance=None):
        captured["provenance"] = provenance

    fake_store = types.ModuleType("fitness_history_store")
    fake_store.record_fitness_run = _fake_record

    with mock.patch.object(env_mod, "_load_sibling", side_effect=_mock_load_sibling):
        with mock.patch.dict(sys.modules, {"fitness_history_store": fake_store}):
            env_mod.compute_environment_fitness(tmp_path, days=30, skip_llm=False, record=True)

    prov = captured["provenance"]
    assert "constitutional" not in prov
