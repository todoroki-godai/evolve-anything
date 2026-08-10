"""safe_llm_call.py のテスト（#410 codex round1 [Must]A/[Must]F）。

無人 daily runner から claude -p を呼ぶ2箇所（correction_semantic.judge_runner /
verbosity.judge）は、判定対象の**生の対話ログ本文**を prompt に埋め込む。発話に
prompt injection が混入していても、ここで組み立てるコマンドライン引数がツールを
一切実行させないことが実測（#410 codex レビュー時に手動検証・下記 docstring 参照）
で確認された唯一の組み合わせであることをこのテストで固定する。

単体テストで LLM を呼ばない: subprocess.run は必ず mock する。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

_lib_dir = Path(__file__).resolve().parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

import safe_llm_call as sc  # noqa: E402


class _FakeCompleted:
    def __init__(self, stdout="", returncode=0, stderr=""):
        self.stdout = stdout
        self.returncode = returncode
        self.stderr = stderr


def test_command_uses_settings_deny_not_disallowed_tools_flag(monkeypatch):
    """#410 実測: --disallowedTools / --allowedTools "" / --permission-mode manual は
    このマシンの ~/.claude/settings.json（permissions.defaultMode=bypassPermissions）を
    上書きできず、いずれも単体では実際にツールが実行されてしまった（秘密ファイルの内容が
    応答に漏洩することで確認）。唯一有効だったのは --settings の明示 deny + defaultMode を
    上書きする組み合わせなので、それを使っていることを固定する。
    """
    captured = {}

    def _fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return _FakeCompleted(stdout="ok")

    monkeypatch.setattr(sc.subprocess, "run", _fake_run)
    sc.call_claude_headless("prompt", model="haiku")

    cmd = captured["cmd"]
    assert cmd[0] == "claude"
    assert "--settings" in cmd
    settings_json = cmd[cmd.index("--settings") + 1]
    settings = json.loads(settings_json)
    deny = settings["permissions"]["deny"]
    # 実測で漏洩を確認した経路の全ツールを塞ぐ（claude --help 実測時点の一覧・2.1.226）。
    for tool in (
        "Bash", "Read", "Write", "Edit", "Glob", "Grep", "WebFetch", "WebSearch",
        "Task", "NotebookEdit",
    ):
        assert tool in deny, f"{tool} が deny リストに無い"
    assert settings["permissions"]["defaultMode"] == "default"
    # --disallowedTools / --allowedTools 単体は実測で無力だったため使わない（誤った安心感を
    # 与えるフラグを残さない）。
    assert "--disallowedTools" not in cmd
    assert "--allowedTools" not in cmd


def test_command_includes_strict_mcp_config_and_no_session_persistence(monkeypatch):
    """環境固有 MCP ツール（deny リスト外の名前）の混入経路も塞ぐ。セッション状態も残さない。"""
    captured = {}

    def _fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return _FakeCompleted(stdout="ok")

    monkeypatch.setattr(sc.subprocess, "run", _fake_run)
    sc.call_claude_headless("prompt")

    assert "--strict-mcp-config" in captured["cmd"]
    assert "--no-session-persistence" in captured["cmd"]


def test_command_passes_prompt_and_model(monkeypatch):
    captured = {}

    def _fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return _FakeCompleted(stdout="ok")

    monkeypatch.setattr(sc.subprocess, "run", _fake_run)
    sc.call_claude_headless("こんにちは", model="haiku")

    cmd = captured["cmd"]
    assert "-p" in cmd
    assert "こんにちは" in cmd
    assert cmd[cmd.index("--model") + 1] == "haiku"


def test_returns_stripped_stdout_on_success(monkeypatch):
    monkeypatch.setattr(
        sc.subprocess, "run",
        lambda cmd, **kw: _FakeCompleted(stdout="  ok  \n", returncode=0),
    )
    assert sc.call_claude_headless("p") == "ok"


def test_raises_on_nonzero_returncode(monkeypatch):
    """#410 [Must]F: returncode を検査せず stdout をそのまま使うと、非ゼロ終了で valid/partial
    な出力が誤って永続化されうる。非ゼロは例外にして呼び出し側の既存失敗経路に合流させる。
    """
    monkeypatch.setattr(
        sc.subprocess, "run",
        lambda cmd, **kw: _FakeCompleted(stdout="", returncode=1, stderr="boom"),
    )
    with pytest.raises(sc.ClaudeCallError):
        sc.call_claude_headless("p")


def test_raised_error_includes_stderr_for_debuggability(monkeypatch):
    monkeypatch.setattr(
        sc.subprocess, "run",
        lambda cmd, **kw: _FakeCompleted(stdout="", returncode=2, stderr="permission denied"),
    )
    with pytest.raises(sc.ClaudeCallError, match="permission denied"):
        sc.call_claude_headless("p")


def test_timeout_kwarg_passed_through(monkeypatch):
    captured = {}

    def _fake_run(cmd, **kwargs):
        captured["kwargs"] = kwargs
        return _FakeCompleted(stdout="ok")

    monkeypatch.setattr(sc.subprocess, "run", _fake_run)
    sc.call_claude_headless("p", timeout=42)
    assert captured["kwargs"]["timeout"] == 42


def test_default_timeout_is_180(monkeypatch):
    captured = {}

    def _fake_run(cmd, **kwargs):
        captured["kwargs"] = kwargs
        return _FakeCompleted(stdout="ok")

    monkeypatch.setattr(sc.subprocess, "run", _fake_run)
    sc.call_claude_headless("p")
    assert captured["kwargs"]["timeout"] == 180


def test_timeout_expired_propagates(monkeypatch):
    """タイムアウトは呼び出し側の except Exception 経路（未判定のまま残す）に委ねる。"""
    def _fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd="claude", timeout=180)

    monkeypatch.setattr(sc.subprocess, "run", _fake_run)
    with pytest.raises(subprocess.TimeoutExpired):
        sc.call_claude_headless("p")
