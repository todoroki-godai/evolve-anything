"""ADR-046 重み昇格レディネスの決定論判定テスト（#461, advisory）。

outcome 3軸（correction 再発率 / 一発成功率 / rework 率近似）を environment fitness の
重みへ繰り入れてよいかを、ADR-046 が定めた3条件で決定論判定する:

  1. 分散が十分    — 軸値が全 PJ で同値でない（全 PJ 同値 = 測定バグ強シグナル, #445 流用）
  2. データ件数下限 — 分母（correction≥10 / sessions≥30）を満たす PJ が複数ある
  3. 方向の妥当性  — env 改善イベント（reflect/evolve 適用）の前後で軸が期待方向へ動く

決定論・LLM 非依存。tmp の DATA_DIR に疑似 jsonl ストアを置いて算出する。
monkeypatch は文字列ターゲットを避け、import した module オブジェクトを直接 patch する
（order-dependent 失敗の既知 pitfall 準拠）。
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_lib_dir = Path(__file__).resolve().parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

from audit import outcome_promotion_readiness as opr  # noqa: E402


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in records) + ("\n" if records else ""))


# corrections.jsonl: project_path で PJ を識別、correction_type / session_id / timestamp。
def _correction(pj: str, ctype: str, sid: str, dt: datetime) -> dict:
    return {
        "project_path": pj,
        "correction_type": ctype,
        "session_id": sid,
        "timestamp": _iso(dt),
    }


# sessions.jsonl: project で PJ を識別、error_count / tool_sequence / timestamp。
def _session(pj: str, sid: str, dt: datetime, *, error_count: int = 0,
             tool_sequence=None) -> dict:
    return {
        "project": pj,
        "session_id": sid,
        "timestamp": _iso(dt),
        "error_count": error_count,
        "tool_sequence": tool_sequence or [],
    }


# ============================================================================
# 条件1: 分散が十分（軸値が全 PJ で同値でないか）
# ============================================================================

class TestVarianceCondition:
    def test_distinct_values_pass(self):
        # 軸値が PJ ごとに異なる → 分散十分 → pass
        per_pj = {"a": 0.1, "b": 0.4, "c": 0.7}
        result = opr.check_variance(per_pj)
        assert result["pass"] is True

    def test_all_identical_nonzero_fail(self):
        # 全 PJ 同値 = 測定バグ強シグナル（#445 思想）→ fail
        per_pj = {"a": 0.42, "b": 0.42, "c": 0.42}
        result = opr.check_variance(per_pj)
        assert result["pass"] is False
        assert result["reason"] == "all_identical"

    def test_fewer_than_two_pj_fail(self):
        # PJ が 1 つしかなければ分散を語れない → fail（reason=insufficient_pj）
        per_pj = {"a": 0.42}
        result = opr.check_variance(per_pj)
        assert result["pass"] is False
        assert result["reason"] == "insufficient_pj"

    def test_empty_fail(self):
        result = opr.check_variance({})
        assert result["pass"] is False


# ============================================================================
# 条件2: データ件数下限（分母 floor を満たす PJ が複数あるか）
# ============================================================================

class TestDenominatorCondition:
    def test_two_pj_meet_floor_pass(self):
        denom = {"a": 12, "b": 35, "c": 3}
        result = opr.check_denominators(denom, floor=10)
        assert result["pass"] is True
        assert sorted(result["meeting"]) == ["a", "b"]

    def test_only_one_pj_meets_floor_fail(self):
        # 「複数 PJ」が条件 → 1 PJ のみでは fail
        denom = {"a": 12, "b": 3}
        result = opr.check_denominators(denom, floor=10)
        assert result["pass"] is False
        assert result["meeting"] == ["a"]

    def test_no_pj_meets_floor_fail(self):
        denom = {"a": 2, "b": 3}
        result = opr.check_denominators(denom, floor=10)
        assert result["pass"] is False
        assert result["meeting"] == []

    def test_empty_fail(self):
        result = opr.check_denominators({}, floor=10)
        assert result["pass"] is False


# ============================================================================
# per-PJ 集計（実ストア → PJ 別の軸値 / 分母）
# ============================================================================

class TestPerPjCorrection:
    def test_no_file_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr(opr, "DATA_DIR", tmp_path)
        out = opr.per_pj_correction_recurrence(days=30)
        assert out == {}

    def test_groups_by_project_path(self, tmp_path, monkeypatch):
        monkeypatch.setattr(opr, "DATA_DIR", tmp_path)
        now = _now()
        records = [
            # PJ a: type "iya" が 2 セッション → 再発 / "stop" 1 セッション → 非再発 → rate 0.5
            _correction("/p/a", "iya", "s1", now - timedelta(days=2)),
            _correction("/p/a", "iya", "s2", now - timedelta(days=1)),
            _correction("/p/a", "stop", "s3", now),
            # PJ b: type "no" が 1 セッションのみ → 再発なし → rate 0.0
            _correction("/p/b", "no", "s4", now),
        ]
        _write_jsonl(tmp_path / "corrections.jsonl", records)
        out = opr.per_pj_correction_recurrence(days=30)
        assert out["/p/a"]["value"] == 0.5
        assert out["/p/a"]["denominator"] == 3  # correction 件数
        assert out["/p/b"]["value"] == 0.0
        assert out["/p/b"]["denominator"] == 1

    def test_window_excludes_old(self, tmp_path, monkeypatch):
        monkeypatch.setattr(opr, "DATA_DIR", tmp_path)
        now = _now()
        records = [
            _correction("/p/a", "iya", "s1", now - timedelta(days=90)),
            _correction("/p/a", "iya", "s2", now - timedelta(days=80)),
        ]
        _write_jsonl(tmp_path / "corrections.jsonl", records)
        out = opr.per_pj_correction_recurrence(days=30)
        assert out == {}


class TestPerPjSession:
    def test_groups_by_project(self, tmp_path, monkeypatch):
        monkeypatch.setattr(opr, "DATA_DIR", tmp_path)
        now = _now()
        records = [
            _session("/p/a", "s1", now, error_count=0),
            _session("/p/a", "s2", now, error_count=2),
            _session("/p/b", "s3", now, error_count=0),
        ]
        _write_jsonl(tmp_path / "sessions.jsonl", records)
        out = opr.per_pj_first_try_success(days=30)
        assert out["/p/a"]["value"] == 0.5  # 1/2 clean
        assert out["/p/a"]["denominator"] == 2
        assert out["/p/b"]["value"] == 1.0
        assert out["/p/b"]["denominator"] == 1

    def test_no_file_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr(opr, "DATA_DIR", tmp_path)
        assert opr.per_pj_first_try_success(days=30) == {}


# ============================================================================
# 条件3: 方向の妥当性（apply イベント前後で軸が期待方向へ動くか）
# ============================================================================

class TestDirectionCondition:
    def test_no_apply_events_fail(self, tmp_path, monkeypatch):
        # apply イベント（optimize_history の human_accepted=True）が無ければ判定不能 → fail
        monkeypatch.setattr(opr, "DATA_DIR", tmp_path)
        result = opr.check_direction(days=30, window_days=14)
        assert result["pass"] is False
        assert result["reason"] == "no_apply_events"

    def test_improvement_after_apply_pass(self, tmp_path, monkeypatch):
        # first_try_success が apply 後に上がる（期待方向）→ pass の証拠が出る
        monkeypatch.setattr(opr, "DATA_DIR", tmp_path)
        now = _now()
        anchor = now - timedelta(days=15)
        # apply イベント（optimize_history/<slug>.jsonl）
        opr_hist = tmp_path / "optimize_history"
        opr_hist.mkdir(parents=True, exist_ok=True)
        (opr_hist / "p_a.jsonl").write_text(
            json.dumps({
                "id": "x1", "human_accepted": True,
                "timestamp": _iso(anchor), "skill_name": "s",
            }) + "\n"
        )
        # before 窓: error あり多め（first_try 低）、after 窓: clean 多め（first_try 高）
        sessions = []
        for i in range(4):  # before: 1/4 clean = 0.25
            sessions.append(_session("p_a", f"b{i}", anchor - timedelta(days=3),
                                     error_count=0 if i == 0 else 1))
        for i in range(4):  # after: 4/4 clean = 1.0
            sessions.append(_session("p_a", f"a{i}", anchor + timedelta(days=3),
                                     error_count=0))
        _write_jsonl(tmp_path / "sessions.jsonl", sessions)
        result = opr.check_direction(days=60, window_days=14)
        # apply イベントを anchor に前後比較し、期待方向（first_try 上昇）の相関がある
        assert result["pass"] is True
        assert result["anchors"] >= 1
        # evidence に before/after の軸値が入る
        assert any("first_try" in str(e) for e in result["evidence"])


# ============================================================================
# 統合: compute_promotion_readiness（3条件 + 提案判定）
# ============================================================================

class TestComputePromotionReadiness:
    def test_real_world_insufficient_data_not_promotable(self, tmp_path, monkeypatch):
        # データ不足（条件2/3 が ✗）→ promote=False、各条件に evidence
        monkeypatch.setattr(opr, "DATA_DIR", tmp_path)
        now = _now()
        # 1 PJ だけ少量データ → 条件2 fail、apply イベントなし → 条件3 fail
        _write_jsonl(tmp_path / "corrections.jsonl", [
            _correction("/p/a", "iya", "s1", now),
        ])
        result = opr.compute_promotion_readiness(days=30, window_days=14)
        assert result["promote"] is False
        # 3軸とも結果を持つ
        assert set(result["axes"].keys()) == {
            "correction_recurrence", "first_try_success", "rework"
        }

    def test_synthetic_all_three_pass_promotes(self, tmp_path, monkeypatch):
        # 合成 fixture で 3 条件すべて ✓ → promote=True
        monkeypatch.setattr(opr, "DATA_DIR", tmp_path)
        now = _now()
        anchor = now - timedelta(days=20)

        # --- corrections: 3 PJ それぞれ分母 floor 超え + 値が異なる（条件1/2）---
        corr = []
        # PJ a: recurrence 高め
        for i in range(12):
            ctype = "iya" if i < 8 else f"t{i}"
            sid = f"a_s{i % 3}"  # iya が複数セッションに跨る → 再発
            corr.append(_correction("/p/a", ctype, sid, now - timedelta(days=1)))
        # PJ b: recurrence 中
        for i in range(12):
            ctype = "iya" if i < 4 else f"u{i}"
            sid = f"b_s{i % 3}"
            corr.append(_correction("/p/b", ctype, sid, now - timedelta(days=1)))
        # PJ c: recurrence 低
        for i in range(12):
            ctype = f"v{i}"  # 全部別 type → 再発ゼロ
            corr.append(_correction("/p/c", ctype, f"c_s{i}", now - timedelta(days=1)))
        _write_jsonl(tmp_path / "corrections.jsonl", corr)

        # --- sessions: 3 PJ それぞれ 30+ + first_try 値が異なる（条件1/2）---
        # + apply 前後で改善する PJ a（条件3）---
        sess = []
        opr_hist = tmp_path / "optimize_history"
        opr_hist.mkdir(parents=True, exist_ok=True)
        # history のファイル名（slug）は session の PJ basename と対応する（/p/a → a）。
        (opr_hist / "a.jsonl").write_text(
            json.dumps({"id": "x", "human_accepted": True,
                        "timestamp": _iso(anchor), "skill_name": "s"}) + "\n"
        )
        # PJ a: before 窓 error 多 → after 窓 clean（改善）
        for i in range(35):
            in_before = i < 18
            dt = anchor - timedelta(days=3) if in_before else anchor + timedelta(days=3)
            ec = 1 if in_before else 0
            sess.append(_session("/p/a", f"pa{i}", dt, error_count=ec))
        # PJ b: first_try ~0.5
        for i in range(35):
            sess.append(_session("/p/b", f"pb{i}", now - timedelta(days=1),
                                 error_count=i % 2))
        # PJ c: first_try ~1.0
        for i in range(35):
            sess.append(_session("/p/c", f"pc{i}", now - timedelta(days=1),
                                 error_count=0))
        _write_jsonl(tmp_path / "sessions.jsonl", sess)

        result = opr.compute_promotion_readiness(days=60, window_days=14)
        assert result["variance"]["pass"] is True
        assert result["denominator"]["pass"] is True
        assert result["direction"]["pass"] is True
        assert result["promote"] is True

    def test_subfloor_pjs_not_flagged_as_measurement_bug(self, tmp_path, monkeypatch):
        # #563-2: distinct_types が floor 未満の PJ は値が統計的に無意味で 0.0/1.0 に振れる。
        # 複数 PJ がサブ floor で一斉に 1.0 になっても「全 PJ 同値 = 測定バグ」(all_identical)
        # の false positive を出してはならない。floor 未満は variance 入力から除外され、
        # 残りが _MIN_PJ 未満なら insufficient_pj（測定バグではなくサンプル不足）になる。
        monkeypatch.setattr(opr, "DATA_DIR", tmp_path)
        now = _now()
        corr = []
        # 3 PJ それぞれ distinct_types=3（< floor 5）・全 type 再発 → 各 PJ value=1.0
        for pj in ("/p/a", "/p/b", "/p/c"):
            for t in range(3):
                for s in range(2):  # 同 type を 2 セッションに跨らせ再発させる
                    corr.append(_correction(pj, f"t{t}", f"{pj}_s{t}_{s}", now))
        _write_jsonl(tmp_path / "corrections.jsonl", corr)
        result = opr.compute_promotion_readiness(days=30, window_days=14)
        assert result["variance"]["pass"] is False
        # 修正前は all_identical（value 1.0）の measurement-bug FP。修正後は除外され
        # 残り 0 PJ → insufficient_pj。
        assert result["variance"]["reason"] == "insufficient_pj"

    def test_at_floor_identical_still_flagged(self, tmp_path, monkeypatch):
        # floor を満たす PJ が真に同値なら従来どおり all_identical を検出する
        # （floor 導入が正当な測定バグシグナルまで握り潰さないことの確認）。
        monkeypatch.setattr(opr, "DATA_DIR", tmp_path)
        now = _now()
        corr = []
        # 2 PJ それぞれ distinct_types=5（= floor）・全 type 再発 → 各 PJ value=1.0
        for pj in ("/p/a", "/p/b"):
            for t in range(5):
                for s in range(2):
                    corr.append(_correction(pj, f"t{t}", f"{pj}_s{t}_{s}", now))
        _write_jsonl(tmp_path / "corrections.jsonl", corr)
        result = opr.compute_promotion_readiness(days=30, window_days=14)
        assert result["variance"]["reason"] == "all_identical"
        assert result["variance"]["value"] == 1.0

    def test_dry_run_no_store_write(self, tmp_path, monkeypatch):
        # 読み取りのみ — compute は DATA_DIR に何も書かない。
        # ファイル名集合の同一性だけでなく各ファイルの read_bytes() を before/after で
        # 照合し、既存ファイルへの追記・書換も検出する（#471: byte 照合強化）。
        monkeypatch.setattr(opr, "DATA_DIR", tmp_path)
        now = _now()
        _write_jsonl(tmp_path / "corrections.jsonl", [
            _correction("/p/a", "iya", "s1", now),
        ])
        # 既存ストア（sessions / optimize_history）も置き、追記・書換も検出対象にする。
        _write_jsonl(tmp_path / "sessions.jsonl", [])
        opr_hist = tmp_path / "optimize_history"
        opr_hist.mkdir(parents=True, exist_ok=True)
        (opr_hist / "a.jsonl").write_text(
            json.dumps({"id": "x", "human_accepted": True,
                        "timestamp": _iso(now), "skill_name": "s"}) + "\n"
        )

        def _snapshot() -> dict[str, bytes]:
            return {
                str(p.relative_to(tmp_path)): p.read_bytes()
                for p in sorted(tmp_path.rglob("*"))
                if p.is_file()
            }

        before = _snapshot()
        opr.compute_promotion_readiness(days=30, window_days=14)
        after = _snapshot()
        assert before == after  # 新規生成・追記・書換いずれもなし（byte 不変）


# ============================================================================
# observability builder（markdown / 構造化 両経路）
# ============================================================================

class TestBuildSection:
    def test_returns_none_when_no_data(self, tmp_path, monkeypatch):
        from audit.sections_promotion_readiness import build_promotion_readiness_section

        monkeypatch.setattr(opr, "DATA_DIR", tmp_path / "empty")
        assert build_promotion_readiness_section(tmp_path) is None

    def test_surfaces_conditions_when_data_present(self, tmp_path, monkeypatch):
        from audit.sections_promotion_readiness import build_promotion_readiness_section

        monkeypatch.setattr(opr, "DATA_DIR", tmp_path)
        now = _now()
        _write_jsonl(tmp_path / "corrections.jsonl", [
            _correction("/p/a", "iya", "s1", now),
        ])
        lines = build_promotion_readiness_section(tmp_path)
        assert lines is not None
        combined = "\n".join(lines)
        # 3条件が ✓/✗ で出る
        assert "分散" in combined or "variance" in combined.lower()
        assert "✗" in combined  # データ不足なので少なくとも 1 つ ✗

    def test_promotion_line_when_all_pass(self, tmp_path, monkeypatch):
        from audit.sections_promotion_readiness import build_promotion_readiness_section

        monkeypatch.setattr(opr, "DATA_DIR", tmp_path)
        now = _now()
        anchor = now - timedelta(days=20)

        corr = []
        for i in range(12):
            ctype = "iya" if i < 8 else f"t{i}"
            corr.append(_correction("/p/a", ctype, f"a_s{i % 3}", now - timedelta(days=1)))
        for i in range(12):
            ctype = "iya" if i < 4 else f"u{i}"
            corr.append(_correction("/p/b", ctype, f"b_s{i % 3}", now - timedelta(days=1)))
        for i in range(12):
            corr.append(_correction("/p/c", f"v{i}", f"c_s{i}", now - timedelta(days=1)))
        _write_jsonl(tmp_path / "corrections.jsonl", corr)

        sess = []
        opr_hist = tmp_path / "optimize_history"
        opr_hist.mkdir(parents=True, exist_ok=True)
        # history のファイル名（slug）は session の PJ basename と対応する（/p/a → a）。
        (opr_hist / "a.jsonl").write_text(
            json.dumps({"id": "x", "human_accepted": True,
                        "timestamp": _iso(anchor), "skill_name": "s"}) + "\n"
        )
        for i in range(35):
            in_before = i < 18
            dt = anchor - timedelta(days=3) if in_before else anchor + timedelta(days=3)
            sess.append(_session("/p/a", f"pa{i}", dt, error_count=1 if in_before else 0))
        for i in range(35):
            sess.append(_session("/p/b", f"pb{i}", now - timedelta(days=1), error_count=i % 2))
        for i in range(35):
            sess.append(_session("/p/c", f"pc{i}", now - timedelta(days=1), error_count=0))
        _write_jsonl(tmp_path / "sessions.jsonl", sess)

        lines = build_promotion_readiness_section(tmp_path)
        assert lines is not None
        combined = "\n".join(lines)
        assert "重み昇格" in combined  # 提案行
        assert "✓" in combined


class TestObservabilityWiring:
    def test_registered_in_observability_builders(self):
        # ADR-028: _OBSERVABILITY_BUILDERS への登録で markdown / 構造化 両経路に伝播
        from audit.observability import _OBSERVABILITY_BUILDERS

        keys = [k for k, _ in _OBSERVABILITY_BUILDERS]
        assert "promotion_readiness" in keys


# ============================================================================
# #469: session 系分母を sessions.db（union read）から得られること
# ============================================================================

import session_store  # noqa: E402

requires_duckdb = pytest.mark.skipif(
    not session_store.HAS_DUCKDB, reason="duckdb が無い環境"
)


def _ingest_sessions_db(data_dir: Path, records: list[dict]) -> None:
    """records を sessions.jsonl に書いて session_store.ingest() で db へ取り込み rotate。

    ingest 後 live jsonl は rotate されるため、その後の読みは db 経由になる
    （実環境: #415 で sessions.jsonl がほぼ常に空になっている状態を再現する）。
    """
    old_dir = session_store.DATA_DIR
    old_db = session_store.SESSIONS_DB
    old_jsonl = session_store.SESSIONS_JSONL
    try:
        session_store.DATA_DIR = data_dir
        session_store.SESSIONS_DB = data_dir / "sessions.db"
        session_store.SESSIONS_JSONL = data_dir / "sessions.jsonl"
        path = data_dir / "sessions.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(json.dumps(r) for r in records) + "\n")
        session_store.ingest()
    finally:
        session_store.DATA_DIR = old_dir
        session_store.SESSIONS_DB = old_db
        session_store.SESSIONS_JSONL = old_jsonl


class TestSessionDenominatorFromDb:
    """#469: live jsonl が rotate で空でも sessions.db から session 系分母を得る。"""

    @requires_duckdb
    def test_per_pj_first_try_reads_from_db_when_jsonl_rotated(self, tmp_path, monkeypatch):
        monkeypatch.setattr(opr, "DATA_DIR", tmp_path)
        now = _now()
        _ingest_sessions_db(tmp_path, [
            _session("/p/a", "s1", now, error_count=0),
            _session("/p/a", "s2", now, error_count=2),
            _session("/p/b", "s3", now, error_count=0),
        ])
        # live jsonl は rotate されて存在しないはず（実環境再現）。
        assert not (tmp_path / "sessions.jsonl").exists()

        out = opr.per_pj_first_try_success(days=30)
        # jsonl 直読なら空 = 永遠に ✗ だったが、db union read で分母が取れる。
        assert out["/p/a"]["denominator"] == 2
        assert out["/p/a"]["value"] == 0.5
        assert out["/p/b"]["denominator"] == 1

    @requires_duckdb
    def test_condition2_denominator_met_from_db(self, tmp_path, monkeypatch):
        """条件2: db 側 session レコードで sessions floor を満たせる。"""
        monkeypatch.setattr(opr, "DATA_DIR", tmp_path)
        now = _now()
        sess = []
        for i in range(35):
            sess.append(_session("/p/a", f"pa{i}", now, error_count=i % 2))
        for i in range(35):
            sess.append(_session("/p/b", f"pb{i}", now, error_count=0))
        _ingest_sessions_db(tmp_path, sess)
        assert not (tmp_path / "sessions.jsonl").exists()

        fs = opr.per_pj_first_try_success(days=30)
        denom = opr.check_denominators(
            {pj: v["denominator"] for pj, v in fs.items()}, floor=opr.SESSION_FLOOR
        )
        assert denom["pass"] is True
        assert sorted(denom["meeting"]) == ["/p/a", "/p/b"]

    @requires_duckdb
    def test_condition3_paired_windows_from_db(self, tmp_path, monkeypatch):
        """条件3: apply anchor 前後の paired session を db から取れて方向判定できる。"""
        monkeypatch.setattr(opr, "DATA_DIR", tmp_path)
        now = _now()
        anchor = now - timedelta(days=15)
        opr_hist = tmp_path / "optimize_history"
        opr_hist.mkdir(parents=True, exist_ok=True)
        (opr_hist / "p_a.jsonl").write_text(
            json.dumps({"id": "x1", "human_accepted": True,
                        "timestamp": _iso(anchor), "skill_name": "s"}) + "\n"
        )
        sessions = []
        for i in range(4):  # before: 1/4 clean = 0.25
            sessions.append(_session("p_a", f"b{i}", anchor - timedelta(days=3),
                                     error_count=0 if i == 0 else 1))
        for i in range(4):  # after: 4/4 clean = 1.0
            sessions.append(_session("p_a", f"a{i}", anchor + timedelta(days=3),
                                     error_count=0))
        _ingest_sessions_db(tmp_path, sessions)
        assert not (tmp_path / "sessions.jsonl").exists()

        result = opr.check_direction(days=60, window_days=14)
        # jsonl 直読なら no_paired_windows（paired session 0）だったが、db で paired が取れる。
        assert result.get("reason") != "no_paired_windows"
        assert result["compared"] >= 1
        assert result["pass"] is True

    @requires_duckdb
    def test_db_read_does_not_write(self, tmp_path, monkeypatch):
        """db 経路の読み取りでも DATA_DIR の byte を変えない（dry-run 契約 #461 維持）。"""
        monkeypatch.setattr(opr, "DATA_DIR", tmp_path)
        now = _now()
        _write_jsonl(tmp_path / "corrections.jsonl", [_correction("/p/a", "iya", "s1", now)])
        _ingest_sessions_db(tmp_path, [_session("/p/a", "s1", now, error_count=0)])

        def _snapshot() -> dict[str, bytes]:
            return {
                str(p.relative_to(tmp_path)): p.read_bytes()
                for p in sorted(tmp_path.rglob("*"))
                if p.is_file()
            }

        before = _snapshot()
        opr.compute_promotion_readiness(days=30, window_days=14)
        after = _snapshot()
        assert before == after  # db / jsonl いずれも byte 不変
