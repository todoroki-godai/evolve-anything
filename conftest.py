"""rl-anything ルート conftest.py

全テストで CLAUDE_PLUGIN_DATA を tmp_path に強制し、本番 ~/.claude/rl-anything/
配下を保護する autouse fixture を提供する。

Why: Phase 1 開発時に test fixture が patch_data_dir に session_store パスを
含めていなかったため、本番 sessions.db に test レコードが流入した。fixture 追加
忘れによる本番汚染を構造的に防ぐ最後の砦としてここに置く。
"""
import sys
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolate_plugin_data(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path))
    if "session_store" in sys.modules:
        ss = sys.modules["session_store"]
        monkeypatch.setattr(ss, "DATA_DIR", tmp_path, raising=False)
        monkeypatch.setattr(ss, "SESSIONS_DB", tmp_path / "sessions.db", raising=False)
        monkeypatch.setattr(ss, "SESSIONS_JSONL", tmp_path / "sessions.jsonl", raising=False)
