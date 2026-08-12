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


def test_load_history_excludes_reverted_accept_and_revert_event(tmp_path, monkeypatch):
    """#402 段階4 §1(S1): raw のままだと revert イベントが history[-10:] に混入し本物の
    decision を押し出す（score trend 汚染）。effective view への移行でこれを防ぐ。"""
    monkeypatch.setattr(store, "HISTORY_ROOT", tmp_path / "optimize_history")
    monkeypatch.setattr(store, "resolve_slug", lambda cwd=None: "proj")
    store.append_entry(
        {"id": "e1", "strategy": "elite", "human_accepted": True, "best_fitness": 0.6}, "proj"
    )
    store.append_entry(
        {
            "event_type": "revert", "reverted_entry_id": "e1", "revert_event_id": "rev1",
            "revert_generation": 1, "scope": "project", "repo_id": "r", "relative_path": "p",
        },
        "proj",
    )
    store.append_entry({"id": "e2", "strategy": "mutation", "human_accepted": False}, "proj")

    history = aggregate_runs.load_history()

    assert len(history) == 1
    assert history[0]["id"] == "e2"
