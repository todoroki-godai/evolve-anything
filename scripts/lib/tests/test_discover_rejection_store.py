"""detect_rejection_patterns が ADR-031 の store から rejection 履歴を読むことの回帰。

従来は plugin 内 generations/history.jsonl を直読していた（更新でリセット）。
DATA_DIR/optimize_history/<slug>.jsonl へ集約後、history_file 注入 / store default の
両経路で rejection_reason 集計が機能することを検証する。
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from discover.errors import detect_rejection_patterns
import optimize_history_store as store


def _write(path: Path, records: list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in records),
        encoding="utf-8",
    )


def test_explicit_history_file_is_read(tmp_path):
    hf = tmp_path / "history.jsonl"
    _write(hf, [{"rejection_reason": "too_verbose"}] * 3)
    patterns = detect_rejection_patterns(threshold=3, history_file=hf)
    assert any(p["pattern"] == "too_verbose" and p["count"] == 3 for p in patterns)


def test_default_routes_through_store_for_current_slug(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "HISTORY_ROOT", tmp_path / "optimize_history")
    monkeypatch.setattr(store, "resolve_slug", lambda cwd=None: "proj-x")
    store.append_entry({"rejection_reason": "off_scope"}, "proj-x")
    store.append_entry({"rejection_reason": "off_scope"}, "proj-x")
    store.append_entry({"rejection_reason": "off_scope"}, "proj-x")
    # 別 slug のレコードは混ざらない
    store.append_entry({"rejection_reason": "noise"}, "other")

    patterns = detect_rejection_patterns(threshold=3)
    reasons = {p["pattern"] for p in patterns}
    assert "off_scope" in reasons
    assert "noise" not in reasons


def test_below_threshold_not_reported(tmp_path):
    hf = tmp_path / "history.jsonl"
    _write(hf, [{"rejection_reason": "rare"}] * 2)
    patterns = detect_rejection_patterns(threshold=3, history_file=hf)
    assert all(p["pattern"] != "rare" for p in patterns)


# --- project_root 明示指定が cwd より優先されること（#400 cwd-leak 修理） ---
# 単一 cwd から他 PJ の project_dir を渡すバッチ経路（evolve-fleet propose / run_discover）で、
# 実行元 PJ（cwd）の rejection 履歴が対象 PJ の判定に混入しないことを保証する。

def test_project_root_param_overrides_cwd_slug(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "HISTORY_ROOT", tmp_path / "optimize_history")
    # store.resolve_slug は cwd 引数の有無で異なる slug を返す（実装同型の簡易フェイク）。
    monkeypatch.setattr(
        store, "resolve_slug",
        lambda cwd=None: Path(cwd).name if cwd else "cwd-slug",
    )
    # cwd（project_root 未指定時の既定）側の履歴 — 混入してはいけない
    store.append_entry({"rejection_reason": "cwd_reason"}, "cwd-slug")
    store.append_entry({"rejection_reason": "cwd_reason"}, "cwd-slug")
    store.append_entry({"rejection_reason": "cwd_reason"}, "cwd-slug")
    # project_root で明示指定する対象 PJ 側の履歴
    pj_b = tmp_path / "pj-b"
    pj_b.mkdir()
    store.append_entry({"rejection_reason": "pj_b_reason"}, "pj-b")
    store.append_entry({"rejection_reason": "pj_b_reason"}, "pj-b")
    store.append_entry({"rejection_reason": "pj_b_reason"}, "pj-b")

    patterns = detect_rejection_patterns(threshold=3, project_root=pj_b)
    reasons = {p["pattern"] for p in patterns}
    assert "pj_b_reason" in reasons
    assert "cwd_reason" not in reasons


def test_history_file_still_wins_over_project_root(tmp_path, monkeypatch):
    """history_file 明示指定は project_root より優先される（既存の優先度を維持）。"""
    monkeypatch.setattr(store, "HISTORY_ROOT", tmp_path / "optimize_history")
    hf = tmp_path / "explicit_history.jsonl"
    _write(hf, [{"rejection_reason": "explicit_reason"}] * 3)
    pj_b = tmp_path / "pj-b"
    pj_b.mkdir()

    patterns = detect_rejection_patterns(threshold=3, history_file=hf, project_root=pj_b)
    reasons = {p["pattern"] for p in patterns}
    assert "explicit_reason" in reasons
