"""aggregate_runs.load_history が ADR-031 の store から読むことの回帰。

旧: GENERATIONS_DIR/history.jsonl 直読（plugin 内・更新リセット）。
新: optimize_history_store の current slug ファイル。
"""
import sys
from pathlib import Path

import pytest

_plugin_root = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(_plugin_root / "scripts" / "lib"))
sys.path.insert(0, str(_plugin_root / "skills" / "audit" / "scripts"))

import optimize_history_store as store
import aggregate_runs


def test_load_history_reads_from_store_current_slug(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "HISTORY_ROOT", tmp_path / "optimize_history")
    monkeypatch.setattr(store, "resolve_slug", lambda cwd=None: "proj")
    store.append_entry({"strategy": "elite", "human_accepted": True, "best_fitness": 0.6}, "proj")
    store.append_entry({"strategy": "mutation", "human_accepted": False}, "proj")
    # 別 slug は混ざらない
    store.append_entry({"strategy": "x", "human_accepted": True}, "other")

    history = aggregate_runs.load_history()
    assert len(history) == 2
    assert {h["strategy"] for h in history} == {"elite", "mutation"}


def test_load_history_empty_when_no_records(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "HISTORY_ROOT", tmp_path / "optimize_history")
    monkeypatch.setattr(store, "resolve_slug", lambda cwd=None: "empty")
    assert aggregate_runs.load_history() == []
