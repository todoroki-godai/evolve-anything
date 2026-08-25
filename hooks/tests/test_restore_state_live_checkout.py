"""restore_state の live_checkout 通知（#548）。

- hook 文脈（CC install レイアウト env）でなければ probe しない（実環境保護）
- danger → tier1（危険警告）/ unknown → tier2（判定不能・低強度）/ safe → 沈黙
- import/実行失敗は本系統が独立に捕捉し tier1 の health notice に落ちる
  （hook と audit の各呼出側が独立に捕捉する、rule 正典）
"""
import json
import subprocess
import sys
from pathlib import Path

_HOOKS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_HOOKS))
sys.path.insert(0, str(_HOOKS.parent / "scripts" / "lib"))

import data_dir_migration as ddm  # noqa: E402
import live_checkout  # noqa: E402
import restore_state  # noqa: E402


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


def _install_env(tmp_path, monkeypatch):
    """install レイアウト env をでっち上げる（他 hook テストと同型）。"""
    source = tmp_path / "plugins" / "data" / "evolve-anything-evolve-anything"
    source.mkdir(parents=True)
    monkeypatch.setattr(ddm, "is_cc_install_layout", lambda p: Path(p) == source)
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(source))
    return source


def test_silent_outside_install_layout(monkeypatch):
    monkeypatch.delenv("CLAUDE_PLUGIN_DATA", raising=False)
    assert restore_state._build_live_checkout_output() is None


def test_silent_when_layout_check_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(ddm, "is_cc_install_layout", lambda p: False)
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path / "isolated"))
    assert restore_state._build_live_checkout_output() is None


def test_danger_produces_tier1_item_with_recovery_command(tmp_path, monkeypatch):
    _install_env(tmp_path, monkeypatch)
    work = _make_repo_pair(tmp_path)
    _git(work, "checkout", "-q", "-b", "feature/x")
    # caller_file は live_checkout_notice.py 自身の __file__（実 worktree）を指すため、
    # 3者照合の①②を tmp fixture repo に揃える（3者照合そのものは test_live_checkout.py
    # がユニットで担保済み。ここでは NotificationItem 変換の配線だけを検証する）。
    monkeypatch.setattr(live_checkout, "_find_plugin_root", lambda start: work)
    monkeypatch.setattr(live_checkout, "_MODULE_ROOT_OVERRIDE", work)

    item = restore_state._build_live_checkout_output()
    assert item is not None
    assert item.tier == 1
    assert "feature/x" in item.text
    assert f"git -C {work}" in item.text
    assert "危険" in item.digest


def test_unknown_produces_tier2_item(tmp_path, monkeypatch):
    _install_env(tmp_path, monkeypatch)
    work = _make_repo_pair(tmp_path)
    _git(work, "remote", "set-head", "origin", "-d")
    # caller_file は live_checkout_notice.py 自身の __file__（実 worktree）を指すため、
    # 3者照合の①②を tmp fixture repo に揃える（3者照合そのものは test_live_checkout.py
    # がユニットで担保済み。ここでは NotificationItem 変換の配線だけを検証する）。
    monkeypatch.setattr(live_checkout, "_find_plugin_root", lambda start: work)
    monkeypatch.setattr(live_checkout, "_MODULE_ROOT_OVERRIDE", work)

    item = restore_state._build_live_checkout_output()
    assert item is not None
    assert item.tier == 2  # 危険警告より低強度
    assert "判定不能" in item.digest


def test_safe_is_silent(tmp_path, monkeypatch):
    _install_env(tmp_path, monkeypatch)
    work = _make_repo_pair(tmp_path)
    # caller_file は live_checkout_notice.py 自身の __file__（実 worktree）を指すため、
    # 3者照合の①②を tmp fixture repo に揃える（3者照合そのものは test_live_checkout.py
    # がユニットで担保済み。ここでは NotificationItem 変換の配線だけを検証する）。
    monkeypatch.setattr(live_checkout, "_find_plugin_root", lambda start: work)
    monkeypatch.setattr(live_checkout, "_MODULE_ROOT_OVERRIDE", work)
    # 実 ~/.claude/plugins/known_marketplaces.json は work（tmp fixture）と一致しない
    # ため registry.status="mismatch" になり得る（副次警告は primary=safe とは独立）。
    # ここは primary=safe の沈黙のみを検証したいので registry を skip させる。
    monkeypatch.setattr(live_checkout, "_MARKETPLACE_REGISTRY", tmp_path / "no_registry.json")

    assert restore_state._build_live_checkout_output() is None


def test_registry_mismatch_is_tier2_secondary_warning(tmp_path, monkeypatch):
    """primary=safe でも registry 副次警告は独立に tier2 で出る。"""
    _install_env(tmp_path, monkeypatch)
    work = _make_repo_pair(tmp_path)
    monkeypatch.setattr(live_checkout, "_find_plugin_root", lambda start: work)
    monkeypatch.setattr(live_checkout, "_MODULE_ROOT_OVERRIDE", work)
    monkeypatch.setattr(live_checkout, "_MARKETPLACE_REGISTRY", tmp_path / "no_registry.json")
    reg = tmp_path / "registry.json"
    reg.write_text(json.dumps({"other": {"installLocation": str(tmp_path / "elsewhere")}}), encoding="utf-8")
    monkeypatch.setattr(live_checkout, "_MARKETPLACE_REGISTRY", reg)

    item = restore_state._build_live_checkout_output()
    assert item is not None
    assert item.tier == 2
    assert "registry照合不能" == item.digest


def test_check_exception_becomes_tier1_health_notice(tmp_path, monkeypatch):
    """呼出側が実行失敗を独立に捕捉し、無音にしない（rule 正典: fail-visible）。"""
    _install_env(tmp_path, monkeypatch)

    def _boom(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(live_checkout, "check", _boom)

    item = restore_state._build_live_checkout_output()
    assert item is not None
    assert item.tier == 1
    assert "実行障害" in item.digest
