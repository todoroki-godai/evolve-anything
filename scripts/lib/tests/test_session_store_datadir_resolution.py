"""session_store の DATA_DIR call-time 解決テスト（#137）。

session_store は従来 module-level で ``os.environ.get("CLAUDE_PLUGIN_DATA")`` を
直読みし、他ストアが使う ``rl_common.resolve_data_dir()`` の marker ゲート
（ADR-042 redirect）を経由していなかった。結果、hook 文脈（env=plugins-data）と
tool 文脈（env なし）で読み書きが別 dir に分裂した（split-brain・#137）。

本テストは以下を直接 assert する:
  - marker あり × env=plugins-data（hook）／env なし（tool）で **同一 canonical** に解決
  - marker なし × env=plugins-data では env をそのまま尊重（redirect しない）
  - marker なし × env なしでは既定 canonical
  - ``_DATA_DIR_OVERRIDE`` を立てると env/marker を無視して override を返す（テスト経路）
  - 派生パス（``SESSIONS_DB`` / ``SESSIONS_JSONL``）が DATA_DIR に追従する

決定論・LLM 非依存。実環境（実 ~/.claude）は一切 probe しない（tmp に閉じる）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_lib_dir = Path(__file__).resolve().parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

import rl_common  # noqa: E402
import session_store  # noqa: E402


@pytest.fixture
def layout(tmp_path, monkeypatch):
    """canonical / plugins-data(CC install レイアウト) の 2 dir を tmp に用意する。

    ``resolve_data_dir`` の判定を tmp 内で完結させるため、
    ``_DEFAULT_DATA_DIR`` と ``_CC_PLUGIN_DATA_BASE`` を tmp へ差し替える。
    """
    canonical = tmp_path / "evolve-anything"
    canonical.mkdir()
    cc_base = tmp_path / "plugins" / "data"
    plugins_data = cc_base / "evolve-anything-evolve-anything"
    plugins_data.mkdir(parents=True)
    monkeypatch.setattr(rl_common, "_DEFAULT_DATA_DIR", canonical)
    monkeypatch.setattr(rl_common, "_CC_PLUGIN_DATA_BASE", cc_base)
    # override が漏れていないことを保証（他テストからのリーク防止）。
    monkeypatch.setattr(session_store, "_DATA_DIR_OVERRIDE", None)
    return canonical, plugins_data


def _marker(canonical: Path) -> None:
    (canonical / rl_common.DATA_DIR_UNIFIED_MARKER).write_text("{}\n", encoding="utf-8")


def test_hook_and_tool_resolve_same_dir_with_marker(layout, monkeypatch):
    """marker あり: hook（env=plugins-data）と tool（env なし）が同一 canonical に解決。"""
    canonical, plugins_data = layout
    _marker(canonical)

    # hook 文脈: CLAUDE_PLUGIN_DATA = plugins-data
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(plugins_data))
    hook_dir = session_store._data_dir()

    # tool 文脈: env なし
    monkeypatch.delenv("CLAUDE_PLUGIN_DATA", raising=False)
    tool_dir = session_store._data_dir()

    assert hook_dir == canonical, "marker あり hook 文脈は canonical に redirect"
    assert tool_dir == canonical, "tool 文脈も canonical"
    assert hook_dir == tool_dir, "split-brain 解消: hook/tool が同一 dir"


def test_derived_paths_follow_data_dir(layout, monkeypatch):
    """SESSIONS_DB / SESSIONS_JSONL は解決後の DATA_DIR 配下に追従する。"""
    canonical, plugins_data = layout
    _marker(canonical)
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(plugins_data))
    assert session_store._sessions_db() == canonical / "sessions.db"
    assert session_store._sessions_jsonl() == canonical / "sessions.jsonl"
    # 外部読み取り（__getattr__ shim）も同一値を返す。
    assert session_store.DATA_DIR == canonical
    assert session_store.SESSIONS_DB == canonical / "sessions.db"
    assert session_store.SESSIONS_JSONL == canonical / "sessions.jsonl"


def test_no_marker_respects_plugins_data_env(layout, monkeypatch):
    """marker なし: env=plugins-data はそのまま尊重（redirect しない = 旧挙動）。"""
    _, plugins_data = layout
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(plugins_data))
    assert session_store._data_dir() == plugins_data


def test_no_marker_no_env_uses_default_canonical(layout, monkeypatch):
    """marker なし × env なし: 既定 canonical。"""
    canonical, _ = layout
    monkeypatch.delenv("CLAUDE_PLUGIN_DATA", raising=False)
    assert session_store._data_dir() == canonical


def test_override_wins_over_env_and_marker(layout, monkeypatch, tmp_path):
    """_DATA_DIR_OVERRIDE を立てると env/marker を無視して override を返す（テスト経路）。"""
    canonical, plugins_data = layout
    _marker(canonical)
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(plugins_data))
    override = tmp_path / "explicit-override"
    monkeypatch.setattr(session_store, "_DATA_DIR_OVERRIDE", override)
    assert session_store._data_dir() == override
    assert session_store._sessions_db() == override / "sessions.db"
    assert session_store.SESSIONS_JSONL == override / "sessions.jsonl"
