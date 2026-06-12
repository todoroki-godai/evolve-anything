"""evolve.run_evolve が「今日の修正確認」phase を常時 emit することの保証（#446）。

daily_review phase の出力（correction_review["daily"]）が、
- 常時 emit される（eligible でなくても result にキーを置く・常時 emit 原則）
- #443 の bootstrap phase と同居する（同じ correction_review dict に相乗り）
- dry_run では既読集合（correction_review_seen.jsonl）を一切書かない
  （pitfall_dryrun_stateful_store_write を最下層まで貫通）
ことを実 run_evolve 経由で検証する。決定論・LLM 非依存。
"""
import sys
from pathlib import Path

import pytest

_plugin_root = Path(__file__).resolve().parent.parent.parent.parent.parent
sys.path.insert(0, str(_plugin_root / "skills" / "evolve" / "scripts"))
sys.path.insert(0, str(_plugin_root / "skills" / "audit" / "scripts"))
sys.path.insert(0, str(_plugin_root / "scripts" / "lib"))
sys.path.insert(0, str(_plugin_root / "scripts"))

from evolve import run_evolve  # noqa: E402


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    """テスト用 DATA_DIR を設定（実環境 DATA_DIR を読み書きさせない）。"""
    monkeypatch.setattr("evolve.DATA_DIR", tmp_path)
    monkeypatch.setattr("evolve.EVOLVE_STATE_FILE", tmp_path / "evolve-state.json")
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path))
    return tmp_path


def test_daily_review_phase_always_emitted(data_dir):
    """correction_review["daily"] が常に result に存在する（常時 emit 原則）。"""
    result = run_evolve(dry_run=True)

    cr = result.get("correction_review", {})
    assert "daily" in cr, "daily_review phase が emit されていない"
    daily = cr["daily"]
    # error でも eligible でも、判定キーは必ず置かれる
    assert "eligible" in daily or "error" in daily


def test_daily_review_coexists_with_bootstrap(data_dir):
    """#443 bootstrap と #446 daily が同じ correction_review dict に同居する。"""
    result = run_evolve(dry_run=True)
    cr = result.get("correction_review", {})
    assert "bootstrap" in cr
    assert "daily" in cr


def test_daily_review_dry_run_writes_no_seen(data_dir):
    """dry_run では既読集合 correction_review_seen.jsonl を DATA_DIR に一切書かない。"""
    run_evolve(dry_run=True)

    seen = data_dir / "correction_review_seen.jsonl"
    assert not seen.exists(), "dry_run で既読集合が書かれた"
