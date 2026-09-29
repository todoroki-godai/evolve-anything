"""bin/evolve-release-sync の契約テスト（#548）。

実 HOME・実 claude CLI には触れない（no-llm-in-tests）。HOME を tmp に差し替え、
``claude`` は PATH 先頭のスタブ（呼び出しを記録するだけ）に置き換える。
"""

import json
import os
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "evolve-release-sync"
KEY = "evolve-anything@evolve-anything"


def _make_home(tmp_path, version="1.126.0", *, with_cache=True, entry=True, with_bin=True, scopes=None):
    """installed_plugins.json と cache の版ディレクトリを持つ偽 HOME を作る。"""
    home = tmp_path / "home"
    plugins = home / ".claude" / "plugins"
    plugins.mkdir(parents=True)
    data = {"version": 2, "plugins": {}}
    if entry:
        # installPath はわざと stale な版にする（version から組むことの検査）。
        data["plugins"][KEY] = scopes or [{"scope": "user", "version": version, "installPath": "/stale/1.0.0"}]
    (plugins / "installed_plugins.json").write_text(json.dumps(data))
    if with_cache:
        vdir = plugins / "cache" / "evolve-anything" / "evolve-anything" / version
        vdir.mkdir(parents=True)
        if with_bin:  # 毎朝の実行の入口。空ディレクトリを指したまま成功で終わらせないためのガードの対象
            (vdir / "bin").mkdir()
            runner = vdir / "bin" / "evolve-daily-run"
            runner.write_text("#!/bin/sh\n")
            runner.chmod(0o755)
    return home


def _stub_claude(tmp_path, exit_code=0):
    """呼び出し引数を calls.log に追記するだけの claude スタブを PATH 用 dir に置く。"""
    bindir = tmp_path / "stubbin"
    bindir.mkdir()
    log = tmp_path / "calls.log"
    stub = bindir / "claude"
    stub.write_text(f'#!/bin/sh\necho "$@" >> "{log}"\nexit {exit_code}\n')
    stub.chmod(0o755)
    return bindir, log


def _run(home, tmp_path, *args, bindir=None):
    env = {**os.environ, "HOME": str(home)}
    if bindir is not None:
        env["PATH"] = f"{bindir}:{env['PATH']}"
    return subprocess.run(
        ["bash", str(SCRIPT), *args], cwd=tmp_path, env=env, capture_output=True, text=True,
    )


def test_dry_run_emits_sync_sequence_without_local_main_ff(tmp_path):
    """marketplace update → plugin update → symlink 張り替えの順。ローカル main の ff は無い。"""
    home = _make_home(tmp_path)
    res = _run(home, tmp_path, "--dry-run")
    out = res.stdout + res.stderr
    assert res.returncode == 0, out
    assert "ff-only" not in out and "git " not in out
    i_mp = out.index("claude plugin marketplace update evolve-anything")
    i_pl = out.index("claude plugin update evolve-anything@evolve-anything")
    i_ln = out.index("ln -sfn")
    assert i_mp < i_pl < i_ln, f"順序違反: {out}"
    assert "cache/evolve-anything/evolve-anything/1.126.0" in out
    assert "/stale/1.0.0" not in out
    assert "plugins/live/evolve-anything" in out


def test_dry_run_does_not_touch_filesystem(tmp_path):
    home = _make_home(tmp_path)
    _run(home, tmp_path, "--dry-run")
    assert not (home / ".claude" / "plugins" / "live").exists()


def test_apply_points_live_symlink_at_installed_version(tmp_path):
    home = _make_home(tmp_path, "1.126.0")
    bindir, log = _stub_claude(tmp_path)
    link = home / ".claude" / "plugins" / "live" / "evolve-anything"
    link.parent.mkdir()
    old = tmp_path / "old_version"
    old.mkdir()
    link.symlink_to(old)  # 旧版を指している状態からの張り替え
    res = _run(home, tmp_path, bindir=bindir)
    assert res.returncode == 0, res.stdout + res.stderr
    expected = home / ".claude/plugins/cache/evolve-anything/evolve-anything/1.126.0"
    assert link.is_symlink() and Path(os.readlink(link)) == expected
    calls = log.read_text().splitlines()
    assert calls == [
        "plugin marketplace update evolve-anything",
        f"plugin update {KEY}",
    ]


def test_missing_version_dir_fails_without_relinking(tmp_path):
    home = _make_home(tmp_path, with_cache=False)
    bindir, _ = _stub_claude(tmp_path)
    res = _run(home, tmp_path, bindir=bindir)
    assert res.returncode == 1, res.stdout + res.stderr
    assert not (home / ".claude/plugins/live/evolve-anything").exists()


def test_plugin_command_failure_exits_1_without_relinking(tmp_path):
    home = _make_home(tmp_path)
    bindir, _ = _stub_claude(tmp_path, exit_code=3)
    res = _run(home, tmp_path, bindir=bindir)
    assert res.returncode == 1, res.stdout + res.stderr
    assert not (home / ".claude/plugins/live/evolve-anything").exists()


def test_refuses_when_live_is_a_real_directory(tmp_path):
    """live が実ディレクトリなら上書きしない（データ損失防止）。"""
    home = _make_home(tmp_path)
    bindir, _ = _stub_claude(tmp_path)
    real = home / ".claude/plugins/live/evolve-anything"
    real.mkdir(parents=True)
    (real / "keep.txt").write_text("x")
    res = _run(home, tmp_path, bindir=bindir)
    assert res.returncode == 1, res.stdout + res.stderr
    assert (real / "keep.txt").exists() and not real.is_symlink()


def test_aborts_when_plugin_not_registered(tmp_path):
    home = _make_home(tmp_path, entry=False)
    res = _run(home, tmp_path, "--dry-run")
    assert res.returncode == 2, res.stdout + res.stderr


def test_aborts_when_installed_plugins_missing(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    res = _run(home, tmp_path, "--dry-run")
    assert res.returncode == 2, res.stdout + res.stderr


def test_version_dir_without_daily_runner_fails_without_relinking(tmp_path):
    """版ディレクトリはあるが bin/evolve-daily-run が無い（空の木）なら張り替えず exit 1（④(e)）。"""
    home = _make_home(tmp_path, with_bin=False)
    bindir, _ = _stub_claude(tmp_path)
    res = _run(home, tmp_path, bindir=bindir)
    assert res.returncode == 1, res.stdout + res.stderr
    assert not (home / ".claude/plugins/live/evolve-anything").exists()


def test_user_scope_entry_is_selected_over_project_scope(tmp_path):
    scopes = [
        {"scope": "project", "version": "9.9.9"},
        {"scope": "user", "version": "1.126.0"},
    ]
    home = _make_home(tmp_path, "1.126.0", scopes=scopes)
    bindir, _ = _stub_claude(tmp_path)
    res = _run(home, tmp_path, bindir=bindir)
    assert res.returncode == 0, res.stdout + res.stderr
    link = home / ".claude/plugins/live/evolve-anything"
    assert os.readlink(link).endswith("/1.126.0")


def test_two_user_scope_entries_abort(tmp_path):
    scopes = [{"scope": "user", "version": "1.126.0"}, {"scope": "user", "version": "1.127.0"}]
    home = _make_home(tmp_path, "1.126.0", scopes=scopes)
    res = _run(home, tmp_path, "--dry-run")
    assert res.returncode == 2, res.stdout + res.stderr


def test_unknown_argument_aborts_without_calling_claude(tmp_path):
    home = _make_home(tmp_path)
    bindir, log = _stub_claude(tmp_path)
    for arg in ("--help", "--dryrun"):
        res = _run(home, tmp_path, arg, bindir=bindir)
        assert res.returncode == 2, res.stdout + res.stderr
    assert not log.exists()
