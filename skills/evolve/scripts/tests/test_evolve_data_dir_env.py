"""evolve.py の module-level DATA_DIR が CLAUDE_PLUGIN_DATA を尊重することの保証（#517）。

evolve.py は従来 `Path.home()/".claude"/"evolve-anything"` をハードコードで
DATA_DIR に置き、`CLAUDE_PLUGIN_DATA` 環境変数を読まなかった。rl_common は
env 優先解決（resolve_data_dir）を提供しており、evolve.py もこれに揃える。

env 設定時に DATA_DIR / EVOLVE_STATE_FILE が env 側へ向くことと、env 無しでは
従来 fallback（~/.claude/evolve-anything）に向くことを reload 経由で assert する。
決定論・LLM 非依存。
"""
import importlib
import sys
from pathlib import Path

import pytest

_plugin_root = Path(__file__).resolve().parent.parent.parent.parent.parent
sys.path.insert(0, str(_plugin_root / "skills" / "evolve" / "scripts"))
sys.path.insert(0, str(_plugin_root / "skills" / "audit" / "scripts"))
sys.path.insert(0, str(_plugin_root / "scripts" / "lib"))
sys.path.insert(0, str(_plugin_root / "scripts"))


def _reload_evolve():
    """evolve を import 直し、module-level DATA_DIR を env で再解決させる。"""
    if "evolve" in sys.modules:
        del sys.modules["evolve"]
    return importlib.import_module("evolve")


def test_data_dir_honors_claude_plugin_data(tmp_path, monkeypatch):
    """CLAUDE_PLUGIN_DATA 設定時、DATA_DIR は env 側を指す。"""
    custom = tmp_path / "custom-data"
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(custom))
    evolve = _reload_evolve()
    assert evolve.DATA_DIR == custom
    assert evolve.EVOLVE_STATE_FILE == custom / "evolve-state.json"


def test_data_dir_falls_back_without_env(monkeypatch):
    """CLAUDE_PLUGIN_DATA 未設定時は rl_common.resolve_data_dir("") と同じ
    既定 fallback（実運用では ~/.claude/evolve-anything）を指す。"""
    monkeypatch.delenv("CLAUDE_PLUGIN_DATA", raising=False)
    from rl_common import resolve_data_dir

    evolve = _reload_evolve()
    assert evolve.DATA_DIR == resolve_data_dir("")
    assert evolve.EVOLVE_STATE_FILE == evolve.DATA_DIR / "evolve-state.json"


@pytest.fixture(autouse=True)
def _restore_evolve_after_each():
    """各テスト後に **元の evolve モジュールオブジェクト** を sys.modules に復元する。

    `_reload_evolve` は `del sys.modules["evolve"]` + reimport で sys.modules を
    別オブジェクトに差し替える。ここで「素の reimport」で復元すると、他テスト
    （例: test_evolve_self_evolution）が collection 時に `from evolve import
    run_evolve` で束縛した関数の `__globals__`（＝元モジュール）が orphan 化し、
    その後の `monkeypatch("evolve.DATA_DIR", tmp_path)` が sys.modules 側の別
    オブジェクトを patch するだけで本体に効かず、run_evolve の最終 state 書込が
    実環境 DATA_DIR へ漏れる（フルスイート -n 0 / xdist 再分配で発火・#407/#408
    と同型の sys.modules 汚染）。元オブジェクトをそのまま戻して根を断つ。"""
    original = sys.modules.get("evolve")
    yield
    if original is not None:
        sys.modules["evolve"] = original
    elif "evolve" in sys.modules:
        del sys.modules["evolve"]
