"""restore_state の icebox 棚卸し気づき通知（#194）。

毎朝の `gh issue list --label icebox --state closed` が `icebox-status.json` に保存した
凍結 issue の件数・最古経過日数を、SessionStart で systemMessage（ADR-038 = user 向け
チャネル）として surface する。

- icebox は evolve-anything 自身の GitHub issue backlog なので、**本体リポジトリ
  （`.claude-plugin/plugin.json` を持つ repo）で作業している時だけ**判定する（他 PJ では沈黙）。
- oldest_days が閾値未満 / ファイル無し → 沈黙（stdout を汚さない）。
- oldest_days が閾値以上 → systemMessage が出る。

env ガード: install レイアウト env のときだけ実環境 DATA_DIR を読む（evolve-queue notice と同型）。
書き込み先は tmp_path のみ。
"""
import json
import sys
from pathlib import Path

_HOOKS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_HOOKS))
sys.path.insert(0, str(_HOOKS.parent / "scripts" / "lib"))

import data_dir_migration as ddm  # noqa: E402
import restore_state  # noqa: E402


STALE_STATUS = {"count": 12, "oldest_days": 200, "generated_at": "2026-06-01T09:00:00Z"}
FRESH_STATUS = {"count": 3, "oldest_days": 10, "generated_at": "2026-07-11T09:00:00Z"}


def _write_status(data_dir: Path, payload: dict) -> None:
    (data_dir / "icebox-status.json").write_text(json.dumps(payload), encoding="utf-8")


def _install_env(tmp_path, monkeypatch):
    """install レイアウト env をでっち上げ DATA_DIR を tmp に固定する。"""
    source = tmp_path / "plugins" / "data" / "evolve-anything-evolve-anything"
    source.mkdir(parents=True)
    monkeypatch.setattr(ddm, "is_cc_install_layout", lambda p: Path(p) == source)
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(source))
    return source


def _install_plugin_self_project(tmp_path, monkeypatch, is_self: bool = True):
    """CLAUDE_PROJECT_DIR を evolve-anything 本体 repo 相当（or 他 PJ）に設定する。"""
    project_dir = tmp_path / "project"
    project_dir.mkdir(parents=True, exist_ok=True)
    if is_self:
        (project_dir / ".claude-plugin").mkdir(parents=True, exist_ok=True)
        (project_dir / ".claude-plugin" / "plugin.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project_dir))
    return project_dir


def test_deliver_fires_with_stale_icebox_in_plugin_self_repo(tmp_path, monkeypatch, capsys):
    _install_plugin_self_project(tmp_path, monkeypatch, is_self=True)
    source = _install_env(tmp_path, monkeypatch)
    _write_status(source, STALE_STATUS)
    restore_state._deliver_icebox_notice()
    out = capsys.readouterr().out
    assert out  # 非空
    payload = json.loads(out.strip())
    assert "systemMessage" in payload
    assert "12件" in payload["systemMessage"]
    assert "200日" in payload["systemMessage"]


def test_deliver_silent_when_below_threshold(tmp_path, monkeypatch, capsys):
    _install_plugin_self_project(tmp_path, monkeypatch, is_self=True)
    source = _install_env(tmp_path, monkeypatch)
    _write_status(source, FRESH_STATUS)
    restore_state._deliver_icebox_notice()
    assert capsys.readouterr().out == ""


def test_deliver_silent_when_no_icebox_file(tmp_path, monkeypatch, capsys):
    _install_plugin_self_project(tmp_path, monkeypatch, is_self=True)
    _install_env(tmp_path, monkeypatch)
    restore_state._deliver_icebox_notice()
    assert capsys.readouterr().out == ""


def test_deliver_silent_outside_install_layout(tmp_path, monkeypatch, capsys):
    _install_plugin_self_project(tmp_path, monkeypatch, is_self=True)
    monkeypatch.setattr(ddm, "is_cc_install_layout", lambda p: False)
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path / "isolated"))
    restore_state._deliver_icebox_notice()
    assert capsys.readouterr().out == ""


def test_deliver_silent_without_data_env(tmp_path, monkeypatch, capsys):
    _install_plugin_self_project(tmp_path, monkeypatch, is_self=True)
    monkeypatch.delenv("CLAUDE_PLUGIN_DATA", raising=False)
    restore_state._deliver_icebox_notice()
    assert capsys.readouterr().out == ""


def test_deliver_silent_outside_plugin_self_repo(tmp_path, monkeypatch, capsys):
    """evolve-anything 本体以外の PJ（plugin.json 無し）では、icebox が stale でも沈黙する。"""
    _install_plugin_self_project(tmp_path, monkeypatch, is_self=False)
    source = _install_env(tmp_path, monkeypatch)
    _write_status(source, STALE_STATUS)
    restore_state._deliver_icebox_notice()
    assert capsys.readouterr().out == ""


def test_deliver_silent_without_project_dir_env(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    source = _install_env(tmp_path, monkeypatch)
    _write_status(source, STALE_STATUS)
    restore_state._deliver_icebox_notice()
    assert capsys.readouterr().out == ""


def test_deliver_does_not_write(tmp_path, monkeypatch):
    _install_plugin_self_project(tmp_path, monkeypatch, is_self=True)
    source = _install_env(tmp_path, monkeypatch)
    _write_status(source, STALE_STATUS)
    before = {p.name for p in source.iterdir()}
    restore_state._deliver_icebox_notice()
    after = {p.name for p in source.iterdir()}
    assert before == after  # icebox 通知は read-only


def test_handle_session_start_invokes_icebox_notice(tmp_path, monkeypatch, capsys):
    """handle_session_start が icebox 通知を配信フローに含む（配線回帰）。"""
    _install_plugin_self_project(tmp_path, monkeypatch, is_self=True)
    source = _install_env(tmp_path, monkeypatch)
    _write_status(source, STALE_STATUS)
    restore_state.handle_session_start({})
    out = capsys.readouterr().out
    assert "12件" in out


def test_deliver_respects_custom_threshold_from_user_config(tmp_path, monkeypatch, capsys):
    """icebox_review_threshold_days を userConfig で下げると、デフォルト閾値未満の
    oldest_days でも発火する（#194 拡張）。"""
    _install_plugin_self_project(tmp_path, monkeypatch, is_self=True)
    source = _install_env(tmp_path, monkeypatch)
    status = {"count": 2, "oldest_days": 25, "generated_at": "2026-07-01T00:00:00Z"}
    _write_status(source, status)
    monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_icebox_review_threshold_days", "20")
    restore_state._deliver_icebox_notice()
    out = capsys.readouterr().out
    assert out
    payload = json.loads(out.strip())
    assert "25日" in payload["systemMessage"]


def test_deliver_silent_below_custom_threshold(tmp_path, monkeypatch, capsys):
    """カスタム閾値未満なら（デフォルト閾値より小さい oldest_days でも）沈黙する。"""
    _install_plugin_self_project(tmp_path, monkeypatch, is_self=True)
    source = _install_env(tmp_path, monkeypatch)
    status = {"count": 2, "oldest_days": 45, "generated_at": "2026-07-01T00:00:00Z"}
    _write_status(source, status)
    monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_icebox_review_threshold_days", "60")
    restore_state._deliver_icebox_notice()
    assert capsys.readouterr().out == ""


def test_deliver_fires_with_default_threshold_no_override(tmp_path, monkeypatch, capsys):
    """env var 未設定でも新デフォルト30日が実際に使われ、30-89日の範囲
    （旧ライブラリデフォルト90日では沈黙するはずの範囲）で発火することを保証する。"""
    _install_plugin_self_project(tmp_path, monkeypatch, is_self=True)
    source = _install_env(tmp_path, monkeypatch)
    status = {"count": 4, "oldest_days": 45, "generated_at": "2026-07-01T00:00:00Z"}
    _write_status(source, status)
    monkeypatch.delenv("CLAUDE_PLUGIN_OPTION_icebox_review_threshold_days", raising=False)
    restore_state._deliver_icebox_notice()
    out = capsys.readouterr().out
    assert out
    payload = json.loads(out.strip())
    assert "45日" in payload["systemMessage"]
