#!/usr/bin/env python3
"""audit の memory_heavy_update 検出ロジックのテスト。

Issue #97 / arXiv:2605.12978: LLM 自己更新メモリの劣化警告。
update_count が閾値 (3) 以上 **かつ** 行数が行数閾値以上の memory entry を
audit が warning として検出する（#353: 更新回数単独の誤検知を解消）。
詳細: docs/research/faulty-updated-memories.md
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from audit.issues import (
    collect_issues,
    MEMORY_HEAVY_UPDATE_THRESHOLD,
    MEMORY_HEAVY_UPDATE_LINE_THRESHOLD,
)


def _setup_minimal_project(tmp_path: Path) -> Path:
    """audit 実行に必要な最低限のディレクトリ構造を作る。

    find_artifacts は .claude/memory/ を memory のソースとして見る。
    """
    (tmp_path / ".claude" / "memory").mkdir(parents=True)
    (tmp_path / ".claude" / "rules").mkdir(parents=True)
    (tmp_path / ".claude" / "skills").mkdir(parents=True)
    (tmp_path / "CLAUDE.md").write_text("# Test\n", encoding="utf-8")
    return tmp_path


def _make_memory(
    tmp_path: Path,
    name: str,
    update_count: int | None,
    extra_lines: int = 0,
) -> Path:
    """update_count=None は frontmatter なしファイル。extra_lines で行数を増やす。"""
    f = tmp_path / ".claude" / "memory" / name
    if update_count is None:
        f.write_text("# No frontmatter\nContent.\n", encoding="utf-8")
    else:
        body_lines = "\n".join([f"- item {i}" for i in range(extra_lines)])
        f.write_text(
            f"---\nname: {name}\nupdate_count: {update_count}\n---\n# Body\n{body_lines}\n",
            encoding="utf-8",
        )
    return f


def test_threshold_default_is_three():
    """閾値定数は 3。"""
    assert MEMORY_HEAVY_UPDATE_THRESHOLD == 3


def test_line_threshold_exported():
    """行数閾値定数が export されている（#353 の複合条件用）。"""
    assert isinstance(MEMORY_HEAVY_UPDATE_LINE_THRESHOLD, int)
    assert MEMORY_HEAVY_UPDATE_LINE_THRESHOLD > 0


def test_at_threshold_with_large_content_detected(tmp_path):
    """update_count == 3 かつ 行数 >= 行数閾値 → memory_heavy_update issue が出る。"""
    _setup_minimal_project(tmp_path)
    # 行数閾値を超えるコンテンツを作成
    _make_memory(tmp_path, "heavy.md", update_count=3, extra_lines=MEMORY_HEAVY_UPDATE_LINE_THRESHOLD)

    issues = collect_issues(tmp_path)
    heavy = [i for i in issues if i["type"] == "memory_heavy_update"]

    assert len(heavy) == 1
    assert heavy[0]["detail"]["update_count"] == 3
    assert heavy[0]["detail"]["threshold"] == 3
    assert heavy[0]["source"] == "build_memory_health_section"
    assert heavy[0]["file"].endswith("heavy.md")


def test_above_threshold_with_large_content_detected(tmp_path):
    """update_count > 3 かつ 行数 >= 行数閾値 も検出される。"""
    _setup_minimal_project(tmp_path)
    _make_memory(tmp_path, "very_heavy.md", update_count=7, extra_lines=MEMORY_HEAVY_UPDATE_LINE_THRESHOLD)

    issues = collect_issues(tmp_path)
    heavy = [i for i in issues if i["type"] == "memory_heavy_update"]

    assert len(heavy) == 1
    assert heavy[0]["detail"]["update_count"] == 7


def test_below_threshold_not_detected(tmp_path):
    """update_count < 3 → memory_heavy_update 出ない（行数に関わらず）。"""
    _setup_minimal_project(tmp_path)
    _make_memory(tmp_path, "light.md", update_count=2, extra_lines=MEMORY_HEAVY_UPDATE_LINE_THRESHOLD)

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


# ---------- #353: 複合条件テスト (更新回数単独では誤検知しない) ----------

def test_high_update_count_but_small_content_not_detected(tmp_path):
    """#353: 高 update_count でも行数が閾値未満なら memory_heavy_update を出さない。

    コスト最適化メモリ（7回更新だが内容が少ない）を誤検知していた問題を修正。
    更新回数だけ多いものは正常な活発運用とみなす。
    """
    _setup_minimal_project(tmp_path)
    # update_count = 7（高い）、extra_lines = 0（行数小）
    _make_memory(tmp_path, "active_small.md", update_count=7, extra_lines=0)

    issues = collect_issues(tmp_path)
    heavy = [i for i in issues if i["type"] == "memory_heavy_update"]

    assert heavy == [], (
        f"高 update_count だが小さいメモリが誤検知された: {heavy}\n"
        f"（行数閾値: {MEMORY_HEAVY_UPDATE_LINE_THRESHOLD}）"
    )


def test_high_update_count_and_large_content_detected(tmp_path):
    """#353: 高 update_count かつ 行数大 → 正しく memory_heavy_update フラグが立つ。

    両条件が揃った場合のみ警告する（更新が多く内容も肥大化 = 劣化リスクあり）。
    """
    _setup_minimal_project(tmp_path)
    # update_count = 7（高い）、extra_lines = 行数閾値（大きい）
    _make_memory(tmp_path, "active_large.md", update_count=7, extra_lines=MEMORY_HEAVY_UPDATE_LINE_THRESHOLD)

    issues = collect_issues(tmp_path)
    heavy = [i for i in issues if i["type"] == "memory_heavy_update"]

    assert len(heavy) == 1, (
        f"高 update_count かつ 大きいメモリが未検出: {heavy}\n"
        f"（行数閾値: {MEMORY_HEAVY_UPDATE_LINE_THRESHOLD}）"
    )
