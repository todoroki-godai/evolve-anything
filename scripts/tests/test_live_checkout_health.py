"""audit への live_checkout health section（#548）。

hook 側（session_notify）とは独立に import/実行失敗を捕捉することを検証する
（rule 正典: 同じ live_checkout モジュールを両方が import する以上、構文エラーは
共通原因障害になるため、捕捉は呼出側それぞれに置く）。
"""
import json
import subprocess
import sys
from pathlib import Path

_PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
_LIB = _PLUGIN_ROOT / "scripts" / "lib"
_SCRIPTS = _PLUGIN_ROOT / "scripts"
for _p in (_LIB, _SCRIPTS):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import live_checkout  # noqa: E402
import plugin_root  # noqa: E402
from audit.live_checkout_health import build_live_checkout_health_section  # noqa: E402


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=str(repo), check=True, capture_output=True, text=True,
    ).stdout


def _write_plugin_marker(repo: Path) -> None:
    marker_dir = repo / ".claude-plugin"
    marker_dir.mkdir(parents=True, exist_ok=True)
    (marker_dir / "plugin.json").write_text(json.dumps({"name": "fixture"}), encoding="utf-8")


def _make_repo_pair(base: Path) -> Path:
    bare = base / "origin.git"
    subprocess.run(["git", "init", "--bare", "-q", str(bare)], check=True, capture_output=True)
    work = base / "work"
    _git(base, "clone", "-q", str(bare), str(work))
    _git(work, "config", "user.email", "t@t")
    _git(work, "config", "user.name", "t")
    _git(work, "checkout", "-q", "-b", "main")
    _write_plugin_marker(work)
    (work / "README.md").write_text("hello\n", encoding="utf-8")
    _git(work, "add", ".")
    _git(work, "commit", "-q", "-m", "init")
    _git(work, "push", "-q", "-u", "origin", "main")
    subprocess.run(
        ["git", "symbolic-ref", "HEAD", "refs/heads/main"],
        cwd=str(bare), check=True, capture_output=True,
    )
    _git(work, "remote", "set-head", "origin", "main")
    return work


def test_import_failure_is_health_notice(monkeypatch):
    """live_checkout の import そのものが失敗しても section は例外を投げず health を返す。

    ``sys.modules["live_checkout"] = None`` は CPython の import 機構により
    次回 import 時に ``ImportError`` を発生させる（ドキュメント化された挙動）。
    """
    monkeypatch.delitem(sys.modules, "live_checkout", raising=False)
    monkeypatch.setitem(sys.modules, "live_checkout", None)

    lines = build_live_checkout_health_section()
    assert lines is not None
    assert any("import に失敗" in ln for ln in lines)


def test_check_exception_is_health_notice(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(live_checkout, "check", _boom)
    lines = build_live_checkout_health_section()
    assert lines is not None
    assert any("判定実行に失敗" in ln for ln in lines)


def test_danger_is_surfaced(tmp_path, monkeypatch):
    work = _make_repo_pair(tmp_path)
    _git(work, "checkout", "-q", "-b", "feature/x")
    monkeypatch.setattr(live_checkout, "_find_plugin_root", lambda start: work)
    monkeypatch.setattr(live_checkout, "_MODULE_ROOT_OVERRIDE", work)
    monkeypatch.setattr(plugin_root, "PLUGIN_ROOT", work)

    lines = build_live_checkout_health_section()
    assert lines is not None
    assert any("feature/x" in ln for ln in lines)
    assert any("危険な状態" in ln for ln in lines)


def test_safe_is_silent(tmp_path, monkeypatch):
    work = _make_repo_pair(tmp_path)
    monkeypatch.setattr(live_checkout, "_find_plugin_root", lambda start: work)
    monkeypatch.setattr(live_checkout, "_MODULE_ROOT_OVERRIDE", work)
    monkeypatch.setattr(plugin_root, "PLUGIN_ROOT", work)
    monkeypatch.setattr(live_checkout, "_MARKETPLACE_REGISTRY", tmp_path / "no_registry.json")

    assert build_live_checkout_health_section() is None


def test_unknown_is_surfaced_low_strength(tmp_path, monkeypatch):
    work = _make_repo_pair(tmp_path)
    _git(work, "remote", "set-head", "origin", "-d")
    monkeypatch.setattr(live_checkout, "_find_plugin_root", lambda start: work)
    monkeypatch.setattr(live_checkout, "_MODULE_ROOT_OVERRIDE", work)
    monkeypatch.setattr(plugin_root, "PLUGIN_ROOT", work)

    lines = build_live_checkout_health_section()
    assert lines is not None
    assert any("判定できません" in ln for ln in lines)
