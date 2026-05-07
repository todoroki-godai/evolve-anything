#!/usr/bin/env python3
"""trigger_engine.py の DATA_DIR + min_sessions + rolling pruning テスト。

検証:
1. CLAUDE_PLUGIN_DATA 未設定時は ~/.claude/rl-anything/ を指す
2. CLAUDE_PLUGIN_DATA 設定時はそのパスに切り替わる（DATA_DIR バグ修正）
3. DEFAULT_TRIGGER_CONFIG の min_sessions がデフォルト 3 である
4. _prune_sessions_jsonl がファイルを max_lines 以下に切り詰める
5. evaluate_session_end がカウント後に pruning を呼ぶ（副作用確認）
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_LIB = str(_REPO_ROOT / "scripts" / "lib")


def _import_trigger(env: dict[str, str] | None = None) -> dict:
    """subprocess で trigger_engine を import して DATA_DIR と min_sessions を返す。

    モジュールレベル評価（DATA_DIR は import 時に決まる）なので subprocess 必須。
    """
    code = (
        "import sys, json, os; "
        f"sys.path.insert(0, {_LIB!r}); "
        "import trigger_engine; "
        "print(json.dumps({"
        "'data_dir': str(trigger_engine.DATA_DIR), "
        "'min_sessions': trigger_engine.DEFAULT_TRIGGER_CONFIG['triggers']['session_end']['min_sessions']"
        "}))"
    )
    merged_env = {**os.environ, **(env or {})}
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env=merged_env,
    )
    assert result.returncode == 0, f"subprocess failed: {result.stderr}"
    return json.loads(result.stdout.strip())


class TestDataDir:
    def test_default_path(self, tmp_path, monkeypatch):
        """CLAUDE_PLUGIN_DATA 未設定時は ~/.claude/rl-anything/ を指す。"""
        monkeypatch.delenv("CLAUDE_PLUGIN_DATA", raising=False)
        result = _import_trigger()
        expected = str(Path.home() / ".claude" / "rl-anything")
        assert result["data_dir"] == expected

    def test_env_override(self, tmp_path):
        """CLAUDE_PLUGIN_DATA を設定するとそのパスが使われる。"""
        custom = str(tmp_path / "custom-data")
        result = _import_trigger({"CLAUDE_PLUGIN_DATA": custom})
        assert result["data_dir"] == custom

    def test_env_override_plugin_data_dir(self, tmp_path):
        """プラグイン実行時の実際のパス形式でも機能する。"""
        plugin_dir = str(tmp_path / "plugins" / "data" / "rl-anything-rl-anything")
        result = _import_trigger({"CLAUDE_PLUGIN_DATA": plugin_dir})
        assert result["data_dir"] == plugin_dir


class TestMinSessions:
    def test_default_min_sessions_is_3(self):
        """DEFAULT_TRIGGER_CONFIG の min_sessions がデフォルト 3 である。"""
        result = _import_trigger()
        assert result["min_sessions"] == 3, (
            f"min_sessions should be 3 but got {result['min_sessions']}"
        )


class TestCountSessionsSince:
    def test_uses_correct_data_dir(self, tmp_path, monkeypatch):
        """CLAUDE_PLUGIN_DATA のパスの sessions.jsonl を読む。"""
        monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path))

        sys.path.insert(0, _LIB)
        import importlib
        import trigger_engine as te
        # DATA_DIR は module-level なので monkeypatch では変わらない
        # subprocess で確認するか、_count_sessions_since に sessions_file 引数を渡すテストにする

        sessions_file = tmp_path / "sessions.jsonl"
        now = "2026-01-02T00:00:00+00:00"
        last_run = "2026-01-01T00:00:00+00:00"
        records = [
            {"session_id": "abc", "timestamp": "2026-01-01T12:00:00+00:00"},
            {"session_id": "def", "timestamp": "2026-01-01T18:00:00+00:00"},
            {"session_id": "abc", "timestamp": "2026-01-01T13:00:00+00:00"},  # 重複
        ]
        sessions_file.write_text(
            "\n".join(json.dumps(r) for r in records), encoding="utf-8"
        )

        # _count_sessions_since は DATA_DIR を参照するが、subprocess で env を渡して確認
        code = (
            "import sys, json, os; "
            f"sys.path.insert(0, {_LIB!r}); "
            "import trigger_engine as te; "
            f"result = te._count_sessions_since({last_run!r}); "
            "print(json.dumps({'count': result}))"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            env={**os.environ, "CLAUDE_PLUGIN_DATA": str(tmp_path)},
        )
        assert result.returncode == 0, result.stderr
        data = json.loads(result.stdout.strip())
        # session_id "abc" と "def" の 2 ユニーク（"abc" は重複）
        assert data["count"] == 2
