"""アウトカム3軸（correction 再発率 / 一発成功率 / rework 率）のテスト（#423）。

決定論・LLM 非依存。tmp の DATA_DIR に疑似 jsonl ストアを置いて算出する。
各軸は (value, evidence) を返し、データ不足時は value=None で「データ不足」を明示する。

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

from audit import outcome_metrics  # noqa: E402


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(r) for r in records) + ("\n" if records else ""))


# ---------- correction 再発率 ----------

class TestCorrectionRecurrence:
    def test_no_corrections_file_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr(outcome_metrics, "DATA_DIR", tmp_path)
        value, evidence = outcome_metrics.correction_recurrence_rate(days=30)
        assert value is None
        assert evidence["reason"] == "no_data"

    def test_recurrence_detected(self, tmp_path, monkeypatch):
        monkeypatch.setattr(outcome_metrics, "DATA_DIR", tmp_path)
        now = _now()
        # #529-2: 最小分母 floor (distinct >= 5) を満たすよう 5 distinct type を用意する。
        # "iya" だけが 2 セッションに跨って再発 → recurring=1 / distinct=5 → 0.2。
        records = [
            {"correction_type": "iya", "session_id": "s1", "timestamp": _iso(now - timedelta(days=2))},
            {"correction_type": "iya", "session_id": "s2", "timestamp": _iso(now - timedelta(days=1))},
            {"correction_type": "stop", "session_id": "s3", "timestamp": _iso(now)},
            {"correction_type": "no", "session_id": "s4", "timestamp": _iso(now)},
            {"correction_type": "wrong", "session_id": "s5", "timestamp": _iso(now)},
            {"correction_type": "redo", "session_id": "s6", "timestamp": _iso(now)},
        ]
        _write_jsonl(tmp_path / "corrections.jsonl", records)
        value, evidence = outcome_metrics.correction_recurrence_rate(days=30)
        # 5 distinct types, 1 recurring → 0.2
        assert value == pytest.approx(0.2)
        assert evidence["distinct_types"] == 5
        assert evidence["recurring_types"] == 1
        assert "iya" in evidence["examples"]

    def test_below_distinct_floor_returns_none(self, tmp_path, monkeypatch):
        """#529-2: distinct type < floor (5) では率を出さず insufficient_sample。"""
        monkeypatch.setattr(outcome_metrics, "DATA_DIR", tmp_path)
        now = _now()
        # distinct 2 type / 9 correction / 再発 1 type = #529-2 で問題視された 0.50 ケース
        records = [
            {"correction_type": "iya", "session_id": f"s{i}", "timestamp": _iso(now)}
            for i in range(8)
        ] + [{"correction_type": "stop", "session_id": "sx", "timestamp": _iso(now)}]
        _write_jsonl(tmp_path / "corrections.jsonl", records)
        value, evidence = outcome_metrics.correction_recurrence_rate(days=30)
        assert value is None
        assert evidence["reason"] == "insufficient_sample"
        assert evidence["distinct_types"] == 2
        assert evidence["floor"] == outcome_metrics.MIN_DISTINCT_TYPES_FLOOR
        assert evidence["records"] == 9

    def test_at_distinct_floor_returns_rate(self, tmp_path, monkeypatch):
        """floor 丁度 (distinct == 5) なら率を出す（境界 inclusive）。"""
        monkeypatch.setattr(outcome_metrics, "DATA_DIR", tmp_path)
        now = _now()
        records = [
            {"correction_type": f"t{i}", "session_id": f"s{i}", "timestamp": _iso(now)}
            for i in range(5)
        ]
        _write_jsonl(tmp_path / "corrections.jsonl", records)
        value, evidence = outcome_metrics.correction_recurrence_rate(days=30)
        assert value is not None
        assert evidence["distinct_types"] == 5

    def test_window_filters_old(self, tmp_path, monkeypatch):
        monkeypatch.setattr(outcome_metrics, "DATA_DIR", tmp_path)
        now = _now()
        records = [
            {"correction_type": "iya", "session_id": "s1", "timestamp": _iso(now - timedelta(days=100))},
            {"correction_type": "iya", "session_id": "s2", "timestamp": _iso(now - timedelta(days=99))},
        ]
        _write_jsonl(tmp_path / "corrections.jsonl", records)
        value, evidence = outcome_metrics.correction_recurrence_rate(days=30)
        assert value is None
        assert evidence["reason"] == "no_data"


# ---------- 一発成功率 ----------

class TestFirstTrySuccess:
    def test_no_sessions_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr(outcome_metrics, "DATA_DIR", tmp_path)
        value, evidence = outcome_metrics.first_try_success_rate(days=30)
        assert value is None
        assert evidence["reason"] == "no_data"

    def test_mixed_sessions(self, tmp_path, monkeypatch):
        monkeypatch.setattr(outcome_metrics, "DATA_DIR", tmp_path)
        now = _now()
        ts = _iso(now)
        records = [
            {"session_id": "s1", "error_count": 0, "timestamp": ts, "first_timestamp": ts},
            {"session_id": "s2", "error_count": 0, "timestamp": ts, "first_timestamp": ts},
            {"session_id": "s3", "error_count": 3, "timestamp": ts, "first_timestamp": ts},
            {"session_id": "s4", "error_count": 1, "timestamp": ts, "first_timestamp": ts},
        ]
        _write_jsonl(tmp_path / "sessions.jsonl", records)
        value, evidence = outcome_metrics.first_try_success_rate(days=30)
        # 2 of 4 sessions error-free
        assert value == pytest.approx(0.5)
        assert evidence["total_sessions"] == 4
        assert evidence["clean_sessions"] == 2

    def test_window_filters_old(self, tmp_path, monkeypatch):
        monkeypatch.setattr(outcome_metrics, "DATA_DIR", tmp_path)
        now = _now()
        old = _iso(now - timedelta(days=100))
        records = [{"session_id": "s1", "error_count": 0, "timestamp": old, "first_timestamp": old}]
        _write_jsonl(tmp_path / "sessions.jsonl", records)
        value, evidence = outcome_metrics.first_try_success_rate(days=30)
        assert value is None


# ---------- rework 率 ----------

class TestReworkRate:
    def test_no_sessions_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr(outcome_metrics, "DATA_DIR", tmp_path)
        value, evidence = outcome_metrics.rework_rate(days=30)
        assert value is None
        assert evidence["reason"] == "no_data"

    def test_edit_burst_detected(self, tmp_path, monkeypatch):
        monkeypatch.setattr(outcome_metrics, "DATA_DIR", tmp_path)
        now = _now()
        ts = _iso(now)
        # s1: 3 連続 Edit（検証ツール介在なし）→ rework
        # s2〜s5: Edit → Bash（介在あり）→ rework でない
        # MIN_EDIT_SESSIONS_FLOOR=5 を満たす 5 件の編集ありセッション
        records = [
            {"session_id": "s1", "tool_sequence": ["Read", "Edit", "Edit", "Edit", "Bash"],
             "timestamp": ts, "first_timestamp": ts},
            {"session_id": "s2", "tool_sequence": ["Edit", "Bash", "Edit", "Bash"],
             "timestamp": ts, "first_timestamp": ts},
            {"session_id": "s3", "tool_sequence": ["Edit", "Bash"],
             "timestamp": ts, "first_timestamp": ts},
            {"session_id": "s4", "tool_sequence": ["Edit", "Bash"],
             "timestamp": ts, "first_timestamp": ts},
            {"session_id": "s5", "tool_sequence": ["Edit", "Bash"],
             "timestamp": ts, "first_timestamp": ts},
        ]
        _write_jsonl(tmp_path / "sessions.jsonl", records)
        value, evidence = outcome_metrics.rework_rate(days=30, min_consecutive=3)
        # 1 of 5 sessions with an edit-burst >= 3
        assert value == pytest.approx(0.2)
        assert evidence["total_sessions"] == 5
        assert evidence["rework_sessions"] == 1
        assert "s1" in evidence["examples"]

    def test_no_edit_sessions_excluded(self, tmp_path, monkeypatch):
        monkeypatch.setattr(outcome_metrics, "DATA_DIR", tmp_path)
        now = _now()
        ts = _iso(now)
        # 編集を 1 度も行わないセッションは分母から除外（rework の母集団は編集ありセッション）
        records = [
            {"session_id": "s1", "tool_sequence": ["Read", "Bash", "Bash"], "timestamp": ts, "first_timestamp": ts},
        ]
        _write_jsonl(tmp_path / "sessions.jsonl", records)
        value, evidence = outcome_metrics.rework_rate(days=30)
        assert value is None
        assert evidence["reason"] == "no_data"

    def test_below_edit_sessions_floor_returns_none(self, tmp_path, monkeypatch):
        """#563: edit_sessions < MIN_EDIT_SESSIONS_FLOOR のとき rate=None / insufficient_sample。

        分母 1 件で rework=1 のとき rate=1.0 に張り付き誤シグナルになる。
        floor 未満では率を出さず「サンプル不足」を明示する（correction_recurrence の
        MIN_DISTINCT_TYPES_FLOOR=5 と同方針, #529-2）。
        """
        monkeypatch.setattr(outcome_metrics, "DATA_DIR", tmp_path)
        now = _now()
        ts = _iso(now)
        # 編集ありセッション 1 件、かつ rework → floor 未満
        records = [
            {"session_id": "s1", "tool_sequence": ["Edit", "Edit", "Edit"],
             "timestamp": ts, "first_timestamp": ts},
        ]
        _write_jsonl(tmp_path / "sessions.jsonl", records)
        value, evidence = outcome_metrics.rework_rate(days=30, min_consecutive=3)
        assert value is None
        assert evidence["reason"] == "insufficient_sample"
        assert evidence["floor"] == outcome_metrics.MIN_EDIT_SESSIONS_FLOOR
        assert "edit_sessions" in evidence

    def test_at_edit_sessions_floor_returns_rate(self, tmp_path, monkeypatch):
        """floor 丁度（edit_sessions == MIN_EDIT_SESSIONS_FLOOR）なら率を出す（境界 inclusive）。"""
        monkeypatch.setattr(outcome_metrics, "DATA_DIR", tmp_path)
        now = _now()
        ts = _iso(now)
        floor = outcome_metrics.MIN_EDIT_SESSIONS_FLOOR
        # floor 件の編集ありセッションを用意（すべて rework でない = 0.0）
        records = [
            {"session_id": f"s{i}", "tool_sequence": ["Edit", "Bash"],
             "timestamp": ts, "first_timestamp": ts}
            for i in range(floor)
        ]
        _write_jsonl(tmp_path / "sessions.jsonl", records)
        value, evidence = outcome_metrics.rework_rate(days=30, min_consecutive=3)
        assert value is not None
        assert evidence["total_sessions"] == floor

    def test_above_floor_computes_rate_correctly(self, tmp_path, monkeypatch):
        """floor を超えたら従来通り率を出す（回帰テスト）。"""
        monkeypatch.setattr(outcome_metrics, "DATA_DIR", tmp_path)
        now = _now()
        ts = _iso(now)
        floor = outcome_metrics.MIN_EDIT_SESSIONS_FLOOR
        # floor+2 件：うち 1 件が rework
        records = [
            {"session_id": "rw", "tool_sequence": ["Edit", "Edit", "Edit"],
             "timestamp": ts, "first_timestamp": ts},
        ] + [
            {"session_id": f"s{i}", "tool_sequence": ["Edit", "Bash"],
             "timestamp": ts, "first_timestamp": ts}
            for i in range(floor + 1)
        ]
        _write_jsonl(tmp_path / "sessions.jsonl", records)
        value, evidence = outcome_metrics.rework_rate(days=30, min_consecutive=3)
        assert value is not None
        assert evidence["rework_sessions"] == 1
        assert evidence["total_sessions"] == floor + 2


# ---------- project スコープ（#489） ----------

class TestProjectScope:
    """当PJレポートの数値は当PJスコープに直す（#489）。

    corrections.jsonl は ``project_path``（フルパス）、sessions.jsonl は ``project``
    （basename）で PJ を識別する。project 指定時は当PJ分のみを数える（全PJ集計の漏出を防ぐ）。
    """

    def test_correction_recurrence_filters_by_project(self, tmp_path, monkeypatch):
        monkeypatch.setattr(outcome_metrics, "DATA_DIR", tmp_path)
        now = _now()
        ts = _iso(now)
        # 当PJ(mine): "iya" が 2 セッションで再発 / 他PJ(other): "iya" は 1 セッションのみ
        records = [
            {"correction_type": "iya", "session_id": "s1", "timestamp": ts,
             "project_path": "/work/mine"},
            {"correction_type": "iya", "session_id": "s2", "timestamp": ts,
             "project_path": "/work/mine"},
            {"correction_type": "iya", "session_id": "s9", "timestamp": ts,
             "project_path": "/work/other"},
        ]
        _write_jsonl(tmp_path / "corrections.jsonl", records)
        # 全PJ集計だと distinct=1 / recurring=1 → 1.0 になる（"iya" が s1,s2,s9 計3セッション）
        # 当PJ "mine" に絞ると distinct=1 / recurring=1 → 1.0 だが records は 2 件
        value, evidence = outcome_metrics.correction_recurrence_rate(days=30, project="mine")
        assert evidence["records"] == 2  # 他PJ の 1 件を含まない

    def test_first_try_success_filters_by_project(self, tmp_path, monkeypatch):
        monkeypatch.setattr(outcome_metrics, "DATA_DIR", tmp_path)
        now = _now()
        ts = _iso(now)
        # 当PJ(mine): 2 clean / 2 total = 1.0
        # 他PJ(other): 0 clean / 2 total（全PJ集計だと 2/4 = 0.5 に汚染される）
        records = [
            {"session_id": "s1", "error_count": 0, "timestamp": ts, "first_timestamp": ts, "project": "mine"},
            {"session_id": "s2", "error_count": 0, "timestamp": ts, "first_timestamp": ts, "project": "mine"},
            {"session_id": "s3", "error_count": 5, "timestamp": ts, "first_timestamp": ts, "project": "other"},
            {"session_id": "s4", "error_count": 3, "timestamp": ts, "first_timestamp": ts, "project": "other"},
        ]
        _write_jsonl(tmp_path / "sessions.jsonl", records)
        value, evidence = outcome_metrics.first_try_success_rate(days=30, project="mine")
        assert value == pytest.approx(1.0)  # 当PJ のみ → 全 clean
        assert evidence["total_sessions"] == 2

    def test_rework_filters_by_project(self, tmp_path, monkeypatch):
        monkeypatch.setattr(outcome_metrics, "DATA_DIR", tmp_path)
        now = _now()
        ts = _iso(now)
        # MIN_EDIT_SESSIONS_FLOOR=5 を満たすため当PJ "mine" に 5 件の編集ありセッションを用意する。
        # うち 2 件が rework（連続編集3以上）
        records = [
            {"session_id": "s1", "tool_sequence": ["Read", "Edit", "Edit", "Edit"],
             "timestamp": ts, "first_timestamp": ts, "project": "mine"},
            {"session_id": "s2", "tool_sequence": ["Edit", "Edit", "Edit"],
             "timestamp": ts, "first_timestamp": ts, "project": "mine"},
            {"session_id": "s3", "tool_sequence": ["Edit", "Bash"],
             "timestamp": ts, "first_timestamp": ts, "project": "mine"},
            {"session_id": "s4", "tool_sequence": ["Edit", "Bash"],
             "timestamp": ts, "first_timestamp": ts, "project": "mine"},
            {"session_id": "s5", "tool_sequence": ["Edit", "Bash"],
             "timestamp": ts, "first_timestamp": ts, "project": "mine"},
            {"session_id": "s9", "tool_sequence": ["Edit", "Bash"],
             "timestamp": ts, "first_timestamp": ts, "project": "other"},
        ]
        _write_jsonl(tmp_path / "sessions.jsonl", records)
        value, evidence = outcome_metrics.rework_rate(days=30, project="mine", min_consecutive=3)
        # 当PJ のみ → 編集ありセッション 5 件、rework 2 件
        assert value == pytest.approx(0.4)
        assert evidence["total_sessions"] == 5

    def test_no_project_arg_keeps_cross_pj_behavior(self, tmp_path, monkeypatch):
        """project 未指定なら従来通り全PJ集計（後方互換・promotion_readiness 等の cross-PJ 用途）。"""
        monkeypatch.setattr(outcome_metrics, "DATA_DIR", tmp_path)
        now = _now()
        ts = _iso(now)
        records = [
            {"session_id": "s1", "error_count": 0, "timestamp": ts, "first_timestamp": ts, "project": "mine"},
            {"session_id": "s2", "error_count": 5, "timestamp": ts, "first_timestamp": ts, "project": "other"},
        ]
        _write_jsonl(tmp_path / "sessions.jsonl", records)
        value, evidence = outcome_metrics.first_try_success_rate(days=30)
        assert evidence["total_sessions"] == 2  # 全PJ

    def test_worktree_record_matches_main_repo_filter(self, tmp_path, monkeypatch):
        """worktree セッションの record（project_path に /.claude/worktrees/）が
        本体 repo の project_dir フィルタにマッチする（worktree slug pitfall, #489）。

        worktree から書いた correction は project_path=/x/evolve-anything/.claude/worktrees/feedback。
        pj_slug_from_cwd で本体 repo 名 evolve-anything に正規化され、当PJ=evolve-anything に含まれる。
        """
        monkeypatch.setattr(outcome_metrics, "DATA_DIR", tmp_path)
        now = _now()
        ts = _iso(now)
        records = [
            {"correction_type": "iya", "session_id": "s1", "timestamp": ts,
             "project_path": "/x/evolve-anything"},
            # worktree セッション（basename だけ見ると "feedback" になり取りこぼす）
            {"correction_type": "iya", "session_id": "s2", "timestamp": ts,
             "project_path": "/x/evolve-anything/.claude/worktrees/feedback"},
            {"correction_type": "iya", "session_id": "s9", "timestamp": ts,
             "project_path": "/x/other"},
        ]
        _write_jsonl(tmp_path / "corrections.jsonl", records)
        project = outcome_metrics._normalize_pj("/somewhere/evolve-anything")
        assert project == "evolve-anything"
        value, evidence = outcome_metrics.correction_recurrence_rate(days=30, project=project)
        # 本体 s1 + worktree s2 = 2 件（other は除外）
        assert evidence["records"] == 2

    def test_project_dir_can_be_worktree_path(self, tmp_path, monkeypatch):
        """project_dir 自体が worktree パスでも本体 slug に正規化されてマッチする（#489）。"""
        monkeypatch.setattr(outcome_metrics, "DATA_DIR", tmp_path)
        now = _now()
        ts = _iso(now)
        records = [
            {"session_id": "s1", "error_count": 0, "timestamp": ts, "first_timestamp": ts,
             "project_path": "/x/evolve-anything"},
            {"session_id": "s9", "error_count": 5, "timestamp": ts, "first_timestamp": ts,
             "project_path": "/x/other"},
        ]
        _write_jsonl(tmp_path / "sessions.jsonl", records)
        # worktree パスから audit しても本体 slug に正規化される
        project = outcome_metrics._normalize_pj("/x/evolve-anything/.claude/worktrees/feedback")
        assert project == "evolve-anything"
        value, evidence = outcome_metrics.first_try_success_rate(days=30, project=project)
        assert evidence["total_sessions"] == 1  # 本体 s1 のみ、other 除外

    def test_records_without_project_are_included(self, tmp_path, monkeypatch):
        """project フィールドの無い（未帰属）レコードは寛容に include する（capture_rate と同様）。"""
        monkeypatch.setattr(outcome_metrics, "DATA_DIR", tmp_path)
        now = _now()
        ts = _iso(now)
        records = [
            {"session_id": "s1", "error_count": 0, "timestamp": ts, "first_timestamp": ts, "project": "mine"},
            {"session_id": "s2", "error_count": 0, "timestamp": ts, "first_timestamp": ts},  # project 無し
            {"session_id": "s3", "error_count": 0, "timestamp": ts, "first_timestamp": ts, "project": "other"},
        ]
        _write_jsonl(tmp_path / "sessions.jsonl", records)
        value, evidence = outcome_metrics.first_try_success_rate(days=30, project="mine")
        assert evidence["total_sessions"] == 2  # mine + 未帰属、other は除外


# ---------- builder（observability） ----------

class TestSectionBuilder:
    def test_returns_lines_with_evidence(self, tmp_path, monkeypatch):
        monkeypatch.setattr(outcome_metrics, "DATA_DIR", tmp_path)
        now = _now()
        ts = _iso(now)
        _write_jsonl(tmp_path / "sessions.jsonl", [
            {"session_id": "s1", "error_count": 0, "tool_sequence": ["Read", "Edit", "Edit", "Edit"],
             "timestamp": ts, "first_timestamp": ts},
            {"session_id": "s2", "error_count": 2, "tool_sequence": ["Edit", "Bash"],
             "timestamp": ts, "first_timestamp": ts},
        ])
        _write_jsonl(tmp_path / "corrections.jsonl", [
            {"correction_type": "iya", "session_id": "s1", "timestamp": ts},
            {"correction_type": "iya", "session_id": "s2", "timestamp": ts},
        ])
        from audit.sections_outcome import build_outcome_metrics_section

        lines = build_outcome_metrics_section(tmp_path)
        assert lines is not None
        joined = "\n".join(lines)
        assert "advisory" in joined.lower()
        # evidence（件数）が含まれる
        assert "session" in joined.lower() or "セッション" in joined

    def test_all_no_data_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr(outcome_metrics, "DATA_DIR", tmp_path)
        from audit.sections_outcome import build_outcome_metrics_section

        # 該当ストアが 1 つも無い環境（評価対象なし）は沈黙（None）。
        # orphan_store / hook_drift と同じ「評価対象が無ければ沈黙」の境界。
        lines = build_outcome_metrics_section(tmp_path)
        assert lines is None

    def test_section_scoped_to_current_pj(self, tmp_path, monkeypatch):
        """builder は project_dir の basename を当PJとし、その分のみを表示する（#489）。

        全PJ集計だと一発成功率は 1/2=0.50 だが、当PJ "mine" に絞れば 1/1=1.00。
        ラベルに「当PJ」を明記する。
        """
        monkeypatch.setattr(outcome_metrics, "DATA_DIR", tmp_path)
        now = _now()
        ts = _iso(now)
        _write_jsonl(tmp_path / "sessions.jsonl", [
            {"session_id": "s1", "error_count": 0, "tool_sequence": ["Read", "Edit"],
             "timestamp": ts, "first_timestamp": ts, "project": "mine"},
            {"session_id": "s2", "error_count": 5, "tool_sequence": ["Edit", "Bash"],
             "timestamp": ts, "first_timestamp": ts, "project": "other"},
        ])
        from audit.sections_outcome import build_outcome_metrics_section

        # project_dir の basename = "mine" を当PJとして使う
        lines = build_outcome_metrics_section(tmp_path / "mine")
        assert lines is not None
        joined = "\n".join(lines)
        # 当PJ のみ（s1）なので一発成功率 1.00 / total 1 sessions
        assert "1.00" in joined
        assert "total 1 sessions" in joined
        # スコープが明記される
        assert "当PJ" in joined

    def test_partial_data_shows_data_insufficient_for_empty_axis(self, tmp_path, monkeypatch):
        monkeypatch.setattr(outcome_metrics, "DATA_DIR", tmp_path)
        now = _now()
        ts = _iso(now)
        # sessions のみ存在 → first_try/rework は算出、correction はデータ不足を明示
        _write_jsonl(tmp_path / "sessions.jsonl", [
            {"session_id": "s1", "error_count": 0, "tool_sequence": ["Read", "Edit"],
             "timestamp": ts, "first_timestamp": ts},
        ])
        from audit.sections_outcome import build_outcome_metrics_section

        lines = build_outcome_metrics_section(tmp_path)
        assert lines is not None
        joined = "\n".join(lines)
        assert "データ不足" in joined  # correction 軸
        assert "advisory" in joined.lower()

    def test_insufficient_sample_shows_sample_shortage(self, tmp_path, monkeypatch):
        """#529-2: correction distinct < floor では率を出さず「サンプル不足」を明示する。"""
        monkeypatch.setattr(outcome_metrics, "DATA_DIR", tmp_path)
        now = _now()
        ts = _iso(now)
        # session 軸を成立させて section を出力させる（all no_data 沈黙を避ける）。
        _write_jsonl(tmp_path / "sessions.jsonl", [
            {"session_id": "s1", "error_count": 0, "tool_sequence": ["Read", "Edit"],
             "timestamp": ts, "first_timestamp": ts},
        ])
        # correction は distinct 2 type のみ（floor 5 未満）= #529-2 の 0.50 誤シグナル元
        _write_jsonl(tmp_path / "corrections.jsonl", [
            {"correction_type": "iya", "session_id": "s1", "timestamp": ts},
            {"correction_type": "iya", "session_id": "s2", "timestamp": ts},
            {"correction_type": "stop", "session_id": "s3", "timestamp": ts},
        ])
        from audit.sections_outcome import build_outcome_metrics_section

        lines = build_outcome_metrics_section(tmp_path)
        assert lines is not None
        joined = "\n".join(lines)
        assert "サンプル不足" in joined
        assert "distinct 2 type" in joined
        # 誤シグナルの率（0.50）が表示されていないこと
        assert "0.50" not in joined

    def test_rework_insufficient_sample_shows_sample_shortage(self, tmp_path, monkeypatch):
        """#563: edit_sessions < MIN_EDIT_SESSIONS_FLOOR では rework 率を出さず「サンプル不足」を表示する。

        分母 1 件で 1.0 に張り付くのを防ぐ（correction_recurrence の #529-2 と同方針）。
        first_try_success が成立している状態で section を出力させる。
        """
        monkeypatch.setattr(outcome_metrics, "DATA_DIR", tmp_path)
        now = _now()
        ts = _iso(now)
        # first_try_success 用に十分なセッションを用意（error_count あり）。
        # ただし edit_sessions（tool_sequence に Edit を含む）は MIN_EDIT_SESSIONS_FLOOR 未満。
        floor = outcome_metrics.MIN_EDIT_SESSIONS_FLOOR
        records = [
            {"session_id": f"clean_{i}", "error_count": 0, "tool_sequence": ["Read", "Bash"],
             "timestamp": ts, "first_timestamp": ts}
            for i in range(5)
        ] + [
            # 編集ありセッション 1 件のみ → floor 未満
            {"session_id": "edit1", "error_count": 0, "tool_sequence": ["Edit", "Edit", "Edit"],
             "timestamp": ts, "first_timestamp": ts},
        ]
        _write_jsonl(tmp_path / "sessions.jsonl", records)
        from audit.sections_outcome import build_outcome_metrics_section

        lines = build_outcome_metrics_section(tmp_path)
        assert lines is not None
        joined = "\n".join(lines)
        # rework 率はサンプル不足で表示されない
        assert "サンプル不足" in joined
        # 誤シグナルの率（1.00）が rework として表示されていないこと
        # （first_try_success の 1.00 は出る可能性があるが rework 軸の行では出ない）
        rework_lines = [l for l in lines if "rework" in l.lower()]
        assert any("サンプル不足" in l for l in rework_lines)


# ---------- #138: 一発成功率の session 単位畳み込み ----------

class TestFoldSessionErrorCounts:
    """#138: 生レコード行を session_id 単位に畳み込むヘルパの単体テスト。"""

    def test_duplicate_rows_folded_to_max(self):
        # 同一 session の複数行（Stop hook 複数発火）。1 行でも error>0 なら non-clean（max 合成）。
        records = [
            {"session_id": "s1", "error_count": 0},
            {"session_id": "s1", "error_count": 3},
            {"session_id": "s2", "error_count": 0},
        ]
        folded = outcome_metrics.fold_session_error_counts(records)
        assert folded == {"s1": 3, "s2": 0}

    def test_error_count_absent_rows_yield_none(self):
        # error_count を持たない record 型（instructions_loaded 等）は None を値にする。
        records = [
            {"session_id": "s1"},  # error_count なし
            {"session_id": "s1", "error_count": 0},  # 同 session に scored 行あり
            {"session_id": "s2"},  # scored 行が一切ない session
        ]
        folded = outcome_metrics.fold_session_error_counts(records)
        assert folded == {"s1": 0, "s2": None}

    def test_missing_session_id_skipped(self):
        records = [
            {"error_count": 0},  # session_id 無し → distinct session へ帰属不能
            {"session_id": "", "error_count": 0},  # 空 session_id → skip
            {"session_id": "s1", "error_count": 0},
        ]
        folded = outcome_metrics.fold_session_error_counts(records)
        assert folded == {"s1": 0}


class TestFirstTrySuccessDistinctSessions:
    """#138: 分母は distinct session（error_count 保有行あり）で数える。"""

    def test_duplicate_session_rows_folded(self, tmp_path, monkeypatch):
        # 行数分母だと 3clean/4=0.75。distinct 化で s1 は 1 セッション → 1clean/2=0.50。
        monkeypatch.setattr(outcome_metrics, "DATA_DIR", tmp_path)
        now = _now()
        records = [
            {"session_id": "s1", "error_count": 0,
             "timestamp": _iso(now - timedelta(minutes=2)), "first_timestamp": _iso(now)},
            {"session_id": "s1", "error_count": 0,
             "timestamp": _iso(now - timedelta(minutes=1)), "first_timestamp": _iso(now)},
            {"session_id": "s1", "error_count": 0,
             "timestamp": _iso(now), "first_timestamp": _iso(now)},
            {"session_id": "s2", "error_count": 2,
             "timestamp": _iso(now), "first_timestamp": _iso(now)},
        ]
        _write_jsonl(tmp_path / "sessions.jsonl", records)
        value, evidence = outcome_metrics.first_try_success_rate(days=30)
        assert value == pytest.approx(0.5)
        assert evidence["total_sessions"] == 2
        assert evidence["clean_sessions"] == 1

    def test_error_count_absent_rows_excluded_from_denominator(self, tmp_path, monkeypatch):
        # #138 の核心（非対称希薄化）: error_count なし行は分子からだけでなく分母からも除外する。
        # 行数分母だと 2clean/4=0.50。distinct・scored 限定で 2clean/2=1.00。
        monkeypatch.setattr(outcome_metrics, "DATA_DIR", tmp_path)
        now = _now()
        ts = _iso(now)
        records = [
            {"session_id": "s1", "error_count": 0, "timestamp": ts, "first_timestamp": ts},
            {"session_id": "s2", "error_count": 0, "timestamp": ts, "first_timestamp": ts},
            # instructions_loaded 型（error_count なし）。分母に混入してはならない。
            {"session_id": "x1", "type": "instructions_loaded", "timestamp": ts, "first_timestamp": ts},
            {"session_id": "x2", "type": "instructions_loaded", "timestamp": ts, "first_timestamp": ts},
        ]
        _write_jsonl(tmp_path / "sessions.jsonl", records)
        value, evidence = outcome_metrics.first_try_success_rate(days=30)
        assert value == pytest.approx(1.0)
        assert evidence["total_sessions"] == 2
        assert evidence["clean_sessions"] == 2

    def test_mixed_error_within_session_is_non_clean(self, tmp_path, monkeypatch):
        # 同一 session 内で 1 行でも error>0 → そのセッションは non-clean（max 合成）。
        monkeypatch.setattr(outcome_metrics, "DATA_DIR", tmp_path)
        now = _now()
        records = [
            {"session_id": "s1", "error_count": 0,
             "timestamp": _iso(now - timedelta(minutes=1)), "first_timestamp": _iso(now)},
            {"session_id": "s1", "error_count": 3,
             "timestamp": _iso(now), "first_timestamp": _iso(now)},
            {"session_id": "s2", "error_count": 0,
             "timestamp": _iso(now), "first_timestamp": _iso(now)},
        ]
        _write_jsonl(tmp_path / "sessions.jsonl", records)
        value, evidence = outcome_metrics.first_try_success_rate(days=30)
        assert value == pytest.approx(0.5)
        assert evidence["total_sessions"] == 2
        assert evidence["clean_sessions"] == 1

    def test_issue_138_reproduction_gap(self, tmp_path, monkeypatch):
        """#138 の再現: 行数分母 + none 混入で大幅過小になるケースを distinct 化で救う。

        25 distinct scored session（24 clean / 1 non-clean）を、重複 session_summary 行と
        error_count なし行で水増しして書く。distinct 化で 24/25 = 0.96 に戻る。
        """
        monkeypatch.setattr(outcome_metrics, "DATA_DIR", tmp_path)
        now = _now()
        records: list[dict] = []
        for i in range(24):
            sid = f"c{i:02d}"
            # session_summary 型を 2 回発火（別 timestamp なので union dedup を生き残る）。
            records.append({"session_id": sid, "error_count": 0,
                            "timestamp": _iso(now - timedelta(minutes=2 * i)),
                            "first_timestamp": _iso(now)})
            records.append({"session_id": sid, "error_count": 0,
                            "timestamp": _iso(now - timedelta(minutes=2 * i, seconds=30)),
                            "first_timestamp": _iso(now)})
            # 同 session の instructions_loaded 行（error_count なし）。
            records.append({"session_id": sid, "type": "instructions_loaded",
                            "timestamp": _iso(now - timedelta(minutes=2 * i, seconds=5)),
                            "first_timestamp": _iso(now)})
        # 非 clean セッション 1 件。
        records.append({"session_id": "b00", "error_count": 1,
                        "timestamp": _iso(now), "first_timestamp": _iso(now)})
        # scored 行を一切持たない session（分母に入れてはならない）。
        for i in range(6):
            records.append({"session_id": f"n{i}", "type": "instructions_loaded",
                            "timestamp": _iso(now - timedelta(seconds=i)),
                            "first_timestamp": _iso(now)})
        _write_jsonl(tmp_path / "sessions.jsonl", records)
        value, evidence = outcome_metrics.first_try_success_rate(days=30)
        assert evidence["total_sessions"] == 25
        assert evidence["clean_sessions"] == 24
        assert value == pytest.approx(0.96)

    def test_only_absent_error_count_returns_no_data(self, tmp_path, monkeypatch):
        monkeypatch.setattr(outcome_metrics, "DATA_DIR", tmp_path)
        now = _now()
        ts = _iso(now)
        records = [
            {"session_id": "x1", "type": "instructions_loaded", "timestamp": ts, "first_timestamp": ts},
            {"session_id": "x2", "type": "instructions_loaded", "timestamp": ts, "first_timestamp": ts},
        ]
        _write_jsonl(tmp_path / "sessions.jsonl", records)
        value, evidence = outcome_metrics.first_try_success_rate(days=30)
        assert value is None
        assert evidence["reason"] == "no_data"
