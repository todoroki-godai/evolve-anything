"""ADR-031 split-brain 回帰 E2E。

旧構造: run_loop は cwd/.evolve-loop/history.jsonl に書き、readers（fitness_evolution /
discover）は plugin generations/history.jsonl を読む → run_loop の accept/reject が
readers に永久に届かない（孤立）。

本テストは run_loop が実際に行う書き込み（store.append_entry(loop_result, slug)）の後、
同一 slug で fitness_evolution.load_history() と detect_rejection_patterns() の双方が
同じレコードを読めることを検証する＝3者が単一 location を共有することの証明。

LLM は呼ばない（run_loop 本体は走らせず、その書き込み契約のみ再現）。
"""
import sys
from pathlib import Path

import pytest

_lib = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_lib))
_plugin_root = _lib.parent.parent
sys.path.insert(0, str(_plugin_root / "skills" / "evolve-fitness" / "scripts"))
sys.path.insert(0, str(_plugin_root / "scripts" / "rl" / "fitness"))

import optimize_history_store as store
import fitness_evolution as fe
from discover.errors import detect_rejection_patterns


@pytest.fixture
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "HISTORY_ROOT", tmp_path / "optimize_history")
    monkeypatch.setattr(store, "resolve_slug", lambda cwd=None: "atlas")
    return store


def _run_loop_write(loop_result: dict) -> None:
    """run_loop.py が行う書き込みと同一の契約（store.append_entry(loop_result, slug)）。"""
    store.append_entry(loop_result, store.resolve_slug())


def test_run_loop_record_is_visible_to_readers(isolated_store):
    """run_loop の書き込みが fitness_evolution / discover の双方から読める。"""
    # fitness_func + best_fitness + human_accepted を持つ正規レコード（calibration 母集団）
    _run_loop_write({
        "fitness_func": "skill_quality",
        "best_fitness": 0.7,
        "human_accepted": False,
        "rejection_reason": "off_scope",
    })

    # 1) store 直読
    assert len(store.load_history("atlas")) == 1
    # 2) fitness_evolution（default slug 経由）
    hist = fe.load_history()
    assert len(hist) == 1
    assert hist[0]["human_accepted"] is False
    # 3) discover の rejection 検出（同 slug）
    for _ in range(2):  # 閾値 3 に到達させる
        _run_loop_write({"rejection_reason": "off_scope"})
    patterns = detect_rejection_patterns(threshold=3)
    assert any(p["pattern"] == "off_scope" and p["count"] == 3 for p in patterns)


def test_other_project_records_do_not_leak(isolated_store, monkeypatch):
    """別 slug のレコードは current slug の reader に混入しない（PJ 分離）。"""
    store.append_entry({"fitness_func": "skill_quality", "best_fitness": 0.9,
                        "human_accepted": True}, "other-project")
    # current slug は "atlas" → 空
    assert fe.load_history() == []
    assert store.load_history("atlas") == []
