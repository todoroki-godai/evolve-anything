#!/usr/bin/env python3
"""audit の memory_heavy_update 検出ロジックのテスト。

Issue #97 / arXiv:2605.12978: LLM 自己更新メモリの劣化警告。
update_count が閾値 (3) 以上の memory entry を audit が warning として検出する。
詳細: docs/research/faulty-updated-memories.md
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from audit.issues import collect_issues, MEMORY_HEAVY_UPDATE_THRESHOLD


def _setup_minimal_project(tmp_path: Path) -> Path:
    """audit 実行に必要な最低限のディレクトリ構造を作る。

    find_artifacts は .claude/memory/ を memory のソースとして見る。
    """
    (tmp_path / ".claude" / "memory").mkdir(parents=True)
    (tmp_path / ".claude" / "rules").mkdir(parents=True)
    (tmp_path / ".claude" / "skills").mkdir(parents=True)
    (tmp_path / "CLAUDE.md").write_text("# Test\n", encoding="utf-8")
    return tmp_path


def _make_memory(tmp_path: Path, name: str, update_count: int | None) -> Path:
    """update_count=None は frontmatter なしファイル。"""
    f = tmp_path / ".claude" / "memory" / name
    if update_count is None:
        f.write_text("# No frontmatter\nContent.\n", encoding="utf-8")
    else:
        f.write_text(
            f"---\nname: {name}\nupdate_count: {update_count}\n---\n# Body\n",
            encoding="utf-8",
        )
    return f


def test_threshold_default_is_three():
    """閾値定数は 3。"""
    assert MEMORY_HEAVY_UPDATE_THRESHOLD == 3


def test_at_threshold_detected(tmp_path):
    """update_count == 3 → memory_heavy_update issue が 1 件出る。"""
    _setup_minimal_project(tmp_path)
    _make_memory(tmp_path, "heavy.md", update_count=3)

    issues = collect_issues(tmp_path)
    heavy = [i for i in issues if i["type"] == "memory_heavy_update"]

    assert len(heavy) == 1
    assert heavy[0]["detail"]["update_count"] == 3
    assert heavy[0]["detail"]["threshold"] == 3
    assert heavy[0]["source"] == "build_memory_health_section"
    assert heavy[0]["file"].endswith("heavy.md")


def test_above_threshold_detected(tmp_path):
    """update_count > 3 も検出される。"""
    _setup_minimal_project(tmp_path)
    _make_memory(tmp_path, "very_heavy.md", update_count=7)

    issues = collect_issues(tmp_path)
    heavy = [i for i in issues if i["type"] == "memory_heavy_update"]

    assert len(heavy) == 1
    assert heavy[0]["detail"]["update_count"] == 7


def test_below_threshold_not_detected(tmp_path):
    """update_count < 3 → memory_heavy_update 出ない。"""
    _setup_minimal_project(tmp_path)
    _make_memory(tmp_path, "light.md", update_count=2)

    issues = collect_issues(tmp_path)
    heavy = [i for i in issues if i["type"] == "memory_heavy_update"]

    assert heavy == []


def test_zero_update_count_not_detected(tmp_path):
    """frontmatter なし (update_count=0) → 検出しない（既存ファイルは影響なし）。"""
    _setup_minimal_project(tmp_path)
    _make_memory(tmp_path, "untracked.md", update_count=None)

    issues = collect_issues(tmp_path)
    heavy = [i for i in issues if i["type"] == "memory_heavy_update"]

    assert heavy == []
