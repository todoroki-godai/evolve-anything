#!/usr/bin/env python3
"""skill_triage_runner.DATA_DIR の marker-aware 解決テスト（#45(b)）。

skill_triage_runner は Stop hook から subprocess 起動される非同期 runner。hook 文脈で
CC が ``CLAUDE_PLUGIN_DATA=~/.claude/plugins/data/...`` を設定するため、naive 解決だと
usage.jsonl を plugins/data から読み（canonical の live usage を取り逃す）、結果の
skill-triage-cache.json も plugins/data に書く。一方そのキャッシュを surface する
``hooks/instructions_loaded.py`` は ``common.DATA_DIR``（marker-aware = canonical）で読むため、
writer/reader が split し triage 候補が silent に出なくなる（migrated 環境）。

本テストは skill_triage_runner.DATA_DIR が ``rl_common.resolve_data_dir`` 経由で marker
ゲートを通り canonical に redirect することを封じる（naive 解決への退行検出）。
"""
import importlib
import sys
from pathlib import Path

import pytest

_HOOKS = Path(__file__).resolve().parent.parent
_LIB = _HOOKS.parent / "scripts" / "lib"
for _p in (str(_HOOKS), str(_LIB)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import rl_common  # noqa: E402


@pytest.fixture(autouse=True)
def _restore_runner_after():
    """全 monkeypatch 復元後に skill_triage_runner を clean reload し汚染を残さない。"""
    yield
    import skill_triage_runner  # noqa: PLC0415
    importlib.reload(skill_triage_runner)


def _setup_layout(tmp_path, monkeypatch, *, with_marker: bool):
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
    import skill_triage_runner
    sr = importlib.reload(skill_triage_runner)
    assert sr.DATA_DIR == canonical
    assert sr.TRIAGE_CACHE_FILE == canonical / "skill-triage-cache.json"


def test_data_dir_respects_env_when_no_marker(tmp_path, monkeypatch):
    """marker なし（未 migrate 環境）: env をそのまま使う（後方互換・naive と同値）。"""
    _canonical, plugin_data = _setup_layout(tmp_path, monkeypatch, with_marker=False)
    import skill_triage_runner
    sr = importlib.reload(skill_triage_runner)
    assert sr.DATA_DIR == plugin_data


def test_data_dir_falls_back_when_env_unset(tmp_path, monkeypatch):
    """env 未設定（standalone 実行）: fallback canonical を返す。"""
    canonical = tmp_path / "canonical"
    canonical.mkdir()
    monkeypatch.setattr(rl_common, "_DEFAULT_DATA_DIR", canonical)
    monkeypatch.delenv("CLAUDE_PLUGIN_DATA", raising=False)
    import skill_triage_runner
    sr = importlib.reload(skill_triage_runner)
    assert sr.DATA_DIR == canonical
