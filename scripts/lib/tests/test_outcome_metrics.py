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
        # 同 correction_type "iya" が 2 つの異なるセッションで発生 → 再発
        # "stop" は 1 セッションのみ → 非再発
        records = [
            {"correction_type": "iya", "session_id": "s1", "timestamp": _iso(now - timedelta(days=2))},
            {"correction_type": "iya", "session_id": "s2", "timestamp": _iso(now - timedelta(days=1))},
            {"correction_type": "stop", "session_id": "s3", "timestamp": _iso(now)},
        ]
        _write_jsonl(tmp_path / "corrections.jsonl", records)
        value, evidence = outcome_metrics.correction_recurrence_rate(days=30)
        # 2 distinct types, 1 recurring → 0.5
        assert value == pytest.approx(0.5)
        assert evidence["distinct_types"] == 2
        assert evidence["recurring_types"] == 1
        assert "iya" in evidence["examples"]

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
        # s2: Edit → Bash → Edit（介在あり）→ rework でない
        records = [
            {"session_id": "s1", "tool_sequence": ["Read", "Edit", "Edit", "Edit", "Bash"],
             "timestamp": ts, "first_timestamp": ts},
            {"session_id": "s2", "tool_sequence": ["Edit", "Bash", "Edit", "Bash"],
             "timestamp": ts, "first_timestamp": ts},
        ]
        _write_jsonl(tmp_path / "sessions.jsonl", records)
        value, evidence = outcome_metrics.rework_rate(days=30, min_consecutive=3)
        # 1 of 2 sessions with an edit-burst >= 3
        assert value == pytest.approx(0.5)
        assert evidence["total_sessions"] == 2
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
        records = [
            {"session_id": "s1", "tool_sequence": ["Read", "Edit", "Edit", "Edit"],
             "timestamp": ts, "first_timestamp": ts, "project": "mine"},
            {"session_id": "s9", "tool_sequence": ["Edit", "Bash"],
             "timestamp": ts, "first_timestamp": ts, "project": "other"},
        ]
        _write_jsonl(tmp_path / "sessions.jsonl", records)
        value, evidence = outcome_metrics.rework_rate(days=30, project="mine")
        # 当PJ のみ → 編集ありセッション 1 件、すべて rework
        assert value == pytest.approx(1.0)
        assert evidence["total_sessions"] == 1

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

        worktree から書いた correction は project_path=/x/rl-anything/.claude/worktrees/feedback。
        pj_slug_from_cwd で本体 repo 名 rl-anything に正規化され、当PJ=rl-anything に含まれる。
        """
        monkeypatch.setattr(outcome_metrics, "DATA_DIR", tmp_path)
        now = _now()
        ts = _iso(now)
        records = [
            {"correction_type": "iya", "session_id": "s1", "timestamp": ts,
             "project_path": "/x/rl-anything"},
            # worktree セッション（basename だけ見ると "feedback" になり取りこぼす）
            {"correction_type": "iya", "session_id": "s2", "timestamp": ts,
             "project_path": "/x/rl-anything/.claude/worktrees/feedback"},
            {"correction_type": "iya", "session_id": "s9", "timestamp": ts,
             "project_path": "/x/other"},
        ]
        _write_jsonl(tmp_path / "corrections.jsonl", records)
        project = outcome_metrics._normalize_pj("/somewhere/rl-anything")
        assert project == "rl-anything"
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
             "project_path": "/x/rl-anything"},
            {"session_id": "s9", "error_count": 5, "timestamp": ts, "first_timestamp": ts,
             "project_path": "/x/other"},
        ]
        _write_jsonl(tmp_path / "sessions.jsonl", records)
        # worktree パスから audit しても本体 slug に正規化される
        project = outcome_metrics._normalize_pj("/x/rl-anything/.claude/worktrees/feedback")
        assert project == "rl-anything"
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
