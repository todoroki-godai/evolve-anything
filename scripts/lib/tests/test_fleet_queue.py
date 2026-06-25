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
import sys
from pathlib import Path

import pytest

_test_dir = Path(__file__).resolve().parent
_lib_dir = _test_dir.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

from fleet import queue as fq  # noqa: E402
from fleet import queue_state as qs  # noqa: E402


# --- select_evolve_queue 純関数 ----------------------------------------------


def _material(slug, weak=0, corr=0, last=None, subagents=0, sessions=0):
    """テスト用の per-PJ material dict を組み立てる。"""
    return {
        "pj_slug": slug,
        "weak_unprocessed": weak,
        "new_corrections": corr,
        "last_evolve_at": last,
        "activity_since": {"subagents": subagents, "sessions": sessions},
    }


class TestSelectEvolveQueue:
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
        mats = [_material("a", weak=7, corr=2)]
        out = fq.select_evolve_queue(mats, threshold=3)
        assert out[0]["reason"] == "weak=7 + new corr=2 >= 3"

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


# --- weak_signals 未処理カウント（PJ 別） -------------------------------------


def _ws(pj_slug, *, promoted=False, expired=False, detected="2026-06-20T00:00:00+00:00", key=None):
    return {
        "channel": "manual_edit",
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
            "alpha", last_evolve_at="2026-06-05T00:00:00+00:00", corrections_path=store
        )
        assert n == 2

    def test_last_evolve_none_counts_all(self, tmp_path):
        """state 不在（None）は全件カウント（初回＝全件待ち）。"""
        store = tmp_path / "corrections.jsonl"
        recs = [_corr("alpha", "2026-06-01T00:00:00+00:00"), _corr("alpha", "2026-06-10T00:00:00+00:00")]
        store.write_text("".join(json.dumps(r) + "\n" for r in recs))
        n = fq.new_corrections_by_pj("alpha", last_evolve_at=None, corrections_path=store)
        assert n == 2

    def test_scopes_by_project_path(self, tmp_path):
        store = tmp_path / "corrections.jsonl"
        recs = [_corr("alpha", "2026-06-10T00:00:00+00:00"), _corr("beta", "2026-06-10T00:00:00+00:00")]
        store.write_text("".join(json.dumps(r) + "\n" for r in recs))
        assert fq.new_corrections_by_pj("alpha", last_evolve_at=None, corrections_path=store) == 1

    def test_missing_store_returns_zero(self, tmp_path):
        assert fq.new_corrections_by_pj("alpha", last_evolve_at=None, corrections_path=tmp_path / "x.jsonl") == 0


# --- per-PJ last_evolve state（read / write barrier 経由）---------------------


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
        assert set(result.keys()) == {"generated_at", "threshold", "tracked_total", "queue"}
        assert result["generated_at"] == "2026-06-25T09:00:00Z"
        assert result["threshold"] == 3
        assert result["tracked_total"] == 1
        assert len(result["queue"]) == 1
        item = result["queue"][0]
        assert set(item.keys()) == {
            "pj_slug",
            "material_count",
            "weak_unprocessed",
            "new_corrections",
            "last_evolve_at",
            "activity_since",
            "reason",
        }
        assert item["pj_slug"] == "alpha"
        assert item["weak_unprocessed"] == 7
        assert item["new_corrections"] == 2
        assert item["material_count"] == 9
        assert item["last_evolve_at"] is None
        assert item["activity_since"] == {"subagents": 40, "sessions": 5}

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
