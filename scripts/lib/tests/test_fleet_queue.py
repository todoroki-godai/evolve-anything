#!/usr/bin/env python3
"""fleet queue（#79 Phase 1a）のテスト — 学習素材ベースの evolve 待ち列挙。

決定論・LLM 非依存。検証対象:
  - ``select_evolve_queue`` 純関数: 閾値境界 / weak のみ / corr のみ / 合算 /
    state 不在=初回全件 / 列挙理由
  - weak_signals 未処理カウントの PJ 別集計（promoted 除外・expired 除外・pj_slug スコープ）
  - 前回 evolve 以降の corrections カウント（project_path スコープ・timestamp フィルタ）
  - per-PJ last_evolve state の read/write（store_write barrier 経由・dry-run 非書込）
  - ``queue`` CLI の --json schema（Phase 1b #80 が読む共有契約）
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_test_dir = Path(__file__).resolve().parent
_lib_dir = _test_dir.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

from fleet import queue as fq  # noqa: E402
from fleet import queue_state as qs  # noqa: E402


# --- select_evolve_queue 純関数 ----------------------------------------------


def _material(slug, weak=0, corr=0, last=None, subagents=0, sessions=0, backlog=0):
    """テスト用の per-PJ material dict を組み立てる。"""
    return {
        "pj_slug": slug,
        "weak_unprocessed": weak,
        "new_corrections": corr,
        "last_evolve_at": last,
        "activity_since": {"subagents": subagents, "sessions": sessions},
        "correction_backlog": backlog,
    }


class TestSelectEvolveQueue:
    def test_below_threshold_with_correction_backlog_is_included(self):
        """#515: 在庫は1件でも永久滞留させず queue へ載せる。"""
        mats = [_material("dormant", weak=0, corr=0, backlog=1)]
        out = fq.select_evolve_queue(mats, threshold=5)
        assert [m["pj_slug"] for m in out] == ["dormant"]
        assert out[0]["material_count"] == 0
        assert out[0]["correction_backlog"] == 1
        assert out[0]["reason"] == "反映待ち在庫 1 件 / material=0 < 5"

    def test_correction_backlog_does_not_change_material_count_or_sort_order(self):
        """在庫は既存 material_count に足さず、閾値以上の通常候補を先に保つ。"""
        mats = [
            _material("dormant", backlog=99),
            _material("active", weak=5),
        ]
        out = fq.select_evolve_queue(mats, threshold=5)
        assert [m["pj_slug"] for m in out] == ["active", "dormant"]
        assert [m["material_count"] for m in out] == [5, 0]

    def test_threshold_boundary_includes_equal(self):
        """material_count == threshold は待ち（>= 比較）。"""
        mats = [_material("a", weak=3, corr=0)]
        out = fq.select_evolve_queue(mats, threshold=3)
        assert [m["pj_slug"] for m in out] == ["a"]

    def test_below_threshold_excluded(self):
        mats = [_material("a", weak=2, corr=0)]
        out = fq.select_evolve_queue(mats, threshold=3)
        assert out == []

    def test_weak_only(self):
        mats = [_material("a", weak=5, corr=0)]
        out = fq.select_evolve_queue(mats, threshold=3)
        assert len(out) == 1
        assert out[0]["material_count"] == 5

    def test_corr_only(self):
        mats = [_material("a", weak=0, corr=4)]
        out = fq.select_evolve_queue(mats, threshold=3)
        assert len(out) == 1
        assert out[0]["material_count"] == 4

    def test_combined_sum(self):
        """material_count = weak_unprocessed + new_corrections。"""
        mats = [_material("a", weak=2, corr=2)]
        out = fq.select_evolve_queue(mats, threshold=3)
        assert out[0]["material_count"] == 4

    def test_reason_string_describes_breakdown(self):
        """drain 済（last_evolve_at あり）は『new corr』表記（前回 evolve 以降の増分）。"""
        mats = [_material("a", weak=7, corr=2, last="2026-06-01T00:00:00+00:00")]
        out = fq.select_evolve_queue(mats, threshold=3)
        assert out[0]["reason"] == "weak=7 + new corr=2 >= 3"

    def test_reason_coldstart_marks_all_unprocessed(self):
        """last_evolve_at=None（初回）は corr 全件計上が一目で分かる業務語にする（#92→A）。

        never なのに『new corr』だと「一度も evolve してないのに前回以降の新規 corr」が
        矛盾に見える。`未 drain` は emit→drain 2 相の内部 plumbing 用語なので、毎朝 queue を
        叩くだけの利用者に意味を要求しないよう `初回・全件` の業務語へ落とす（tacchi ①）。
        """
        mats = [_material("a", weak=7, corr=2, last=None)]
        out = fq.select_evolve_queue(mats, threshold=3)
        assert out[0]["reason"] == "weak=7 + corr=2（初回・全件）>= 3"

    def test_sorted_by_material_count_desc(self):
        """material_count 降順で並ぶ（多い PJ が先頭）。"""
        mats = [
            _material("low", weak=3, corr=0),
            _material("high", weak=9, corr=1),
            _material("mid", weak=4, corr=1),
        ]
        out = fq.select_evolve_queue(mats, threshold=3)
        assert [m["pj_slug"] for m in out] == ["high", "mid", "low"]

    def test_carries_through_state_and_activity(self):
        mats = [_material("a", weak=3, last="2026-06-01T00:00:00+00:00", subagents=40, sessions=5)]
        out = fq.select_evolve_queue(mats, threshold=3)
        assert out[0]["last_evolve_at"] == "2026-06-01T00:00:00+00:00"
        assert out[0]["activity_since"] == {"subagents": 40, "sessions": 5}

    def test_state_absent_pj_treated_as_first_time(self):
        """last_evolve_at=None でも material が閾値以上なら待ち（初回＝全件待ち）。"""
        mats = [_material("fresh", weak=3, corr=0, last=None)]
        out = fq.select_evolve_queue(mats, threshold=3)
        assert [m["pj_slug"] for m in out] == ["fresh"]

    def test_no_verify_pending_key_keeps_reason_unchanged(self):
        """material dict に verify_pending キーが無ければ従来通りの reason 文字列（後方互換）。"""
        mats = [_material("a", weak=7, corr=2, last="2026-06-01T00:00:00+00:00")]
        out = fq.select_evolve_queue(mats, threshold=3)
        assert out[0]["reason"] == "weak=7 + new corr=2 >= 3"
        assert out[0]["verify_pending"] is None

    def test_verify_pending_zero_accepted_keeps_reason_unchanged(self):
        """verify_pending はあるが accepted=0（status=none）でも reason は従来通り。"""
        mats = [_material("a", weak=7, corr=2, last="2026-06-01T00:00:00+00:00")]
        mats[0]["verify_pending"] = {
            "run_id": None,
            "accepted": 0,
            "exposure_sessions": 0,
            "status": "none",
        }
        out = fq.select_evolve_queue(mats, threshold=3)
        assert out[0]["reason"] == "weak=7 + new corr=2 >= 3"

    def test_verify_pending_verifiable_appends_reason_suffix(self):
        mats = [_material("a", weak=7, corr=2, last="2026-06-01T00:00:00+00:00")]
        mats[0]["verify_pending"] = {
            "run_id": "run1",
            "accepted": 2,
            "exposure_sessions": 3,
            "status": "verifiable",
        }
        out = fq.select_evolve_queue(mats, threshold=3)
        assert out[0]["reason"] == (
            "weak=7 + new corr=2 >= 3 / verify 待ち 2 件（前回 accept・検証可能）"
        )
        assert out[0]["verify_pending"]["status"] == "verifiable"

    def test_verify_pending_awaiting_exposure_appends_reason_suffix(self):
        mats = [_material("a", weak=7, corr=2, last="2026-06-01T00:00:00+00:00")]
        mats[0]["verify_pending"] = {
            "run_id": "run1",
            "accepted": 1,
            "exposure_sessions": 0,
            "status": "awaiting_exposure",
        }
        out = fq.select_evolve_queue(mats, threshold=3)
        assert "verify 待ち 1 件" in out[0]["reason"]
        assert "露出セッションなし" in out[0]["reason"]

    # --- C1: verify 待ちは material 閾値未満でも queue に含める ----------------

    def test_below_threshold_with_verify_pending_verifiable_is_included(self):
        """#267 C1: material が閾値未満でも verify 待ち（verifiable）なら queue に出す。"""
        mats = [_material("a", weak=1, corr=0, last="2026-06-01T00:00:00+00:00")]
        mats[0]["verify_pending"] = {
            "run_id": "run1",
            "accepted": 2,
            "exposure_sessions": 3,
            "status": "verifiable",
        }
        out = fq.select_evolve_queue(mats, threshold=5)
        assert [m["pj_slug"] for m in out] == ["a"]
        assert out[0]["material_count"] == 1
        assert "verify 待ち 2 件（前回 accept・検証可能）" in out[0]["reason"]
        assert "material=1 < 5" in out[0]["reason"]

    def test_below_threshold_with_verify_pending_awaiting_exposure_is_included(self):
        """#267 C1: awaiting_exposure でも none でなければ昇格させる。"""
        mats = [_material("a", weak=0, corr=0, last="2026-06-01T00:00:00+00:00")]
        mats[0]["verify_pending"] = {
            "run_id": "run1",
            "accepted": 1,
            "exposure_sessions": 0,
            "status": "awaiting_exposure",
        }
        out = fq.select_evolve_queue(mats, threshold=5)
        assert [m["pj_slug"] for m in out] == ["a"]
        assert "露出セッションなし" in out[0]["reason"]
        assert "material=0 < 5" in out[0]["reason"]

    def test_below_threshold_with_verify_pending_none_is_still_excluded(self):
        """verify_pending の status が none（accept 記録なし/失効）なら従来通り除外する。"""
        mats = [_material("a", weak=1, corr=0, last="2026-06-01T00:00:00+00:00")]
        mats[0]["verify_pending"] = {
            "run_id": None,
            "accepted": 0,
            "exposure_sessions": 0,
            "status": "none",
        }
        out = fq.select_evolve_queue(mats, threshold=5)
        assert out == []

    def test_below_threshold_without_verify_pending_key_is_still_excluded(self):
        """verify_pending キー自体が無い（後方互換）material は従来通り除外する。"""
        mats = [_material("a", weak=1, corr=0, last="2026-06-01T00:00:00+00:00")]
        out = fq.select_evolve_queue(mats, threshold=5)
        assert out == []

    def test_verify_promoted_items_sort_by_material_count_like_others(self):
        """ソート順は material_count 降順のまま（verify 昇格でも特別扱いしない）。"""
        mats = [
            _material("low", weak=1, corr=0, last="2026-06-01T00:00:00+00:00"),
            _material("high", weak=10, corr=0, last="2026-06-01T00:00:00+00:00"),
        ]
        mats[0]["verify_pending"] = {
            "run_id": "run1",
            "accepted": 1,
            "exposure_sessions": 1,
            "status": "verifiable",
        }
        out = fq.select_evolve_queue(mats, threshold=5)
        assert [m["pj_slug"] for m in out] == ["high", "low"]

    def test_at_or_above_threshold_with_verify_pending_uses_normal_reason(self):
        """閾値以上は通常の material 主体 reason のまま（C1 の語順反転が適用されない）。"""
        mats = [_material("a", weak=7, corr=2, last="2026-06-01T00:00:00+00:00")]
        mats[0]["verify_pending"] = {
            "run_id": "run1",
            "accepted": 2,
            "exposure_sessions": 3,
            "status": "verifiable",
        }
        out = fq.select_evolve_queue(mats, threshold=3)
        assert out[0]["reason"].startswith("weak=7 + new corr=2 >= 3")


# --- weak_signals 未処理カウント（PJ 別） -------------------------------------


def _ws(pj_slug, *, promoted=False, expired=False, detected=None, key=None):
    # content-rich channel（#113: material 計数は REVIEW_CHANNELS のみ）。この helper の
    # weak は promoted/expired/scope/dead/untracked 判定の検証用ゆえ昇格可能 channel を使う。
    # detected_at は TTL（weak_signals.ttl.TTL_DAYS）を read 時に実 clock（now）で導出する
    # ため（#89 is_effectively_expired）、固定日だと TTL 境界を越えた日から判定が反転する
    # （#370）。既定は「境界から十分内側」を意図した call 時 now-1日にする。
    if detected is None:
        detected = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    return {
        "channel": "llm_judge",
        "provenance": {"k": key or pj_slug + str(promoted) + str(expired) + detected},
        "detected_at": detected,
        "session_id": "s",
        "pj_slug": pj_slug,
        "promoted": promoted,
        "expired": expired,
        "signal_key": key or (pj_slug + str(promoted) + str(expired) + detected),
    }


class TestWeakUnprocessedByPj:
    def test_counts_unpromoted_unexpired_for_pj(self, tmp_path):
        store = tmp_path / "weak_signals.jsonl"
        recs = [
            _ws("alpha", key="a1"),
            _ws("alpha", key="a2"),
            _ws("beta", key="b1"),
        ]
        store.write_text("".join(json.dumps(r) + "\n" for r in recs))
        assert fq.weak_unprocessed_by_pj("alpha", weak_signals_path=store) == 2
        assert fq.weak_unprocessed_by_pj("beta", weak_signals_path=store) == 1

    def test_excludes_promoted(self, tmp_path):
        store = tmp_path / "weak_signals.jsonl"
        recs = [_ws("alpha", key="a1"), _ws("alpha", promoted=True, key="a2")]
        store.write_text("".join(json.dumps(r) + "\n" for r in recs))
        assert fq.weak_unprocessed_by_pj("alpha", weak_signals_path=store) == 1

    def test_excludes_expired(self, tmp_path):
        store = tmp_path / "weak_signals.jsonl"
        recs = [_ws("alpha", key="a1"), _ws("alpha", expired=True, key="a2")]
        store.write_text("".join(json.dumps(r) + "\n" for r in recs))
        assert fq.weak_unprocessed_by_pj("alpha", weak_signals_path=store) == 1

    def test_missing_store_returns_zero(self, tmp_path):
        store = tmp_path / "nope.jsonl"
        assert fq.weak_unprocessed_by_pj("alpha", weak_signals_path=store) == 0


# --- 前回 evolve 以降の corrections カウント ----------------------------------


def _corr(project_path, ts):
    return {"project_path": project_path, "timestamp": ts, "message": "x"}


def _read_records(store):
    """テスト用: corrections.jsonl を1回 read してレコード列だけを返す（#538 round8 —
    ``new_corrections_by_pj`` は ``records`` 必須の純集計になったため、テストも production と
    同じ1回 read 経路で snapshot を作ってから渡す）。
    """
    return fq.read_corrections_records_with_health(store).records


class TestNewCorrectionsByPj:
    def test_counts_corrections_since_last_evolve(self, tmp_path):
        store = tmp_path / "corrections.jsonl"
        recs = [
            _corr("alpha", "2026-06-01T00:00:00+00:00"),
            _corr("alpha", "2026-06-10T00:00:00+00:00"),
            _corr("alpha", "2026-06-20T00:00:00+00:00"),
        ]
        store.write_text("".join(json.dumps(r) + "\n" for r in recs))
        # last_evolve_at が 06-05 → 06-10, 06-20 の 2 件
        n = fq.new_corrections_by_pj(
            "alpha", last_evolve_at="2026-06-05T00:00:00+00:00", records=_read_records(store)
        )
        assert n == 2

    def test_last_evolve_none_counts_all(self, tmp_path):
        """state 不在（None）は全件カウント（初回＝全件待ち）。"""
        store = tmp_path / "corrections.jsonl"
        recs = [_corr("alpha", "2026-06-01T00:00:00+00:00"), _corr("alpha", "2026-06-10T00:00:00+00:00")]
        store.write_text("".join(json.dumps(r) + "\n" for r in recs))
        n = fq.new_corrections_by_pj("alpha", last_evolve_at=None, records=_read_records(store))
        assert n == 2

    def test_scopes_by_project_path(self, tmp_path):
        store = tmp_path / "corrections.jsonl"
        recs = [_corr("alpha", "2026-06-10T00:00:00+00:00"), _corr("beta", "2026-06-10T00:00:00+00:00")]
        store.write_text("".join(json.dumps(r) + "\n" for r in recs))
        assert fq.new_corrections_by_pj("alpha", last_evolve_at=None, records=_read_records(store)) == 1

    def test_missing_store_returns_zero(self, tmp_path):
        assert (
            fq.new_corrections_by_pj(
                "alpha", last_evolve_at=None, records=_read_records(tmp_path / "x.jsonl")
            )
            == 0
        )

    def test_mixed_tz_suffix_same_instant_excluded(self, tmp_path):
        """`Z` 終端 corr と `+00:00` 終端 last_evolve が同一 instant なら新規にカウントしない。

        実コーパスの corrections.jsonl は `Z` 終端 / `+00:00` 終端が混在し、
        `persist_last_evolve` は ``.isoformat()``＝`+00:00` を書く。辞書順比較だと
        ``"...Z" > "...+00:00"`` が同一 instant でも True になり、drain と同時刻の
        `Z` 終端 corr を誤って「前回 evolve 以降の新規」に数えてしまう（潜在シーム）。
        """
        store = tmp_path / "corrections.jsonl"
        store.write_text(json.dumps(_corr("alpha", "2026-06-23T10:00:00Z")) + "\n")
        # last_evolve とちょうど同一 instant（表記だけ +00:00）→ ts <= last → 除外（0 件）
        n = fq.new_corrections_by_pj(
            "alpha", last_evolve_at="2026-06-23T10:00:00+00:00", records=_read_records(store)
        )
        assert n == 0

    def test_mixed_tz_suffix_after_still_counted(self, tmp_path):
        """suffix 違いでも実時刻が後なら新規としてカウントする（修正が過剰除外しない保証）。"""
        store = tmp_path / "corrections.jsonl"
        store.write_text(json.dumps(_corr("alpha", "2026-06-23T10:00:01Z")) + "\n")
        n = fq.new_corrections_by_pj(
            "alpha", last_evolve_at="2026-06-23T10:00:00+00:00", records=_read_records(store)
        )
        assert n == 1


# --- per-PJ last_evolve state（read / write barrier 経由）---------------------


class TestCountUnattributedCorrections:
    """project_path 欠落で PJ 帰属不能な corrections を source 別に数える（#91）。

    ``_correction_slug`` が空文字に落ちるレコードはどの PJ の material にも数えられず、
    untracked/phantom にも出ないため queue から構造的に不可視（silent truncation）。
    #86/#88 の「無音で落とさない」原則の最後の穴埋めとして件数+source 内訳を advisory 化する。
    """

    def test_counts_empty_project_path_by_source(self, tmp_path):
        store = tmp_path / "corrections.jsonl"
        store.write_text(
            json.dumps({"project_path": "", "source": "backfill", "timestamp": "t"}) + "\n"
            + json.dumps({"project_path": None, "source": "backfill", "timestamp": "t"}) + "\n"
            + json.dumps({"project_path": None, "source": "hook", "timestamp": "t"}) + "\n"
            + json.dumps(_corr("/Users/x/amamo", "t")) + "\n"  # 帰属可能 → 対象外
        )
        out = fq.count_unattributed_corrections(store)
        assert out["total"] == 3
        assert out["by_source"] == {"backfill": 2, "hook": 1}

    def test_missing_source_falls_back_to_unknown(self, tmp_path):
        store = tmp_path / "corrections.jsonl"
        store.write_text(json.dumps({"project_path": "", "timestamp": "t"}) + "\n")
        out = fq.count_unattributed_corrections(store)
        assert out == {"total": 1, "by_source": {"(unknown)": 1}}

    def test_attributed_records_excluded(self, tmp_path):
        store = tmp_path / "corrections.jsonl"
        store.write_text(
            json.dumps(_corr("/Users/x/amamo", "t")) + "\n"
            + json.dumps(_corr("sys-bots", "t")) + "\n"  # bare slug も帰属可能
        )
        out = fq.count_unattributed_corrections(store)
        assert out == {"total": 0, "by_source": {}}

    def test_missing_store_returns_zero(self, tmp_path):
        out = fq.count_unattributed_corrections(tmp_path / "nope.jsonl")
        assert out == {"total": 0, "by_source": {}}

    def test_since_none_counts_all_records_back_compat(self, tmp_path):
        """C5: since 未指定は従来通り全件（後方互換）。"""
        store = tmp_path / "corrections.jsonl"
        store.write_text(
            json.dumps(
                {"project_path": "", "source": "backfill", "timestamp": "2020-01-01T00:00:00+00:00"}
            )
            + "\n"
        )
        out = fq.count_unattributed_corrections(store)
        assert out == {"total": 1, "by_source": {"backfill": 1}}

    def test_since_filters_out_older_records(self, tmp_path):
        """C5: since 指定時は、それより後の timestamp のみ数える。"""
        store = tmp_path / "corrections.jsonl"
        store.write_text(
            json.dumps(
                {"project_path": "", "source": "backfill", "timestamp": "2026-06-01T00:00:00+00:00"}
            )
            + "\n"
            + json.dumps(
                {"project_path": "", "source": "backfill", "timestamp": "2026-07-01T00:00:00+00:00"}
            )
            + "\n"
        )
        out = fq.count_unattributed_corrections(store, since="2026-06-15T00:00:00+00:00")
        assert out == {"total": 1, "by_source": {"backfill": 1}}


# --- corrections.jsonl 共有 read health（#533）--------------------------------


class TestReadCorrectionsRecordsWithHealth:
    """``silence != evaluated``: 正常な空在庫と読取不能・壊れた行を区別する（issue #533）。"""

    def test_healthy_file_is_readable_with_no_malformed_lines(self, tmp_path):
        store = tmp_path / "corrections.jsonl"
        store.write_text(json.dumps(_corr("alpha", "2026-06-01T00:00:00+00:00")) + "\n")
        records, health = fq.read_corrections_records_with_health(store)
        assert len(records) == 1
        assert health == {"readable": True, "error": None, "malformed_lines": 0}

    def test_missing_file_is_normal_empty_not_degraded(self, tmp_path):
        """ファイル不在は「読めなかった」でなく「正常な空在庫」— evaluated 扱い。"""
        store = tmp_path / "nope.jsonl"
        records, health = fq.read_corrections_records_with_health(store)
        assert records == []
        assert health == {"readable": True, "error": None, "malformed_lines": 0}

    def test_os_error_marks_unreadable_with_error_text(self, tmp_path, monkeypatch):
        store = tmp_path / "corrections.jsonl"
        store.write_text(json.dumps(_corr("alpha", "t")) + "\n")

        def _raise(*_args, **_kwargs):
            raise OSError("Permission denied")

        monkeypatch.setattr(Path, "read_bytes", _raise)
        records, health = fq.read_corrections_records_with_health(store)
        assert records == []
        assert health["readable"] is False
        assert "Permission denied" in (health["error"] or "")
        assert health["malformed_lines"] == 0

    def test_malformed_lines_are_counted_not_silently_dropped(self, tmp_path):
        store = tmp_path / "corrections.jsonl"
        store.write_text(
            json.dumps(_corr("alpha", "t")) + "\n"
            "{not valid json\n"
            "[]\n"  # JSON として valid だが dict でない → malformed 扱い
        )
        records, health = fq.read_corrections_records_with_health(store)
        assert len(records) == 1
        assert health["readable"] is True
        assert health["malformed_lines"] == 2

    def test_corrections_read_health_standalone_probe(self, tmp_path):
        """``corrections_read_health`` は records を捨てて health だけ返す薄いラッパー。"""
        store = tmp_path / "corrections.jsonl"
        store.write_text("{bad json\n")
        assert fq.corrections_read_health(store) == {
            "readable": True,
            "error": None,
            "malformed_lines": 1,
        }

    def test_invalid_utf8_bytes_counted_as_malformed_not_silently_replaced(self, tmp_path):
        """#538 round2 [Must]3: 不正 UTF-8 は置換文字で偶然 JSON parse に成功させず malformed 扱いにする。

        従来の ``errors="replace"`` decode だと不正バイトが U+FFFD へ丸められ、
        ``alp\\xffha`` が ``alp�ha`` として JSON parse に成功し readable=true,
        malformed_lines=0 の健全表示になっていた（issue #538 round2 レビュー指摘）。
        """
        store = tmp_path / "corrections.jsonl"
        healthy_line = json.dumps(_corr("alpha", "2026-06-01T00:00:00+00:00")).encode("utf-8")
        broken_line = b'{"project_path":"alp\xffha","timestamp":"t","message":"x"}'
        store.write_bytes(healthy_line + b"\n" + broken_line + b"\n")

        records, health = fq.read_corrections_records_with_health(store)

        assert len(records) == 1
        assert records[0]["project_path"] == "alpha"
        assert health["readable"] is True
        assert health["malformed_lines"] == 1

    def test_dangling_symlink_is_degraded_not_normal_empty(self, tmp_path):
        """#538 round2 [Must]4: リンク切れ symlink は「正常な空在庫」と区別する。

        ``Path.exists()`` は dangling symlink に対して False を返すため、素通しすると
        真の未作成（正常な空）と同じ扱いになり readable=true のまま EMPTY を返してしまう。
        """
        target = tmp_path / "does-not-exist.jsonl"
        link = tmp_path / "corrections.jsonl"
        link.symlink_to(target)

        records, health = fq.read_corrections_records_with_health(link)

        assert records == []
        assert health["readable"] is False
        assert health["error"]

    def test_real_missing_file_stays_normal_empty(self, tmp_path):
        """陽性対照: symlink でない真の未作成ファイルは従来通り正常な空在庫のまま。"""
        store = tmp_path / "corrections.jsonl"  # 未作成・symlink でもない
        records, health = fq.read_corrections_records_with_health(store)
        assert records == []
        assert health == {"readable": True, "error": None, "malformed_lines": 0}

    def test_race_delete_after_lstat_succeeds_is_true_absence_not_unreadable(
        self, tmp_path, monkeypatch
    ):
        """#538 round4 [Must]2: lstat() 成功直後に unlink される競合（真の不在）を、
        その他の読取不能 ``OSError`` と区別する。旧実装は ``path.read_bytes()`` が投げる
        ``FileNotFoundError`` を他の ``OSError`` と一括処理していたため、この競合が
        ``readable=false``（劣化）に化けていた。ここでは実プロセス並行での再現（競合窓が
        µs で不安定）を避け、``lstat()`` は成功させたまま ``read_bytes()`` だけを
        ``FileNotFoundError`` にする決定論的な monkeypatch で構成する。
        """
        store = tmp_path / "corrections.jsonl"
        store.write_text(json.dumps(_corr("alpha", "t")) + "\n")  # lstat() は成功させる

        def _raise_fnf(self):
            raise FileNotFoundError(2, "No such file or directory", str(self))

        monkeypatch.setattr(Path, "read_bytes", _raise_fnf)

        records, health = fq.read_corrections_records_with_health(store)

        assert records == []
        assert health == {"readable": True, "error": None, "malformed_lines": 0}

    def test_non_fnf_oserror_at_read_bytes_still_marks_unreadable(self, tmp_path, monkeypatch):
        """陽性対照: ``read_bytes()`` の非 ``FileNotFoundError`` 系 ``OSError``（権限変更等の
        真の読取不能）は、上の真の不在分岐に巻き込まれず従来通り劣化として扱われる。
        """
        store = tmp_path / "corrections.jsonl"
        store.write_text(json.dumps(_corr("alpha", "t")) + "\n")

        def _raise_permission(self):
            raise PermissionError(13, "Permission denied", str(self))

        monkeypatch.setattr(Path, "read_bytes", _raise_permission)

        records, health = fq.read_corrections_records_with_health(store)

        assert records == []
        assert health["readable"] is False
        assert "Permission denied" in (health["error"] or "")

    def test_dangling_symlink_still_unreadable_after_race_fix(self, tmp_path):
        """陽性対照: round4 の read_bytes() 例外分岐変更後も dangling symlink は
        ``path.stat()`` 分岐で先に捕捉され、readable=false のまま（round2 [Must]4 挙動を壊さない）。
        """
        target = tmp_path / "does-not-exist.jsonl"
        link = tmp_path / "corrections.jsonl"
        link.symlink_to(target)

        records, health = fq.read_corrections_records_with_health(link)

        assert records == []
        assert health["readable"] is False
        assert health["error"]

    def test_symlink_target_vanishes_after_stat_link_remains_is_degraded(
        self, tmp_path, monkeypatch
    ):
        """#538 round5 [Must]1(I1): symlink 判定→``path.stat()``（実体確認）成功後、
        ``read_bytes()`` 直前に **target だけ** が unlink される決定論レース。symlink
        エントリ自体（``lstat()``）は残ったままなので、直前の ``path.stat()`` 分岐と同じ
        「劣化」（readable=False）が正しい。round4 が入れた「``read_bytes()`` の
        ``FileNotFoundError`` は常に真の不在」という一括扱いは、この経路では誤りだった
        （壊す不変条件: dangling symlink は常に readable=False。通したい検査経路:
        symlink 判定→stat 成功→target 消失→read_bytes FNF→lstat 成功、の順で到達する分岐）。
        """
        target = tmp_path / "real-target.jsonl"
        target.write_text(json.dumps(_corr("alpha", "t")) + "\n")
        link = tmp_path / "corrections.jsonl"
        link.symlink_to(target)

        def _raise_fnf(self):
            raise FileNotFoundError(2, "No such file or directory", str(self))

        monkeypatch.setattr(Path, "read_bytes", _raise_fnf)
        # lstat() は素通し（symlink エントリ自体は実在するまま＝target だけが消えた想定）。

        records, health = fq.read_corrections_records_with_health(link)

        assert records == []
        assert health["readable"] is False
        assert health["error"]

    def test_symlink_and_target_both_vanish_after_stat_is_true_absence(
        self, tmp_path, monkeypatch
    ):
        """#538 round5 [Must]1(I1) 陽性対照側: symlink エントリ自体も target と一緒に消えた
        場合（link ごと unlink/rename）は、通常ファイルと同じ「真の不在」（readable=True・
        正常な空在庫）のまま。target だけの消失（上のテスト）と link ごとの消失を混同しない
        ことを検査する（壊す不変条件: link 自体が無ければ真の不在。通したい検査経路:
        read_bytes FNF 後の再 lstat() が FileNotFoundError を投げる分岐）。
        """
        target = tmp_path / "real-target.jsonl"
        target.write_text(json.dumps(_corr("alpha", "t")) + "\n")
        link = tmp_path / "corrections.jsonl"
        link.symlink_to(target)

        real_lstat = Path.lstat
        call_count = {"n": 0}

        def _lstat_then_vanish(self):
            call_count["n"] += 1
            if call_count["n"] >= 2:
                raise FileNotFoundError(2, "No such file or directory", str(self))
            return real_lstat(self)

        def _raise_fnf_read(self):
            raise FileNotFoundError(2, "No such file or directory", str(self))

        monkeypatch.setattr(Path, "lstat", _lstat_then_vanish)
        monkeypatch.setattr(Path, "read_bytes", _raise_fnf_read)

        records, health = fq.read_corrections_records_with_health(link)

        assert records == []
        assert health == {"readable": True, "error": None, "malformed_lines": 0}

    def test_symlink_pointing_to_directory_is_degraded(self, tmp_path):
        """破綻経路の自主構成①: symlink の参照先が directory（IsADirectoryError）。
        ``path.stat()`` は成功（dir stat）するが ``read_bytes()`` が
        ``IsADirectoryError``（``OSError`` の非 FNF サブクラス）を投げ、既存の generic
        ``OSError`` 分岐で readable=False になることを検査する（symlink 分離ロジックの
        追加が、通常の非 FNF エラー経路を壊していないことの確認）。
        """
        target_dir = tmp_path / "a-directory"
        target_dir.mkdir()
        link = tmp_path / "corrections.jsonl"
        link.symlink_to(target_dir)

        records, health = fq.read_corrections_records_with_health(link)

        assert records == []
        assert health["readable"] is False
        assert health["error"]

    def test_symlink_loop_is_degraded_not_true_absence(self, tmp_path):
        """破綻経路の自主構成②: symlink ループ（自己参照）。``path.stat()`` が ``OSError``
        （``ELOOP``）を投げ、既存の generic ``OSError`` 分岐で readable=False になる
        ことを検査する（真の不在＝readable=True に誤分類されないこと）。
        """
        link = tmp_path / "corrections.jsonl"
        link.symlink_to(link)  # 自己参照ループ

        records, health = fq.read_corrections_records_with_health(link)

        assert records == []
        assert health["readable"] is False
        assert health["error"]


class TestCorrectionsSnapshotFieldIdentity:
    """#538 round8 [Must](2)(b): API surface snapshot（``fleet_api_surface.txt``）は
    ``CorrectionsSnapshot`` の ``records``/``health`` field の**存在**（signature の field 名・
    ``_tuplegetter`` という型名）しか検査しておらず、**意味**（どちらの field がどちらの値を
    持つか）は検査していない。``records`` と ``health`` を入れ替えるフィールド順の入れ替え
    変異を適用しても、両方とも同じ ``_tuplegetter`` descriptor なので収集文字列が一切変わらず
    snapshot テストは緑のまま通ってしまう（レビュー実測）。ここでは実際の値を使った振る舞い
    テストで、field 名と値の対応・construction 順・unpack 順を検査する。
    """

    def test_records_and_health_are_the_exact_objects_passed_by_field_name(self):
        records = [{"project_path": "alpha", "timestamp": "t"}]
        health = {"readable": True, "error": None, "malformed_lines": 0}
        snapshot = fq.CorrectionsSnapshot(records=records, health=health)

        # 壊す不変条件: 「records field は records 引数の値、health field は health 引数の値」。
        # ``records``/``health`` を入れ替える変異（`.records ↔ .health`）を適用すると、
        # この identity 検査が直接壊れる（値の型が違うので equality でも検出できるが、
        # identity（`is`）の方が「同じオブジェクトかどうか」までより厳密に見る）。
        assert snapshot.records is records
        assert snapshot.health is health
        assert snapshot.records is not health
        assert snapshot.health is not records

    def test_tuple_unpack_order_matches_records_then_health(self):
        """``records, health = snapshot`` の unpack 順序が ``(records, health)`` の
        construction 順と一致すること（通したい検査経路: 呼び出し側の
        ``corr_records, corr_read_health = read_corrections_records_with_health(...)`` の
        ような位置依存 unpack が正しい変数に正しい値を束縛すること）。
        """
        records = [{"project_path": "alpha", "timestamp": "t"}]
        health = {"readable": False, "error": "distinct-from-records", "malformed_lines": 3}
        snapshot = fq.CorrectionsSnapshot(records=records, health=health)

        unpacked_first, unpacked_second = snapshot
        assert unpacked_first is records
        assert unpacked_second is health
        # records と health は型が明確に異なる（list vs dict）ので、入れ替わっていれば
        # ここで即座に検出できる。
        assert isinstance(unpacked_first, list)
        assert isinstance(unpacked_second, dict)


class TestQueueState:
    def test_read_empty_when_missing(self, tmp_path):
        assert qs.read_last_evolve(data_dir=tmp_path) == {}

    def test_read_folds_last_append_wins(self, tmp_path):
        store = tmp_path / qs.STORE_NAME
        recs = [
            {"pj_slug": "alpha", "last_evolve_at": "2026-06-01T00:00:00+00:00", "ts": "2026-06-01T00:00:00+00:00"},
            {"pj_slug": "alpha", "last_evolve_at": "2026-06-20T00:00:00+00:00", "ts": "2026-06-20T00:00:00+00:00"},
            {"pj_slug": "beta", "last_evolve_at": "2026-06-10T00:00:00+00:00", "ts": "2026-06-10T00:00:00+00:00"},
        ]
        store.write_text("".join(json.dumps(r) + "\n" for r in recs))
        got = qs.read_last_evolve(data_dir=tmp_path)
        assert got == {
            "alpha": "2026-06-20T00:00:00+00:00",
            "beta": "2026-06-10T00:00:00+00:00",
        }

    def test_persist_writes_through_store_write_barrier(self, tmp_path, monkeypatch):
        """persist は store_write("evolve-queue-state.jsonl") 経由（ADR-049）。"""
        import rl_common
        d = tmp_path / "evolve-anything"
        d.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(rl_common, "DATA_DIR", d)
        monkeypatch.delenv("EVOLVE_WRITE_GUARD", raising=False)
        from unittest import mock
        with mock.patch.object(rl_common, "store_write") as m_sw:
            qs.persist_last_evolve("alpha", ts="2026-06-25T00:00:00+00:00")
        assert m_sw.call_count == 1
        assert m_sw.call_args.args[0] == qs.STORE_NAME
        rec = m_sw.call_args.args[1]
        assert rec["pj_slug"] == "alpha"
        assert rec["last_evolve_at"] == "2026-06-25T00:00:00+00:00"

    def test_persist_dry_run_writes_nothing(self, tmp_path, monkeypatch):
        """dry_run=True は store に一切触れない（apply 境界のみ書く・#308/#513）。"""
        import rl_common
        d = tmp_path / "evolve-anything"
        d.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(rl_common, "DATA_DIR", d)
        from unittest import mock
        with mock.patch.object(rl_common, "store_write") as m_sw:
            res = qs.persist_last_evolve("alpha", ts="2026-06-25T00:00:00+00:00", dry_run=True)
        assert m_sw.call_count == 0
        assert not (d / qs.STORE_NAME).exists()
        assert res["dry_run"] is True
        assert res["written"] == 0

    def test_store_registered_active(self):
        import store_registry
        assert qs.STORE_NAME in store_registry.active_store_names()
        decl = store_registry.declaration_for(qs.STORE_NAME)
        assert decl is not None
        assert decl.writer_locus == "batch"


# --- gather + build_queue（統合・store 注入）----------------------------------


class TestBuildQueueResult:
    def test_json_schema_matches_phase1b_contract(self, tmp_path):
        """--json schema が Phase 1b #80 の共有契約に一致する。"""
        ws = tmp_path / "weak_signals.jsonl"
        ws.write_text("".join(json.dumps(_ws("alpha", key=f"a{i}")) + "\n" for i in range(7)))
        corr = tmp_path / "corrections.jsonl"
        corr.write_text(
            "".join(json.dumps(_corr("alpha", "2026-06-20T00:00:00+00:00")) + "\n" for _ in range(2))
        )
        result = fq.build_queue_result(
            pj_slugs=["alpha"],
            threshold=3,
            weak_signals_path=ws,
            corrections_path=corr,
            last_evolve_map={},
            activity_map={"alpha": {"subagents": 40, "sessions": 5}},
            generated_at="2026-06-25T09:00:00Z",
        )
        assert set(result.keys()) == {
            "generated_at",
            "threshold",
            "tracked_total",
            "queue",
            "queue_status",
            "queue_status_reason",
            "skipped_dead",
            "untracked_with_material",
            "skipped_phantom",
            "bootstrap_consumed",
            "weak_content_poor",
            "weak_machinery",
            "unattributed_corrections",
            "corrections_read_health",
            "weak_signals_read_health",
        }
        assert result["unattributed_corrections"] == {"total": 0, "by_source": {}}
        assert result["corrections_read_health"] == {
            "readable": True,
            "error": None,
            "malformed_lines": 0,
        }
        assert result["weak_signals_read_health"] == {
            "sources": [
                {
                    "path": str(ws),
                    "readable": True,
                    "error": None,
                    "malformed_lines": 0,
                }
            ]
        }
        assert result["generated_at"] == "2026-06-25T09:00:00Z"
        assert result["threshold"] == 3
        assert result["tracked_total"] == 1
        assert result["queue_status"] == "READY"
        assert result["queue_status_reason"]
        assert len(result["queue"]) == 1
        item = result["queue"][0]
        assert set(item.keys()) == {
            "pj_slug",
            "project_path",
            "material_count",
            "verify_pending",
            "weak_unprocessed",
            "new_corrections",
            "last_evolve_at",
            "activity_since",
            "reason",
            "correction_backlog",
        }
        assert item["pj_slug"] == "alpha"
        assert item["weak_unprocessed"] == 7
        assert item["new_corrections"] == 2
        assert item["material_count"] == 9
        assert item["last_evolve_at"] is None
        assert item["activity_since"] == {"subagents": 40, "sessions": 5}
        assert item["verify_pending"]["status"] == "none"  # accept 記録なし

    def test_below_threshold_pj_not_in_queue_but_counted(self, tmp_path):
        ws = tmp_path / "weak_signals.jsonl"
        ws.write_text(json.dumps(_ws("quiet", key="q1")) + "\n")
        corr = tmp_path / "corrections.jsonl"
        corr.write_text("")
        result = fq.build_queue_result(
            pj_slugs=["quiet"],
            threshold=3,
            weak_signals_path=ws,
            corrections_path=corr,
            last_evolve_map={},
            activity_map={},
            generated_at="2026-06-25T09:00:00Z",
        )
        assert result["tracked_total"] == 1
        assert result["queue"] == []
        assert result["queue_status"] == "EMPTY"
        assert result["queue_status_reason"]

    def test_promoted_backlog_bypasses_material_threshold(self, tmp_path):
        """#515 E2E: 前回 evolve より古い promoted 在庫だけでも queue に出る。"""
        ws = tmp_path / "weak_signals.jsonl"
        ws.write_text("")
        corr = tmp_path / "corrections.jsonl"
        rec = _corr("dormant", "2026-06-01T00:00:00+00:00")
        rec.update({"reflect_status": "promoted", "invalidated": False})
        corr.write_text(json.dumps(rec) + "\n")
        result = fq.build_queue_result(
            pj_slugs=["dormant"],
            threshold=5,
            weak_signals_path=ws,
            corrections_path=corr,
            last_evolve_map={"dormant": "2026-06-20T00:00:00+00:00"},
            activity_map={},
            generated_at="2026-06-25T09:00:00Z",
        )
        assert result["queue_status"] == "READY"
        assert result["queue"][0]["material_count"] == 0
        assert result["queue"][0]["correction_backlog"] == 1

    def test_degraded_corrections_read_does_not_claim_empty(self, tmp_path):
        """#533: corrections.jsonl の壊れた行を、待ち0件の EMPTY 断定に紛れ込ませない。"""
        ws = tmp_path / "weak_signals.jsonl"
        ws.write_text("")
        corr = tmp_path / "corrections.jsonl"
        corr.write_text("{not valid json\n")
        result = fq.build_queue_result(
            pj_slugs=["quiet"],
            threshold=3,
            weak_signals_path=ws,
            corrections_path=corr,
            last_evolve_map={},
            activity_map={},
            generated_at="2026-06-25T09:00:00Z",
        )
        assert result["queue"] == []
        # EMPTY のままでは「本当に0件」と読めてしまう。読取が劣化しているので断定しない。
        assert result["queue_status"] == "SETUP_REQUIRED"
        assert "corrections.jsonl" in result["queue_status_reason"]
        assert result["corrections_read_health"]["malformed_lines"] == 1

    def test_degraded_weak_signals_read_does_not_claim_empty(self, tmp_path):
        """#539: weak_signals の部分破損を待ち0件の EMPTY 断定に紛れ込ませない。"""
        ws = tmp_path / "weak_signals.jsonl"
        ws.write_text("{not valid json\n", encoding="utf-8")
        corr = tmp_path / "corrections.jsonl"
        corr.write_text("", encoding="utf-8")

        result = fq.build_queue_result(
            pj_slugs=["quiet"],
            threshold=3,
            weak_signals_path=ws,
            corrections_path=corr,
            last_evolve_map={},
            activity_map={},
            generated_at="2026-06-25T09:00:00Z",
        )

        assert result["queue"] == []
        assert result["queue_status"] == "SETUP_REQUIRED"
        assert "weak_signals.jsonl" in result["queue_status_reason"]
        assert result["weak_signals_read_health"]["sources"][0]["malformed_lines"] == 1

    def test_degraded_corrections_read_note_appended_when_queue_ready(self, tmp_path):
        """#533: queue が非空（READY）でも劣化注記は reason に残る（無音にしない）。"""
        ws = tmp_path / "weak_signals.jsonl"
        ws.write_text("".join(json.dumps(_ws("alpha", key=f"a{i}")) + "\n" for i in range(4)))
        corr = tmp_path / "corrections.jsonl"
        corr.write_text("{not valid json\n")
        result = fq.build_queue_result(
            pj_slugs=["alpha"],
            threshold=3,
            weak_signals_path=ws,
            corrections_path=corr,
            last_evolve_map={},
            activity_map={},
            generated_at="2026-06-25T09:00:00Z",
        )
        assert result["queue_status"] == "READY"
        assert "corrections.jsonl" in result["queue_status_reason"]
        assert result["corrections_read_health"]["malformed_lines"] == 1

    def test_healthy_corrections_read_stays_empty(self, tmp_path):
        """陽性対照: 正常な空在庫（読取自体は健全）は従来通り EMPTY のまま。"""
        ws = tmp_path / "weak_signals.jsonl"
        ws.write_text("")
        corr = tmp_path / "corrections.jsonl"
        corr.write_text("")
        result = fq.build_queue_result(
            pj_slugs=["quiet"],
            threshold=3,
            weak_signals_path=ws,
            corrections_path=corr,
            last_evolve_map={},
            activity_map={},
            generated_at="2026-06-25T09:00:00Z",
        )
        assert result["queue_status"] == "EMPTY"
        assert result["corrections_read_health"] == {
            "readable": True,
            "error": None,
            "malformed_lines": 0,
        }

    def test_missing_corrections_file_stays_empty(self, tmp_path):
        """陽性対照その2: corrections.jsonl が存在しない（初回セットアップ等）も正常な空扱い。"""
        ws = tmp_path / "weak_signals.jsonl"
        ws.write_text("")
        corr = tmp_path / "corrections.jsonl"  # 未作成
        result = fq.build_queue_result(
            pj_slugs=["quiet"],
            threshold=3,
            weak_signals_path=ws,
            corrections_path=corr,
            last_evolve_map={},
            activity_map={},
            generated_at="2026-06-25T09:00:00Z",
        )
        assert result["queue_status"] == "EMPTY"
        assert result["corrections_read_health"]["readable"] is True
        assert result["corrections_read_health"]["malformed_lines"] == 0

    def test_corrections_jsonl_read_exactly_once_across_multiple_pjs(
        self, tmp_path, monkeypatch
    ):
        """#538 round2 [Must]1・round7: 複数 PJ・複数集計にまたがっても、公開
        ``build_queue_result`` が行う corrections.jsonl の物理 read は1回だけ。

        probe（health）と各集計（backlog counts / weak+corr ×N PJ / unattributed）が別読みだと、
        probe 成功後に read が失敗する（逆も同様）ケースで health が「正常」なのに集計結果だけ
        劣化を反映しない、または劣化が無いのに劣化扱いになる silent スナップショット不一致が
        起きる。

        #538 round7: 公開 API から corrections 関連の注入引数を完全に排除したため、
        テストは再び module-level 名の monkeypatch で read 回数を数える（round6 の
        ``corrections_reader=`` 明示注入は round7 で廃止 — その注入口自体が forge の
        温床だったため）。``build_queue_result`` は ``read_corrections_records_with_health``
        を bare name で直接呼ぶので、``fq.read_corrections_records_with_health`` を差し替える
        だけで捕捉できる。
        """
        ws = tmp_path / "weak_signals.jsonl"
        ws.write_text("")
        corr = tmp_path / "corrections.jsonl"
        corr.write_text(
            "".join(
                json.dumps(_corr(slug, "2026-06-01T00:00:00+00:00")) + "\n"
                for slug in ("alpha", "beta", "gamma")
            )
        )

        real_reader = fq.read_corrections_records_with_health
        calls = []

        def _counting_reader(path):
            calls.append(path)
            return real_reader(path)

        monkeypatch.setattr(fq, "read_corrections_records_with_health", _counting_reader)

        result = fq.build_queue_result(
            pj_slugs=["alpha", "beta", "gamma"],
            threshold=1,
            weak_signals_path=ws,
            corrections_path=corr,
            last_evolve_map={},
            activity_map={},
            generated_at="2026-06-25T09:00:00Z",
        )

        assert len(calls) == 1, f"corrections.jsonl was read {len(calls)} times, expected 1"
        assert {item["pj_slug"] for item in result["queue"]} == {"alpha", "beta", "gamma"}

    def test_weak_signals_read_exactly_once_across_all_queue_collectors(
        self, tmp_path, monkeypatch
    ):
        """#539: health と全 weak 集計が同じ1回の snapshot を使う。"""
        ws = tmp_path / "weak_signals.jsonl"
        ws.write_text(
            "".join(
                json.dumps(_ws(slug, key=f"{slug}-{i}")) + "\n"
                for slug in ("alpha", "beta", "delta", "epsilon")
                for i in range(2)
            ),
            encoding="utf-8",
        )
        corr = tmp_path / "corrections.jsonl"
        corr.write_text("", encoding="utf-8")
        delta_dir = tmp_path / "delta"
        delta_dir.mkdir()

        from weak_signals import store as weak_store

        real_reader = weak_store.read_signals
        calls = []

        def _counting_reader(path=None):
            calls.append(path)
            return real_reader(path)

        monkeypatch.setattr(weak_store, "read_signals", _counting_reader)

        result = fq.build_queue_result(
            pj_slugs=["alpha", "beta"],
            threshold=1,
            weak_signals_path=ws,
            corrections_path=corr,
            last_evolve_map={},
            activity_map={},
            generated_at="2026-06-25T09:00:00Z",
            material_slugs=["alpha", "beta", "delta", "epsilon"],
            untracked_dir_map={"delta": str(delta_dir)},
        )

        assert calls == [ws]
        assert {item["pj_slug"] for item in result["queue"]} == {"alpha", "beta"}
        assert result["untracked_with_material"][0]["pj_slug"] == "delta"
        assert result["skipped_phantom"][0]["pj_slug"] == "epsilon"

    def test_corrections_jsonl_read_exactly_once_including_untracked_and_phantom(
        self, tmp_path, monkeypatch
    ):
        """#538 round3 [Must]1・[Must]3・round7: untracked/phantom collectors も
        corrections.jsonl を再度読まない。

        round2 の同名テストは ``material_slugs``/``untracked_dir_map`` を渡していなかったため、
        ``collect_untracked_materials``/``collect_phantom_materials`` が独自に
        ``new_corrections_by_pj(..., corrections_path=...)`` で再 read する経路を通らず、
        「read は1回」が実際には成立していなかった（call 回数が1のまま緑になっていた）。
        ここでは untracked（実 dir あり）と phantom（実 dir なし）の両方を material_slugs に
        含めて経路を強制的に通し、それでも物理 read 回数が1回であることを検査する。
        """
        ws = tmp_path / "weak_signals.jsonl"
        ws.write_text("")
        corr = tmp_path / "corrections.jsonl"
        corr.write_text(
            "".join(
                json.dumps(_corr(slug, "2026-06-01T00:00:00+00:00")) + "\n"
                for slug in ("alpha", "delta", "epsilon", "epsilon", "epsilon")
            )
        )
        delta_dir = tmp_path / "delta"
        delta_dir.mkdir()

        real_reader = fq.read_corrections_records_with_health
        calls = []

        def _counting_reader(path):
            calls.append(path)
            return real_reader(path)

        monkeypatch.setattr(fq, "read_corrections_records_with_health", _counting_reader)

        result = fq.build_queue_result(
            pj_slugs=["alpha"],
            threshold=1,
            weak_signals_path=ws,
            corrections_path=corr,
            last_evolve_map={},
            activity_map={},
            generated_at="2026-06-25T09:00:00Z",
            material_slugs=["alpha", "delta", "epsilon"],
            untracked_dir_map={"delta": str(delta_dir)},  # epsilon は実 dir 無し=phantom
        )

        assert len(calls) == 1, f"corrections.jsonl was read {len(calls)} times, expected 1"
        untracked = {u["pj_slug"]: u for u in result["untracked_with_material"]}
        phantom = {p["pj_slug"]: p for p in result["skipped_phantom"]}
        assert untracked["delta"]["new_corrections"] == 1
        assert phantom["epsilon"]["new_corrections"] == 3

    def test_permission_denied_parent_dir_marks_unreadable_not_empty(self, tmp_path):
        """#538 round3 [Must]2: 親ディレクトリの検索権限が無いと ``exists()``/``is_symlink()`` は
        揃って ``OSError`` を握りつぶし False を返すため、素通しすると「正常な空在庫」に
        誤判定していた（旧実装の欠陥）。read を試みて権限エラーを区別する。
        """
        if os.geteuid() == 0:
            pytest.skip("root では chmod 000 が effective でない")
        parent = tmp_path / "locked"
        parent.mkdir()
        store = parent / "corrections.jsonl"
        store.write_text(json.dumps(_corr("alpha", "t")) + "\n")
        os.chmod(parent, 0o000)
        try:
            records, health = fq.read_corrections_records_with_health(store)
        finally:
            os.chmod(parent, 0o755)
        assert records == []
        assert health["readable"] is False
        assert health["error"]


class TestBuildQueueResultPublicApi:
    """#538 round8。脅威モデル: このモジュールが守るのは未改変の production 経路
    （公開 API/CLI が corrections.jsonl を1回 read し、その1つの snapshot から records と
    health を組で下流へ渡すこと）。テストが module 属性を monkeypatch で差し替える経路は
    対象外（Python では原理的に防げないため設計の対象にしない）。「差し替えれば偽装できる」
    系のテストは資産にならないため round8 で削除した（設計経緯は issue 履歴参照）。
    """

    def test_public_api_reads_from_corrections_path(self, tmp_path):
        """陽性対照: 公開 API は ``corrections_path`` から実 read して正しく集計する。"""
        ws = tmp_path / "weak_signals.jsonl"
        ws.write_text("")
        corr = tmp_path / "corrections.jsonl"
        corr.write_text(json.dumps(_corr("alpha", "2026-06-01T00:00:00+00:00")) + "\n")

        result = fq.build_queue_result(
            pj_slugs=["alpha"],
            threshold=1,
            weak_signals_path=ws,
            corrections_path=corr,
            last_evolve_map={},
            activity_map={},
            generated_at="2026-06-25T09:00:00Z",
        )
        assert [item["pj_slug"] for item in result["queue"]] == ["alpha"]
        assert result["queue"][0]["new_corrections"] == 1

    def test_permission_error_reports_setup_required_not_empty(self, tmp_path):
        """#538 round8 (3): この PR 本来の目的（#533）が縮小後も維持されていることの実測。
        corrections.jsonl の ``lstat()`` が ``PermissionError`` になったとき、
        queue が「本当に空」（EMPTY・在庫ゼロ）と誤報告せず、「読めていない」
        （SETUP_REQUIRED・``corrections_read_health.readable=False``）と区別して報告する。
        """
        ws = tmp_path / "weak_signals.jsonl"
        ws.write_text("")
        corr = tmp_path / "corrections.jsonl"
        corr.write_text(json.dumps(_corr("alpha", "t")) + "\n")

        from unittest import mock

        def _raise_permission(self):
            raise PermissionError(13, "Permission denied", str(self))

        with mock.patch.object(Path, "lstat", _raise_permission):
            result = fq.build_queue_result(
                pj_slugs=["alpha"],
                threshold=1,
                weak_signals_path=ws,
                corrections_path=corr,
                last_evolve_map={},
                activity_map={},
                generated_at="2026-06-25T09:00:00Z",
            )

        assert result["queue"] == []
        assert result["queue_status"] == "SETUP_REQUIRED"
        assert result["corrections_read_health"]["readable"] is False

    def test_reader_exception_propagates_not_silently_swallowed(self, tmp_path, monkeypatch):
        """実 read が例外を投げた場合、``build_queue_result`` はそれを握りつぶさず伝播させる。
        #533 の原問題（読取失敗が silent に在庫ゼロへ丸められる）を再導入しないことを確認する。
        """
        ws = tmp_path / "weak_signals.jsonl"
        ws.write_text("")
        corr = tmp_path / "corrections.jsonl"
        corr.write_text("")

        def _boom(_path):
            raise RuntimeError("simulated reader failure")

        monkeypatch.setattr(fq, "read_corrections_records_with_health", _boom)

        with pytest.raises(RuntimeError, match="simulated reader failure"):
            fq.build_queue_result(
                pj_slugs=["alpha"],
                threshold=1,
                weak_signals_path=ws,
                corrections_path=corr,
                last_evolve_map={},
                activity_map={},
                generated_at="2026-06-25T09:00:00Z",
            )

    def test_corrections_snapshot_reexported_from_top_level_fleet_package(self):
        """``CorrectionsSnapshot``（``read_corrections_records_with_health`` の戻り値型）は
        同じ公開 namespace（``from fleet import X``）から import 可能であること。
        """
        import fleet

        assert fleet.CorrectionsSnapshot is fq.CorrectionsSnapshot


class TestBuildQueueResultFromSnapshotPrivateHelper:
    """#538 round7 [Must]I2/I4: 純集計 private helper ``_build_queue_result_from_snapshot``
    の直接検査。

    team-lead 指示: 「テストは private helper に対して行う。公開 API に注入口を残さない。
    テストが private を触るのは正当です（内部の整合性契約であってセキュリティ境界では
    ないため）」。ここでは記録した ``corr_records``/``corr_read_health`` の組が一貫して
    使われることと、private helper 自体は I/O を一切行わないことを検査する。
    """

    def test_private_helper_uses_passed_records_and_health_consistently(self, tmp_path):
        """private helper に渡した ``corr_records``/``corr_read_health`` が一貫して
        使われる（disk 上の ``corrections_path`` の実ファイルは一切参照されない）。
        """
        ws = tmp_path / "weak_signals.jsonl"
        ws.write_text("")
        corr = tmp_path / "corrections.jsonl"
        corr.write_text(json.dumps(_corr("alpha", "t")) + "\n")  # 実ファイルは alpha のみ

        sentinel_health = {"readable": True, "error": None, "malformed_lines": 0}
        result = fq._build_queue_result_from_snapshot(
            pj_slugs=["sentinel-pj"],
            threshold=1,
            weak_signals_path=ws,
            corrections_path=corr,
            last_evolve_map={},
            activity_map={},
            generated_at="2026-06-25T09:00:00Z",
            corr_records=[
                {"project_path": "sentinel-pj", "timestamp": "2026-06-01T00:00:00+00:00"}
            ],
            corr_read_health=sentinel_health,
            weak_records=[],
            weak_read_health={"sources": []},
        )
        assert result["corrections_read_health"] == sentinel_health
        assert [item["pj_slug"] for item in result["queue"]] == ["sentinel-pj"]
        assert result["queue"][0]["new_corrections"] == 1  # sentinel のみ・alpha は不使用

    def test_private_helper_performs_zero_io(self, tmp_path, monkeypatch):
        """private helper は corrections.jsonl を一切 read しない（渡された値だけを使う）。
        壊す不変条件: 「private helper は I/O を行わない純関数」。通したい検査経路:
        ``corrections_path`` が実在しない・アクセス不能でも動くこと。
        """
        ws = tmp_path / "weak_signals.jsonl"
        ws.write_text("")

        def _fail_if_called(path):
            raise AssertionError("private helper は read_corrections_records_with_health を呼んではいけない")

        monkeypatch.setattr(fq, "read_corrections_records_with_health", _fail_if_called)

        nonexistent = tmp_path / "does-not-exist" / "corrections.jsonl"
        result = fq._build_queue_result_from_snapshot(
            pj_slugs=["alpha"],
            threshold=1,
            weak_signals_path=ws,
            corrections_path=nonexistent,  # 存在しないパスでも動く＝read しない証拠
            last_evolve_map={},
            activity_map={},
            generated_at="2026-06-25T09:00:00Z",
            corr_records=[{"project_path": "alpha", "timestamp": "2026-06-01T00:00:00+00:00"}],
            corr_read_health={"readable": True, "error": None, "malformed_lines": 0},
            weak_records=[],
            weak_read_health={"sources": []},
        )
        assert result["queue"][0]["new_corrections"] == 1

    def test_private_helper_performs_zero_io_with_untracked_and_phantom_paths(
        self, tmp_path, monkeypatch
    ):
        """自主構成の破綻経路①（team-lead 指摘とは種類の異なるもの）: private helper の
        zero-I/O 保証は ``material_slugs``/``untracked_dir_map`` を渡し
        ``collect_untracked_materials``/``collect_phantom_materials`` の経路を強制的に通しても
        成立すること。#538 round3 の原バグ（untracked/phantom collector が独自に
        corrections.jsonl を再 read していた）を、round7 のプライベート化後に再導入して
        いないかを確認する（壊す不変条件: 「helper 経由の全下流 collector が I/O をしない」。
        通したい検査経路: untracked（実 dir あり）+ phantom（実 dir なし）の両方が material
        に乗る経路）。
        """
        ws = tmp_path / "weak_signals.jsonl"
        ws.write_text("")

        def _fail_if_called(path):
            raise AssertionError("下流 collector が corrections.jsonl を再 read してはいけない")

        monkeypatch.setattr(fq, "read_corrections_records_with_health", _fail_if_called)

        delta_dir = tmp_path / "delta"
        delta_dir.mkdir()
        nonexistent = tmp_path / "does-not-exist" / "corrections.jsonl"

        result = fq._build_queue_result_from_snapshot(
            pj_slugs=["alpha"],
            threshold=1,
            weak_signals_path=ws,
            corrections_path=nonexistent,
            last_evolve_map={},
            activity_map={},
            generated_at="2026-06-25T09:00:00Z",
            material_slugs=["alpha", "delta", "epsilon"],
            untracked_dir_map={"delta": str(delta_dir)},  # epsilon は実 dir 無し=phantom
            corr_records=[
                _corr("alpha", "2026-06-01T00:00:00+00:00"),
                _corr("delta", "2026-06-01T00:00:00+00:00"),
                _corr("epsilon", "2026-06-01T00:00:00+00:00"),
            ],
            corr_read_health={"readable": True, "error": None, "malformed_lines": 0},
            weak_records=[],
            weak_read_health={"sources": []},
        )
        untracked = {u["pj_slug"]: u for u in result["untracked_with_material"]}
        phantom = {p["pj_slug"]: p for p in result["skipped_phantom"]}
        assert untracked["delta"]["new_corrections"] == 1
        assert phantom["epsilon"]["new_corrections"] == 1

    def test_private_helper_records_consumed_only_once_not_a_single_use_iterator(
        self, tmp_path
    ):
        """自主構成の破綻経路②（team-lead 指摘とは種類の異なるもの・#538 round8 で修正）:
        ``corr_records`` に list でなく一度しか iterate できない generator を渡しても、
        下流集計（backlog counts / weak+corr ×N PJ / untracked・phantom collectors）が
        全て正しい件数を見る。壊す不変条件:「全下流が同じ corr_records を完全に見る」。
        通したい検査経路: material 母集団が2箇所以上（backlog counts と per-PJ 集計）で
        同じ corr_records を参照する経路。

        修正前は private helper が ``corr_records`` を list に実体化せずそのままイテレート
        していたため、generator を渡すと1回目の消費（backlog counts）で使い果たされ、
        後続の per-PJ 集計が silent に 0 件へ落ちていた（`records` が1件あるのに
        ``queue == []`` になる欠陥）。private helper の入口で ``list(corr_records)`` に
        実体化することで解消した。
        """
        ws = tmp_path / "weak_signals.jsonl"
        ws.write_text("")

        def _records_gen():
            yield _corr("alpha", "2026-06-01T00:00:00+00:00")

        result = fq._build_queue_result_from_snapshot(
            pj_slugs=["alpha"],
            threshold=1,
            weak_signals_path=ws,
            corrections_path=tmp_path / "corrections.jsonl",
            last_evolve_map={},
            activity_map={},
            generated_at="2026-06-25T09:00:00Z",
            corr_records=_records_gen(),
            corr_read_health={"readable": True, "error": None, "malformed_lines": 0},
            weak_records=[],
            weak_read_health={"sources": []},
        )
        assert [item["pj_slug"] for item in result["queue"]] == ["alpha"]
        assert result["queue"][0]["new_corrections"] == 1


class TestGatherQueueResultSingleRead:
    """#538 round3 [Must]4: 通常 CLI 経路（``_gather_queue_result``）でも corrections.jsonl の
    read は1回だけ。material 母集団収集（``_collect_material_slugs``）と ``build_queue_result``
    が別々に read すると、両呼び出しの間で untracked PJ の corrections が増減した場合に
    material 母集団だけ旧 snapshot になる（health/counts と食い違う）。
    """

    def test_corrections_read_once_across_material_slugs_and_build_queue_result(
        self, tmp_path, monkeypatch
    ):
        import argparse

        import fleet
        import fleet_config
        from fleet import cli as fcli
        from fleet import collectors as fcollectors
        from fleet import queue_materials as qm
        from fleet import queue_state as fqstate

        data_dir = tmp_path / "data"
        data_dir.mkdir()
        ws = data_dir / "weak_signals.jsonl"
        ws.write_text("")
        corr = data_dir / "corrections.jsonl"
        corr.write_text(json.dumps(_corr("alpha", "2026-06-01T00:00:00+00:00")) + "\n")

        tracked_dir = tmp_path / "alpha"
        tracked_dir.mkdir()

        monkeypatch.setattr(fleet, "_current_data_dir", lambda: data_dir)
        monkeypatch.setattr(
            fleet_config, "load_config", lambda: {"tracked_projects": [str(tracked_dir)]}
        )
        monkeypatch.setattr(fleet_config, "discover_cc_projects", lambda: [])
        monkeypatch.setattr(fleet_config, "filter_valid_projects", lambda paths: paths)
        monkeypatch.setattr(fcollectors, "aggregate_subagents_by_project", lambda: {})
        monkeypatch.setattr(fcollectors, "aggregate_sessions_by_project", lambda: {})
        monkeypatch.setattr(fqstate, "read_last_evolve", lambda *, data_dir=None: {})

        real_reader = qm.read_corrections_records_with_health
        calls = []

        def _counting_reader(path):
            calls.append(path)
            return real_reader(path)

        monkeypatch.setattr(qm, "read_corrections_records_with_health", _counting_reader)
        # ``build_queue_result``（fleet.queue）は queue_materials から re-export された
        # module-level 束縛を bare 名で直接呼ぶため、qm 側だけの patch では拾えない
        # （フォールバック経路のみを叩いて「読めていた」ことにする偽陰性を防ぐ）。
        monkeypatch.setattr(fq, "read_corrections_records_with_health", _counting_reader)

        args = argparse.Namespace(threshold=1, root=None)
        result = fcli._gather_queue_result(args)

        assert len(calls) == 1, f"corrections.jsonl was read {len(calls)} times, expected 1"
        assert [item["pj_slug"] for item in result["queue"]] == ["alpha"]
        assert result["queue"][0]["new_corrections"] == 1


# --- CLI --json 出力 ----------------------------------------------------------


class TestQueueCli:
    def test_json_flag_emits_valid_contract(self, tmp_path, monkeypatch, capsys):
        ws = tmp_path / "weak_signals.jsonl"
        ws.write_text("".join(json.dumps(_ws("alpha", key=f"a{i}")) + "\n" for i in range(4)))
        corr = tmp_path / "corrections.jsonl"
        corr.write_text("")

        from fleet import cli as fcli

        def _fake_gather(args):
            return fq.build_queue_result(
                pj_slugs=["alpha"],
                threshold=args.threshold,
                weak_signals_path=ws,
                corrections_path=corr,
                last_evolve_map={},
                activity_map={},
                generated_at="2026-06-25T09:00:00Z",
            )

        monkeypatch.setattr(fcli, "_gather_queue_result", _fake_gather)

        rc = fcli.main(["queue", "--json", "--threshold", "3"])
        assert rc == 0
        out = capsys.readouterr().out
        data = json.loads(out)
        assert data["threshold"] == 3
        assert data["queue"][0]["pj_slug"] == "alpha"
        assert data["queue"][0]["material_count"] == 4
        # #538 round2 [Must]5 変更1: corrections_read_health が JSON 出力前に落とされていないこと
        # を検査する（旧テストは threshold と queue しか見ておらず、この key の欠落を検出できなかった）。
        assert data["corrections_read_health"] == {
            "readable": True,
            "error": None,
            "malformed_lines": 0,
        }
        assert data["weak_signals_read_health"]["sources"][0]["readable"] is True

    def test_json_flag_surfaces_degraded_corrections_read_health(self, tmp_path, monkeypatch, capsys):
        """#538 round2 [Must]5 変更1: 劣化状態でも corrections_read_health が JSON に残ること。"""
        ws = tmp_path / "weak_signals.jsonl"
        ws.write_text("")
        corr = tmp_path / "corrections.jsonl"
        corr.write_text("{not valid json\n")

        from fleet import cli as fcli

        def _fake_gather(args):
            return fq.build_queue_result(
                pj_slugs=["quiet"],
                threshold=args.threshold,
                weak_signals_path=ws,
                corrections_path=corr,
                last_evolve_map={},
                activity_map={},
                generated_at="2026-06-25T09:00:00Z",
            )

        monkeypatch.setattr(fcli, "_gather_queue_result", _fake_gather)

        rc = fcli.main(["queue", "--json", "--threshold", "3"])
        assert rc == 0
        data = json.loads(capsys.readouterr().out)
        assert data["corrections_read_health"]["readable"] is True
        assert data["corrections_read_health"]["malformed_lines"] == 1
        assert data["queue_status"] == "SETUP_REQUIRED"

    def test_json_flag_surfaces_degraded_weak_signals_read_health(
        self, tmp_path, monkeypatch, capsys
    ):
        """#539: --json でも weak_signals の部分破損を構造化して残す。"""
        ws = tmp_path / "weak_signals.jsonl"
        ws.write_text("{not valid json\n", encoding="utf-8")
        corr = tmp_path / "corrections.jsonl"
        corr.write_text("", encoding="utf-8")

        from fleet import cli as fcli

        monkeypatch.setattr(
            fcli,
            "_gather_queue_result",
            lambda args: fq.build_queue_result(
                pj_slugs=["quiet"],
                threshold=args.threshold,
                weak_signals_path=ws,
                corrections_path=corr,
                last_evolve_map={},
                activity_map={},
                generated_at="2026-06-25T09:00:00Z",
            ),
        )

        assert fcli.main(["queue", "--json", "--threshold", "3"]) == 0
        data = json.loads(capsys.readouterr().out)
        assert data["weak_signals_read_health"]["sources"][0]["malformed_lines"] == 1
        assert data["queue_status"] == "SETUP_REQUIRED"

    def test_non_json_output_stays_silent_when_healthy(self, tmp_path, monkeypatch, capsys):
        """#538 round2 [Must]5 変更2: 非 JSON（人間向けテーブル）E2E。健全時は corrections.jsonl 注記を出さない。

        formatter 単体テスト（``TestFormatQueueTableCorrectionsReadHealth``）は
        ``format_queue_table`` を直接呼ぶため、CLI 層（``_run_queue``）が formatter を経由せず
        常時 health 行を追加するような配線ミスは検出できない。ここでは実際に
        ``fcli.main(["queue"])`` を呼び、標準出力の実文字列で検査する（陽性対照）。
        """
        ws = tmp_path / "weak_signals.jsonl"
        ws.write_text("".join(json.dumps(_ws("alpha", key=f"a{i}")) + "\n" for i in range(4)))
        corr = tmp_path / "corrections.jsonl"
        corr.write_text("")

        from fleet import cli as fcli

        def _fake_gather(args):
            return fq.build_queue_result(
                pj_slugs=["alpha"],
                threshold=args.threshold,
                weak_signals_path=ws,
                corrections_path=corr,
                last_evolve_map={},
                activity_map={},
                generated_at="2026-06-25T09:00:00Z",
            )

        monkeypatch.setattr(fcli, "_gather_queue_result", _fake_gather)

        rc = fcli.main(["queue", "--threshold", "3"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "corrections.jsonl" not in out

    def test_non_json_output_surfaces_degraded_read_via_full_cli_path(
        self, tmp_path, monkeypatch, capsys
    ):
        """#538 round2 [Must]5 変更2 の陰性試験: 劣化時は CLI 経由の非 JSON 出力にも注記が出る。"""
        ws = tmp_path / "weak_signals.jsonl"
        ws.write_text("")
        corr = tmp_path / "corrections.jsonl"
        corr.write_text("{not valid json\n")

        from fleet import cli as fcli

        def _fake_gather(args):
            return fq.build_queue_result(
                pj_slugs=["quiet"],
                threshold=args.threshold,
                weak_signals_path=ws,
                corrections_path=corr,
                last_evolve_map={},
                activity_map={},
                generated_at="2026-06-25T09:00:00Z",
            )

        monkeypatch.setattr(fcli, "_gather_queue_result", _fake_gather)

        rc = fcli.main(["queue", "--threshold", "3"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "corrections.jsonl" in out


# --- _collect_material_slugs: corrections 読取は共有 reader 経由（#538 round2 [Must]2）-------


class TestCollectMaterialSlugsUsesSharedReader:
    """``_collect_material_slugs`` の corrections 側 read が独自の silent-fail 実装でなく
    ``queue_materials.read_corrections_records_with_health``（queue read health の単一ソース）
    を経由することを検査する。
    """

    def test_delegates_to_shared_reader(self, tmp_path, monkeypatch):
        from fleet import cli as fcli
        from fleet.queue_materials import (
            _correction_slug,
            read_corrections_records_with_health,
        )

        corr = tmp_path / "corrections.jsonl"
        corr.write_text(json.dumps(_corr("alpha", "t")) + "\n")

        calls = []

        def _spy(path):
            calls.append(path)
            return read_corrections_records_with_health(path)

        monkeypatch.setattr(
            "fleet.queue_materials.read_corrections_records_with_health", _spy
        )

        slugs = fcli._collect_material_slugs(
            weak_signals_path=None,
            corrections_path=corr,
            correction_slug=_correction_slug,
        )
        assert calls == [corr], "corrections 側 read が共有 reader を経由していない"
        assert "alpha" in slugs

    def test_spy_return_value_is_sole_input_not_real_file(self, tmp_path, monkeypatch):
        """#538 round3 [Must]3: spy が返す値だけが結果へ反映されることを検査する。

        旧版のテストは spy 内で本物の reader を1回呼んで返値を捨てても（＝別の独自 read を
        復活させても）``calls == [corr]`` と ``"alpha" in slugs`` が両方通ってしまい、
        「spy の返値を唯一の入力として使っている」ことを固定できていなかった。実ファイルには
        存在しない sentinel record だけを spy から返し、その sentinel だけが slugs に出て
        実ファイルの内容（alpha）は出ないことを検査する。
        """
        from fleet import cli as fcli
        from fleet.queue_materials import _correction_slug

        corr = tmp_path / "corrections.jsonl"
        corr.write_text(json.dumps(_corr("alpha", "t")) + "\n")  # 実ファイルは alpha のみ

        sentinel_records = [{"project_path": "sentinel-pj", "timestamp": "t"}]

        def _spy(path):
            return sentinel_records, {"readable": True, "error": None, "malformed_lines": 0}

        monkeypatch.setattr(
            "fleet.queue_materials.read_corrections_records_with_health", _spy
        )

        slugs = fcli._collect_material_slugs(
            weak_signals_path=None,
            corrections_path=corr,
            correction_slug=_correction_slug,
        )
        assert "sentinel-pj" in slugs
        assert "alpha" not in slugs

    def test_corr_records_param_bypasses_read_entirely(self, tmp_path, monkeypatch):
        """corr_records を渡すと reader そのものを一切呼ばず、渡した records だけを使う。"""
        from fleet import cli as fcli
        from fleet.queue_materials import _correction_slug

        corr = tmp_path / "corrections.jsonl"
        corr.write_text(json.dumps(_corr("alpha", "t")) + "\n")  # 実ファイルは alpha のみ

        called = {"n": 0}

        def _fail_if_called(path):
            called["n"] += 1
            raise AssertionError("read_corrections_records_with_health は呼ばれてはいけない")

        monkeypatch.setattr(
            "fleet.queue_materials.read_corrections_records_with_health", _fail_if_called
        )

        slugs = fcli._collect_material_slugs(
            weak_signals_path=None,
            corrections_path=corr,
            correction_slug=_correction_slug,
            corr_records=[{"project_path": "sentinel-pj", "timestamp": "t"}],
        )
        assert called["n"] == 0
        assert "sentinel-pj" in slugs
        assert "alpha" not in slugs

    def test_os_error_falls_back_to_empty_without_crashing(self, tmp_path, monkeypatch):
        """旧実装は独自 try/except で空文字列へ倒していた。共有 reader 経由でも同じく落ちない。"""
        from fleet import cli as fcli
        from fleet.queue_materials import _correction_slug

        corr = tmp_path / "corrections.jsonl"
        corr.write_text(json.dumps(_corr("alpha", "t")) + "\n")

        def _raise(*_args, **_kwargs):
            raise OSError("Permission denied")

        monkeypatch.setattr(Path, "read_bytes", _raise)

        slugs = fcli._collect_material_slugs(
            weak_signals_path=None,
            corrections_path=corr,
            correction_slug=_correction_slug,
        )
        assert slugs == []  # 例外を投げずに空へ倒す（advisory ゆえ落とさない）

    def test_malformed_line_does_not_produce_phantom_slug(self, tmp_path, monkeypatch):
        """壊れた行から slug を作らない（従来の独自 skip と同じ挙動を共有 reader 経由でも保つ）。"""
        from fleet import cli as fcli
        from fleet.queue_materials import _correction_slug

        corr = tmp_path / "corrections.jsonl"
        corr.write_text("{not valid json\n" + json.dumps(_corr("beta", "t")) + "\n")

        slugs = fcli._collect_material_slugs(
            weak_signals_path=None,
            corrections_path=corr,
            correction_slug=_correction_slug,
        )
        assert slugs == ["beta"]


# --- pj_paths: dead PJ skip + project_path 伝播（繋ぎ目バグ #79）--------------


class TestPjPathsDeadSkip:
    def test_dead_dir_skipped_and_recorded(self, tmp_path):
        """pj_paths が指す dir が不在の PJ は queue に出ず skipped_dead に入る。"""
        ws = tmp_path / "weak_signals.jsonl"
        ws.write_text("".join(json.dumps(_ws("dead", key=f"d{i}")) + "\n" for i in range(7)))
        corr = tmp_path / "corrections.jsonl"
        corr.write_text("")
        dead_path = str(tmp_path / "no_such_pj")  # 実在しない
        result = fq.build_queue_result(
            pj_slugs=["dead"],
            threshold=3,
            weak_signals_path=ws,
            corrections_path=corr,
            last_evolve_map={},
            activity_map={},
            generated_at="2026-06-25T09:00:00Z",
            pj_paths={"dead": dead_path},
        )
        assert result["queue"] == []
        # #87 ②: skipped_dead entry は material 数を添えて透明化する
        assert result["skipped_dead"] == [
            {
                "pj_slug": "dead",
                "project_path": dead_path,
                "weak_unprocessed": 7,
                "new_corrections": 0,
                "correction_backlog": 0,
                "material_count": 7,
            }
        ]
        # tracked_total は dead 含む全 tracked 数のまま（沈黙させない・透明化）
        assert result["tracked_total"] == 1

    def test_live_dir_carries_project_path(self, tmp_path):
        """pj_paths が実在 dir を指す PJ は queue/material entry に project_path を持つ。"""
        live = tmp_path / "live_pj"
        live.mkdir()
        ws = tmp_path / "weak_signals.jsonl"
        ws.write_text("".join(json.dumps(_ws("live", key=f"l{i}")) + "\n" for i in range(5)))
        corr = tmp_path / "corrections.jsonl"
        corr.write_text("")
        result = fq.build_queue_result(
            pj_slugs=["live"],
            threshold=3,
            weak_signals_path=ws,
            corrections_path=corr,
            last_evolve_map={},
            activity_map={},
            generated_at="2026-06-25T09:00:00Z",
            pj_paths={"live": str(live)},
        )
        assert result["skipped_dead"] == []
        assert len(result["queue"]) == 1
        assert result["queue"][0]["project_path"] == str(live)

    def test_pj_paths_none_is_backward_compatible(self, tmp_path):
        """pj_paths 未指定（None）は全件 live・project_path=None・skipped_dead=[]（後方互換）。"""
        ws = tmp_path / "weak_signals.jsonl"
        ws.write_text("".join(json.dumps(_ws("alpha", key=f"a{i}")) + "\n" for i in range(5)))
        corr = tmp_path / "corrections.jsonl"
        corr.write_text("")
        result = fq.build_queue_result(
            pj_slugs=["alpha"],
            threshold=3,
            weak_signals_path=ws,
            corrections_path=corr,
            last_evolve_map={},
            activity_map={},
            generated_at="2026-06-25T09:00:00Z",
        )
        assert result["skipped_dead"] == []
        assert len(result["queue"]) == 1
        assert result["queue"][0]["project_path"] is None


# --- #87: rename-but-live PJ の redirect + skipped_dead 透明化 + activity fold ---


class TestRenameRedirect:
    """tracked が旧 dead path を指すが canonical 先が live dir に解決できる PJ を
    skipped_dead に飲み込まず live path に redirect して waiting に乗せる（#87 ①）。
    """

    def test_dead_tracked_redirects_to_canonical_live_dir(self, tmp_path):
        # tracked slug = 旧 dead "rl-anything"、store も旧 slug、discovery は現 live dir。
        live = tmp_path / "evolve-anything"
        live.mkdir()
        dead_path = str(tmp_path / "rl-anything")  # 実在しない（rename 済）
        ws = tmp_path / "weak_signals.jsonl"
        ws.write_text(
            "".join(json.dumps(_ws("rl-anything", key=f"r{i}")) + "\n" for i in range(7))
        )
        corr = tmp_path / "corrections.jsonl"
        corr.write_text("")
        result = fq.build_queue_result(
            pj_slugs=["rl-anything"],
            threshold=3,
            weak_signals_path=ws,
            corrections_path=corr,
            last_evolve_map={},
            activity_map={},
            generated_at="2026-06-25T09:00:00Z",
            pj_paths={"rl-anything": dead_path},
            untracked_dir_map={"evolve-anything": str(live)},
        )
        # skipped_dead に行かず waiting に出る（material は alias fold で 7 件）
        assert result["skipped_dead"] == []
        assert len(result["queue"]) == 1
        item = result["queue"][0]
        # redirect 後は canonical slug + live path で集計される
        assert item["pj_slug"] == "evolve-anything"
        assert item["project_path"] == str(live)
        assert item["weak_unprocessed"] == 7
        assert item["material_count"] == 7

    def test_unresolvable_dead_stays_skipped_with_material_count(self, tmp_path):
        """canonical 先が live dir に解決できない真の dead は skipped_dead に行き、
        かつ material 数（weak/corr/total）が添えられる（#87 ②透明化）。"""
        dead_path = str(tmp_path / "gone")  # 実在しない
        ws = tmp_path / "weak_signals.jsonl"
        ws.write_text(
            "".join(json.dumps(_ws("gone", key=f"g{i}")) + "\n" for i in range(4))
        )
        corr = tmp_path / "corrections.jsonl"
        backlog = _corr("gone", "2026-06-01T00:00:00+00:00")
        backlog.update({"reflect_status": "promoted", "invalidated": False})
        corr.write_text(json.dumps(backlog) + "\n")
        result = fq.build_queue_result(
            pj_slugs=["gone"],
            threshold=3,
            weak_signals_path=ws,
            corrections_path=corr,
            last_evolve_map={"gone": "2026-06-20T00:00:00+00:00"},
            activity_map={},
            generated_at="2026-06-25T09:00:00Z",
            pj_paths={"gone": dead_path},
            untracked_dir_map={},  # 解決先なし
        )
        assert result["queue"] == []
        assert len(result["skipped_dead"]) == 1
        sd = result["skipped_dead"][0]
        assert sd["pj_slug"] == "gone"
        assert sd["project_path"] == dead_path
        # 透明化: dead でも material 数を可視化
        assert sd["weak_unprocessed"] == 4
        assert sd["new_corrections"] == 0
        assert sd["correction_backlog"] == 1
        assert sd["material_count"] == 4
        assert "gone (backlog 1)" in format_queue_table(
            _result(skipped=result["skipped_dead"])
        )

    def test_redirect_not_attempted_without_untracked_dir_map(self, tmp_path):
        """untracked_dir_map=None（後方互換）なら redirect せず従来通り skipped_dead。
        ただし material 数の透明化（②）は施す。"""
        dead_path = str(tmp_path / "rl-anything")
        ws = tmp_path / "weak_signals.jsonl"
        ws.write_text(
            "".join(json.dumps(_ws("rl-anything", key=f"r{i}")) + "\n" for i in range(7))
        )
        corr = tmp_path / "corrections.jsonl"
        corr.write_text("")
        result = fq.build_queue_result(
            pj_slugs=["rl-anything"],
            threshold=3,
            weak_signals_path=ws,
            corrections_path=corr,
            last_evolve_map={},
            activity_map={},
            generated_at="2026-06-25T09:00:00Z",
            pj_paths={"rl-anything": dead_path},
        )
        assert result["queue"] == []
        assert len(result["skipped_dead"]) == 1
        sd = result["skipped_dead"][0]
        assert sd["pj_slug"] == "rl-anything"
        # untracked_dir_map=None でも weak は alias 非依存で旧 slug 直集計 7 件
        assert sd["material_count"] == 7


class TestFoldActivityCounts:
    """activity counts を alias fold して weak/corr と同じ namespace に揃える（#87 ③）。"""

    def test_legacy_tracked_slug_folds_canonical_counts(self):
        # counts は canonical slug "evolve-anything" でキー付け（collectors が畳む）。
        # tracked slug は旧 "rl-anything"。fold で現 slug の値を回収する。
        subagent_counts = {"evolve-anything": 12, "other": 3}
        session_counts = {"evolve-anything": 155, "other": 9}
        got = fq.fold_activity_counts(
            "rl-anything", subagent_counts, session_counts
        )
        assert got == {"subagents": 12, "sessions": 155}

    def test_plain_slug_passthrough(self):
        got = fq.fold_activity_counts(
            "other", {"other": 3}, {"other": 9}
        )
        assert got == {"subagents": 3, "sessions": 9}

    def test_missing_slug_zero(self):
        got = fq.fold_activity_counts("absent", {}, {})
        assert got == {"subagents": 0, "sessions": 0}

    def test_sums_across_aliases_when_both_present(self):
        # 旧 slug と現 slug の両方に値があれば合算する（重複しない event log 前提）。
        got = fq.fold_activity_counts(
            "evolve-anything",
            {"evolve-anything": 10, "rl-anything": 2},
            {"evolve-anything": 100, "rl-anything": 55},
        )
        assert got == {"subagents": 12, "sessions": 155}


class TestSelectQueueCarriesProjectPath:
    def test_project_path_propagated_to_selected(self):
        """select_evolve_queue は material の project_path を selected entry へ伝播する。"""
        mats = [
            {
                "pj_slug": "a",
                "weak_unprocessed": 3,
                "new_corrections": 0,
                "last_evolve_at": None,
                "activity_since": {"subagents": 0, "sessions": 0},
                "project_path": "/some/path/a",
            }
        ]
        out = fq.select_evolve_queue(mats, threshold=3)
        assert out[0]["project_path"] == "/some/path/a"


# --- alias fold: rename 済 PJ の旧 slug レコードを現 slug に集計（#79）---------


class TestAliasFold:
    def test_weak_unprocessed_folds_legacy_slug(self, tmp_path):
        """weak_signals の旧 slug "rl-anything" を現 slug "evolve-anything" で数える。"""
        store = tmp_path / "weak_signals.jsonl"
        recs = [
            _ws("rl-anything", key="r1"),
            _ws("rl-anything", key="r2"),
            _ws("unrelated", key="u1"),
        ]
        store.write_text("".join(json.dumps(r) + "\n" for r in recs))
        assert fq.weak_unprocessed_by_pj("evolve-anything", weak_signals_path=store) == 2
        # 無関係 slug は数えない
        assert fq.weak_unprocessed_by_pj("unrelated", weak_signals_path=store) == 1

    def test_new_corrections_folds_legacy_slug(self, tmp_path):
        """corrections の旧 slug "rl-anything" を現 slug "evolve-anything" で数える。"""
        store = tmp_path / "corrections.jsonl"
        recs = [
            _corr("rl-anything", "2026-06-10T00:00:00+00:00"),
            _corr("rl-anything", "2026-06-11T00:00:00+00:00"),
            _corr("other-pj", "2026-06-10T00:00:00+00:00"),
        ]
        store.write_text("".join(json.dumps(r) + "\n" for r in recs))
        records = _read_records(store)
        assert fq.new_corrections_by_pj(
            "evolve-anything", last_evolve_at=None, records=records
        ) == 2
        # 無関係 slug は数えない
        assert fq.new_corrections_by_pj(
            "other-pj", last_evolve_at=None, records=records
        ) == 1


# --- aggregate_sessions_by_project（activity_since.sessions の実値配線・#85）----


from fleet import collectors as fc  # noqa: E402


def _sess(session_id, ts, project):
    """テスト用 session レコード。distinct session_id を数えるので複数行で同 id を使える。"""
    rec = {"session_id": session_id, "timestamp": ts, "project": project}
    return rec


class TestAggregateSessions:
    """aggregate_sessions_by_project: session_store union read から distinct session_id を
    project 別に数える（#85）。canonical を tmp/evolve-anything にすると iter_read_data_dirs
    が canonical.parent を起点に候補を導出するため、兄弟 dir を作らなければ hermetic。
    """

    @staticmethod
    def _canonical(root: Path) -> Path:
        c = root / "evolve-anything"
        c.mkdir(parents=True, exist_ok=True)
        return c

    @staticmethod
    def _write(canonical: Path, records: list) -> None:
        (canonical / "sessions.jsonl").write_text(
            "".join(json.dumps(r) + "\n" for r in records)
        )

    def test_counts_distinct_session_ids_by_project(self, tmp_path):
        """同一 session_id 複数行は 1 とカウントし、project 別に分ける。"""
        canonical = self._canonical(tmp_path)
        self._write(
            canonical,
            [
                _sess("s1", "2026-06-20T00:00:00+00:00", "/p/alpha"),
                _sess("s1", "2026-06-20T01:00:00+00:00", "/p/alpha"),  # 同 session の別行
                _sess("s2", "2026-06-21T00:00:00+00:00", "/p/alpha"),
                _sess("s3", "2026-06-21T00:00:00+00:00", "/p/beta"),
            ],
        )
        now = __import__("datetime").datetime(2026, 6, 25, tzinfo=__import__("datetime").timezone.utc)
        counts = fc.aggregate_sessions_by_project(canonical=canonical, now=now)
        assert counts.get("alpha") == 2  # s1, s2（distinct）
        assert counts.get("beta") == 1

    def test_excludes_out_of_window(self, tmp_path):
        """window_days より古い record は数えない。"""
        canonical = self._canonical(tmp_path)
        self._write(
            canonical,
            [
                _sess("recent", "2026-06-24T00:00:00+00:00", "/p/alpha"),
                _sess("old", "2026-01-01T00:00:00+00:00", "/p/alpha"),  # 窓外
            ],
        )
        now = __import__("datetime").datetime(2026, 6, 25, tzinfo=__import__("datetime").timezone.utc)
        counts = fc.aggregate_sessions_by_project(
            canonical=canonical, now=now, window_days=30
        )
        assert counts.get("alpha") == 1  # recent のみ

    def test_empty_project_goes_to_unknown(self, tmp_path):
        """空 / 欠損 project は (unknown) に分類する。"""
        canonical = self._canonical(tmp_path)
        self._write(
            canonical,
            [
                _sess("s1", "2026-06-20T00:00:00+00:00", ""),
                {"session_id": "s2", "timestamp": "2026-06-20T00:00:00+00:00"},  # project 欠損
            ],
        )
        now = __import__("datetime").datetime(2026, 6, 25, tzinfo=__import__("datetime").timezone.utc)
        counts = fc.aggregate_sessions_by_project(canonical=canonical, now=now)
        assert counts.get(fc._UNKNOWN_PROJECT_LABEL) == 2

    def test_missing_session_id_not_counted(self, tmp_path):
        """session_id 欠損 / 空の record は distinct 母数に入らない。"""
        canonical = self._canonical(tmp_path)
        self._write(
            canonical,
            [
                _sess("s1", "2026-06-20T00:00:00+00:00", "/p/alpha"),
                {"timestamp": "2026-06-20T00:00:00+00:00", "project": "/p/alpha"},  # id 欠損
                _sess("", "2026-06-20T00:00:00+00:00", "/p/alpha"),  # 空 id
            ],
        )
        now = __import__("datetime").datetime(2026, 6, 25, tzinfo=__import__("datetime").timezone.utc)
        counts = fc.aggregate_sessions_by_project(canonical=canonical, now=now)
        assert counts.get("alpha") == 1  # s1 のみ

    def test_empty_when_no_data(self, tmp_path):
        canonical = self._canonical(tmp_path)
        now = __import__("datetime").datetime(2026, 6, 25, tzinfo=__import__("datetime").timezone.utc)
        assert fc.aggregate_sessions_by_project(canonical=canonical, now=now) == {}

    def test_since_overrides_window_days(self, tmp_path):
        """C2: since 指定時は window_days より優先し、その時刻以降のみ数える。"""
        import datetime as _dt

        canonical = self._canonical(tmp_path)
        self._write(
            canonical,
            [
                _sess("before", "2026-06-10T00:00:00+00:00", "/p/alpha"),  # since より前
                _sess("after", "2026-06-20T00:00:00+00:00", "/p/alpha"),  # since より後
            ],
        )
        since = _dt.datetime(2026, 6, 15, tzinfo=_dt.timezone.utc)
        # window_days=30・now=2026-06-25 だけなら両方窓内だが、since が優先されるべき。
        now = _dt.datetime(2026, 6, 25, tzinfo=_dt.timezone.utc)
        counts = fc.aggregate_sessions_by_project(
            canonical=canonical, now=now, window_days=30, since=since
        )
        assert counts.get("alpha") == 1  # after のみ

    def test_since_none_falls_back_to_window_days(self, tmp_path):
        """C2: since 未指定は従来の window_days 挙動のまま（後方互換）。"""
        canonical = self._canonical(tmp_path)
        self._write(
            canonical,
            [
                _sess("recent", "2026-06-24T00:00:00+00:00", "/p/alpha"),
                _sess("old", "2026-01-01T00:00:00+00:00", "/p/alpha"),  # 窓外
            ],
        )
        now = __import__("datetime").datetime(2026, 6, 25, tzinfo=__import__("datetime").timezone.utc)
        counts = fc.aggregate_sessions_by_project(
            canonical=canonical, now=now, window_days=30, since=None
        )
        assert counts.get("alpha") == 1  # recent のみ（旧挙動と同じ）


# --- collect_untracked_materials（material 母集団まで母数拡張・#86）------------


class TestCollectUntrackedMaterials:
    """material を持つ untracked PJ を advisory として surface する純関数（#86 O2）。"""

    def _stores(self, tmp_path, weak_recs, corr_recs):
        ws = tmp_path / "weak_signals.jsonl"
        ws.write_text("".join(json.dumps(r) + "\n" for r in weak_recs))
        corr = tmp_path / "corrections.jsonl"
        corr.write_text("".join(json.dumps(r) + "\n" for r in corr_recs))
        return ws, corr

    def test_surfaces_untracked_with_material_and_dir(self, tmp_path):
        """tracked 外 + 実 dir あり + material >= threshold は surface する。"""
        live = tmp_path / "amamo"
        live.mkdir()
        ws, corr = self._stores(
            tmp_path,
            [_ws("amamo", key=f"a{i}") for i in range(6)],
            [],
        )
        out = fq.collect_untracked_materials(
            material_slugs=["amamo"],
            tracked_slugs={"evolve-anything"},
            threshold=5,
            weak_signals_path=ws,
            corrections_path=corr,
            dir_map={"amamo": str(live)},
        )
        assert len(out) == 1
        item = out[0]
        assert item["pj_slug"] == "amamo"
        assert item["project_path"] == str(live)
        assert item["material_count"] == 6
        assert item["weak_unprocessed"] == 6
        assert item["new_corrections"] == 0

    def test_tracked_slug_excluded(self, tmp_path):
        """tracked に既にある slug は untracked から除外する。"""
        live = tmp_path / "amamo"
        live.mkdir()
        ws, corr = self._stores(tmp_path, [_ws("amamo", key=f"a{i}") for i in range(6)], [])
        out = fq.collect_untracked_materials(
            material_slugs=["amamo"],
            tracked_slugs={"amamo"},  # 既に tracked
            threshold=5,
            weak_signals_path=ws,
            corrections_path=corr,
            dir_map={"amamo": str(live)},
        )
        assert out == []

    def test_phantom_no_dir_excluded(self, tmp_path):
        """dir_map に無い / 実 dir 不在の slug（phantom/temp）は除外する。"""
        ws, corr = self._stores(tmp_path, [_ws("ghost", key=f"g{i}") for i in range(6)], [])
        # dir_map に ghost が無い
        out_missing = fq.collect_untracked_materials(
            material_slugs=["ghost"],
            tracked_slugs=set(),
            threshold=5,
            weak_signals_path=ws,
            corrections_path=corr,
            dir_map={},
        )
        assert out_missing == []
        # dir_map にあるが dir 不在
        out_dead = fq.collect_untracked_materials(
            material_slugs=["ghost"],
            tracked_slugs=set(),
            threshold=5,
            weak_signals_path=ws,
            corrections_path=corr,
            dir_map={"ghost": str(tmp_path / "no_such")},
        )
        assert out_dead == []

    def test_below_threshold_excluded(self, tmp_path):
        """material < threshold は surface しない。"""
        live = tmp_path / "quiet"
        live.mkdir()
        ws, corr = self._stores(tmp_path, [_ws("quiet", key="q1")], [])
        out = fq.collect_untracked_materials(
            material_slugs=["quiet"],
            tracked_slugs=set(),
            threshold=5,
            weak_signals_path=ws,
            corrections_path=corr,
            dir_map={"quiet": str(live)},
        )
        assert out == []

    def test_promoted_backlog_surfaces_below_threshold_untracked(self, tmp_path):
        """#515: tracked 外でも promoted 在庫1件なら閾値未満で沈黙しない。"""
        live = tmp_path / "dormant"
        live.mkdir()
        ws, corr = self._stores(tmp_path, [], [])
        out = fq.collect_untracked_materials(
            material_slugs=[],
            tracked_slugs=set(),
            threshold=5,
            weak_signals_path=ws,
            corrections_path=corr,
            dir_map={"dormant": str(live)},
            correction_backlog_counts={"dormant": 1},
        )
        assert out == [
            {
                "pj_slug": "dormant",
                "project_path": str(live),
                "material_count": 0,
                "weak_unprocessed": 0,
                "new_corrections": 0,
                "correction_backlog": 1,
            }
        ]

    def test_promoted_backlog_surfaces_below_threshold_phantom(self, tmp_path):
        """#515: 実 dir 未解決でも promoted 在庫1件を phantom として透明化する。"""
        ws, corr = self._stores(tmp_path, [], [])
        out = fq.collect_phantom_materials(
            material_slugs=[],
            tracked_slugs=set(),
            threshold=5,
            weak_signals_path=ws,
            corrections_path=corr,
            dir_map={},
            correction_backlog_counts={"ghost": 1},
        )
        assert out == [
            {
                "pj_slug": "ghost",
                "material_count": 0,
                "weak_unprocessed": 0,
                "new_corrections": 0,
                "correction_backlog": 1,
            }
        ]

    def test_legacy_slug_folds_into_tracked_and_excluded(self, tmp_path):
        """canonical fold で旧 slug rl-anything が現 slug evolve-anything の tracked に畳まれ除外。"""
        live = tmp_path / "evolve-anything"
        live.mkdir()
        ws, corr = self._stores(
            tmp_path, [_ws("rl-anything", key=f"r{i}") for i in range(6)], []
        )
        out = fq.collect_untracked_materials(
            material_slugs=["rl-anything"],  # 旧 slug の material
            tracked_slugs={"evolve-anything"},  # 現 slug が tracked
            threshold=5,
            weak_signals_path=ws,
            corrections_path=corr,
            dir_map={"evolve-anything": str(live)},
        )
        # rl-anything は canonical_pj_slug で evolve-anything に畳まれ tracked 済み → 除外
        assert out == []

    def test_sorted_by_material_desc_then_slug(self, tmp_path):
        """material_count 降順・同数は pj_slug 昇順で返す。"""
        for name in ("aaa", "bbb", "ccc"):
            (tmp_path / name).mkdir()
        ws, corr = self._stores(
            tmp_path,
            [_ws("aaa", key=f"a{i}") for i in range(5)]
            + [_ws("bbb", key=f"b{i}") for i in range(9)]
            + [_ws("ccc", key=f"c{i}") for i in range(5)],
            [],
        )
        out = fq.collect_untracked_materials(
            material_slugs=["ccc", "aaa", "bbb"],
            tracked_slugs=set(),
            threshold=5,
            weak_signals_path=ws,
            corrections_path=corr,
            dir_map={n: str(tmp_path / n) for n in ("aaa", "bbb", "ccc")},
        )
        assert [(o["pj_slug"], o["material_count"]) for o in out] == [
            ("bbb", 9),
            ("aaa", 5),
            ("ccc", 5),
        ]


class TestBuildQueueResultUntracked:
    def test_untracked_with_material_default_empty(self, tmp_path):
        """material_slugs/untracked_dir_map 未指定（None）は untracked_with_material==[]（後方互換）。"""
        ws = tmp_path / "weak_signals.jsonl"
        ws.write_text("".join(json.dumps(_ws("alpha", key=f"a{i}")) + "\n" for i in range(5)))
        corr = tmp_path / "corrections.jsonl"
        corr.write_text("")
        result = fq.build_queue_result(
            pj_slugs=["alpha"],
            threshold=3,
            weak_signals_path=ws,
            corrections_path=corr,
            last_evolve_map={},
            activity_map={},
            generated_at="2026-06-25T09:00:00Z",
        )
        assert result["untracked_with_material"] == []

    def test_untracked_surfaced_when_inputs_given(self, tmp_path):
        """material_slugs + untracked_dir_map を渡すと untracked が surface され tracked_total は不変。"""
        # tracked: alpha（実 dir 任意）。untracked: amamo（material 6・実 dir あり）。
        amamo = tmp_path / "amamo"
        amamo.mkdir()
        ws = tmp_path / "weak_signals.jsonl"
        recs = [_ws("alpha", key=f"al{i}") for i in range(5)] + [
            _ws("amamo", key=f"am{i}") for i in range(6)
        ]
        ws.write_text("".join(json.dumps(r) + "\n" for r in recs))
        corr = tmp_path / "corrections.jsonl"
        corr.write_text("")
        result = fq.build_queue_result(
            pj_slugs=["alpha"],
            threshold=5,
            weak_signals_path=ws,
            corrections_path=corr,
            last_evolve_map={},
            activity_map={},
            generated_at="2026-06-25T09:00:00Z",
            material_slugs=["alpha", "amamo"],
            untracked_dir_map={"amamo": str(amamo)},
        )
        assert result["tracked_total"] == 1  # tracked 母数のまま
        um = result["untracked_with_material"]
        assert [u["pj_slug"] for u in um] == ["amamo"]
        assert um[0]["material_count"] == 6


# --- format_queue_table の footer/untracked 表示（#86 O1+O2）-------------------


from fleet.formatters import format_queue_table  # noqa: E402


def _result(
    queue=None,
    tracked=10,
    untracked=None,
    skipped=None,
    phantom=None,
    threshold=5,
    unattributed=None,
    queue_status=None,
    queue_status_reason=None,
    corrections_read_health=None,
):
    out = {
        "generated_at": "2026-06-25T09:00:00Z",
        "threshold": threshold,
        "tracked_total": tracked,
        "queue": queue or [],
        "skipped_dead": skipped or [],
        "untracked_with_material": untracked or [],
        "skipped_phantom": phantom or [],
        "unattributed_corrections": unattributed or {"total": 0, "by_source": {}},
    }
    if queue_status is not None:
        out["queue_status"] = queue_status
        out["queue_status_reason"] = queue_status_reason
    if corrections_read_health is not None:
        out["corrections_read_health"] = corrections_read_health
    return out


class TestFormatQueueTableColdstart:
    """純 cold-start（全待ち PJ が未 evolve）時の material 意味警告（A・tacchi 採点）。

    cold-start では new_corrections が「前回 evolve 以降の増分」でなく全履歴 backlog の
    全件計上になるため、material_count は velocity でなく累積量を表す。この非互換を
    純 cold-start 時だけ surface する（一部でも drained なら混在ノイズなので出さない）。
    """

    @staticmethod
    def _q(slug, last):
        return {
            "pj_slug": slug,
            "material_count": 9,
            "weak_unprocessed": 5,
            "new_corrections": 4,
            "last_evolve_at": last,
            "reason": "x",
        }

    def test_coldstart_notice_when_all_never(self):
        """全待ち PJ が last_evolve_at=None なら累積量順の警告を出す。"""
        q = [self._q("a", None), self._q("b", None)]
        out = format_queue_table(_result(queue=q))
        assert "累積量順" in out
        assert "velocity" in out
        assert "増分のみ" in out

    def test_backlog_header_and_cell_are_rendered(self):
        """#515 review: JSON 配線だけでなく人間向け BACKLOG 列も固定する。"""
        q = [self._q("dormant", "2026-06-01T00:00:00+00:00")]
        q[0]["correction_backlog"] = 29
        out = format_queue_table(_result(queue=q))
        header, separator, row = out.splitlines()[:3]
        assert "NEW_CORR" in header
        assert "BACKLOG" in header
        assert separator
        assert "29" in row

    def test_coldstart_silent_when_any_drained(self):
        """1 件でも drain 済（last_evolve_at あり）なら混在ノイズなので出さない。"""
        q = [self._q("a", None), self._q("b", "2026-06-01T00:00:00+00:00")]
        out = format_queue_table(_result(queue=q))
        assert "累積量順" not in out

    def test_coldstart_silent_when_empty_queue(self):
        """待ち 0 件なら誤ランキングの余地がないので出さない。"""
        out = format_queue_table(_result(queue=[]))
        assert "累積量順" not in out


class TestFormatQueueTableUntracked:
    def test_footer_marks_config_when_empty_queue(self):
        """待ち 0 件パスの footer は `tracked (config)` 母数の出所を明示する（O1）。"""
        out = format_queue_table(_result(queue=[]))
        assert "10 tracked (config)" in out

    def test_footer_marks_config_when_queue_present(self):
        """待ちありパスの footer も `tracked (config)` を出す（O1・2 箇所目）。"""
        q = [
            {
                "pj_slug": "alpha",
                "material_count": 7,
                "weak_unprocessed": 5,
                "new_corrections": 2,
                "last_evolve_at": None,
                "reason": "x",
            }
        ]
        out = format_queue_table(_result(queue=q))
        assert "tracked (config)" in out

    def test_untracked_line_when_nonempty(self):
        """untracked_with_material が非空なら advisory 1 行を出す（O2）。"""
        um = [
            {"pj_slug": "amamo", "material_count": 64, "project_path": "/p/amamo"},
            {"pj_slug": "foo", "material_count": 9, "project_path": "/p/foo"},
        ]
        out = format_queue_table(_result(queue=[], untracked=um))
        assert "未追跡だが学習素材あり" in out
        assert "amamo (material 64)" in out
        assert "foo (material 9)" in out
        assert "evolve-fleet discover" in out

    def test_untracked_backlog_is_visible(self):
        um = [
            {
                "pj_slug": "dormant",
                "material_count": 0,
                "correction_backlog": 1,
                "project_path": "/p/dormant",
            }
        ]
        out = format_queue_table(_result(queue=[], untracked=um))
        assert "dormant (material 0, backlog 1)" in out

    def test_untracked_silent_when_empty(self):
        """untracked が空なら advisory 行を出さない。"""
        out = format_queue_table(_result(queue=[], untracked=[]))
        assert "未追跡" not in out

    def test_untracked_caps_at_five_with_ellipsis(self):
        """untracked は上位 5 件まで、超過は … で省略する。"""
        um = [
            {"pj_slug": f"pj{i}", "material_count": 100 - i, "project_path": f"/p/{i}"}
            for i in range(7)
        ]
        out = format_queue_table(_result(queue=[], untracked=um))
        assert "pj0 (material 100)" in out
        assert "pj4 (material 96)" in out
        assert "pj5" not in out  # 6 件目以降は出さない
        assert ", …" in out


class TestFormatQueueTableUnattributed:
    """PJ 未帰属 corrections の advisory 行（#91）。footer に件数 + source 内訳を出す。"""

    def test_unattributed_line_when_nonempty(self):
        ua = {"total": 9, "by_source": {"backfill": 8, "hook": 1}}
        out = format_queue_table(_result(queue=[], unattributed=ua))
        assert "PJ 未帰属 corrections: 9 件" in out
        assert "backfill=8" in out
        assert "hook=1" in out

    def test_unattributed_silent_when_zero(self):
        out = format_queue_table(_result(queue=[], unattributed={"total": 0, "by_source": {}}))
        assert "未帰属" not in out

    def test_unattributed_silent_when_key_absent(self):
        """後方互換: unattributed_corrections キー欠落でも落ちず無音。"""
        r = _result(queue=[])
        del r["unattributed_corrections"]
        out = format_queue_table(r)
        assert "未帰属" not in out

    def test_unattributed_line_on_waiting_path(self):
        q = [
            {
                "pj_slug": "alpha",
                "material_count": 7,
                "weak_unprocessed": 5,
                "new_corrections": 2,
                "last_evolve_at": None,
                "reason": "x",
            }
        ]
        ua = {"total": 3, "by_source": {"hook": 3}}
        out = format_queue_table(_result(queue=q, unattributed=ua))
        assert "PJ 未帰属 corrections: 3 件" in out


class TestFormatQueueTablePhantom:
    def test_phantom_backlog_is_visible(self):
        ph = [{"pj_slug": "ghost", "material_count": 0, "correction_backlog": 1}]
        out = format_queue_table(_result(queue=[], phantom=ph))
        assert "ghost (material 0, backlog 1)" in out

    def test_phantom_line_when_nonempty(self):
        """skipped_phantom が非空なら footer に phantom 透明化 1 行を出す（#88）。"""
        ph = [{"pj_slug": "tmpdcm8avo8", "material_count": 5}]
        out = format_queue_table(_result(queue=[], phantom=ph))
        assert "skipped 1 phantom" in out
        assert "tmpdcm8avo8 (material 5)" in out
        assert "実 dir 未解決" in out

    def test_phantom_silent_when_empty(self):
        """skipped_phantom が空/欠落なら phantom 行を出さない（temp slug が無いのが通常）。"""
        out = format_queue_table(_result(queue=[], phantom=[]))
        assert "phantom" not in out

    def test_phantom_line_on_waiting_path(self):
        """待ちあり path でも phantom footer を出す（2 箇所目）。"""
        q = [
            {
                "pj_slug": "alpha",
                "material_count": 7,
                "weak_unprocessed": 5,
                "new_corrections": 2,
                "last_evolve_at": None,
                "reason": "x",
            }
        ]
        ph = [{"pj_slug": "tmpzzz", "material_count": 8}]
        out = format_queue_table(_result(queue=q, phantom=ph))
        assert "skipped 1 phantom" in out
        assert "tmpzzz (material 8)" in out


class TestFormatQueueTableStatus:
    """queue_status / queue_status_reason を先頭に必ず1行出す（#267 Sprint 1）。

    EMPTY と SETUP_REQUIRED が待ち 0 件という表示だけでは見分けられない現状を直す。
    """

    def test_ready_status_shown_with_nonempty_queue(self):
        q = [
            {
                "pj_slug": "alpha",
                "material_count": 5,
                "weak_unprocessed": 3,
                "new_corrections": 2,
                "last_evolve_at": None,
                "reason": "weak=3 + corr=2（初回・全件）>= 3",
                "verify_pending": None,
            }
        ]
        out = format_queue_table(
            _result(queue=q, queue_status="READY", queue_status_reason="待ち PJ 1 件")
        )
        assert "status=READY" in out
        assert "待ち PJ 1 件" in out

    def test_empty_status_shown_when_queue_empty(self):
        out = format_queue_table(
            _result(
                queue=[],
                queue_status="EMPTY",
                queue_status_reason="待ち PJ 0件・処理できない学習素材もありません（閾値未満か素材なし）",
            )
        )
        assert "status=EMPTY" in out

    def test_setup_required_status_shown_when_queue_empty_but_blocked(self):
        out = format_queue_table(
            _result(
                queue=[],
                queue_status="SETUP_REQUIRED",
                queue_status_reason="待ち PJ は0件ですが処理できない学習素材があります: skipped_dead 1 件",
                skipped=[{"pj_slug": "dead1", "material_count": 5}],
            )
        )
        assert "status=SETUP_REQUIRED" in out
        assert "skipped_dead 1 件" in out

    def test_no_status_key_emits_no_status_line(self):
        """queue_status キー無し（旧 schema）の result dict は何も出さない（後方互換）。"""
        out = format_queue_table(_result(queue=[]))
        assert "status=" not in out

    def test_reason_column_carries_verify_pending_suffix(self):
        """verify_pending suffix は select_evolve_queue が reason に既に埋め込んでいる。
        formatters は REASON 列をそのまま出すだけで verify 待ちが可視化される。"""
        q = [
            {
                "pj_slug": "alpha",
                "material_count": 5,
                "weak_unprocessed": 3,
                "new_corrections": 2,
                "last_evolve_at": "2026-06-01T00:00:00+00:00",
                "reason": "weak=3 + new corr=2 >= 3 / verify 待ち 2 件（前回 accept・検証可能）",
                "verify_pending": {
                    "run_id": "run1",
                    "accepted": 2,
                    "exposure_sessions": 3,
                    "status": "verifiable",
                },
            }
        ]
        out = format_queue_table(
            _result(queue=q, queue_status="READY", queue_status_reason="待ち PJ 1 件")
        )
        assert "verify 待ち 2 件（前回 accept・検証可能）" in out


class TestFormatQueueTableCorrectionsReadHealth:
    """corrections.jsonl の read health が劣化しているときだけ人間向け出力に詳細を出す（#533）。"""

    def test_healthy_emits_no_footer(self):
        out = format_queue_table(
            _result(
                queue=[],
                queue_status="EMPTY",
                queue_status_reason="待ち PJ 0件・処理できない学習素材もありません（閾値未満か素材なし）",
                corrections_read_health={"readable": True, "error": None, "malformed_lines": 0},
            )
        )
        assert "corrections.jsonl" not in out

    def test_missing_key_emits_no_footer_back_compat(self):
        """旧 schema（キー無し）の result dict は何も出さない（後方互換）。"""
        out = format_queue_table(_result(queue=[]))
        assert "corrections.jsonl" not in out

    def test_unreadable_emits_error_footer(self):
        out = format_queue_table(
            _result(
                queue=[],
                queue_status="SETUP_REQUIRED",
                queue_status_reason=(
                    "待ち PJ は0件ですが処理できない学習素材があります: "
                    "corrections.jsonl 読取失敗（Permission denied）— 反映待ち在庫が過小表示の可能性"
                ),
                corrections_read_health={
                    "readable": False,
                    "error": "Permission denied",
                    "malformed_lines": 0,
                },
            )
        )
        assert "corrections.jsonl 読取失敗" in out
        assert "Permission denied" in out

    def test_malformed_lines_emits_footer_with_count(self):
        out = format_queue_table(
            _result(
                queue=[],
                queue_status="SETUP_REQUIRED",
                queue_status_reason="待ち PJ は0件ですが処理できない学習素材があります: corrections.jsonl に壊れた行 4 件",
                corrections_read_health={"readable": True, "error": None, "malformed_lines": 4},
            )
        )
        assert "corrections.jsonl に壊れた行 4 件" in out


class TestFormatQueueTableWeakSignalsReadHealth:
    """#539: weak_signals の劣化だけを人間向け footer に出す。"""

    def test_healthy_emits_no_footer(self):
        out = format_queue_table(
            {
                **_result(queue=[]),
                "weak_signals_read_health": {
                    "sources": [
                        {
                            "path": "/data/weak_signals.jsonl",
                            "readable": True,
                            "error": None,
                            "malformed_lines": 0,
                        }
                    ]
                },
            }
        )
        assert "weak_signals.jsonl 読取失敗" not in out
        assert "weak_signals.jsonl に壊れた行" not in out

    def test_mixed_union_degradation_emits_source_details(self):
        out = format_queue_table(
            {
                **_result(queue=[]),
                "weak_signals_read_health": {
                    "sources": [
                        {
                            "path": "/canonical/weak_signals.jsonl",
                            "readable": True,
                            "error": None,
                            "malformed_lines": 2,
                        },
                        {
                            "path": "/legacy/weak_signals.jsonl",
                            "readable": False,
                            "error": "Permission denied",
                            "malformed_lines": 0,
                        },
                    ]
                },
            }
        )
        assert "/canonical/weak_signals.jsonl に壊れた行 2 件" in out
        assert "/legacy/weak_signals.jsonl 読取失敗: Permission denied" in out


# --- build_queue_result 統合テスト（#267 Phase 5・実ストア E2E）--------------
#
# conftest.py の autouse fixture ``_isolate_plugin_data`` が CLAUDE_PLUGIN_DATA=tmp_path に
# 固定し、import 済み store モジュール（advisory_decision_log / optimize_history_store /
# session_store）の DATA_DIR を機械的に同じ tmp_path へ rebase する（#420）。よってここでは
# tmp_path 直下に各ストアの実ファイルを書くだけで、build_queue_result の実 I/O 経路
# （advisory_decisions.jsonl 読込 → optimize_history alias union → session store since クエリ）を
# hermetic に検証できる。


def _write_advisory_decisions(tmp_path, records):
    (tmp_path / "advisory_decisions.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in records)
    )


def _write_optimize_history(tmp_path, slug, records):
    d = tmp_path / "optimize_history"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{slug}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in records))


def _write_sessions(tmp_path, records):
    (tmp_path / "sessions.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in records)
    )


class TestBuildQueueResultVerifyIntegration:
    """build_queue_result 経由の E2E — verify_pending/queue_status の各 finding を通しで検証。"""

    def _build(self, tmp_path, *, pj_slugs, threshold=5, weak=None, corr=None, **kwargs):
        ws = tmp_path / "weak_signals.jsonl"
        ws.write_text("".join(json.dumps(r) + "\n" for r in (weak or [])))
        corr_path = tmp_path / "corrections.jsonl"
        corr_path.write_text("".join(json.dumps(r) + "\n" for r in (corr or [])))
        return fq.build_queue_result(
            pj_slugs=pj_slugs,
            threshold=threshold,
            weak_signals_path=ws,
            corrections_path=corr_path,
            last_evolve_map=kwargs.pop("last_evolve_map", {}),
            activity_map=kwargs.pop("activity_map", {}),
            generated_at="2026-07-27T09:00:00+00:00",
            **kwargs,
        )

    def test_exposure_counts_only_sessions_after_accept(self, tmp_path):
        """C2 E2E: exposure は accept 記録時刻**以降**のセッションのみ数える。"""
        now = datetime.now(timezone.utc)
        accept_ts = (now - timedelta(hours=1)).isoformat()
        _write_advisory_decisions(
            tmp_path,
            [
                {
                    "pj_slug": "alpha",
                    "proposal_id": "p1",
                    "decision": "accept",
                    "run_id": "run1",
                    "recorded_at": accept_ts,
                }
            ],
        )
        _write_sessions(
            tmp_path,
            [
                _sess("before", (now - timedelta(hours=2)).isoformat(), "/p/alpha"),
                _sess("after", (now - timedelta(minutes=10)).isoformat(), "/p/alpha"),
            ],
        )
        weak = [_ws("alpha", key=f"a{i}") for i in range(7)]  # threshold 到達（C1 昇格と分離）
        result = self._build(tmp_path, pj_slugs=["alpha"], threshold=5, weak=weak)

        vp = result["queue"][0]["verify_pending"]
        assert vp["exposure_sessions"] == 1  # after のみ
        assert vp["status"] == "verifiable"

    def test_ttl_15_days_is_none_13_days_still_active(self, tmp_path):
        """I1 E2E: accept が 15日前なら status=none、13日前なら残る。"""
        now = datetime.now(timezone.utc)

        _write_advisory_decisions(
            tmp_path,
            [
                {
                    "pj_slug": "expired",
                    "proposal_id": "p1",
                    "decision": "accept",
                    "run_id": "run_old",
                    "recorded_at": (now - timedelta(days=15)).isoformat(),
                },
                {
                    "pj_slug": "active",
                    "proposal_id": "p2",
                    "decision": "accept",
                    "run_id": "run_recent",
                    "recorded_at": (now - timedelta(days=13)).isoformat(),
                },
            ],
        )
        weak = [_ws("expired", key="e1", detected=now.isoformat())] + [
            _ws("active", key="a1", detected=now.isoformat())
        ]
        result = self._build(
            tmp_path, pj_slugs=["expired", "active"], threshold=1, weak=weak
        )

        by_slug = {m["pj_slug"]: m for m in result["queue"]}
        assert by_slug["expired"]["verify_pending"]["status"] == "none"
        assert by_slug["active"]["verify_pending"]["status"] != "none"

    def test_material_zero_but_verify_pending_still_queues(self, tmp_path):
        """C1 E2E: material=0 でも verify 待ちがあれば queue に出る。"""
        now = datetime.now(timezone.utc)
        _write_advisory_decisions(
            tmp_path,
            [
                {
                    "pj_slug": "alpha",
                    "proposal_id": "p1",
                    "decision": "accept",
                    "run_id": "run1",
                    "recorded_at": (now - timedelta(hours=1)).isoformat(),
                }
            ],
        )
        result = self._build(tmp_path, pj_slugs=["alpha"], threshold=5)  # weak/corr 無し

        assert [m["pj_slug"] for m in result["queue"]] == ["alpha"]
        item = result["queue"][0]
        assert item["material_count"] == 0
        assert item["verify_pending"]["status"] != "none"
        assert "material=0 < 5" in item["reason"]

    def test_naive_optimize_timestamp_mixed_with_aware_advisory_resolves_latest(
        self, tmp_path
    ):
        """C3 E2E: naive local（optimize lane）と aware UTC（advisory lane）が混在しても
        最新 run 判定が正しい（本 PR 前は naive を UTC 決め打ちし JST 環境で 9 時間ずれた）。
        """
        older_aware = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
        newer_aware_instant = datetime.now(timezone.utc) - timedelta(hours=1)
        # naive local 文字列を本番 writer と同じ変換（astimezone → tzinfo 剥がし）で導出する。
        newer_naive = (
            newer_aware_instant.astimezone().replace(tzinfo=None).isoformat()
        )

        _write_advisory_decisions(
            tmp_path,
            [
                {
                    "pj_slug": "alpha",
                    "proposal_id": "p_old",
                    "decision": "accept",
                    "run_id": "run_old",
                    "recorded_at": older_aware,
                }
            ],
        )
        _write_optimize_history(
            tmp_path,
            "alpha",
            [
                {
                    "id": "e1",
                    "human_accepted": True,
                    "run_id": "run_new",
                    "timestamp": newer_naive,
                }
            ],
        )
        result = self._build(tmp_path, pj_slugs=["alpha"], threshold=5)

        vp = result["queue"][0]["verify_pending"]
        assert vp["run_id"] == "run_new"
        assert vp["accepted"] == 1

    def test_dead_pj_zero_material_is_empty_not_setup_required(self, tmp_path):
        """C4 E2E: material_count=0 の dead PJ だけがある状態は EMPTY（SETUP_REQUIRED でない）。"""
        result = self._build(
            tmp_path,
            pj_slugs=["ghost"],
            threshold=5,
            pj_paths={"ghost": str(tmp_path / "does-not-exist")},
        )
        assert result["skipped_dead"] == [
            {
                "pj_slug": "ghost",
                "project_path": str(tmp_path / "does-not-exist"),
                "weak_unprocessed": 0,
                "new_corrections": 0,
                "correction_backlog": 0,
                "material_count": 0,
            }
        ]
        assert result["queue_status"] == "EMPTY"

    def test_dead_pj_backlog_only_is_setup_required(self, tmp_path):
        """#515: dead PJ の promoted 在庫だけでも朝通知対象の blocked 状態にする。"""
        result = self._build(
            tmp_path,
            pj_slugs=["ghost"],
            threshold=5,
            pj_paths={"ghost": str(tmp_path / "does-not-exist")},
            last_evolve_map={"ghost": "2026-08-02T00:00:00+00:00"},
            corr=[
                {
                    "project_path": "ghost",
                    "timestamp": "2026-08-01T00:00:00+00:00",
                    "session_id": "s1",
                    "reflect_status": "promoted",
                    "invalidated": False,
                }
            ],
        )
        assert result["skipped_dead"][0]["material_count"] == 0
        assert result["skipped_dead"][0]["correction_backlog"] == 1
        assert result["queue_status"] == "SETUP_REQUIRED"
        assert "skipped_dead 1 件" in result["queue_status_reason"]

    def test_unattributed_correction_31_days_old_does_not_trigger_setup_required(
        self, tmp_path
    ):
        """C5 E2E: 31日前の未帰属 correction のみでは SETUP_REQUIRED にならない。"""
        old_ts = (datetime.now(timezone.utc) - timedelta(days=31)).isoformat()
        result = self._build(
            tmp_path,
            pj_slugs=[],
            threshold=5,
            corr=[{"project_path": "", "source": "backfill", "timestamp": old_ts}],
        )
        assert result["unattributed_corrections"]["total"] == 0
        assert result["queue_status"] == "EMPTY"

    def test_alias_optimize_history_accept_recovered_via_current_slug(self, tmp_path):
        """I2 E2E: rename 済 PJ の旧 slug 名義 optimize_history accept が現 slug の
        verify_pending に反映される（PJ_SLUG_ALIASES の実エントリ rl-anything→evolve-anything）。
        """
        now = datetime.now(timezone.utc)
        _write_optimize_history(
            tmp_path,
            "rl-anything",  # 旧 slug（現 slug は evolve-anything）
            [
                {
                    "id": "legacy1",
                    "human_accepted": True,
                    "run_id": "r1",
                    "timestamp": (now - timedelta(hours=1)).isoformat(),
                }
            ],
        )
        result = self._build(tmp_path, pj_slugs=["evolve-anything"], threshold=5)

        assert [m["pj_slug"] for m in result["queue"]] == ["evolve-anything"]
        vp = result["queue"][0]["verify_pending"]
        assert vp["accepted"] == 1
        assert vp["run_id"] == "r1"
