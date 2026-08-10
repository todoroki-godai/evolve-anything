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

import ast
import re
import sys
from datetime import datetime, timezone
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


# time bomb 対策: このファイルの一部テストは固定の絶対日付（2026-07-27 の各時刻）を
# accept レコードの recorded_at/timestamp に使う。``compute_verify_pending`` は
# VERIFY_PENDING_TTL_DAYS（14日）で read 時失効するため、``now`` を省略して実行時刻
# （wall clock）に判定させると実行日が14日進むたびに status="none"/run_id=None へ
# 静かに反転して壊れる（#410 実測: 2026-08-10 09:00 UTC の境界超過で3件が同時に赤化）。
# 固定レコード日より後・TTL 内に収まる固定 now を明示的に渡し、実行時刻に一切依存させない
# （``TestVerifyPendingTtl`` が既に確立している「now を明示注入する」流儀を踏襲）。
_FIXED_NOW = datetime(2026, 7, 28, tzinfo=timezone.utc)


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
            now=_FIXED_NOW,
        )
        assert out["accepted"] == 1
        assert out["run_id"] == "run1"
        assert out["status"] == "awaiting_exposure"

    def test_accepted_with_exposure_is_verifiable(self):
        out = qv.compute_verify_pending(
            advisory_records=[],
            optimize_records=[_opt(True, "run2", "2026-07-27T10:00:00+00:00")],
            exposure_sessions=3,
            now=_FIXED_NOW,
        )
        assert out["accepted"] == 1
        assert out["run_id"] == "run2"
        assert out["status"] == "verifiable"

    def test_only_reject_records_is_none(self):
        out = qv.compute_verify_pending(
            advisory_records=[_adv("reject", "run1", "2026-07-27T10:00:00+00:00")],
            optimize_records=[],
            exposure_sessions=5,
            now=_FIXED_NOW,
        )
        assert out["accepted"] == 0
        assert out["status"] == "none"

    def test_combines_both_lanes_same_run(self):
        out = qv.compute_verify_pending(
            advisory_records=[_adv("accept", "run3", "2026-07-27T10:00:00+00:00")],
            optimize_records=[_opt(True, "run3", "2026-07-27T10:05:00+00:00")],
            exposure_sessions=1,
            now=_FIXED_NOW,
        )
        assert out["accepted"] == 2
        assert out["run_id"] == "run3"
        assert out["status"] == "verifiable"


class TestVerifyPendingTtl:
    """#267 I1: verify 待ちは記録から VERIFY_PENDING_TTL_DAYS(=14) 日で read 時失効する。"""

    def test_ttl_constant_is_14_days(self):
        assert qv.VERIFY_PENDING_TTL_DAYS == 14

    def test_expired_after_ttl_is_none(self):
        from datetime import datetime, timedelta, timezone

        now = datetime(2026, 7, 27, tzinfo=timezone.utc)
        old = (now - timedelta(days=15)).isoformat()
        out = qv.compute_verify_pending(
            advisory_records=[_adv("accept", "run1", old)],
            optimize_records=[],
            exposure_sessions=3,
            now=now,
        )
        assert out["status"] == "none"
        assert out["accepted"] == 0

    def test_within_ttl_is_not_expired(self):
        from datetime import datetime, timedelta, timezone

        now = datetime(2026, 7, 27, tzinfo=timezone.utc)
        recent = (now - timedelta(days=13)).isoformat()
        out = qv.compute_verify_pending(
            advisory_records=[_adv("accept", "run1", recent)],
            optimize_records=[],
            exposure_sessions=3,
            now=now,
        )
        assert out["status"] == "verifiable"
        assert out["accepted"] == 1

    def test_exactly_at_ttl_boundary_is_expired(self):
        """境界（ちょうど14日）は失効側に倒す（14日『以内』でなく『未満』が有効）。"""
        from datetime import datetime, timedelta, timezone

        now = datetime(2026, 7, 27, tzinfo=timezone.utc)
        boundary = (now - timedelta(days=qv.VERIFY_PENDING_TTL_DAYS)).isoformat()
        out = qv.compute_verify_pending(
            advisory_records=[_adv("accept", "run1", boundary)],
            optimize_records=[],
            exposure_sessions=3,
            now=now,
        )
        assert out["status"] == "none"

    def test_default_now_is_current_time(self):
        """now 省略時は datetime.now(timezone.utc) が既定（テスト容易性のための注入は任意）。"""
        from datetime import datetime, timezone

        just_now = datetime.now(timezone.utc).isoformat()
        out = qv.compute_verify_pending(
            advisory_records=[_adv("accept", "run1", just_now)],
            optimize_records=[],
            exposure_sessions=1,
        )
        assert out["status"] == "verifiable"  # 未失効（記録時刻がテスト実行時刻そのもの）


class TestLatestRunIdTimestampParsing:
    def test_z_suffix_and_offset_suffix_compare_correctly(self):
        """同一 instant の Z 終端 / +00:00 終端が辞書順でなく datetime で正しく比較される。"""
        older = _adv("accept", "run_old", "2026-07-27T09:00:00Z")
        newer = _adv("accept", "run_new", "2026-07-27T09:00:01+00:00")
        out = qv.compute_verify_pending(
            advisory_records=[older, newer], optimize_records=[], exposure_sessions=1,
            now=_FIXED_NOW,
        )
        assert out["run_id"] == "run_new"
        assert out["accepted"] == 1

    def test_z_suffix_picked_over_earlier_offset_suffix(self):
        older = _adv("accept", "run_old", "2026-07-27T09:00:00+00:00")
        newer = _adv("accept", "run_new", "2026-07-27T10:00:00Z")
        out = qv.compute_verify_pending(
            advisory_records=[older, newer], optimize_records=[], exposure_sessions=1,
            now=_FIXED_NOW,
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
            now=_FIXED_NOW,
        )
        assert out["run_id"] == "run_a"
        assert out["accepted"] == 1  # legacy はどちらの集計にも入らない

    def test_all_records_without_run_id_is_none(self):
        """accepts=[] は TTL 判定に到達する前に return するため now 非依存で安全（不変）。

        機能的には now 省略でも安全だが、静的ガード（下記
        test_no_fixed_iso_date_without_now_kwarg_regression_guard）が例外を持たずに
        済むよう他の呼び出しと揃えて now を明示する。
        """
        legacy = _adv("accept", None, "2026-07-27T12:00:00+00:00")
        out = qv.compute_verify_pending(
            advisory_records=[legacy], optimize_records=[], exposure_sessions=1,
            now=_FIXED_NOW,
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
            advisory_records=[bad, good], optimize_records=[], exposure_sessions=1,
            now=_FIXED_NOW,
        )
        assert out["run_id"] == "run_good"
        assert out["accepted"] == 1


# ─────────────────────────────────────────────────────────────────
# #410 time bomb 再発防止（静的契約テスト）
# ─────────────────────────────────────────────────────────────────
# このファイル自身が「固定の絶対 ISO 日付を compute_verify_pending の accept 記録に渡すのに
# now= を伴わない」誤りを踏んだ実例（VERIFY_PENDING_TTL_DAYS=14 を実行時刻基準で判定するため、
# 記録した固定日付から 14 日進むと実行時刻に応じて黙って赤化する。2026-08-10 に3件同時発火）。
# 新ストア・新 observability section を作らず、既存テストの1関数として同型の再発を検出する
# （このファイル自身のソースを ``ast`` で静的走査する自己参照テスト）。
#
# 検出単位はテスト**関数**（呼び出し引数の字面ではない）: このファイルの実バグ事例
# （test_unparsable_timestamp_excluded 等）は accept レコードを ``good = _adv(..., "2026-...")``
# のように呼び出しの**手前**で変数に組み立ててから渡す書き方が大半で、呼び出し引数の字面だけを
# 見る素朴な文字列スキャンでは日付リテラルを取りこぼす（実測: paren 深さで引数ブロックだけを
# 抽出する試作版は、まさにこのファイルの主要な違反パターンを検出できなかった）。関数本体全体に
# 固定 ISO 日付リテラルが1つでもあれば、その関数内の compute_verify_pending 呼び出し全てに
# now= キーワードを要求する。
_ISO_DATE_LITERAL_RE = re.compile(r"^202\d-\d{2}-\d{2}T")


def _is_compute_verify_pending_call(node: "ast.AST") -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "compute_verify_pending"
    )


def test_no_fixed_iso_date_without_now_kwarg_regression_guard() -> None:
    """compute_verify_pending への呼び出しが固定 ISO 日付リテラルを使う関数なら now= も伴うこと。

    このファイル自身のソースを ``ast`` で静的走査する（新規テストが同じ誤りを再導入したら
    赤くする）。テスト対象は本ファイル限定（他の TTL コンポーネント — weak_signals/
    evolve_decisions/triage_ledger/icebox 系 — の既存テストは #410 是正時に横断調査済みで、
    いずれも相対日付（``datetime.now(timezone.utc) - timedelta(...)``）か固定 now とのペアで
    安全と確認済み。汎用の全ファイル走査は過剰と判断しこのファイルに閉じた最小ガードに留める）。
    """
    source = Path(__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)

    offenders: "list[str]" = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        calls = [n for n in ast.walk(node) if _is_compute_verify_pending_call(n)]
        if not calls:
            continue
        has_fixed_date = any(
            isinstance(n, ast.Constant)
            and isinstance(n.value, str)
            and _ISO_DATE_LITERAL_RE.match(n.value)
            for n in ast.walk(node)
        )
        if not has_fixed_date:
            continue
        if any(not any(kw.arg == "now" for kw in call.keywords) for call in calls):
            offenders.append(node.name)

    assert not offenders, (
        "固定 ISO 日付リテラルを使う関数なのに now= を伴わない compute_verify_pending 呼び出しを"
        f"検出しました（time bomb・#410 再発）: {offenders}"
    )


class TestExposureSessionsSinceLatestAccept:
    """#267 C2: exposure は最新 accept run の記録時刻を since としてセッション数を数える。"""

    def test_since_kwarg_equals_latest_accept_timestamp(self, monkeypatch):
        received = {}

        def _fake_sessions(*, since=None, **kwargs):
            received["since"] = since
            return {"alpha": 3}

        from fleet import collectors as fcol
        monkeypatch.setattr(fcol, "aggregate_sessions_by_project", _fake_sessions)

        advisory_records = [
            _adv("accept", "r1", "2026-07-10T00:00:00+00:00"),
        ]
        exposure = qv._exposure_sessions_since_latest_accept("alpha", advisory_records, [])

        assert exposure == 3
        assert received["since"] == qv._parse_iso("2026-07-10T00:00:00+00:00")

    def test_no_accept_records_skips_session_query(self, monkeypatch):
        """accept 記録が無ければ session store すら読まない（無駄クエリを避ける）。"""
        called = []

        def _fake_sessions(**kwargs):
            called.append(kwargs)
            return {}

        from fleet import collectors as fcol
        monkeypatch.setattr(fcol, "aggregate_sessions_by_project", _fake_sessions)

        exposure = qv._exposure_sessions_since_latest_accept("alpha", [], [])
        assert exposure == 0
        assert called == []


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
        # #267 I1: TTL(14日) 判定が実時刻基準で入るため、fixture は「今」を起点にする
        # （固定日付だとテスト実行日によって TTL 失効し status="none" に化ける）。
        recent = datetime.now(timezone.utc).isoformat()

        def _fake_read(slug=None):
            calls.append(slug)
            return [
                {
                    "pj_slug": "alpha",
                    "decision": "accept",
                    "run_id": "r1",
                    "recorded_at": recent,
                },
                {
                    "pj_slug": "beta",
                    "decision": "accept",
                    "run_id": "r2",
                    "recorded_at": recent,
                },
            ]

        import advisory_decision_log
        import optimize_history_store
        from fleet import collectors as fcol

        monkeypatch.setattr(advisory_decision_log, "read_advisory_decisions", _fake_read)
        monkeypatch.setattr(optimize_history_store, "load_history", lambda slug: [])
        # #267 C2: exposure は accept 記録時刻以降の session 数から算出する（since 窓クエリ）。
        # alpha=露出あり(verifiable) / beta=露出なし(awaiting_exposure) を再現する。
        monkeypatch.setattr(
            fcol,
            "aggregate_sessions_by_project",
            lambda **kwargs: {"alpha": 2, "beta": 0},
        )

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
        recent = datetime.now(timezone.utc).isoformat()

        def _fake_read(slug=None):
            return [
                {
                    "pj_slug": "rl-anything",  # 旧 slug のまま残る legacy レコード
                    "decision": "accept",
                    "run_id": "r1",
                    "recorded_at": recent,
                }
            ]

        import advisory_decision_log
        import optimize_history_store
        from fleet import collectors as fcol

        monkeypatch.setattr(advisory_decision_log, "read_advisory_decisions", _fake_read)
        monkeypatch.setattr(optimize_history_store, "load_history", lambda slug: [])
        monkeypatch.setattr(
            fcol,
            "aggregate_sessions_by_project",
            lambda **kwargs: {"evolve-anything": 1},
        )

        materials = [{"pj_slug": "evolve-anything", "activity_since": {"sessions": 1}}]
        qv.attach_verify_pending(
            materials,
            canonicalize=lambda s: "evolve-anything" if s == "rl-anything" else s,
        )

        assert materials[0]["verify_pending"]["status"] == "verifiable"
        assert materials[0]["verify_pending"]["accepted"] == 1


# --- optimize_history の rename alias union read（#267 I2）--------------------


class TestLoadOptimizeHistoryWithAliases:
    """advisory は pj_slug_match で alias 対応済みだが optimize_history_store.load_history は
    完全一致のみ ⇒ 2レーンの rename 耐性が非対称だった。alias 分を union read + id dedup する。
    """

    def test_unions_records_across_aliases(self, monkeypatch):
        from fleet import queue as fq

        monkeypatch.setattr(
            fq, "_equivalence_slugs", lambda slug: {"evolve-anything", "rl-anything"}
        )

        import optimize_history_store

        def _fake_load_history(slug):
            if slug == "evolve-anything":
                return [{"id": "e1", "human_accepted": True, "run_id": "r1", "timestamp": "t1"}]
            if slug == "rl-anything":
                return [{"id": "r1", "human_accepted": True, "run_id": "r2", "timestamp": "t2"}]
            return []

        monkeypatch.setattr(optimize_history_store, "load_history", _fake_load_history)

        out = qv._load_optimize_history_with_aliases("evolve-anything")
        ids = sorted(rec["id"] for rec in out)
        assert ids == ["e1", "r1"]

    def test_dedups_by_id_canonical_first(self, monkeypatch):
        """canonical 側の entry を優先して dedup する（候補列先頭優先、他ストアと同じ流儀）。"""
        from fleet import queue as fq

        monkeypatch.setattr(
            fq, "_equivalence_slugs", lambda slug: {"evolve-anything", "rl-anything"}
        )

        import optimize_history_store

        def _fake_load_history(slug):
            if slug == "evolve-anything":
                return [{"id": "dup", "human_accepted": True, "run_id": "canon", "timestamp": "t1"}]
            if slug == "rl-anything":
                return [{"id": "dup", "human_accepted": True, "run_id": "legacy", "timestamp": "t2"}]
            return []

        monkeypatch.setattr(optimize_history_store, "load_history", _fake_load_history)

        out = qv._load_optimize_history_with_aliases("evolve-anything")
        assert len(out) == 1
        assert out[0]["run_id"] == "canon"

    def test_records_without_id_are_all_kept(self, monkeypatch):
        """id 欠落 entry は安全に dedup できないため全件保持する。"""
        from fleet import queue as fq

        monkeypatch.setattr(fq, "_equivalence_slugs", lambda slug: {"a", "b"})

        import optimize_history_store

        def _fake_load_history(slug):
            return [{"human_accepted": True, "run_id": f"run_{slug}", "timestamp": "t"}]

        monkeypatch.setattr(optimize_history_store, "load_history", _fake_load_history)

        out = qv._load_optimize_history_with_aliases("a")
        assert len(out) == 2

    def test_import_failure_falls_back_to_bare_slug(self, monkeypatch):
        """fleet.queue._equivalence_slugs が import できない環境では自身のみで安全側に倒す。"""
        from fleet import queue as fq

        monkeypatch.delattr(fq, "_equivalence_slugs", raising=True)

        import optimize_history_store

        monkeypatch.setattr(
            optimize_history_store,
            "load_history",
            lambda slug: [{"id": "x", "human_accepted": True, "run_id": "r", "timestamp": "t"}],
        )

        out = qv._load_optimize_history_with_aliases("alpha")
        assert len(out) == 1

    def test_verify_pending_by_pj_uses_alias_union(self, monkeypatch):
        """verify_pending_by_pj は optimize_history を alias union で読む（E2E 配線確認）。"""
        recent = datetime.now(timezone.utc).isoformat()

        from fleet import queue as fq

        monkeypatch.setattr(
            fq, "_equivalence_slugs", lambda slug: {"evolve-anything", "rl-anything"}
        )

        import advisory_decision_log
        import optimize_history_store
        from fleet import collectors as fcol

        monkeypatch.setattr(
            advisory_decision_log, "read_advisory_decisions", lambda slug=None: []
        )
        monkeypatch.setattr(fcol, "aggregate_sessions_by_project", lambda **kwargs: {})

        def _fake_load_history(slug):
            if slug == "rl-anything":  # 旧 slug 名義の legacy accept
                return [
                    {
                        "id": "legacy1",
                        "human_accepted": True,
                        "run_id": "r1",
                        "timestamp": recent,
                    }
                ]
            return []

        monkeypatch.setattr(optimize_history_store, "load_history", _fake_load_history)

        out = qv.verify_pending_by_pj("evolve-anything")
        assert out["accepted"] == 1
        assert out["status"] == "awaiting_exposure"  # exposure 0（accept はあるが露出なし）
