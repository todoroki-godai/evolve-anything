"""fleet/cli.py の _show_active_agents() ユニットテスト。

v2.1.145 で追加された `claude agents --json` を使うアクティブセッション表示をカバーする。
"""
import json
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest

_LIB = Path(__file__).resolve().parent.parent / "lib"
sys.path.insert(0, str(_LIB))

from fleet.cli import _show_active_agents  # noqa: E402


def _make_sessions(names: list[str]) -> bytes:
    sessions = [{"id": f"id-{i}", "name": n, "status": "working"} for i, n in enumerate(names)]
    return json.dumps(sessions).encode()


def test_no_sessions_returns_none():
    """セッションが 0 件 → None を返す（表示しない）。"""
    with mock.patch("subprocess.run") as m:
        m.return_value = mock.Mock(returncode=0, stdout=b"[]")
        result = _show_active_agents()
    assert result is None


def test_single_session_shows_name():
    """1セッション → セッション名を含む文字列を返す。"""
    with mock.patch("subprocess.run") as m:
        m.return_value = mock.Mock(returncode=0, stdout=_make_sessions(["my-agent"]))
        result = _show_active_agents()
    assert result is not None
    assert "my-agent" in result or "1" in result


def test_multiple_sessions_shows_count():
    """3セッション → 件数が含まれる。"""
    with mock.patch("subprocess.run") as m:
        m.return_value = mock.Mock(returncode=0, stdout=_make_sessions(["a", "b", "c"]))
        result = _show_active_agents()
    assert result is not None
    assert "3" in result


def test_command_failure_returns_none():
    """`claude agents --json` が失敗 → None を返す（表示しない）。"""
    with mock.patch("subprocess.run") as m:
        m.return_value = mock.Mock(returncode=1, stdout=b"")
        result = _show_active_agents()
    assert result is None


def test_subprocess_exception_returns_none():
    """subprocess.run が例外 → None を返す（表示しない）。"""
    with mock.patch("subprocess.run", side_effect=FileNotFoundError("claude not found")):
        result = _show_active_agents()
    assert result is None


def test_invalid_json_returns_none():
    """`claude agents --json` が不正 JSON → None を返す（表示しない）。"""
    with mock.patch("subprocess.run") as m:
        m.return_value = mock.Mock(returncode=0, stdout=b"not json")
        result = _show_active_agents()
    assert result is None
