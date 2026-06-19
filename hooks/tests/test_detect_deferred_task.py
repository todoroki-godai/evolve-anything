"""~/.claude/hooks/detect-deferred-task.py のユニットテスト。

v2.1.145 で追加された background_tasks / session_crons フィールドの
処理を含む Stop フック全体をカバーする。
"""
import importlib.util
import io
import json
import sys
from pathlib import Path
from unittest import mock

import pytest

_GLOBAL_HOOK = Path.home() / ".claude" / "hooks" / "detect-deferred-task.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("detect_deferred_task", _GLOBAL_HOOK)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def mod(tmp_path):
    """モジュールをロードし、DATA_DIR を一時ディレクトリに差し替える。"""
    m = _load_module()
    with mock.patch.object(m, "DATA_DIR", tmp_path / "evolve-anything"):
        yield m


def _run(mod, payload: dict) -> tuple[dict | None, str]:
    """mod.main() を stdin/stdout をモックして実行し (stdout_json, stderr_text) を返す。"""
    stdin = io.StringIO(json.dumps(payload))
    stdout = io.StringIO()
    stderr_buf = io.StringIO()
    with mock.patch("sys.stdin", stdin), mock.patch("sys.stdout", stdout), mock.patch("sys.stderr", stderr_buf):
        try:
            mod.main()
        except SystemExit:
            pass
    raw = stdout.getvalue()
    result = json.loads(raw) if raw.strip() else None
    return result, stderr_buf.getvalue()


# ── 既存動作の保全 ──────────────────────────────────────────────


def test_no_deferral_no_background_passes(mod):
    """先送りなし + background_tasks なし → ブロックしない。"""
    result, _ = _run(mod, {"last_assistant_message": "実装完了しました。", "session_id": "s1"})
    assert result is None


def test_deferral_blocks(mod):
    """先送り表現あり → decision: block を返す。"""
    result, _ = _run(mod, {
        "last_assistant_message": "後で対応しましょう。",
        "session_id": "s1",
    })
    assert result is not None
    assert result["decision"] == "block"
    assert "先送り" in result["reason"]


def test_stop_hook_active_skips(mod):
    """stop_hook_active: true → 何もしない（無限ループ防止）。"""
    result, _ = _run(mod, {
        "stop_hook_active": True,
        "last_assistant_message": "後で対応しましょう。",
        "session_id": "s1",
    })
    assert result is None


# ── v2.1.145 新機能: background_tasks / session_crons ──────────


def test_background_tasks_only_logs_warning(mod, tmp_path):
    """先送りなし + background_tasks あり → ブロックせず stderr に警告を出す。"""
    result, stderr = _run(mod, {
        "last_assistant_message": "作業完了です。",
        "session_id": "s2",
        "background_tasks": [{"id": "bg1", "command": "sleep 60"}],
        "session_crons": [],
    })
    assert result is None, "background_tasks のみではブロックしない"
    assert "background" in stderr.lower() or "バックグラウンド" in stderr


def test_session_crons_only_logs_warning(mod):
    """先送りなし + session_crons あり → ブロックせず stderr に警告を出す。"""
    result, stderr = _run(mod, {
        "last_assistant_message": "完了しました。",
        "session_id": "s3",
        "background_tasks": [],
        "session_crons": [{"id": "cron1", "schedule": "*/5 * * * *"}],
    })
    assert result is None
    assert "cron" in stderr.lower() or "Cron" in stderr


def test_deferral_with_background_tasks_includes_context(mod):
    """先送り + background_tasks → block かつ reason にバックグラウンド情報を含む。"""
    result, _ = _run(mod, {
        "last_assistant_message": "後で実装しましょう。",
        "session_id": "s4",
        "background_tasks": [{"id": "bg1"}, {"id": "bg2"}],
        "session_crons": [],
    })
    assert result is not None
    assert result["decision"] == "block"
    assert "2" in result["reason"] or "バックグラウンド" in result["reason"]


def test_both_background_and_crons_warn(mod):
    """background_tasks + session_crons 両方あり → stderr に両方の情報が出る。"""
    result, stderr = _run(mod, {
        "last_assistant_message": "完了しました。",
        "session_id": "s5",
        "background_tasks": [{"id": "bg1"}],
        "session_crons": [{"id": "cron1"}],
    })
    assert result is None
    assert stderr  # 何らかの警告が出る


def test_empty_background_tasks_no_warning(mod):
    """background_tasks: [] (空) → 警告なし。"""
    result, stderr = _run(mod, {
        "last_assistant_message": "完了しました。",
        "session_id": "s6",
        "background_tasks": [],
        "session_crons": [],
    })
    assert result is None
    assert not stderr.strip()
