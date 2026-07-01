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
    *,
    superseded: bool = False,
) -> Path:
    """update_count=None は frontmatter なしファイル。extra_lines で行数を増やす。

    #104: memory_heavy_update は「健全でない（stale/superseded）」memory にのみ発火する。
    superseded=True で過去日 superseded_at を付与し、非健全（要対応）メモリを作る。
    """
    f = tmp_path / ".claude" / "memory" / name
    if update_count is None:
        f.write_text("# No frontmatter\nContent.\n", encoding="utf-8")
    else:
        body_lines = "\n".join([f"- item {i}" for i in range(extra_lines)])
        fm = [f"name: {name}", f"update_count: {update_count}"]
        if superseded:
            # YAML 非引用のタイムスタンプは datetime に変換されるため引用符で文字列固定
            fm.append("superseded_at: '2020-01-01T00:00:00Z'")
        fm_block = "\n".join(fm)
        f.write_text(
            f"---\n{fm_block}\n---\n# Body\n{body_lines}\n",
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
    """update_count == 3 かつ 行数 >= 行数閾値 かつ 非健全（superseded）→ issue が出る（#104）。"""
    _setup_minimal_project(tmp_path)
    # 行数閾値を超えるコンテンツ + superseded（非健全）
    _make_memory(tmp_path, "heavy.md", update_count=3,
                 extra_lines=MEMORY_HEAVY_UPDATE_LINE_THRESHOLD, superseded=True)

    issues = collect_issues(tmp_path)
    heavy = [i for i in issues if i["type"] == "memory_heavy_update"]

    assert len(heavy) == 1
    assert heavy[0]["detail"]["update_count"] == 3
    assert heavy[0]["detail"]["threshold"] == 3
    assert heavy[0]["detail"]["superseded"] is True
    assert heavy[0]["source"] == "build_memory_health_section"
    assert heavy[0]["file"].endswith("heavy.md")


def test_above_threshold_with_large_content_detected(tmp_path):
    """update_count > 3 かつ 行数 >= 行数閾値 かつ 非健全 も検出される（#104）。"""
    _setup_minimal_project(tmp_path)
    _make_memory(tmp_path, "very_heavy.md", update_count=7,
                 extra_lines=MEMORY_HEAVY_UPDATE_LINE_THRESHOLD, superseded=True)

    issues = collect_issues(tmp_path)
    heavy = [i for i in issues if i["type"] == "memory_heavy_update"]

    assert len(heavy) == 1
    assert heavy[0]["detail"]["update_count"] == 7


def test_below_threshold_not_detected(tmp_path):
    """update_count < 3 → memory_heavy_update 出ない（行数・健全性に関わらず）。"""
    _setup_minimal_project(tmp_path)
    _make_memory(tmp_path, "light.md", update_count=2,
                 extra_lines=MEMORY_HEAVY_UPDATE_LINE_THRESHOLD, superseded=True)

    issues = collect_issues(tmp_path)
    heavy = [i for i in issues if i["type"] == "memory_heavy_update"]

    assert heavy == []


def test_healthy_heavy_memory_not_detected(tmp_path):
    """#104: 健全（非 stale・非 superseded）memory は高 update_count・大行数でも検出しない。

    amamo で update 55 / 40 行の健全メモリが memory_capability=1.00（健全）と
    memory_heavy_update（要対応）に同時分類された矛盾の回帰封じ。update_count は
    memory_capability の use 軸で「活性（良）」として加点される指標なので、健全メモリでは
    heavy_update を出さない。
    """
    _setup_minimal_project(tmp_path)
    # 高 update_count + 大行数 だが superseded なし（= 健全）
    _make_memory(tmp_path, "healthy_active.md", update_count=55,
                 extra_lines=MEMORY_HEAVY_UPDATE_LINE_THRESHOLD + 10, superseded=False)

    issues = collect_issues(tmp_path)
    heavy = [i for i in issues if i["type"] == "memory_heavy_update"]

    assert heavy == [], (
        f"健全な高頻度更新メモリが誤検知された（#104 の矛盾再発）: {heavy}"
    )


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
    """#353/#104: 高 update_count かつ 行数大 かつ 非健全 → memory_heavy_update フラグが立つ。

    3 条件が揃った場合のみ警告する（更新が多く内容も肥大化 かつ 健全でない = 劣化リスクあり）。
    """
    _setup_minimal_project(tmp_path)
    # update_count = 7（高い）、extra_lines = 行数閾値（大きい）、superseded（非健全）
    _make_memory(tmp_path, "active_large.md", update_count=7,
                 extra_lines=MEMORY_HEAVY_UPDATE_LINE_THRESHOLD, superseded=True)

    issues = collect_issues(tmp_path)
    heavy = [i for i in issues if i["type"] == "memory_heavy_update"]

    assert len(heavy) == 1, (
        f"高 update_count かつ 大きい非健全メモリが未検出: {heavy}\n"
        f"（行数閾値: {MEMORY_HEAVY_UPDATE_LINE_THRESHOLD}）"
    )
