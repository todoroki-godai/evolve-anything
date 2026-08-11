"""safe_llm_call.py のテスト（#410 codex round1/round2 [Must]A/[Must]F）。

無人 daily runner から claude -p を呼ぶ2箇所（correction_semantic.judge_runner /
verbosity.judge）は、判定対象の**生の対話ログ本文**を prompt に埋め込む。発話に
prompt injection が混入していても、ここで組み立てるコマンドライン引数がツールを
一切実行させないことが実測（#410 codex レビュー時に手動検証・下記 docstring 参照）
で確認された唯一の組み合わせであることをこのテストで固定する。

単体テストで LLM を呼ばない: subprocess.run は必ず mock する。

**このテストファイルの位置づけ（#410 round2 [Should]④）**: 本ファイルのテストは
組み立てられる**コマンドライン引数の形**（`--tools ""` / `--settings` の中身 /
`--strict-mcp-config` 等が含まれること）を固定する回帰テストであり、**封じ込めが
実際に効くこと自体の behavioral 回帰テストではない**（単体テストで実 LLM を呼べない
以上、それを直接検証することはできない）。封じ込めの実効性は無人の decisive test
（``/tmp`` の乱数秘密ファイルを読ませ、応答に実際の秘密値が現れないか）で手動実測して
担保している（safe_llm_call.py モジュール docstring に実測結果を記録）。**claude CLI の
フラグ体系が変わるとこのテストは赤くなる**ので、赤くなったら CLI 引数の形だけでなく
封じ込めの実効性も decisive test で再実測すること。
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


def test_command_uses_tools_empty_as_primary_defense(monkeypatch):
    """#410 round2 [Must]A: --tools "" を主防御として使う（codex 指摘: deny 列挙方式は
    将来ツールが増えたとき fail-open する。--tools "" は built-in セット全体を空にするため
    列挙非依存）。実測で ``--tools ""`` 単体（+ --strict-mcp-config）でも秘密ファイル漏洩を
    阻止できることを確認済み（下記 docstring 参照）。
    """
    captured = {}

    def _fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return _FakeCompleted(stdout="ok")

    monkeypatch.setattr(sc.subprocess, "run", _fake_run)
    sc.call_claude_headless("prompt", model="haiku")

    cmd = captured["cmd"]
    assert "--tools" in cmd
    assert cmd[cmd.index("--tools") + 1] == ""


def test_command_uses_settings_deny_as_defense_in_depth(monkeypatch):
    """#410 round1 実測: --disallowedTools / --allowedTools "" / --permission-mode manual は
    このマシンの ~/.claude/settings.json（permissions.defaultMode=bypassPermissions）を
    上書きできず、いずれも単体では実際にツールが実行されてしまった（秘密ファイルの内容が
    応答に漏洩することで確認）。--settings の明示 deny + defaultMode 上書きは単体でも
    有効だったが、round2 では --tools "" を主防御に格上げし、--settings deny は
    defense-in-depth として残す（-p モードは不正な settings を無言で無視するため
    単独の砦にしない・下記モジュール docstring 参照）。
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
    """環境固有 MCP ツールの混入経路も塞ぐ。--tools "" は built-in セットのみを空にし
    MCP サーバのツールは対象外（実測: --tools "" 単体だと Google Drive 系 MCP ツールが
    応答に言及された）ため、--strict-mcp-config（MCP サーバを一切ロードしない）が必須。
    セッション状態も残さない。
    """
    captured = {}

    def _fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return _FakeCompleted(stdout="ok")

    monkeypatch.setattr(sc.subprocess, "run", _fake_run)
    sc.call_claude_headless("prompt")

    assert "--strict-mcp-config" in captured["cmd"]
    assert "--no-session-persistence" in captured["cmd"]


def test_command_includes_safe_mode(monkeypatch):
    """#410 round3 [Must]1: --tools "" / --strict-mcp-config は built-in ツールと MCP を
    塞ぐが、hooks・plugins・CLAUDE.md 等の customization 経路は素通しだった（生ログを
    受けた UserPromptSubmit hook が外部コマンドを実行しうる）。--safe-mode
    （CLAUDE.md/skills/plugins/hooks/MCP servers/custom commands・agents/output styles/
    workflows 等の customization を全無効化。built-in tools・permissions・auth・model
    選択は normal に動作＝claude --help 実測）を追加し、この経路も塞ぐ。実測: 無害な
    UserPromptSubmit hook を settings 経由で仕込み、--safe-mode 無しでは発火（marker
    ファイル書込）、--safe-mode 有りでは発火しないことを確認済み（モジュール docstring
    参照）。
    """
    captured = {}

    def _fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return _FakeCompleted(stdout="ok")

    monkeypatch.setattr(sc.subprocess, "run", _fake_run)
    sc.call_claude_headless("prompt")

    assert "--safe-mode" in captured["cmd"]


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
