#!/usr/bin/env python3
"""trigger_engine.DATA_DIR の marker-aware 解決テスト（#45(b)）。

背景: hook 文脈（CC が ``CLAUDE_PLUGIN_DATA=~/.claude/plugins/data/...`` を設定）で
trigger_engine が naive 解決（env をそのまま使う）すると、一元化 marker
(``.data-dir-unified``) のある canonical dir を無視して plugins/data に解決される。
一方 co-reader は全て canonical を見る:
  - ``hooks/instructions_loaded.py`` … ``common.DATA_DIR``（= rl_common.DATA_DIR・marker-aware）
  - ``hooks/restore_state.py``       … ``rl_common.resolve_data_dir``（marker-aware）
  - batch reader（audit / prune / reorganize 等） … env 無し → fallback = canonical
したがって writer の trigger_engine だけ plugins/data に書くと、pending-trigger /
evolve-state / corrections が reader と split し silent に破断する（migrated 環境）。

本テストは trigger_engine.DATA_DIR が ``rl_common.resolve_data_dir`` 経由で marker
ゲートを通り canonical に redirect することを封じる（naive 解決への退行検出）。

注: marker-aware は **単一 dir redirect**（cross-dir union ではない）。ADR-049 の
「hot-path（trigger count）は union しない＝legacy 71MB を毎発火で開かない」原則に従う。
"""
import importlib
import sys
from pathlib import Path

import pytest

_LIB = Path(__file__).resolve().parents[1] / "lib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

import rl_common  # noqa: E402


@pytest.fixture(autouse=True)
def _restore_trigger_engine_after():
    """全 monkeypatch 復元後に trigger_engine を clean reload し import 時 state へ戻す。

    autouse（= 非 autouse の monkeypatch より先にセットアップ → teardown は後）なので、
    env / rl_common defaults が実値に戻った後で reload でき、reload テストが残した
    defunct tmp DATA_DIR の汚染を後続テストへ漏らさない。
    """
    yield
    import trigger_engine  # noqa: PLC0415
    importlib.reload(trigger_engine)


def _setup_layout(tmp_path, monkeypatch, *, with_marker: bool):
    """canonical / plugins-data レイアウトを組み、hook env を plugins-data に向ける。"""
    canonical = tmp_path / "canonical"
    canonical.mkdir()
    if with_marker:
        (canonical / rl_common.DATA_DIR_UNIFIED_MARKER).write_text("{}")
    cc_base = tmp_path / "plugins" / "data"
    plugin_data = cc_base / "evolve-anything-evolve-anything"
    plugin_data.mkdir(parents=True)
    monkeypatch.setattr(rl_common, "_DEFAULT_DATA_DIR", canonical)
    monkeypatch.setattr(rl_common, "_CC_PLUGIN_DATA_BASE", cc_base)
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(plugin_data))
    return canonical, plugin_data


def test_data_dir_redirects_to_canonical_when_marker_present(tmp_path, monkeypatch):
    """marker あり: hook env(plugins/data) でも canonical に redirect（split を塞ぐ）。"""
    canonical, _plugin_data = _setup_layout(tmp_path, monkeypatch, with_marker=True)
    import trigger_engine
    te = importlib.reload(trigger_engine)
    assert te.DATA_DIR == canonical
    # 派生定数も canonical へ追従（evolve-state cooldown / pending-trigger surface の整合）
    assert te.EVOLVE_STATE_FILE == canonical / "evolve-state.json"
    assert te.PENDING_TRIGGER_FILE == canonical / "pending-trigger.json"
    assert te.SNOOZE_FILE == canonical / "trigger-snooze.json"


def test_data_dir_respects_env_when_no_marker(tmp_path, monkeypatch):
    """marker なし（未 migrate 環境）: env をそのまま使う（後方互換・naive と同値）。"""
    _canonical, plugin_data = _setup_layout(tmp_path, monkeypatch, with_marker=False)
    import trigger_engine
    te = importlib.reload(trigger_engine)
    assert te.DATA_DIR == plugin_data


def test_data_dir_falls_back_when_env_unset(tmp_path, monkeypatch):
    """env 未設定（batch 文脈）: fallback canonical を返す（marker 有無に依らず）。"""
    canonical = tmp_path / "canonical"
    canonical.mkdir()
    monkeypatch.setattr(rl_common, "_DEFAULT_DATA_DIR", canonical)
    monkeypatch.delenv("CLAUDE_PLUGIN_DATA", raising=False)
    import trigger_engine
    te = importlib.reload(trigger_engine)
    assert te.DATA_DIR == canonical
