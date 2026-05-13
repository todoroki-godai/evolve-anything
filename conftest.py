"""rl-anything ルート conftest.py

全テストで CLAUDE_PLUGIN_DATA を tmp_path に強制し、本番 ~/.claude/rl-anything/
配下を保護する autouse fixture を提供する。

Why: Phase 1 開発時に test fixture が patch_data_dir に session_store パスを
含めていなかったため、本番 sessions.db に test レコードが流入した。fixture 追加
忘れによる本番汚染を構造的に防ぐ最後の砦としてここに置く。

加えて、テスト中に LLM (claude CLI / anthropic SDK) を直接呼ぶことを禁止する
guard を session 起動時にインストールする。issue #41: テスト時の LLM 実呼び出しは
1 セッション 1.5M token 消費の主要因。mock 漏れを構造的に検出するため、
subprocess.run(["claude", ...]) を呼んだ瞬間に RuntimeError を投げる。
正当な用途 (integration テスト等) は環境変数 RL_ALLOW_LLM_IN_TESTS=1 で解除可。
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest


def _install_llm_guard():
    """テスト中の claude CLI subprocess 呼び出しを検出して落とす。

    subprocess.run / subprocess.Popen をプロセス全体で差し替える。mock.patch で
    更に上書きするテストは setattr/delattr の通常動作で衝突しない（既存テストで
    実証済み）。RL_ALLOW_LLM_IN_TESTS の評価は runtime（_guarded_* 関数内）に
    寄せて、test 起動後に環境変数を変えても効くようにしている。
    """
    _orig_run = subprocess.run
    _orig_popen = subprocess.Popen

    def _allowed() -> bool:
        return os.environ.get("RL_ALLOW_LLM_IN_TESTS") == "1"

    def _is_llm_call(args) -> bool:
        # list/tuple のみ判定対象。shell=True 由来の string コマンドは誤検出を避けるため対象外
        if not isinstance(args, (list, tuple)) or not args:
            return False
        first = args[0]
        if isinstance(first, (list, tuple)) and first:
            first = first[0]
        if not isinstance(first, str):
            return False
        return first == "claude" or first.endswith("/claude")

    def _guarded_run(args, *a, **kw):
        if not _allowed() and _is_llm_call(args):
            raise RuntimeError(
                "LLM call from test detected (subprocess.run claude ...). "
                "Mock subprocess.run or the calling function. "
                "See .claude/rules/no-llm-in-tests.md. "
                "Override with RL_ALLOW_LLM_IN_TESTS=1 for integration tests."
            )
        return _orig_run(args, *a, **kw)

    def _guarded_popen(args, *a, **kw):
        if not _allowed() and _is_llm_call(args):
            raise RuntimeError(
                "LLM call from test detected (subprocess.Popen claude ...). "
                "Mock subprocess.Popen or the calling function. "
                "See .claude/rules/no-llm-in-tests.md."
            )
        return _orig_popen(args, *a, **kw)

    subprocess.run = _guarded_run
    subprocess.Popen = _guarded_popen


_install_llm_guard()


@pytest.fixture(autouse=True)
def _isolate_plugin_data(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path))
    if "session_store" in sys.modules:
        ss = sys.modules["session_store"]
        monkeypatch.setattr(ss, "DATA_DIR", tmp_path, raising=False)
        monkeypatch.setattr(ss, "SESSIONS_DB", tmp_path / "sessions.db", raising=False)
        monkeypatch.setattr(ss, "SESSIONS_JSONL", tmp_path / "sessions.jsonl", raising=False)
    if "token_usage_store" in sys.modules:
        tus = sys.modules["token_usage_store"]
        monkeypatch.setattr(tus, "DATA_DIR", tmp_path, raising=False)
        monkeypatch.setattr(tus, "USAGE_DB", tmp_path / "token_usage.db", raising=False)
        monkeypatch.setattr(tus, "USAGE_JSONL", tmp_path / "token_usage.jsonl", raising=False)
