#!/usr/bin/env python3
"""queue_verify.py のテスト — verify 待ち read-time 導出 + queue 全体状態ラベル（Epic #267 Sprint 1）。

決定論・LLM 非依存。検証対象:
  - ``compute_verify_pending`` 純関数: accepted=0→none / accepted>0&exposure=0→awaiting_exposure /
    accepted>0&exposure>=1→verifiable
  - 最新 run_id 判定は ``Z`` 終端・``+00:00`` 終端混在でも datetime 比較で正しく解決する
  - run_id を持たない旧 schema レコードは最新 run 判定から除外される
  - ``compute_queue_status``: queue 非空→READY / 空+blocked material あり→SETUP_REQUIRED /
    空+blocked なし→EMPTY、``queue_status_reason`` は常に非空
  - ``format_verify_pending_suffix``: verify_pending 無し/accepted=0 は空文字列（reason 不変条件）
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

_test_dir = Path(__file__).resolve().parent
_lib_dir = _test_dir.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

from fleet import queue_verify as qv  # noqa: E402


# --- compute_verify_pending 純関数 -------------------------------------------


def _adv(decision, run_id, recorded_at):
    return {"decision": decision, "run_id": run_id, "recorded_at": recorded_at}


def _opt(human_accepted, run_id, timestamp):
    return {"human_accepted": human_accepted, "run_id": run_id, "timestamp": timestamp}


class TestComputeVerifyPendingStatus:
    def test_no_accept_records_is_none(self):
        out = qv.compute_verify_pending(
            advisory_records=[], optimize_records=[], exposure_sessions=0
        )
        assert out == {
            "run_id": None,
            "accepted": 0,
            "exposure_sessions": 0,
            "status": "none",
        }

    def test_accepted_with_zero_exposure_is_awaiting_exposure(self):
        out = qv.compute_verify_pending(
            advisory_records=[_adv("accept", "run1", "2026-07-27T10:00:00+00:00")],
            optimize_records=[],
            exposure_sessions=0,
        )
        assert out["accepted"] == 1
        assert out["run_id"] == "run1"
        assert out["status"] == "awaiting_exposure"

    def test_accepted_with_exposure_is_verifiable(self):
        out = qv.compute_verify_pending(
            advisory_records=[],
            optimize_records=[_opt(True, "run2", "2026-07-27T10:00:00+00:00")],
            exposure_sessions=3,
        )
        assert out["accepted"] == 1
        assert out["run_id"] == "run2"
        assert out["status"] == "verifiable"

    def test_only_reject_records_is_none(self):
        out = qv.compute_verify_pending(
            advisory_records=[_adv("reject", "run1", "2026-07-27T10:00:00+00:00")],
            optimize_records=[],
            exposure_sessions=5,
        )
        assert out["accepted"] == 0
        assert out["status"] == "none"

    def test_combines_both_lanes_same_run(self):
        out = qv.compute_verify_pending(
            advisory_records=[_adv("accept", "run3", "2026-07-27T10:00:00+00:00")],
            optimize_records=[_opt(True, "run3", "2026-07-27T10:05:00+00:00")],
            exposure_sessions=1,
        )
        assert out["accepted"] == 2
        assert out["run_id"] == "run3"
        assert out["status"] == "verifiable"


class TestLatestRunIdTimestampParsing:
    def test_z_suffix_and_offset_suffix_compare_correctly(self):
        """同一 instant の Z 終端 / +00:00 終端が辞書順でなく datetime で正しく比較される。"""
        older = _adv("accept", "run_old", "2026-07-27T09:00:00Z")
        newer = _adv("accept", "run_new", "2026-07-27T09:00:01+00:00")
        out = qv.compute_verify_pending(
            advisory_records=[older, newer], optimize_records=[], exposure_sessions=1
        )
        assert out["run_id"] == "run_new"
        assert out["accepted"] == 1

    def test_z_suffix_picked_over_earlier_offset_suffix(self):
        older = _adv("accept", "run_old", "2026-07-27T09:00:00+00:00")
        newer = _adv("accept", "run_new", "2026-07-27T10:00:00Z")
        out = qv.compute_verify_pending(
            advisory_records=[older, newer], optimize_records=[], exposure_sessions=1
        )
        assert out["run_id"] == "run_new"

    def test_records_without_run_id_excluded_from_latest_determination(self):
        """run_id 欠落（旧 schema）は最新 record でも最新 run 判定に混ぜない。"""
        legacy = _adv("accept", None, "2026-07-27T12:00:00+00:00")  # 一番新しいが run_id 無し
        older_with_run = _adv("accept", "run_a", "2026-07-27T09:00:00+00:00")
        out = qv.compute_verify_pending(
            advisory_records=[legacy, older_with_run],
            optimize_records=[],
            exposure_sessions=1,
        )
        assert out["run_id"] == "run_a"
        assert out["accepted"] == 1  # legacy はどちらの集計にも入らない

    def test_all_records_without_run_id_is_none(self):
        legacy = _adv("accept", None, "2026-07-27T12:00:00+00:00")
        out = qv.compute_verify_pending(
            advisory_records=[legacy], optimize_records=[], exposure_sessions=1
        )
        assert out == {
            "run_id": None,
            "accepted": 0,
            "exposure_sessions": 1,
            "status": "none",
        }

    def test_unparsable_timestamp_excluded(self):
        bad = _adv("accept", "run_bad", "not-a-timestamp")
        good = _adv("accept", "run_good", "2026-07-27T09:00:00+00:00")
        out = qv.compute_verify_pending(
            advisory_records=[bad, good], optimize_records=[], exposure_sessions=1
        )
        assert out["run_id"] == "run_good"
        assert out["accepted"] == 1


class TestParseIsoNaiveLocal:
    """#267 C3: naive（tz 無し）はローカル時刻として解釈する（UTC 決め打ちはしない）。

    既存 naive レコード（``fitness_evolution.py`` 修正前や run_loop.py 等）は
    書き手の実行機ローカル時刻（この開発環境では JST）で書かれているため、UTC 扱いすると
    9 時間ずれる。期待値はテスト実行機の system local tz に追従させる（本番コードと同じ
    ``datetime.astimezone()`` の変換で導出）ことで、CI のタイムゾーンに依存せず
    「naive を UTC 決め打ちしなくなった」ことだけを検証する。
    """

    def test_naive_string_interpreted_as_local_not_utc(self):
        naive_str = "2026-07-27T09:00:00"
        parsed = qv._parse_iso(naive_str)
        expected = datetime.fromisoformat(naive_str).astimezone()
        assert parsed == expected
        assert parsed.tzinfo is not None

    def test_aware_string_unaffected(self):
        """aware 入力は従来通りそのままパースされる（naive 変更の副作用が無いこと）。"""
        aware_str = "2026-07-27T09:00:00+00:00"
        parsed = qv._parse_iso(aware_str)
        assert parsed == datetime.fromisoformat(aware_str)


# --- format_verify_pending_suffix --------------------------------------------


class TestFormatVerifyPendingSuffix:
    def test_none_input_is_empty(self):
        assert qv.format_verify_pending_suffix(None) == ""

    def test_zero_accepted_is_empty(self):
        vp = {"run_id": None, "accepted": 0, "exposure_sessions": 0, "status": "none"}
        assert qv.format_verify_pending_suffix(vp) == ""

    def test_verifiable_mentions_count(self):
        vp = {"run_id": "r1", "accepted": 2, "exposure_sessions": 3, "status": "verifiable"}
        out = qv.format_verify_pending_suffix(vp)
        assert "2" in out
        assert "検証可能" in out

    def test_awaiting_exposure_mentions_count(self):
        vp = {
            "run_id": "r1",
            "accepted": 1,
            "exposure_sessions": 0,
            "status": "awaiting_exposure",
        }
        out = qv.format_verify_pending_suffix(vp)
        assert "1" in out
        assert out != ""


# --- compute_queue_status -----------------------------------------------------


class TestComputeQueueStatus:
    def test_non_empty_queue_is_ready(self):
        out = qv.compute_queue_status(
            queue=[{"pj_slug": "a"}],
            untracked_with_material=[],
            skipped_dead=[],
            skipped_phantom=[],
            unattributed_total=0,
        )
        assert out["queue_status"] == "READY"
        assert out["queue_status_reason"]

    def test_empty_queue_with_untracked_material_is_setup_required(self):
        out = qv.compute_queue_status(
            queue=[],
            untracked_with_material=[{"pj_slug": "b"}],
            skipped_dead=[],
            skipped_phantom=[],
            unattributed_total=0,
        )
        assert out["queue_status"] == "SETUP_REQUIRED"
        assert out["queue_status_reason"]

    def test_empty_queue_with_skipped_dead_is_setup_required(self):
        out = qv.compute_queue_status(
            queue=[],
            untracked_with_material=[],
            skipped_dead=[{"pj_slug": "c", "material_count": 2}],
            skipped_phantom=[],
            unattributed_total=0,
        )
        assert out["queue_status"] == "SETUP_REQUIRED"

    def test_empty_queue_with_zero_material_skipped_dead_is_empty(self):
        """C4: material_count=0 の dead PJ だけでは SETUP_REQUIRED を誤発火させない。"""
        out = qv.compute_queue_status(
            queue=[],
            untracked_with_material=[],
            skipped_dead=[{"pj_slug": "c", "material_count": 0}],
            skipped_phantom=[],
            unattributed_total=0,
        )
        assert out["queue_status"] == "EMPTY"

    def test_empty_queue_with_mixed_skipped_dead_counts_only_nonzero(self):
        """material_count=0 のものは無視し、非ゼロのものだけで件数を数える。"""
        out = qv.compute_queue_status(
            queue=[],
            untracked_with_material=[],
            skipped_dead=[
                {"pj_slug": "c", "material_count": 0},
                {"pj_slug": "d", "material_count": 3},
            ],
            skipped_phantom=[],
            unattributed_total=0,
        )
        assert out["queue_status"] == "SETUP_REQUIRED"
        assert "1" in out["queue_status_reason"]

    def test_empty_queue_with_skipped_phantom_is_setup_required(self):
        out = qv.compute_queue_status(
            queue=[],
            untracked_with_material=[],
            skipped_dead=[],
            skipped_phantom=[{"pj_slug": "d"}],
            unattributed_total=0,
        )
        assert out["queue_status"] == "SETUP_REQUIRED"

    def test_empty_queue_with_unattributed_corrections_is_setup_required(self):
        out = qv.compute_queue_status(
            queue=[],
            untracked_with_material=[],
            skipped_dead=[],
            skipped_phantom=[],
            unattributed_total=3,
        )
        assert out["queue_status"] == "SETUP_REQUIRED"

    def test_all_empty_is_empty_status(self):
        out = qv.compute_queue_status(
            queue=[],
            untracked_with_material=[],
            skipped_dead=[],
            skipped_phantom=[],
            unattributed_total=0,
        )
        assert out["queue_status"] == "EMPTY"
        assert out["queue_status_reason"]


# --- attach_verify_pending（#267 I3: バルク read + group by）-----------------


class TestAttachVerifyPending:
    def test_reads_advisory_store_exactly_once_for_all_materials(self, monkeypatch):
        """PJ 数に依らず read_advisory_decisions(None) は1回だけ呼ぶ（O(PJ数×ログ全体)の解消）。"""
        calls = []

        def _fake_read(slug=None):
            calls.append(slug)
            return [
                {
                    "pj_slug": "alpha",
                    "decision": "accept",
                    "run_id": "r1",
                    "recorded_at": "2026-07-01T00:00:00+00:00",
                },
                {
                    "pj_slug": "beta",
                    "decision": "accept",
                    "run_id": "r2",
                    "recorded_at": "2026-07-02T00:00:00+00:00",
                },
            ]

        import advisory_decision_log
        import optimize_history_store

        monkeypatch.setattr(advisory_decision_log, "read_advisory_decisions", _fake_read)
        monkeypatch.setattr(optimize_history_store, "load_history", lambda slug: [])

        materials = [
            {"pj_slug": "alpha", "activity_since": {"sessions": 1}},
            {"pj_slug": "beta", "activity_since": {"sessions": 0}},
            {"pj_slug": "gamma", "activity_since": {"sessions": 5}},
        ]
        qv.attach_verify_pending(materials, canonicalize=lambda s: s)

        assert calls == [None]  # 全 PJ まとめ読みが 1 回だけ
        assert materials[0]["verify_pending"]["status"] == "verifiable"
        assert materials[1]["verify_pending"]["status"] == "awaiting_exposure"
        assert materials[2]["verify_pending"]["status"] == "none"  # accept 記録なし

    def test_canonicalize_folds_alias_records_into_current_slug(self, monkeypatch):
        """advisory レコードが旧 slug タグでも canonicalize で現 slug の group に畳む。"""

        def _fake_read(slug=None):
            return [
                {
                    "pj_slug": "rl-anything",  # 旧 slug のまま残る legacy レコード
                    "decision": "accept",
                    "run_id": "r1",
                    "recorded_at": "2026-07-01T00:00:00+00:00",
                }
            ]

        import advisory_decision_log
        import optimize_history_store

        monkeypatch.setattr(advisory_decision_log, "read_advisory_decisions", _fake_read)
        monkeypatch.setattr(optimize_history_store, "load_history", lambda slug: [])

        materials = [{"pj_slug": "evolve-anything", "activity_since": {"sessions": 1}}]
        qv.attach_verify_pending(
            materials,
            canonicalize=lambda s: "evolve-anything" if s == "rl-anything" else s,
        )

        assert materials[0]["verify_pending"]["status"] == "verifiable"
        assert materials[0]["verify_pending"]["accepted"] == 1
