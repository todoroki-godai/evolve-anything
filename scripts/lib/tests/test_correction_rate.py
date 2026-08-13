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


def _tp(u, *, detected_at=None, session_id=None, pj_slug=None, reason="test"):
    return {
        "channel": "llm_judge",
        "provenance": {
            "source_path": u["source_path"],
            "line_no": u["line_no"],
            "text": u["text"],
            "reason": reason,
            "idiom": "",
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


class TestSourceKindAndSidechainExclusion:
    def test_non_dialogue_source_kind_is_caller_responsibility(self):
        """compute_weekly_correction_rate は渡された utterances をそのまま母集団にする
        （dialogue-only 絞り込みは query 層＝collect_raw_data の責務）。"""
        u1 = _utt("a", source_kind="long_paste")
        raw = _raw([u1], [_judged(u1)], [])
        result = correction_rate.compute_weekly_correction_rate(now=_AFTER_CUTOFF, raw=raw)
        w = next(w for w in result["weeks"] if w["week_id"] == "2026-W34")
        assert w["total_population"] == 1  # フィルタしない＝呼び出し側の契約


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


# ── build_correction_rate_summary（表示用の集約） ───────────────────


class TestBuildCorrectionRateSummary:
    def test_gate_closed_returns_latest_coverage_only(self):
        u1, u2 = _utt("a"), _utt("b")
        raw = _raw([u1, u2], [_judged(u1)], [])
        summary = correction_rate.build_correction_rate_summary(now=_AFTER_CUTOFF, raw=raw)
        assert summary["gate"]["gate_open"] is False
        assert summary["displayed_weeks"] == []
        assert summary["latest_coverage"] == {
            "week_id": "2026-W34", "judged": 1, "total": 2,
        }

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

        def _fake_compute(*, now=None, raw=None):
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
