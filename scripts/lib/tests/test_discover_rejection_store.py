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
