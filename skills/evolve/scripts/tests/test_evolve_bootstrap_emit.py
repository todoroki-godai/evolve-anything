"""evolve.run_evolve が bootstrap phase を常時 emit することの保証（#443）。

初回バックログ bootstrap モードの phase 出力（correction_review["bootstrap"]）が、
- 常時 emit される（eligible でなくても result にキーを置く・常時 emit 原則）
- dry_run では marker を一切書かない（pitfall_dryrun_stateful_store_write を最下層まで貫通）
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
    """テスト用 DATA_DIR を設定（実環境 DATA_DIR を読み書きさせない）。

    bootstrap の marker / weak_signals は ADR-042 resolver 経由でパス解決されるため、
    CLAUDE_PLUGIN_DATA を tmp に向けてストア書き込み先を隔離する。
    """
    monkeypatch.setattr("evolve.DATA_DIR", tmp_path)
    monkeypatch.setattr("evolve.EVOLVE_STATE_FILE", tmp_path / "evolve-state.json")
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path))
    return tmp_path


def test_bootstrap_phase_always_emitted(data_dir):
    """correction_review["bootstrap"] が常に result に存在する（常時 emit 原則）。"""
    result = run_evolve(dry_run=True)

    cr = result.get("correction_review", {})
    assert "bootstrap" in cr, "bootstrap phase が emit されていない"
    bs = cr["bootstrap"]
    # error でも is_bootstrap でも、キーは必ず置かれる
    assert "is_bootstrap" in bs or "error" in bs


def test_bootstrap_dry_run_writes_no_marker(data_dir):
    """dry_run では bootstrap marker を DATA_DIR に一切書かない。"""
    run_evolve(dry_run=True)

    markers = list(data_dir.glob("bootstrap_done-*.marker"))
    assert markers == [], f"dry_run で marker が書かれた: {markers}"
