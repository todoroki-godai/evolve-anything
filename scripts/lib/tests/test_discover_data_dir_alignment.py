"""discover.DATA_DIR が rl_common.resolve_data_dir に揃うことの回帰（#702）。

discover は従来 ``Path.home() / ".claude" / "evolve-anything"`` を import 時に
固定しており、CLAUDE_PLUGIN_DATA（テスト隔離の tmp dir を含む）を無視していた。
本テストは discover を CLAUDE_PLUGIN_DATA 変更後に再 import（reload）し、

  - env が非 plugin-data レイアウト（テスト tmp dir 等）なら env をそのまま尊重
  - env が plugin-data レイアウト かつ 一元化 marker あり なら canonical dir へ
    redirect（rl_common.resolve_data_dir と同一結果）

を検証する。決定論・LLM 非依存。実 ~/.claude は一切 probe しない（tmp に閉じる）。
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

_lib_dir = Path(__file__).resolve().parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

import rl_common  # noqa: E402
import discover  # noqa: E402


@pytest.fixture
def layout(tmp_path, monkeypatch):
    """canonical / plugins-data(CC install レイアウト) の 2 dir を tmp に用意する。

    resolve_data_dir の判定を tmp 内で完結させるため、
    ``_DEFAULT_DATA_DIR`` と ``_CC_PLUGIN_DATA_BASE`` を tmp へ差し替える
    （test_session_store_datadir_resolution.py と同じ手法）。
    """
    canonical = tmp_path / "evolve-anything"
    canonical.mkdir()
    cc_base = tmp_path / "plugins" / "data"
    plugins_data = cc_base / "evolve-anything-evolve-anything"
    plugins_data.mkdir(parents=True)
    monkeypatch.setattr(rl_common, "_DEFAULT_DATA_DIR", canonical)
    monkeypatch.setattr(rl_common, "_CC_PLUGIN_DATA_BASE", cc_base)
    return canonical, plugins_data


def _marker(canonical: Path) -> None:
    (canonical / rl_common.DATA_DIR_UNIFIED_MARKER).write_text("{}\n", encoding="utf-8")


def _reload_discover():
    return importlib.reload(discover)


def test_non_plugin_data_env_is_respected(tmp_path, monkeypatch):
    """plugin-data レイアウト外の env（テスト隔離 tmp 等）はそのまま尊重される。"""
    custom_dir = tmp_path / "custom-data-dir"
    custom_dir.mkdir()
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(custom_dir))

    mod = _reload_discover()

    assert mod.DATA_DIR == custom_dir
    assert mod.SUPPRESSION_FILE == custom_dir / "discover-suppression.jsonl"


def test_plugin_data_env_with_marker_redirects_to_canonical(layout, monkeypatch):
    """plugin-data レイアウト + 一元化 marker あり → canonical dir（rl_common と一致）。"""
    canonical, plugins_data = layout
    _marker(canonical)
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(plugins_data))

    mod = _reload_discover()

    expected = rl_common.resolve_data_dir(str(plugins_data))
    assert expected == canonical, "テスト前提: marker ありなら rl_common 自体も canonical に解決する"
    assert mod.DATA_DIR == canonical
    assert mod.DATA_DIR == expected
