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
    MEMORY_HEAVY_UPDATE_DUP_SECTION_THRESHOLD,
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


def _make_memory_body(
    tmp_path: Path,
    name: str,
    update_count: int,
    body: str,
) -> Path:
    """body をそのまま流し込む（重複セクション等の構造を持たせるため）。"""
    f = tmp_path / ".claude" / "memory" / name
    f.write_text(
        f"---\nname: {name}\nupdate_count: {update_count}\n---\n{body}\n",
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


# ---------- #104: memory_capability=健全 との矛盾解消 ----------
# update_count は memory_capability の use 軸では「活性=良」として加点される指標。
# 同じ指標を memory_heavy_update が「要対応」とすると同一 run で矛盾する。
# 肥大化判定を構造指標（行数・重複セクション）へ寄せ、活発な健全メモリを誤検知しない。

def test_line_threshold_above_observed_minor_range():
    """#104: 実測の軽微メモリ（frontmatter 込 33〜46 行）を肥大化扱いしない閾値。

    旧 30 行は 33〜46 行の健全な活発メモリ（memory_capability use 軸では活性=良）を
    誤検知していた。閾値を実測 minor 範囲（〜46 行）より上へ引き上げる。
    """
    assert MEMORY_HEAVY_UPDATE_LINE_THRESHOLD > 46


def test_dup_section_threshold_exported():
    """重複セクション閾値が export されている（構造的肥大化指標）。"""
    assert isinstance(MEMORY_HEAVY_UPDATE_DUP_SECTION_THRESHOLD, int)
    assert MEMORY_HEAVY_UPDATE_DUP_SECTION_THRESHOLD >= 1


def test_active_healthy_memory_not_flagged(tmp_path):
    """#104: 高 update_count + 軽微な行数（33〜46 行）+ 重複セクションなし → 検出しない。

    memory_capability が健全(use 軸で活性=良)と判定する活発メモリを、同一 run の
    remediation が肥大化扱いして矛盾していた問題（#104）の回帰防止。
    """
    _setup_minimal_project(tmp_path)
    # 実測 #104 の代表値: update_count 多め・frontmatter 込で 40 行前後・内容は一意
    _make_memory(tmp_path, "active_healthy.md", update_count=30, extra_lines=40)

    issues = collect_issues(tmp_path)
    heavy = [i for i in issues if i["type"] == "memory_heavy_update"]

    assert heavy == [], (
        f"健全な活発メモリ（高 update_count・軽微な行数・重複なし）が誤検知された: {heavy}"
    )


def test_duplicate_sections_flagged_even_when_small(tmp_path):
    """#104: 行数は軽微でも重複セクション（再要約の構造的肥大化）があれば検出する。

    arXiv:2605.12978 の劣化は同一見出しの反復追記として現れる。行数単独では
    軽微でも、重複セクションという構造指標で本物の肥大化を捉える（過剰緩和の回帰防止）。
    """
    _setup_minimal_project(tmp_path)
    # 行数は line 閾値未満だが "## Notes" が 3 回反復（重複セクション 2）
    body = "## Notes\nalpha\n## Notes\nbeta\n## Notes\ngamma\n"
    _make_memory_body(tmp_path, "resummarized.md", update_count=10, body=body)

    issues = collect_issues(tmp_path)
    heavy = [i for i in issues if i["type"] == "memory_heavy_update"]

    assert len(heavy) == 1, f"重複セクションによる肥大化が未検出: {heavy}"
    assert heavy[0]["detail"]["duplicate_sections"] >= 1


def test_unique_sections_not_flagged(tmp_path):
    """#104: 見出しが全て一意なら（重複なし）構造的肥大化とみなさない。"""
    _setup_minimal_project(tmp_path)
    body = "## A\nx\n## B\ny\n## C\nz\n"
    _make_memory_body(tmp_path, "unique_sections.md", update_count=10, body=body)

    issues = collect_issues(tmp_path)
    heavy = [i for i in issues if i["type"] == "memory_heavy_update"]

    assert heavy == [], f"一意な見出しのメモリが誤検知された: {heavy}"
