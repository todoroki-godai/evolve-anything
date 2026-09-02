#!/usr/bin/env python3
"""correction_rate のテスト — ADR-054 §7.2.1 柱3(a)「指摘率」（read 時 3ストア join）。

設計正典: docs/decisions/drafts/054-c-a-numerator.md（codex 2巡 + tacchi 1巡・全 [Must] 反映済み）。

3ストア（utterances.db / correction_judged.jsonl / weak_signals.jsonl）を read 時に join し、
週次の「指摘率」（judge が判定した発話のうち TP と判定された割合）を決定論算出する。
新ストアなし・LLM 非依存・read-only。

各テストは ``compute_weekly_correction_rate`` に生データを直接注入する（DuckDB/jsonl I/O は
別途 dry-run で実測する。ここでは純粋関数としての集計ロジックのみを検証）。
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_lib_dir = Path(__file__).resolve().parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

import correction_rate  # noqa: E402


# ── #466: 分母フィルタの既定 tracked slug をテスト全体で固定する ──────────
# 実 fleet-config.json（tracked_projects 未指定時の production 既定）を読ませないため、
# tracked_projects を明示指定しない全既存テストの pj_slug（"evolve-anything" /
# "tiny-pj" / "big-pj"）を常に tracked 扱いにする。個別テストが tracked_projects を
# 明示すれば本フィクスチャを経由せず本来の解決（bare 文字列 → basename）が動く。
_DEFAULT_TEST_TRACKED_SLUGS = {"evolve-anything", "tiny-pj", "big-pj"}


@pytest.fixture(autouse=True)
def _default_tracked_slugs(monkeypatch):
    original = correction_rate._resolve_tracked_slugs

    def _fake(tracked_projects):
        if tracked_projects is None:
            return set(_DEFAULT_TEST_TRACKED_SLUGS)
        return original(tracked_projects)

    monkeypatch.setattr(correction_rate, "_resolve_tracked_slugs", _fake)


# ── 週境界ヘルパー ──────────────────────────────────────────────────


class TestWeekBounds:
    def test_week_id_for_monday_start(self):
        dt = datetime(2026, 8, 17, 0, 0, 0, tzinfo=timezone.utc)  # 2026-08-17 は月曜
        assert correction_rate.week_id_for(dt) == "2026-W34"

    def test_week_id_for_sunday_end(self):
        dt = datetime(2026, 8, 23, 23, 59, 59, tzinfo=timezone.utc)  # 同じ週の日曜
        assert correction_rate.week_id_for(dt) == "2026-W34"

    def test_week_bounds_start_end(self):
        start, end = correction_rate.week_bounds("2026-W34")
        assert start == datetime(2026, 8, 17, tzinfo=timezone.utc)
        assert end == datetime(2026, 8, 24, tzinfo=timezone.utc)
        assert end - start == timedelta(days=7)

    def test_next_week_id_simple(self):
        assert correction_rate._next_week_id("2026-W34") == "2026-W35"

    def test_next_week_id_year_boundary(self):
        # 2025-W52 の次は 2026-W01（年境界をまたぐ）
        nxt = correction_rate._next_week_id("2025-W52")
        assert nxt in ("2026-W01", "2025-W53")  # ISO 週数は年により52/53週
        # ラウンドトリップ: 実際に7日進めた日付の week_id と一致すること
        start, _ = correction_rate.week_bounds("2025-W52")
        expected = correction_rate.week_id_for(start + timedelta(days=7))
        assert nxt == expected


# ── ISO8601 パース ──────────────────────────────────────────────────


class TestParseIso:
    def test_z_and_offset_same_instant(self):
        a = correction_rate._parse_iso("2026-08-17T12:00:00Z")
        b = correction_rate._parse_iso("2026-08-17T12:00:00+00:00")
        assert a == b

    def test_naive_treated_as_utc(self):
        dt = correction_rate._parse_iso("2026-08-17T12:00:00")
        assert dt.tzinfo is not None
        assert dt == datetime(2026, 8, 17, 12, tzinfo=timezone.utc)

    def test_unparseable_returns_none(self):
        assert correction_rate._parse_iso("not-a-date") is None
        assert correction_rate._parse_iso(None) is None
        assert correction_rate._parse_iso("") is None


# ── compute_weekly_correction_rate ─────────────────────────────────


_W34_START = datetime(2026, 8, 17, tzinfo=timezone.utc)
_W34_END = datetime(2026, 8, 24, tzinfo=timezone.utc)
_W34_CUTOFF = _W34_END + timedelta(days=correction_rate.FREEZE_DELAY_DAYS)  # 08-27
_AFTER_CUTOFF = _W34_CUTOFF + timedelta(hours=1)
_BEFORE_CUTOFF = _W34_CUTOFF - timedelta(hours=1)


def _utt(key_suffix, *, ts=None, ingested_at=None, pj_slug="evolve-anything", source_kind="dialogue"):
    return {
        "source_path": f"/tmp/session-{key_suffix}.jsonl",
        "line_no": 1,
        "pj_slug": pj_slug,
        "session_id": f"sess-{key_suffix}",
        "timestamp": (ts or (_W34_START + timedelta(hours=1))).isoformat(),
        "text": f"utterance {key_suffix}",
        "text_hash": f"hash-{key_suffix}",
        "prev_action": None,
        "source_kind": source_kind,
        "extractor_version": 3,
        "ingested_at": (ingested_at or (_W34_START + timedelta(hours=2))).isoformat(),
    }


def _key(u):
    return f"{u['source_path']}:{u['line_no']}"


def _judged(u, *, judged_at=None):
    return {"key": _key(u), "judged_at": (judged_at or _BEFORE_CUTOFF).isoformat()}


def _tp(u, *, detected_at=None, session_id=None, pj_slug=None, reason="test", category=None):
    return {
        "channel": "llm_judge",
        "provenance": {
            "source_path": u["source_path"],
            "line_no": u["line_no"],
            "text": u["text"],
            "reason": reason,
            "idiom": "",
            "category": category,
        },
        "detected_at": (detected_at or _BEFORE_CUTOFF).isoformat(),
        "session_id": session_id if session_id is not None else u["session_id"],
        "pj_slug": pj_slug if pj_slug is not None else u["pj_slug"],
    }


def _raw(utterances, judged, weak_signals):
    return {"utterances": utterances, "judged": judged, "weak_signals": weak_signals}


class TestComputeWeeklyCorrectionRateBasic:
    def test_full_coverage_one_tp(self):
        u1, u2 = _utt("a"), _utt("b")
        raw = _raw(
            [u1, u2],
            [_judged(u1), _judged(u2)],
            [_tp(u1)],
        )
        result = correction_rate.compute_weekly_correction_rate(now=_AFTER_CUTOFF, raw=raw)
        weeks = {w["week_id"]: w for w in result["weeks"]}
        w = weeks["2026-W34"]
        assert w["total_population"] == 2
        assert w["judged_count"] == 2
        assert w["tp_count"] == 1
        assert w["coverage"] == 1.0
        assert w["measured"] is True
        assert w["rate"] == pytest.approx(0.5)

    def test_in_progress_week_excluded_entirely(self):
        """cutoff 未到達（週終了+D日 前）の週は候補にすら現れない。"""
        u1 = _utt("a")
        raw = _raw([u1], [_judged(u1)], [])
        result = correction_rate.compute_weekly_correction_rate(now=_BEFORE_CUTOFF, raw=raw)
        assert "2026-W34" not in {w["week_id"] for w in result["weeks"]}

    def test_partial_coverage_not_measured(self):
        u1, u2 = _utt("a"), _utt("b")
        raw = _raw([u1, u2], [_judged(u1)], [])  # u2 未判定
        result = correction_rate.compute_weekly_correction_rate(now=_AFTER_CUTOFF, raw=raw)
        w = next(w for w in result["weeks"] if w["week_id"] == "2026-W34")
        assert w["coverage"] == pytest.approx(0.5)
        assert w["measured"] is False
        assert w["rate"] is None


class TestFreezeCutoff:
    def test_delayed_ingest_drops_from_population(self):
        """ingested_at が cutoff 後 → 分母から除外される（遅延 ingest は確定後に分母を増やさない）。"""
        u_late = _utt("late", ingested_at=_AFTER_CUTOFF + timedelta(days=1))
        u_ok = _utt("ok")
        raw = _raw([u_late, u_ok], [_judged(u_ok)], [])
        result = correction_rate.compute_weekly_correction_rate(now=_AFTER_CUTOFF, raw=raw)
        w = next(w for w in result["weeks"] if w["week_id"] == "2026-W34")
        assert w["total_population"] == 1
        assert w["judged_count"] == 1
        assert w["measured"] is True

    def test_judged_at_after_cutoff_not_counted(self):
        u1 = _utt("a")
        raw = _raw([u1], [_judged(u1, judged_at=_AFTER_CUTOFF + timedelta(days=1))], [])
        result = correction_rate.compute_weekly_correction_rate(now=_AFTER_CUTOFF, raw=raw)
        w = next(w for w in result["weeks"] if w["week_id"] == "2026-W34")
        assert w["total_population"] == 1
        assert w["judged_count"] == 0
        assert w["measured"] is False

    def test_coverage_gap_reason_separates_late_unjudged_and_unclassified(self):
        """未確定分を締切超過・未判定・分類不能へ排他的に分け、合計を固定する。"""
        u_on_time = _utt("on-time")
        u_late = _utt("late")
        u_late_2 = _utt("late-2")
        u_missing = _utt("missing")
        u_invalid = _utt("invalid")
        raw = _raw(
            [u_on_time, u_late, u_late_2, u_missing, u_invalid],
            [
                _judged(u_on_time, judged_at=_W34_CUTOFF),
                _judged(u_late, judged_at=_W34_CUTOFF + timedelta(microseconds=1)),
                _judged(u_late_2, judged_at=_W34_CUTOFF + timedelta(days=1)),
                {"key": _key(u_invalid), "judged_at": "not-a-date"},
            ],
            [],
        )

        result = correction_rate.compute_weekly_correction_rate(now=_AFTER_CUTOFF, raw=raw)
        w = next(w for w in result["weeks"] if w["week_id"] == "2026-W34")

        assert w["judged_count"] == 1  # cutoff ちょうどは確定内（境界は >）
        assert w["coverage_gap_reason"] == {
            "measured": True,
            "deadline_exceeded_count": 2,
            "unjudged_count": 1,
            "unclassified_count": 1,
            "reason": None,
        }
        reason = w["coverage_gap_reason"]
        assert (
            reason["deadline_exceeded_count"]
            + reason["unjudged_count"]
            + reason["unclassified_count"]
            == w["total_population"] - w["judged_count"]
        )

    def test_coverage_gap_reason_is_unmeasured_when_judged_source_unavailable(self):
        """judged reader 取得不能を未判定0件/全件へ化けさせない。"""
        u1 = _utt("a")
        raw = _raw([u1], [], [])
        raw["_measurement_health"] = {
            "judged": {"measured": False, "reason": "読取失敗", "dropped_lines": 0},
        }

        result = correction_rate.compute_weekly_correction_rate(now=_AFTER_CUTOFF, raw=raw)
        w = next(w for w in result["weeks"] if w["week_id"] == "2026-W34")

        assert w["coverage_gap_reason"] == {
            "measured": False,
            "deadline_exceeded_count": None,
            "unjudged_count": None,
            "unclassified_count": None,
            "reason": "読取失敗",
        }

    def test_coverage_gap_reason_preserves_judged_source_failure_reason(self):
        """部分読取失敗の欠落行数を固定文言で潰さず、そのまま表示層へ運ぶ。"""
        u1 = _utt("a")
        raw = _raw([u1], [], [])
        raw["_measurement_health"] = {
            "judged": {
                "measured": False,
                "reason": "不正な JSON を 2 行除外しました",
                "dropped_lines": 2,
            },
        }

        result = correction_rate.compute_weekly_correction_rate(now=_AFTER_CUTOFF, raw=raw)
        w = next(w for w in result["weeks"] if w["week_id"] == "2026-W34")

        assert w["coverage_gap_reason"]["reason"] == "不正な JSON を 2 行除外しました"

    def test_coverage_gap_invariant_mismatch_is_explicitly_unmeasured(self):
        """内訳合計と母集団−判定済が不一致なら、成功扱いせず分類不能を surface する。"""
        breakdown = correction_rate._coverage_gap_reason(
            population_keys=["a", "b"],
            judged_key_set={"a"},
            judged_at_by_key={"a": _BEFORE_CUTOFF},
            judged_record_keys={"a"},
            cutoff=_W34_CUTOFF,
            expected_gap_count=2,  # 実際の未確定集合は b の1件。意図的な配線不整合。
            judged_source_measured=True,
        )

        assert breakdown["measured"] is False
        assert breakdown["reason"] == "内訳合計が母集団と一致しません（1/2 件）"

    def test_coverage_gap_deadline_boundary_is_strictly_after_cutoff(self):
        """締切ちょうどを超過へ含めない（内訳境界の > を >= に広げない）。"""
        breakdown = correction_rate._coverage_gap_reason(
            population_keys=["at-cutoff"],
            judged_key_set=set(),  # 配線不整合を注入し、内訳関数自身の境界を直接通す
            judged_at_by_key={"at-cutoff": _W34_CUTOFF},
            judged_record_keys={"at-cutoff"},
            cutoff=_W34_CUTOFF,
            expected_gap_count=1,
            judged_source_measured=True,
        )

        assert breakdown["deadline_exceeded_count"] == 0
        assert breakdown["measured"] is False

    def test_tp_detected_after_cutoff_not_counted_but_week_can_still_be_measured(self):
        u1 = _utt("a")
        raw = _raw(
            [u1],
            [_judged(u1)],
            [_tp(u1, detected_at=_AFTER_CUTOFF + timedelta(days=1))],
        )
        result = correction_rate.compute_weekly_correction_rate(now=_AFTER_CUTOFF, raw=raw)
        w = next(w for w in result["weeks"] if w["week_id"] == "2026-W34")
        assert w["judged_count"] == 1
        assert w["tp_count"] == 0
        assert w["measured"] is True
        assert w["rate"] == 0.0

    def test_earliest_judged_at_wins_on_duplicate(self):
        u1 = _utt("a")
        raw = _raw(
            [u1],
            [
                _judged(u1, judged_at=_BEFORE_CUTOFF - timedelta(hours=5)),
                _judged(u1, judged_at=_BEFORE_CUTOFF),
            ],
            [],
        )
        result = correction_rate.compute_weekly_correction_rate(now=_AFTER_CUTOFF, raw=raw)
        w = next(w for w in result["weeks"] if w["week_id"] == "2026-W34")
        assert w["judged_count"] == 1
        assert w["measured"] is True


class TestValidationFailures:
    def test_tp_conflict_marks_week_unmeasured(self):
        """同一物理キーに相反する（session_id が食い違う）TP 記録 → 集計失敗として非表示。"""
        u1 = _utt("a")
        raw = _raw(
            [u1],
            [_judged(u1)],
            [_tp(u1, session_id="sess-a"), _tp(u1, session_id="sess-OTHER")],
        )
        result = correction_rate.compute_weekly_correction_rate(now=_AFTER_CUTOFF, raw=raw)
        w = next(w for w in result["weeks"] if w["week_id"] == "2026-W34")
        assert w["measured"] is False
        assert "tp_conflict" in w["failure_reasons"]
        assert result["diagnostics"]["conflict_keys"] >= 1

    def test_tp_without_judged_record_marks_week_unmeasured(self):
        u1 = _utt("a")
        raw = _raw([u1], [], [_tp(u1)])  # 判定記録なしに TP だけ存在＝分子 ⊆ 分母 違反
        result = correction_rate.compute_weekly_correction_rate(now=_AFTER_CUTOFF, raw=raw)
        w = next(w for w in result["weeks"] if w["week_id"] == "2026-W34")
        assert w["measured"] is False
        assert "tp_without_judged_record" in w["failure_reasons"]


class TestDiagnosticsSurfacing:
    def test_judged_missing_key_counted(self):
        u1 = _utt("a")
        raw = _raw(
            [u1],
            [_judged(u1), {"billed_attempt": True, "judged_at": _BEFORE_CUTOFF.isoformat(), "est_tokens": 100}],
            [],
        )
        result = correction_rate.compute_weekly_correction_rate(now=_AFTER_CUTOFF, raw=raw)
        assert result["diagnostics"]["judged_missing_key"] == 1

    def test_orphan_tp_no_utterance_counted(self):
        """TP の物理キーが dialogue 母集団に存在しない（long_paste/subagents 等）→ surface。"""
        phantom = _utt("phantom")
        raw = _raw([], [], [_tp(phantom)])
        result = correction_rate.compute_weekly_correction_rate(now=_AFTER_CUTOFF, raw=raw)
        assert result["diagnostics"]["orphan_tp_no_utterance"] == 1

    def test_unparseable_judged_at_counted(self):
        u1 = _utt("a")
        raw = _raw([u1], [{"key": _key(u1), "judged_at": "garbage"}], [])
        result = correction_rate.compute_weekly_correction_rate(now=_AFTER_CUTOFF, raw=raw)
        assert result["diagnostics"]["judged_unparseable_judged_at"] == 1


class TestPjBreakdown:
    def test_rate_hidden_below_floor_but_counts_present(self):
        u1 = _utt("a", pj_slug="tiny-pj")
        raw = _raw([u1], [_judged(u1)], [_tp(u1)])
        result = correction_rate.compute_weekly_correction_rate(now=_AFTER_CUTOFF, raw=raw)
        w = next(w for w in result["weeks"] if w["week_id"] == "2026-W34")
        pj = w["pj_breakdown"]["tiny-pj"]
        assert pj["judged"] == 1
        assert pj["tp"] == 1
        assert pj["rate"] is None  # 分母1桁は非表示

    def test_rate_shown_at_or_above_floor(self):
        utts = [_utt(f"a{i}", pj_slug="big-pj") for i in range(10)]
        judged = [_judged(u) for u in utts]
        tps = [_tp(utts[0])]
        raw = _raw(utts, judged, tps)
        result = correction_rate.compute_weekly_correction_rate(now=_AFTER_CUTOFF, raw=raw)
        w = next(w for w in result["weeks"] if w["week_id"] == "2026-W34")
        pj = w["pj_breakdown"]["big-pj"]
        assert pj["judged"] == 10
        assert pj["rate"] == pytest.approx(0.1)


# ── #400 A5: カテゴリ内訳（設計 §2.6） ─────────────────────────────


class TestCategoryBreakdown:
    def test_counts_by_category_physical_key_unit(self):
        u1, u2 = _utt("a"), _utt("b")
        raw = _raw(
            [u1, u2],
            [_judged(u1), _judged(u2)],
            [_tp(u1, category="factual"), _tp(u2, category="presentation")],
        )
        result = correction_rate.compute_weekly_correction_rate(now=_AFTER_CUTOFF, raw=raw)
        w = next(w for w in result["weeks"] if w["week_id"] == "2026-W34")
        cb = w["category_breakdown"]
        assert cb["measured"] is True
        assert cb["counts"] == {"factual": 1, "presentation": 1}
        assert cb["unclassified_count"] == 0
        assert cb["conflict_keys"] == 0

    def test_same_key_same_category_counted_once(self):
        """同一 physical key に同一カテゴリの重複 TP 記録があっても1件として数える
        （§2.6 末尾: 内訳も physical key 単位で数える。3重昇格の実測を踏まえた回帰防止）。
        """
        u1 = _utt("a")
        raw = _raw(
            [u1],
            [_judged(u1)],
            [
                _tp(u1, category="factual", detected_at=_BEFORE_CUTOFF - timedelta(hours=1)),
                _tp(u1, category="factual", detected_at=_BEFORE_CUTOFF),
            ],
        )
        result = correction_rate.compute_weekly_correction_rate(now=_AFTER_CUTOFF, raw=raw)
        w = next(w for w in result["weeks"] if w["week_id"] == "2026-W34")
        assert w["category_breakdown"]["counts"] == {"factual": 1}

    def test_unclassified_when_category_missing(self):
        """category を持たない（旧プロンプト・A5 以前の）TP 記録は unclassified に計上し、
        conflict 扱いにはしない。
        """
        u1 = _utt("a")
        raw = _raw([u1], [_judged(u1)], [_tp(u1, category=None)])
        result = correction_rate.compute_weekly_correction_rate(now=_AFTER_CUTOFF, raw=raw)
        w = next(w for w in result["weeks"] if w["week_id"] == "2026-W34")
        cb = w["category_breakdown"]
        assert cb["measured"] is True
        assert cb["counts"] == {}
        assert cb["unclassified_count"] == 1

    def test_conflicting_categories_excludes_key_and_marks_unmeasured(self):
        """設計 §2.4/§2.6: 同一 physical key に複数 category が付いたら、黙って多数決・
        最新値を採らず当該週を未測定にする。
        """
        u1 = _utt("a")
        raw = _raw(
            [u1],
            [_judged(u1)],
            [
                _tp(u1, category="factual", detected_at=_BEFORE_CUTOFF - timedelta(hours=1)),
                _tp(u1, category="presentation", detected_at=_BEFORE_CUTOFF),
            ],
        )
        result = correction_rate.compute_weekly_correction_rate(now=_AFTER_CUTOFF, raw=raw)
        w = next(w for w in result["weeks"] if w["week_id"] == "2026-W34")
        cb = w["category_breakdown"]
        assert cb["measured"] is False
        assert cb["conflict_keys"] == 1
        assert cb["counts"] == {}

    def test_top_category_and_example_surfaced(self):
        u1, u2, u3 = _utt("a"), _utt("b"), _utt("c")
        raw = _raw(
            [u1, u2, u3],
            [_judged(u1), _judged(u2), _judged(u3)],
            [
                _tp(u1, category="presentation", reason="見た目の指摘"),
                _tp(u2, category="presentation", reason="別の見た目指摘",
                    detected_at=_BEFORE_CUTOFF - timedelta(hours=1)),
                _tp(u3, category="factual"),
            ],
        )
        result = correction_rate.compute_weekly_correction_rate(now=_AFTER_CUTOFF, raw=raw)
        w = next(w for w in result["weeks"] if w["week_id"] == "2026-W34")
        cb = w["category_breakdown"]
        assert cb["counts"] == {"presentation": 2, "factual": 1}
        assert cb["top_category"] == "presentation"
        assert cb["top_category_example"]["reason"] == "見た目の指摘"  # 最新（detected_at が新しい方）

    def test_no_tp_yields_empty_measured_breakdown(self):
        u1 = _utt("a")
        raw = _raw([u1], [_judged(u1)], [])
        result = correction_rate.compute_weekly_correction_rate(now=_AFTER_CUTOFF, raw=raw)
        w = next(w for w in result["weeks"] if w["week_id"] == "2026-W34")
        cb = w["category_breakdown"]
        assert cb["measured"] is True
        assert cb["counts"] == {}
        assert cb["top_category"] is None


class TestSourceKindAndSidechainExclusion:
    def test_non_dialogue_source_kind_is_caller_responsibility(self):
        """compute_weekly_correction_rate は渡された utterances をそのまま母集団にする
        （dialogue-only 絞り込みは query 層＝collect_raw_data の責務）。"""
        u1 = _utt("a", source_kind="long_paste")
        raw = _raw([u1], [_judged(u1)], [])
        result = correction_rate.compute_weekly_correction_rate(now=_AFTER_CUTOFF, raw=raw)
        w = next(w for w in result["weeks"] if w["week_id"] == "2026-W34")
        assert w["total_population"] == 1  # フィルタしない＝呼び出し側の契約


# ── #466: 分母フィルタ（tracked PJ + 90日 cutoff + ホーム起動除外） ─────────


class TestDenominatorPopulationFilters:
    """分母を judge_runner の判定母集団と揃える（tracked外 PJ の発話が永久に判定されず
    カバレッジが 100% に到達しない問題の修正）。除外は3種別を排他的に集計し、0件でも
    必ず diagnostics に出す（silence != evaluated）。
    """

    def test_untracked_pj_excluded_from_population_and_counted(self):
        u_tracked = _utt("a", pj_slug="evolve-anything")
        u_untracked = _utt("b", pj_slug="some-other-pj")
        raw = _raw([u_tracked, u_untracked], [_judged(u_tracked), _judged(u_untracked)], [])
        result = correction_rate.compute_weekly_correction_rate(now=_AFTER_CUTOFF, raw=raw)
        w = next(w for w in result["weeks"] if w["week_id"] == "2026-W34")
        assert w["total_population"] == 1
        d = result["diagnostics"]
        assert d["excluded_untracked_total"] == 1
        assert d["excluded_before_cutoff_total"] == 0
        assert d["excluded_home_dir_total"] == 0
        assert d["excluded_total"] == 1

    def test_utterance_older_than_max_age_excluded_and_counted(self):
        old_ts = _AFTER_CUTOFF - timedelta(days=correction_rate._JUDGE_MAX_AGE_DAYS_DEFAULT + 10)
        u_old = _utt("old", ts=old_ts, ingested_at=old_ts + timedelta(hours=1))
        u_ok = _utt("ok")
        raw = _raw([u_old, u_ok], [_judged(u_ok)], [])
        result = correction_rate.compute_weekly_correction_rate(now=_AFTER_CUTOFF, raw=raw)
        d = result["diagnostics"]
        assert d["excluded_before_cutoff_total"] == 1
        assert d["excluded_untracked_total"] == 0
        w = next(w for w in result["weeks"] if w["week_id"] == "2026-W34")
        assert w["total_population"] == 1

    def test_home_dir_session_excluded_and_counted_separately_from_untracked(self):
        home_slug = correction_rate._home_pj_slug()
        assert home_slug  # 環境前提: Path.home() が basename を持つこと
        u_home = _utt("home", pj_slug=home_slug)
        u_ok = _utt("ok")
        raw = _raw([u_home, u_ok], [_judged(u_ok)], [])
        result = correction_rate.compute_weekly_correction_rate(now=_AFTER_CUTOFF, raw=raw)
        d = result["diagnostics"]
        assert d["excluded_home_dir_total"] == 1
        # home は tracked外 とは別枠で数える（二重計上しない）。
        assert d["excluded_untracked_total"] == 0
        assert d["excluded_total"] == 1
        w = next(w for w in result["weeks"] if w["week_id"] == "2026-W34")
        assert w["total_population"] == 1

    def test_zero_exclusions_reported_as_explicit_zero(self):
        u1 = _utt("a")
        raw = _raw([u1], [_judged(u1)], [])
        result = correction_rate.compute_weekly_correction_rate(now=_AFTER_CUTOFF, raw=raw)
        d = result["diagnostics"]
        assert d["excluded_untracked_total"] == 0
        assert d["excluded_before_cutoff_total"] == 0
        assert d["excluded_home_dir_total"] == 0
        assert d["excluded_total"] == 0

    def test_explicit_tracked_projects_override_bypasses_default_fixture(self):
        """tracked_projects を明示指定すれば、autouse フィクスチャの既定シードでなく
        本来の解決（bare 文字列 → basename）が動く（DI 契約の確認）。"""
        u1 = _utt("a", pj_slug="only-this-pj")
        raw = _raw([u1], [_judged(u1)], [])
        result = correction_rate.compute_weekly_correction_rate(
            now=_AFTER_CUTOFF, raw=raw, tracked_projects=["only-this-pj"],
        )
        w = next(w for w in result["weeks"] if w["week_id"] == "2026-W34")
        assert w["total_population"] == 1
        assert result["diagnostics"]["excluded_untracked_total"] == 0


# ── 表示ゲート（k=4週連続） ─────────────────────────────────────────


def _measured_week(week_id, rate=0.1):
    return {"week_id": week_id, "measured": True, "rate": rate}


def _unmeasured_week(week_id):
    return {"week_id": week_id, "measured": False, "rate": None}


class TestDisplayGate:
    def test_gate_closed_below_k(self):
        weeks = [_measured_week(f"2026-W{n:02d}") for n in (30, 31, 32)]
        gate = correction_rate.compute_display_gate(weeks)
        assert gate["gate_open"] is False

    def test_gate_open_at_k_consecutive(self):
        weeks = [_measured_week(f"2026-W{n:02d}") for n in (30, 31, 32, 33)]
        gate = correction_rate.compute_display_gate(weeks)
        assert gate["gate_open"] is True
        assert gate["display_start_week"] == "2026-W30"

    def test_run_resets_on_unmeasured_week(self):
        weeks = [
            _measured_week("2026-W20"),
            _measured_week("2026-W21"),
            _unmeasured_week("2026-W22"),
            _measured_week("2026-W23"),
            _measured_week("2026-W24"),
            _measured_week("2026-W25"),
            _measured_week("2026-W26"),
        ]
        gate = correction_rate.compute_display_gate(weeks)
        assert gate["gate_open"] is True
        assert gate["display_start_week"] == "2026-W23"

    def test_run_resets_on_week_id_gap(self):
        """暦週として隣接しない（間に population=0 の週が挟まる）場合も連続とみなさない。"""
        weeks = [
            _measured_week("2026-W20"),
            _measured_week("2026-W21"),
            # W22 が候補に無い（population=0）→ W23 は隣接しない
            _measured_week("2026-W23"),
            _measured_week("2026-W24"),
            _measured_week("2026-W25"),
        ]
        gate = correction_rate.compute_display_gate(weeks)
        assert gate["gate_open"] is False


# ── #508: point_week / current_run_length（点表示専用フィールド） ──────────
#
# gate_open の判定式（best_run>=k）は round2 [Must] により一切変更していない。
# ここでは gate_open とは独立に、点表示に使う week と進捗 n/k が正しいことを固定する。


class TestDisplayGatePointWeek:
    def test_point_week_is_latest_measured_week(self):
        """I9: point_week は weeks 中で week_id が最大の measured=True 週。"""
        weeks = [
            _measured_week("2026-W10"),
            _measured_week("2026-W11"),
        ]
        gate = correction_rate.compute_display_gate(weeks)
        assert gate["point_week"]["week_id"] == "2026-W11"

    def test_point_week_none_when_no_measured_weeks(self):
        weeks = [_unmeasured_week("2026-W10"), _unmeasured_week("2026-W11")]
        gate = correction_rate.compute_display_gate(weeks)
        assert gate["point_week"] is None
        assert gate["current_run_length"] == 0

    def test_point_week_ignores_trailing_unmeasured_week(self):
        """最新候補週が未測定でも、より古い measured 週が point_week になる（I6 の材料）。"""
        weeks = [
            _measured_week("2026-W10"),
            _measured_week("2026-W11"),
            _unmeasured_week("2026-W12"),
        ]
        gate = correction_rate.compute_display_gate(weeks)
        assert gate["point_week"]["week_id"] == "2026-W11"

    def test_point_week_correct_with_unsorted_input(self):
        """§2-1-(c): 呼出契約は昇順だが、非ソート入力でも week_id 最大の measured 週を選ぶ
        （N2: 「最初に見つかった確定週」を選ぶバグを検出する）。"""
        weeks = [
            _measured_week("2026-W12"),
            _measured_week("2026-W08"),
            _measured_week("2026-W10"),
        ]
        gate = correction_rate.compute_display_gate(weeks)
        assert gate["point_week"]["week_id"] == "2026-W12"

    def test_current_run_length_counts_contiguous_run_ending_at_point_week(self):
        """I8: n は point_week で終わる連続 run 長。"""
        weeks = [
            _measured_week("2026-W10"),
            _measured_week("2026-W11"),
            _measured_week("2026-W12"),
        ]
        gate = correction_rate.compute_display_gate(weeks)
        assert gate["gate_open"] is False  # k=4 未満
        assert gate["point_week"]["week_id"] == "2026-W12"
        assert gate["current_run_length"] == 3

    def test_current_run_length_resets_on_gap_before_point_week(self):
        weeks = [
            _measured_week("2026-W08"),
            # W09/W10 が候補に無い → W11 は隣接しない
            _measured_week("2026-W11"),
            _measured_week("2026-W12"),
        ]
        gate = correction_rate.compute_display_gate(weeks)
        assert gate["current_run_length"] == 2

    def test_current_run_length_not_inflated_by_noncontiguous_total(self):
        """I8: 非連続な確定週が k=4 件以上あっても n は run 長を超えない
        （N4: n を「確定週の総数」で算出するバグを検出する）。"""
        weeks = [
            _measured_week("2026-W01"),
            _measured_week("2026-W03"),
            _measured_week("2026-W05"),
            _measured_week("2026-W07"),
            _measured_week("2026-W09"),
        ]
        gate = correction_rate.compute_display_gate(weeks)
        assert gate["gate_open"] is False
        assert gate["current_run_length"] == 1
        assert gate["current_run_length"] <= gate["required"]

    def test_current_run_length_boundary_k_minus_one(self):
        """境界値: 確定週が k-1=3 件ちょうど連続 → gate は閉じたまま n=3。"""
        weeks = [_measured_week(f"2026-W{n:02d}") for n in (20, 21, 22)]
        gate = correction_rate.compute_display_gate(weeks)
        assert gate["gate_open"] is False
        assert gate["current_run_length"] == 3

    def test_point_week_and_run_length_span_iso_year_boundary(self):
        """表現差: week_id の年跨ぎ（2025-W52 → 2026-W01。2025年はISO週52週）でも連続と
        判定する（_next_week_id の ISO 週年境界処理に依存。素朴な文字列 +1 だと壊れる。
        test_next_week_id_year_boundary と同じ境界を使う）。"""
        weeks = [_measured_week("2025-W52"), _measured_week("2026-W01")]
        gate = correction_rate.compute_display_gate(weeks)
        assert gate["point_week"]["week_id"] == "2026-W01"
        assert gate["current_run_length"] == 2

    def test_point_week_and_run_length_correct_with_unsorted_input(self):
        """§2-1-(c): current_run_length も非ソート入力で正しく出る。"""
        weeks = [
            _measured_week("2026-W12"),
            _measured_week("2026-W10"),
            _measured_week("2026-W11"),
        ]
        gate = correction_rate.compute_display_gate(weeks)
        assert gate["point_week"]["week_id"] == "2026-W12"
        assert gate["current_run_length"] == 3


# ── build_correction_rate_summary（表示用の集約） ───────────────────


class TestBuildCorrectionRateSummary:
    def test_gate_closed_returns_latest_coverage_only(self):
        u1, u2 = _utt("a"), _utt("b")
        raw = _raw([u1, u2], [_judged(u1)], [])
        summary = correction_rate.build_correction_rate_summary(now=_AFTER_CUTOFF, raw=raw)
        assert summary["gate"]["gate_open"] is False
        assert summary["displayed_weeks"] == []
        assert summary["latest_coverage"] == {
            "week_id": "2026-W34", "judged": 1, "total": 2, "failure_reasons": [],
        }
        assert summary["coverage_gaps"] == [{
            "week_id": "2026-W34",
            "judged": 1,
            "total": 2,
            "reason": {
                "measured": True,
                "deadline_exceeded_count": 0,
                "unjudged_count": 1,
                "unclassified_count": 0,
                "reason": None,
            },
        }]

    def test_unavailable_utterances_make_coverage_gaps_unavailable(self):
        """utterances の部分読取失敗を、読めた行だけの正常な週別件数へ化けさせない。"""
        u1 = _utt("a")
        raw = _raw([u1], [_judged(u1)], [])
        raw["_measurement_health"] = {
            "utterances": {
                "measured": False,
                "reason": "不正な行を 1 行除外しました",
                "dropped_lines": 1,
            },
        }

        summary = correction_rate.build_correction_rate_summary(now=_AFTER_CUTOFF, raw=raw)

        assert summary["coverage_gaps"] is None

    def test_zero_population_week_is_not_listed_as_coverage_gap(self, monkeypatch):
        """母集団0件の確定週を「不足0件」の理由行として列挙しない。"""
        weeks = [{
            "week_id": "2026-W26", "measured": False, "rate": None,
            "judged_count": 0, "tp_count": 0, "total_population": 0, "coverage": 0.0,
            "pj_breakdown": {}, "top3_examples": [], "failure_reasons": [],
            "coverage_gap_reason": {
                "measured": True, "deadline_exceeded_count": 0,
                "unjudged_count": 0, "unclassified_count": 0, "reason": None,
            },
        }]
        monkeypatch.setattr(
            correction_rate,
            "compute_weekly_correction_rate",
            lambda **_kwargs: {
                "weeks": weeks, "diagnostics": {}, "generated_at": _AFTER_CUTOFF.isoformat(),
            },
        )

        summary = correction_rate.build_correction_rate_summary(now=_AFTER_CUTOFF, raw={})

        assert summary["coverage_gaps"] == []

    def test_gate_open_lists_measured_weeks_with_worsening_flag(self):
        # 4週分の finalized データを直接 weeks 相当で組み立てるのは大掛かりなので、
        # compute_weekly_correction_rate の出力を模して gate ロジックとの結線だけ確認する。
        import correction_rate as cr

        weeks = [
            {"week_id": "2026-W10", "measured": True, "rate": 0.1, "judged_count": 10,
             "tp_count": 1, "total_population": 10, "coverage": 1.0,
             "pj_breakdown": {}, "top3_examples": [], "failure_reasons": []},
            {"week_id": "2026-W11", "measured": True, "rate": 0.2, "judged_count": 10,
             "tp_count": 2, "total_population": 10, "coverage": 1.0,
             "pj_breakdown": {}, "top3_examples": [{"text": "x"}], "failure_reasons": []},
            {"week_id": "2026-W12", "measured": True, "rate": 0.1, "judged_count": 10,
             "tp_count": 1, "total_population": 10, "coverage": 1.0,
             "pj_breakdown": {}, "top3_examples": [], "failure_reasons": []},
            {"week_id": "2026-W13", "measured": True, "rate": 0.05, "judged_count": 10,
             "tp_count": 0, "total_population": 10, "coverage": 1.0,
             "pj_breakdown": {}, "top3_examples": [], "failure_reasons": []},
        ]

        def _fake_compute(*, now=None, raw=None, **_ignored):
            return {"weeks": weeks, "diagnostics": {}, "generated_at": now.isoformat()}

        orig = cr.compute_weekly_correction_rate
        cr.compute_weekly_correction_rate = _fake_compute
        try:
            summary = cr.build_correction_rate_summary(now=_AFTER_CUTOFF, raw={})
        finally:
            cr.compute_weekly_correction_rate = orig

        assert summary["gate"]["gate_open"] is True
        displayed = summary["displayed_weeks"]
        assert [w["week_id"] for w in displayed] == [
            "2026-W10", "2026-W11", "2026-W12", "2026-W13",
        ]
        # W10: 最初の表示週は比較対象なし → 悪化フラグなし
        assert displayed[0]["is_worsening"] is False
        # W11: 0.1 → 0.2 に悪化（増加）
        assert displayed[1]["is_worsening"] is True
        assert displayed[1]["top3_examples"] == [{"text": "x"}]
        # W12: 0.2 → 0.1 は改善（悪化ではない）→ top3 は削られる
        assert displayed[2]["is_worsening"] is False
        assert "top3_examples" not in displayed[2] or displayed[2]["top3_examples"] == []
