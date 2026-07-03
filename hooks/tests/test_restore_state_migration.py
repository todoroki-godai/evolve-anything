"""restore_state の DATA_DIR 一元化リマインド（#364）。

SessionStart で「marker 無し & 旧 plugin-data dir にストア残存」を検出して
`evolve-fleet migrate-data` を案内するか、marker 済み / 残存なし / 非 hook 文脈では
沈黙するかを決定論で固定する（#402 drain リマインドと同型の
install ≠ enforcement 検出層）。

判定の入口は CLAUDE_PLUGIN_DATA env（hook 文脈で CC が設定）であり、
install レイアウト外の env（テスト isolation / custom 環境）では一切実環境を
probe しない（test 衛生: グローバル状態読みの FP を構造回避）。
"""
import sys
from pathlib import Path

import pytest

_HOOKS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_HOOKS))
sys.path.insert(0, str(_HOOKS.parent / "scripts" / "lib"))

import data_dir_migration as ddm  # noqa: E402
import restore_state  # noqa: E402


@pytest.fixture
def env(tmp_path, monkeypatch):
    canonical = tmp_path / "canonical"
    canonical.mkdir()
    source = tmp_path / "plugins" / "data" / "evolve-anything-evolve-anything"
    source.mkdir(parents=True)
    monkeypatch.setattr(ddm, "default_canonical", lambda: canonical)
    monkeypatch.setattr(ddm, "is_cc_install_layout", lambda p: Path(p) == source)
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(source))
    return canonical, source


def test_reminder_fires_when_split_unresolved(env, capsys):
    _, source = env
    (source / "usage.jsonl").write_text("{}\n")
    restore_state._deliver_data_dir_migration_reminder()
    out = capsys.readouterr().out
    assert "evolve-fleet migrate-data" in out


def test_reminder_fires_on_resplit_when_marker_exists(env, capsys):
    """#137: marker 済みでも source に未マージのストアが再蓄積したら再警告する。

    marker は「一度 migrate した」事実しか意味しない。旧版 hook が plugin-data に
    書き続ける等で分裂が再発した場合、旧実装は ``if marker.exists(): return`` で
    永久に沈黙していた（split-brain の恒久沈黙）。再分裂を検出して案内する。
    """
    canonical, source = env
    (source / "usage.jsonl").write_text("{}\n")
    (canonical / ddm._marker_name()).write_text("{}")
    restore_state._deliver_data_dir_migration_reminder()
    out = capsys.readouterr().out
    assert "evolve-fleet migrate-data" in out
    assert "#137" in out, "再分裂の案内は #137 を参照する"


def test_reminder_silent_when_marker_exists_and_source_clean(env, capsys):
    """#137: marker 済み × source に未マージストアなし（migrate 完了の定常状態）は沈黙。"""
    canonical, _ = env
    (canonical / ddm._marker_name()).write_text("{}")
    restore_state._deliver_data_dir_migration_reminder()
    assert capsys.readouterr().out == ""


def test_reminder_silent_when_source_empty(env, capsys):
    restore_state._deliver_data_dir_migration_reminder()
    assert capsys.readouterr().out == ""


def test_reminder_silent_outside_install_layout(env, capsys, monkeypatch, tmp_path):
    """テスト isolation の tmp env では実環境 probe も発火もしない。"""
    _, source = env
    (source / "usage.jsonl").write_text("{}\n")
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path / "isolated"))
    restore_state._deliver_data_dir_migration_reminder()
    assert capsys.readouterr().out == ""


def test_reminder_silent_without_env(env, capsys, monkeypatch):
    _, source = env
    (source / "usage.jsonl").write_text("{}\n")
    monkeypatch.delenv("CLAUDE_PLUGIN_DATA")
    restore_state._deliver_data_dir_migration_reminder()
    assert capsys.readouterr().out == ""
