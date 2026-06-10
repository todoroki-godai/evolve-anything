"""evolve._resolve_pj_slug が utterances.db の pj_slug と同じ導出になることの保証（#431/#440）。

weak_signals / correction_semantic は utterances.db の pj_slug と突合するため、
worktree 内実行でも本体 repo basename に正規化される（worktree 名にならない）ことを assert。
決定論・LLM 非依存。
"""
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent
_PLUGIN_ROOT = _SCRIPTS.parent.parent.parent
sys.path.insert(0, str(_SCRIPTS))
sys.path.insert(0, str(_PLUGIN_ROOT / "scripts" / "lib"))

import evolve  # noqa: E402


def test_plain_project_dir():
    assert evolve._resolve_pj_slug("/Users/x/tools/rl-anything") == "rl-anything"


def test_worktree_dir_normalizes_to_main_repo():
    # worktree 内パスでも本体 repo basename になる（worktree 名にならない）
    wt = "/Users/x/tools/rl-anything/.claude/worktrees/agent-abc123"
    assert evolve._resolve_pj_slug(wt) == "rl-anything"


def test_matches_utterance_archive_derivation():
    # utterance_archive.pj_slug_from_cwd と完全一致（突合の前提）
    from utterance_archive.extractor import pj_slug_from_cwd
    wt = "/Users/x/repo/.claude/worktrees/agent-zzz"
    assert evolve._resolve_pj_slug(wt) == pj_slug_from_cwd(wt)


def test_none_falls_back_to_cwd_basename():
    # None でも例外を投げず文字列を返す
    assert isinstance(evolve._resolve_pj_slug(None), str)
    assert evolve._resolve_pj_slug(None) != ""
