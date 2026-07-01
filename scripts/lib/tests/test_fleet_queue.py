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


# --- weak_signals 未処理カウント（PJ 別） -------------------------------------


def _ws(pj_slug, *, promoted=False, expired=False, detected="2026-06-20T00:00:00+00:00", key=None):
    # content-rich channel（#113: material 計数は REVIEW_CHANNELS のみ）。この helper の
    # weak は promoted/expired/scope/dead/untracked 判定の検証用ゆえ昇格可能 channel を使う。
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
            "alpha", last_evolve_at="2026-06-23T10:00:00+00:00", corrections_path=store
        )
        assert n == 0

    def test_mixed_tz_suffix_after_still_counted(self, tmp_path):
        """suffix 違いでも実時刻が後なら新規としてカウントする（修正が過剰除外しない保証）。"""
        store = tmp_path / "corrections.jsonl"
        store.write_text(json.dumps(_corr("alpha", "2026-06-23T10:00:01Z")) + "\n")
        n = fq.new_corrections_by_pj(
            "alpha", last_evolve_at="2026-06-23T10:00:00+00:00", corrections_path=store
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
            "skipped_dead",
            "untracked_with_material",
            "skipped_phantom",
            "bootstrap_consumed",
            "weak_content_poor",
            "unattributed_corrections",
        }
        assert result["unattributed_corrections"] == {"total": 0, "by_source": {}}
        assert result["generated_at"] == "2026-06-25T09:00:00Z"
        assert result["threshold"] == 3
        assert result["tracked_total"] == 1
        assert len(result["queue"]) == 1
        item = result["queue"][0]
        assert set(item.keys()) == {
            "pj_slug",
            "project_path",
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
        corr.write_text("")
        result = fq.build_queue_result(
            pj_slugs=["gone"],
            threshold=3,
            weak_signals_path=ws,
            corrections_path=corr,
            last_evolve_map={},
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
        assert sd["material_count"] == 4

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
        assert fq.new_corrections_by_pj(
            "evolve-anything", last_evolve_at=None, corrections_path=store
        ) == 2
        # 無関係 slug は数えない
        assert fq.new_corrections_by_pj(
            "other-pj", last_evolve_at=None, corrections_path=store
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
):
    return {
        "generated_at": "2026-06-25T09:00:00Z",
        "threshold": threshold,
        "tracked_total": tracked,
        "queue": queue or [],
        "skipped_dead": skipped or [],
        "untracked_with_material": untracked or [],
        "skipped_phantom": phantom or [],
        "unattributed_corrections": unattributed or {"total": 0, "by_source": {}},
    }


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
