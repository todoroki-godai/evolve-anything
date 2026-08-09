#!/usr/bin/env python3
"""results_board のテスト — 戦果ボード（#379 Step 4・growth-journal harness 削除の置換成果物）。

classify_decision（optimize_history 1エントリ→accepted/rejected/pending/excluded の純粋関数）と
build_results_board（optimize_history + corrections の直読み集計）を検証する。

実データ較正（~/.claude/evolve-anything/optimize_history/evolve-anything.jsonl・38件・
2026-08-10 読み取り時点）で判明した事実:
  - source は None（optimize/evolve-loop）と "evolve_remediation" の2系統に分裂し、判定
    フィールドがそれぞれ approved / human_accepted と異なる（#279/#286/#290 と同根の split）。
  - target/skill_name のテスト汚染パスは "pytest-of-" だけでなく macOS tmpfile 規約
    （/T/tmp<random>/ 等）にも及ぶ。狭い "pytest-of-" 限定では 30 件中 13 件を取り逃す。
  - fitness_eligible=False（#376 の無効化フラグ）は全件 source="evolve_remediation" だった。

HOME 隔離は root conftest.py の autouse（#119）が全テストへ効かせる。LLM 呼び出しなし。
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_lib_dir = Path(__file__).resolve().parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

import results_board  # noqa: E402


# ── classify_decision ──────────────────────────────────────────────


class TestClassifyDecisionFitnessEligible:
    def test_fitness_eligible_false_is_excluded_even_if_accepted(self):
        """fitness_eligible=False（#376 無効化）は human_accepted=True でも excluded 優先。"""
        entry = {
            "source": "evolve_remediation",
            "human_accepted": True,
            "fitness_eligible": False,
            "skill_name": "queue",
        }
        assert results_board.classify_decision(entry) == "excluded"

    def test_fitness_eligible_missing_does_not_exclude(self):
        """fitness_eligible キー無し（None）は除外条件にならない。"""
        entry = {"source": None, "approved": True, "skill_name": "agent-brushup"}
        assert results_board.classify_decision(entry) == "accepted"

    def test_fitness_eligible_true_does_not_exclude(self):
        entry = {"source": None, "approved": True, "fitness_eligible": True, "skill_name": "x"}
        assert results_board.classify_decision(entry) == "accepted"


class TestClassifyDecisionTestPollution:
    def test_pytest_of_prefix_excluded(self):
        entry = {
            "source": None,
            "approved": True,
            "target": "/private/var/folders/gg/x/T/pytest-of-user/pytest-757/popen-gw0/test-skill.md",
        }
        assert results_board.classify_decision(entry) == "excluded"

    def test_generic_tmp_dir_excluded(self):
        """実データ較正で発見: pytest-of- を含まない一般的な tmp<random>/ パスも汚染。"""
        entry = {
            "source": None,
            "approved": True,
            "target": "/var/folders/gg/x/T/tmpiadtjmmn/test-skill/SKILL.md",
        }
        assert results_board.classify_decision(entry) == "excluded"

    def test_skill_name_pollution_also_excluded(self):
        entry = {"source": None, "approved": True, "skill_name": "/T/tmpabc123/test-skill"}
        assert results_board.classify_decision(entry) == "excluded"

    def test_real_project_path_not_excluded(self):
        entry = {"source": None, "approved": True, "target": "skills/agent-brushup/SKILL.md"}
        assert results_board.classify_decision(entry) == "accepted"


class TestClassifyDecisionEvolveRemediationSource:
    def test_human_accepted_true_is_accepted(self):
        entry = {"source": "evolve_remediation", "human_accepted": True, "skill_name": "queue"}
        assert results_board.classify_decision(entry) == "accepted"

    def test_human_accepted_false_is_rejected(self):
        entry = {"source": "evolve_remediation", "human_accepted": False, "skill_name": "queue"}
        assert results_board.classify_decision(entry) == "rejected"

    def test_human_accepted_missing_is_pending(self):
        entry = {"source": "evolve_remediation", "skill_name": "queue"}
        assert results_board.classify_decision(entry) == "pending"


class TestClassifyDecisionOptimizeSource:
    def test_approved_true_is_accepted(self):
        entry = {"source": None, "approved": True, "verdict": "IMPROVED"}
        assert results_board.classify_decision(entry) == "accepted"

    def test_approved_false_is_rejected(self):
        entry = {"source": None, "approved": False, "verdict": "STABLE"}
        assert results_board.classify_decision(entry) == "rejected"

    def test_approved_missing_is_pending(self):
        """dry-run 生成のみで accept/reject 未決定のレコード（実データで13件確認）。"""
        entry = {"source": None, "target": "skills/foo/SKILL.md"}
        assert results_board.classify_decision(entry) == "pending"

    def test_unknown_source_defaults_to_pending(self):
        entry = {"source": "some_future_source"}
        assert results_board.classify_decision(entry) == "pending"


# ── build_results_board ─────────────────────────────────────────────


_NOW = datetime(2026, 8, 10, 12, 0, 0, tzinfo=timezone.utc)


def _iso(days_ago: float) -> str:
    return (_NOW - timedelta(days=days_ago)).isoformat()


@pytest.fixture
def stub_history(monkeypatch):
    """optimize_history_store.load_history をスタブに差し替える。"""
    def _set(entries):
        monkeypatch.setattr(results_board, "load_history", lambda slug: entries)
    return _set


@pytest.fixture
def stub_corrections(monkeypatch):
    """telemetry_query.query_corrections をスタブに差し替える。"""
    def _set(records):
        monkeypatch.setattr(results_board, "query_corrections", lambda **kw: records)
    return _set


class TestBuildResultsBoardRework:
    def test_rework_delta_negative_when_decreasing(self, stub_history, stub_corrections):
        """直近30日3件・その前30日5件 → delta=-2（手直しが減少）。"""
        stub_history([])
        corrections = (
            [{"source": "reflect_confirmed", "timestamp": _iso(5)} for _ in range(3)]
            + [{"source": "reflect_confirmed", "timestamp": _iso(45)} for _ in range(5)]
        )
        stub_corrections(corrections)

        board = results_board.build_results_board("evolve-anything", now=_NOW)

        assert board["rework"]["recent_30d"] == 3
        assert board["rework"]["previous_30d"] == 5
        assert board["rework"]["delta"] == -2

    def test_machine_corrections_not_counted_as_rework(self, stub_history, stub_corrections):
        """source=hook（機械生成）は human corrections カウントに含まれない。"""
        stub_history([])
        stub_corrections([{"source": "hook", "timestamp": _iso(1)} for _ in range(4)])

        board = results_board.build_results_board("evolve-anything", now=_NOW)

        assert board["rework"]["recent_30d"] == 0

    def test_window_boundary_60_days_ago_excluded_from_previous(
        self, stub_history, stub_corrections
    ):
        """60日超前の correction はどちらの window にも入らない。"""
        stub_history([])
        stub_corrections([{"source": "reflect_confirmed", "timestamp": _iso(61)}])

        board = results_board.build_results_board("evolve-anything", now=_NOW)

        assert board["rework"]["recent_30d"] == 0
        assert board["rework"]["previous_30d"] == 0

    def test_query_failure_is_graceful(self, stub_history, monkeypatch):
        """query_corrections が例外を投げても KeyError にならず 0 扱い。"""
        stub_history([])

        def _boom(**kw):
            raise RuntimeError("boom")

        monkeypatch.setattr(results_board, "query_corrections", _boom)

        board = results_board.build_results_board("evolve-anything", now=_NOW)

        assert board["rework"]["recent_30d"] == 0
        assert board["rework"]["previous_30d"] == 0


class TestBuildResultsBoardDecisions:
    def test_counts_by_bucket_within_window(self, stub_history, stub_corrections):
        stub_corrections([])
        stub_history([
            {"source": None, "approved": True, "skill_name": "a", "timestamp": _iso(1)},
            {"source": None, "approved": False, "skill_name": "b", "timestamp": _iso(2)},
            {"source": None, "target": "skills/c/SKILL.md", "timestamp": _iso(3)},  # pending
            {
                "source": "evolve_remediation",
                "human_accepted": True,
                "fitness_eligible": False,
                "skill_name": "d",
                "timestamp": _iso(4),
            },  # excluded
        ])

        board = results_board.build_results_board("evolve-anything", now=_NOW)

        assert board["decisions"] == {
            "accepted": 1,
            "rejected": 1,
            "pending": 1,
            "excluded": 1,
        }

    def test_entries_outside_window_not_counted(self, stub_history, stub_corrections):
        stub_corrections([])
        stub_history([
            {"source": None, "approved": True, "skill_name": "old", "timestamp": _iso(40)},
        ])

        board = results_board.build_results_board("evolve-anything", now=_NOW)

        assert board["decisions"]["accepted"] == 0

    def test_excluded_always_present_even_when_zero(self, stub_history, stub_corrections):
        """silence != evaluated — excluded=0 でもキー自体は必ず出す。"""
        stub_corrections([])
        stub_history([{"source": None, "approved": True, "skill_name": "a", "timestamp": _iso(1)}])

        board = results_board.build_results_board("evolve-anything", now=_NOW)

        assert board["decisions"]["excluded"] == 0

    def test_history_load_failure_is_graceful(self, stub_corrections, monkeypatch):
        stub_corrections([])

        def _boom(slug):
            raise RuntimeError("boom")

        monkeypatch.setattr(results_board, "load_history", _boom)

        board = results_board.build_results_board("evolve-anything", now=_NOW)

        assert board["decisions"] == {"accepted": 0, "rejected": 0, "pending": 0, "excluded": 0}


class TestBuildResultsBoardAcceptedList:
    def test_capped_at_ten_sorted_newest_first(self, stub_history, stub_corrections):
        stub_corrections([])
        entries = [
            {"source": None, "approved": True, "skill_name": f"s{i}", "timestamp": _iso(i)}
            for i in range(15)
        ]
        stub_history(entries)

        board = results_board.build_results_board("evolve-anything", now=_NOW)

        assert len(board["accepted_list"]) == 10
        assert board["accepted_list"][0]["skill_name"] == "s0"  # 最新（days_ago=0）が先頭

    def test_falls_back_to_target_when_skill_name_missing(self, stub_history, stub_corrections):
        stub_corrections([])
        stub_history([
            {"source": None, "approved": True, "target": "skills/foo/SKILL.md", "timestamp": _iso(1)},
        ])

        board = results_board.build_results_board("evolve-anything", now=_NOW)

        assert board["accepted_list"][0]["skill_name"] == "skills/foo/SKILL.md"


class TestBuildResultsBoardWithdrawalCandidates:
    def test_accepted_and_regressed_is_withdrawal_candidate(self, stub_history, stub_corrections):
        stub_corrections([])
        stub_history([
            {
                "source": None, "approved": True, "verdict": "REGRESSED",
                "skill_name": "bad-apply", "timestamp": _iso(1),
            },
            {
                "source": None, "approved": True, "verdict": "STABLE",
                "skill_name": "fine", "timestamp": _iso(1),
            },
        ])

        board = results_board.build_results_board("evolve-anything", now=_NOW)

        names = [c["skill_name"] for c in board["withdrawal_candidates"]]
        assert names == ["bad-apply"]

    def test_rejected_and_regressed_is_not_a_withdrawal_candidate(
        self, stub_history, stub_corrections
    ):
        """withdrawal candidate は accepted のみが対象（rejected は最初から不採用）。"""
        stub_corrections([])
        stub_history([
            {
                "source": None, "approved": False, "verdict": "REGRESSED",
                "skill_name": "never-applied", "timestamp": _iso(1),
            },
        ])

        board = results_board.build_results_board("evolve-anything", now=_NOW)

        assert board["withdrawal_candidates"] == []


# ── render_results_board ────────────────────────────────────────────


class TestRenderResultsBoard:
    def _board(self, **overrides):
        base = {
            "slug": "evolve-anything",
            "generated_at": _NOW.isoformat(),
            "rework": {"recent_30d": 3, "previous_30d": 5, "delta": -2},
            "decisions": {"accepted": 1, "rejected": 2, "pending": 1, "excluded": 4},
            "accepted_list": [{"skill_name": "queue", "timestamp": _iso(1)}],
            "withdrawal_candidates": [],
        }
        base.update(overrides)
        return base

    def test_header_present(self):
        lines = results_board.render_results_board(self._board())
        assert lines[0] == "## 🏆 戦果ボード"

    def test_headline_shows_decrease(self):
        lines = results_board.render_results_board(self._board())
        text = "\n".join(lines)
        assert "5" in text and "3" in text
        assert "減少" in text

    def test_headline_shows_increase(self):
        board = self._board(rework={"recent_30d": 8, "previous_30d": 3, "delta": 5})
        lines = results_board.render_results_board(board)
        assert "増加" in "\n".join(lines)

    def test_headline_shows_flat(self):
        board = self._board(rework={"recent_30d": 3, "previous_30d": 3, "delta": 0})
        lines = results_board.render_results_board(board)
        assert "横ばい" in "\n".join(lines)

    def test_excluded_count_always_shown_even_when_zero(self):
        board = self._board(decisions={"accepted": 1, "rejected": 0, "pending": 0, "excluded": 0})
        lines = results_board.render_results_board(board)
        text = "\n".join(lines)
        assert "excluded" in text and "0" in text

    def test_no_withdrawal_section_when_empty(self):
        lines = results_board.render_results_board(self._board())
        text = "\n".join(lines)
        assert "取り下げ候補" not in text

    def test_withdrawal_section_when_present(self):
        board = self._board(withdrawal_candidates=[
            {"skill_name": "bad-apply", "timestamp": _iso(1), "verdict": "REGRESSED"},
        ])
        lines = results_board.render_results_board(board)
        text = "\n".join(lines)
        assert "取り下げ候補" in text
        assert "bad-apply" in text

    def test_is_read_only_no_disk_write(self, tmp_path):
        before = set(tmp_path.rglob("*"))
        results_board.render_results_board(self._board())
        after = set(tmp_path.rglob("*"))
        assert before == after
