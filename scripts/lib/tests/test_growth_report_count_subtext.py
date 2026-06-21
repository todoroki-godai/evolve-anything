"""growth_report.build_growth_report の corrections 進捗行に「カウント条件」サブテキストが
付くことの保証（#51 LOW）。

「あと N 件」の分子が何を数えた数か（/reflect approve / --promote-weak 昇格のみ・自動検出 /
Stop hook 由来は除外）をユーザーが行内から読めることを assert する。決定論・LLM 非依存。
"""
from __future__ import annotations

import sys
from pathlib import Path

_lib_dir = Path(__file__).resolve().parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

from growth_report import build_growth_report

# サブテキストの load-bearing なキーワード（文言全体一致ではなく要素で検証する）。
_SUBTEXT_KEYWORDS = ("カウント", "reflect", "promote-weak", "Stop hook")


def _has_count_subtext(lines):
    """lines のいずれかに、カウント条件サブテキストの全要素が含まれるかを返す。"""
    return any(all(kw in ln for kw in _SUBTEXT_KEYWORDS) for ln in lines)


def test_count_subtext_present_when_remaining() -> None:
    """未達（remaining > 0）の corrections 進捗行にカウント条件サブテキストが付く。"""
    corrections = [
        {"source": "reflect_confirmed", "correction_type": "idiom"} for _ in range(3)
    ]
    report = build_growth_report(
        "evolve-anything",
        corrections=corrections,
        review_result=None,
        autopromote_result=None,
    )
    lines = report["lines"]
    # 進捗行（分子 3/10）が出ている
    assert any("3/10" in ln for ln in lines)
    # カウント条件サブテキストが lines に含まれる
    assert _has_count_subtext(lines), lines


def test_count_subtext_present_when_target_reached() -> None:
    """達成（remaining == 0）の場合もカウント条件サブテキストが付く。"""
    corrections = [
        {"source": "reflect_confirmed", "correction_type": "idiom"} for _ in range(10)
    ]
    report = build_growth_report(
        "evolve-anything",
        corrections=corrections,
        review_result=None,
        autopromote_result=None,
    )
    lines = report["lines"]
    assert any("10/10" in ln for ln in lines)
    assert _has_count_subtext(lines), lines


def test_count_subtext_present_with_empty_corrections() -> None:
    """corrections 0 件（0/10）でもカウント条件サブテキストが付く（沈黙≠説明）。"""
    report = build_growth_report(
        "evolve-anything",
        corrections=[],
        review_result=None,
        autopromote_result=None,
    )
    lines = report["lines"]
    assert any("0/10" in ln for ln in lines)
    assert _has_count_subtext(lines), lines
