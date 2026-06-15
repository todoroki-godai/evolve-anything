"""#523-1: Chaos/Constitutional スキップ通知の1行要約の回帰テスト。

Chaos Testing が `.claude/worktrees/` の stale worktree を shadow コピーしようとして
失敗すると shutil.Error が生 Python タプル（ファイルパスの長大リスト）を str 展開し、
stderr を汚していた。_summarize_skip_reason がこれを 1 行サマリへ畳むことを検証する。
"""
import shutil
import sys
from pathlib import Path

_lib_dir = Path(__file__).resolve().parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

import audit.orchestrator as orch  # noqa: E402


def test_summarize_worktree_residue_shutil_error():
    """worktrees 由来の shutil.Error は「スキップ N 件（worktree 残骸）」に畳む。"""
    err = shutil.Error([
        ("/p/.claude/worktrees/agent-x/dangling", "/dst", "[Errno 2] No such file"),
        ("/p/.claude/worktrees/agent-y/dangling", "/dst", "[Errno 2] No such file"),
    ])
    summary = orch._summarize_skip_reason(err)
    assert summary == "スキップ 2 件（worktree 残骸）", summary
    assert "\n" not in summary


def test_summarize_generic_shutil_error_counts():
    """worktree 以外の copytree 失敗は件数サマリにする。"""
    err = shutil.Error([("/p/other/file", "/dst", "boom")])
    summary = orch._summarize_skip_reason(err)
    assert summary == "コピー失敗 1 件", summary


def test_summarize_truncates_multiline_long_message():
    """複数行・長文の例外は1行・上限長に畳む。"""
    long_msg = "line1\n" + ("x" * 500)
    summary = orch._summarize_skip_reason(RuntimeError(long_msg), max_len=160)
    assert "\n" not in summary
    assert len(summary) <= 160
    assert summary.endswith("…")


def test_summarize_short_message_passthrough():
    summary = orch._summarize_skip_reason(ValueError("簡潔な理由"))
    assert summary == "簡潔な理由"
