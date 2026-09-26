#!/usr/bin/env python3
"""results_board_rate_render のテスト（#690: 版の系列軸・標準化率・最小検出可能差）。

既存の柱3レンダリングテスト（is_worsening・PJ別内訳・カテゴリ内訳・除外diagnostics）は
test_results_board.py（``render_results_board`` 経由）が既に担う。ここは #690 で追加した
3点（版の分割表示・標準化率の併記・最小検出可能差の表示）だけを、``_render_correction_rate``
／``_render_correction_rate_point`` を直接呼んで検証する。
"""
from __future__ import annotations

import sys
from pathlib import Path

_lib_dir = Path(__file__).resolve().parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

import results_board_rate_render as rr  # noqa: E402


def _week(
    week_id="2026-W36",
    rate=0.1,
    judged=10,
    tp=1,
    version_breakdown=None,
    standardized=None,
    min_detectable_diff=None,
    is_worsening=False,
):
    return {
        "week_id": week_id,
        "rate": rate,
        "judged_count": judged,
        "tp_count": tp,
        "coverage": 1.0,
        "measured": True,
        "pj_breakdown": {},
        "is_worsening": is_worsening,
        "top3_examples": [],
        "version_breakdown": version_breakdown or {},
        "standardized": standardized or {},
        "min_detectable_diff": min_detectable_diff,
    }


def _correction_rate(displayed_weeks):
    return {
        "gate": {
            "gate_open": True, "display_start_week": displayed_weeks[0]["week_id"],
            "required": 4, "best_run_length": 4,
        },
        "displayed_weeks": displayed_weeks,
        "latest_coverage": None,
        "coverage_gaps": [],
        "diagnostics": {},
        "generated_at": None,
    }


# ── #690 変更1: 版が混在する週は単一の値に潰さない（blocking(a)） ──────


class TestVersionMixedWeekRendering:
    def test_mixed_week_does_not_show_single_blended_rate(self):
        vb = {
            "28c25437f34a": {"judged": 855, "tp": 100, "rate": 100 / 855},
            "53c3982a2738": {"judged": 222, "tp": 30, "rate": 30 / 222},
        }
        w = _week(rate=0.1207, judged=1077, tp=130, version_breakdown=vb)
        lines = rr._render_correction_rate(_correction_rate([w]))
        text = "\n".join(lines)
        assert "版混在のため単一値なし" in text
        assert "12.1%" not in text  # 混在週の単一ブレンド値が headline に出ない
        assert "28c25437f34a" in text
        assert "53c3982a2738" in text

    def test_single_version_week_shows_headline_rate_without_split(self):
        vb = {"28c25437f34a": {"judged": 10, "tp": 1, "rate": 0.1}}
        w = _week(rate=0.1, judged=10, tp=1, version_breakdown=vb)
        lines = rr._render_correction_rate(_correction_rate([w]))
        text = "\n".join(lines)
        assert "版混在のため単一値なし" not in text
        assert "10.0%" in text
        assert "版別" not in text  # 単一版週は重複表示しない

    def test_no_version_breakdown_falls_back_to_plain_rendering(self):
        """既存呼び出し（version_breakdown を持たない旧フォーマット）は壊れない。"""
        w = {
            "week_id": "2026-W10", "rate": 0.1, "judged_count": 10, "tp_count": 1,
            "coverage": 1.0, "measured": True, "pj_breakdown": {}, "is_worsening": False,
            "top3_examples": [],
        }
        lines = rr._render_correction_rate(_correction_rate([w]))
        text = "\n".join(lines)
        assert "10.0%" in text
        assert "版混在" not in text


# ── #690 変更2: 標準化率の併記 ────────────────────────────────────


class TestStandardizedRateRendering:
    def test_measured_standardized_rate_is_shown(self):
        std = {
            "measured": True, "rate": 0.15, "variance": 0.001,
            "coverage": 0.93, "standard_mix_id": "2026-W35..2026-W38",
        }
        w = _week(standardized=std)
        lines = rr._render_correction_rate(_correction_rate([w]))
        text = "\n".join(lines)
        assert "標準化率" in text
        assert "15.0%" in text
        assert "2026-W35..2026-W38" in text
        assert "93%" in text

    def test_unmeasured_standardized_rate_shows_nothing(self):
        std = {"measured": False, "rate": None, "variance": None, "coverage": 0.0}
        w = _week(standardized=std)
        lines = rr._render_correction_rate(_correction_rate([w]))
        text = "\n".join(lines)
        assert "標準化率" not in text


# ── #690 変更3: 最小検出可能差 ────────────────────────────────────


class TestMinDetectableDiffRendering:
    def test_min_detectable_diff_line_is_shown(self):
        w = _week(min_detectable_diff=0.0345)
        lines = rr._render_correction_rate(_correction_rate([w]))
        text = "\n".join(lines)
        assert "最小検出可能差" in text
        assert "±3.5pt" in text
        assert "変化なし" in text

    def test_none_min_detectable_diff_shows_nothing(self):
        w = _week(min_detectable_diff=None)
        lines = rr._render_correction_rate(_correction_rate([w]))
        text = "\n".join(lines)
        assert "最小検出可能差" not in text


# ── 点表示（状態(ii)）でも版混在を単一値に潰さない ─────────────────


class TestPointDisplayVersionSplit:
    def _gate_with_point_week(self, point_week):
        return {
            "gate_open": False, "required": 4, "current_run_length": 1,
            "point_week": point_week,
        }

    def test_point_week_mixed_version_shows_split(self):
        vb = {
            "28c25437f34a": {"judged": 5, "tp": 1, "rate": 0.2},
            "53c3982a2738": {"judged": 5, "tp": 0, "rate": 0.0},
        }
        point_week = {
            "week_id": "2026-W36", "judged_count": 10, "tp_count": 1, "rate": 0.1,
            "version_breakdown": vb, "pj_breakdown": {"evolve-anything": {
                "judged": 10, "tp": 1, "rate": 0.1,
            }},
            "standardized": {},
        }
        correction_rate = {"latest_coverage": None}
        lines = rr._render_correction_rate_point(
            self._gate_with_point_week(point_week), correction_rate,
        )
        text = "\n".join(lines)
        assert "版混在のため単一値なし" in text
        assert "28c25437f34a" in text
