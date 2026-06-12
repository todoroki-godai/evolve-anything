"""evolve.run_evolve が mark_expired に pj_slug を渡すことの保証（#495）。

ttl.mark_expired の cross-PJ write 防止（pj_slug フィルタ）は呼び出し側が
slug を渡してはじめて発火する。API 追加だけで配線を忘れる繋ぎ目バグを
このテストが封じる。決定論・LLM 非依存。
"""
import sys
from pathlib import Path

import pytest

_plugin_root = Path(__file__).resolve().parent.parent.parent.parent.parent
sys.path.insert(0, str(_plugin_root / "skills" / "evolve" / "scripts"))
sys.path.insert(0, str(_plugin_root / "skills" / "audit" / "scripts"))
sys.path.insert(0, str(_plugin_root / "scripts" / "lib"))
sys.path.insert(0, str(_plugin_root / "scripts"))

from evolve import run_evolve, _resolve_pj_slug  # noqa: E402


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    """テスト用 DATA_DIR を設定（実環境 DATA_DIR を読み書きさせない）。"""
    monkeypatch.setattr("evolve.DATA_DIR", tmp_path)
    monkeypatch.setattr("evolve.EVOLVE_STATE_FILE", tmp_path / "evolve-state.json")
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path))
    return tmp_path


def test_mark_expired_receives_current_pj_slug(data_dir, monkeypatch):
    """run_evolve は mark_expired に当 PJ の pj_slug を渡す（cross-PJ write 防止 #495）。"""
    import weak_signals.ttl as ws_ttl

    captured = {}

    def _fake_mark_expired(*args, **kwargs):
        captured.update(kwargs)
        return {"expired": 0, "scanned": 0, "dry_run": kwargs.get("dry_run", False)}

    monkeypatch.setattr(ws_ttl, "mark_expired", _fake_mark_expired)

    run_evolve(dry_run=True)

    assert "pj_slug" in captured, "mark_expired に pj_slug が渡っていない（配線漏れ）"
    assert captured["pj_slug"] == _resolve_pj_slug(None)
    assert captured["pj_slug"], "pj_slug が空"
