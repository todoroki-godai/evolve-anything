"""daily install/uninstall（launchd ジョブ登録・撤去・冪等）の単体テスト（#80 Phase 1b）。

launchctl は subprocess mock。plist の書き込み先 / LaunchAgents dir は tmp に向ける。
- install: plist が書かれ launchctl load が呼ばれる
- install 冪等: 2 回目でも壊れず（既存なら unload→load で再登録）
- uninstall: launchctl unload + plist 削除。plist 無しでも壊れない
"""
import sys
from pathlib import Path
from unittest import mock

import pytest

from daily import install as inst


@pytest.fixture
def fake_paths(tmp_path, monkeypatch):
    la = tmp_path / "LaunchAgents"
    plist = la / f"{inst.LAUNCHD_LABEL}.plist"
    monkeypatch.setattr(inst.plist_mod, "plist_path", lambda: plist)
    return tmp_path, plist


def test_install_writes_plist_and_loads(fake_paths, monkeypatch):
    tmp_path, plist = fake_paths
    calls = []
    monkeypatch.setattr(inst, "_launchctl", lambda *a: calls.append(a) or 0)
    rc = inst.install(
        plugin_root="/p/evolve-anything",
        data_dir=str(tmp_path / "data"),
        hour=9,
        minute=0,
    )
    assert rc == 0
    assert plist.exists()
    body = plist.read_text(encoding="utf-8")
    assert inst.LAUNCHD_LABEL in body
    runner = "/p/evolve-anything/bin/evolve-daily-run"
    assert runner in body
    # python_exe を省略すると install が sys.executable で pin し、runner より前に入る
    assert f"<string>{sys.executable}</string>" in body
    assert body.index(f"<string>{sys.executable}</string>") < body.index(f"<string>{runner}</string>")
    # load が呼ばれた
    assert any(c[0] == "load" for c in calls)


def test_install_pins_explicit_python_exe(fake_paths, monkeypatch):
    """python_exe を明示指定すると plist にそのインタプリタが焼かれる。"""
    tmp_path, plist = fake_paths
    monkeypatch.setattr(inst, "_launchctl", lambda *a: 0)
    inst.install(
        plugin_root="/p/evolve-anything",
        data_dir=str(tmp_path / "data"),
        python_exe="/opt/homebrew/bin/python3.14",
    )
    body = plist.read_text(encoding="utf-8")
    assert "<string>/opt/homebrew/bin/python3.14</string>" in body


def test_install_adds_gh_dir_to_path(fake_paths, monkeypatch):
    """install が shutil.which('gh') の dir を PATH に焼き込む（#196）。

    launchd 実環境の最小 PATH に /opt/homebrew/bin が無く、icebox 集計（#194）の
    gh 起動が FileNotFoundError で恒久 fail-open になる穴を install 時検出で塞ぐ。
    """
    tmp_path, plist = fake_paths
    monkeypatch.setattr(inst, "_launchctl", lambda *a: 0)
    monkeypatch.setattr(
        inst.shutil, "which", lambda cmd: "/opt/homebrew/bin/gh" if cmd == "gh" else None
    )
    inst.install(
        plugin_root="/p/evolve-anything",
        data_dir=str(tmp_path / "data"),
        python_exe="/opt/homebrew/opt/python@3.14/bin/python3.14",
    )
    body = plist.read_text(encoding="utf-8")
    assert "/opt/homebrew/opt/python@3.14/bin:/opt/homebrew/bin:/usr/bin" in body


def test_install_without_gh_still_installs(fake_paths, monkeypatch):
    """gh 不在（which=None）でも install は成功し PATH は python dir のみ（fail-open 維持）。"""
    tmp_path, plist = fake_paths
    monkeypatch.setattr(inst, "_launchctl", lambda *a: 0)
    monkeypatch.setattr(inst.shutil, "which", lambda cmd: None)
    rc = inst.install(
        plugin_root="/p/evolve-anything",
        data_dir=str(tmp_path / "data"),
        python_exe="/opt/homebrew/opt/python@3.14/bin/python3.14",
    )
    assert rc == 0
    body = plist.read_text(encoding="utf-8")
    assert "/opt/homebrew/opt/python@3.14/bin:/usr/bin:/bin:/usr/sbin:/sbin" in body


def test_install_is_idempotent(fake_paths, monkeypatch):
    tmp_path, plist = fake_paths
    calls = []
    monkeypatch.setattr(inst, "_launchctl", lambda *a: calls.append(a) or 0)
    inst.install(plugin_root="/p", data_dir=str(tmp_path / "data"))
    calls.clear()
    # 2 回目: 既存 plist あり → unload してから load（再登録）。壊れない。
    rc = inst.install(plugin_root="/p", data_dir=str(tmp_path / "data"))
    assert rc == 0
    assert plist.exists()
    assert any(c[0] == "unload" for c in calls)
    assert any(c[0] == "load" for c in calls)


def test_uninstall_unloads_and_removes_plist(fake_paths, monkeypatch):
    tmp_path, plist = fake_paths
    calls = []
    monkeypatch.setattr(inst, "_launchctl", lambda *a: calls.append(a) or 0)
    inst.install(plugin_root="/p", data_dir=str(tmp_path / "data"))
    calls.clear()
    rc = inst.uninstall()
    assert rc == 0
    assert not plist.exists()
    assert any(c[0] == "unload" for c in calls)


def test_uninstall_without_plist_is_noop(fake_paths, monkeypatch):
    tmp_path, plist = fake_paths
    calls = []
    monkeypatch.setattr(inst, "_launchctl", lambda *a: calls.append(a) or 0)
    rc = inst.uninstall()
    assert rc == 0
    assert not plist.exists()


def test_uninstall_is_idempotent(fake_paths, monkeypatch):
    tmp_path, plist = fake_paths
    monkeypatch.setattr(inst, "_launchctl", lambda *a: 0)
    inst.install(plugin_root="/p", data_dir=str(tmp_path / "data"))
    assert inst.uninstall() == 0
    assert inst.uninstall() == 0  # 2 回目も壊れない
