#!/usr/bin/env python3
"""results_board のテスト — 戦果ボード（#379 Step 4・ADR-054 §7.2.1 柱3(a)）。

classify_decision（optimize_history 1エントリ→accepted/rejected/pending/excluded の純粋関数）と
build_results_board（optimize_history + correction_rate の直読み集計配線）を検証する。
correction_rate 自体の集計ロジック（週次 join / freeze / gate）は test_correction_rate.py が
別途担う。ここでは build_results_board がその summary を正しく配線し read 失敗を握りつぶさない
こと、render_results_board が summary を正しく markdown 化することだけを検証する。

実データ較正（~/.claude/evolve-anything/optimize_history/evolve-anything.jsonl・38件・
2026-08-10 読み取り時点）と canonical writer 3種の実コード確認で判明した事実:
  - classify_decision は source 文字列でなくフィールドの実在と bool 型を優先して判定する
    （human_accepted が bool ならそれを採用 → 次に approved が bool なら採用 → どちらも
    無ければ pending）。当初は「source=evolve_remediation→human_accepted /
    source=None→approved」で判定していたが、`skills/genetic-prompt-optimizer/scripts/
    optimize.py` の `save_history_entry` は source を一切書かず human_accepted のみを
    書くため、この writer のレコードが全件 pending に落ちる構造的バグがあった
    （#398 codex レビュー Must 1 是正・#279/#286/#290 の提案ID/判断イベントID分離と
    同根の split 誤認）。
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

    def test_templates_dir_not_excluded(self):
        """FP 修正: `/templates/` を含む正当な skill パスを汚染扱いしない（頭レビュー指摘）。"""
        entry = {
            "source": None, "approved": True,
            "target": ".claude/skills/foo/templates/x.md",
        }
        assert results_board.classify_decision(entry) == "accepted"

    def test_tmpl_dir_not_excluded(self):
        """FP 修正: `/tmpl/` ディレクトリ名を含む正当な skill パスを汚染扱いしない。"""
        entry = {
            "source": None, "approved": True,
            "skill_name": "skills/foo/tmpl/bar.md",
        }
        assert results_board.classify_decision(entry) == "accepted"

    def test_bare_var_folders_tmp_prefix_still_excluded(self):
        """/private プレフィックス無しの /var/folders/.../T/tmp<random>/ も汚染扱い（実データ較正）。"""
        entry = {
            "source": None, "approved": True,
            "target": "/var/folders/gg/x/T/tmpiadtjmmn/test-skill/SKILL.md",
        }
        assert results_board.classify_decision(entry) == "excluded"

    def test_private_var_folders_prefix_excluded(self):
        entry = {
            "source": None, "approved": True,
            "target": "/private/var/folders/gg/x/T/tmpabc123/test-skill/SKILL.md",
        }
        assert results_board.classify_decision(entry) == "excluded"

    def test_bare_unix_tmp_segment_excluded(self):
        entry = {"source": None, "approved": True, "target": "/tmp/whatever/SKILL.md"}
        assert results_board.classify_decision(entry) == "excluded"


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


class TestClassifyDecisionLegacyRuleApply:
    """#512: `human_accepted` を書く前の rule 反映 entry（legacy shape）の救済。

    実データ（2026-08-18・実ストア 43 entry）で該当したのは 1 件のみ。同じく決定フラグを
    持たない optimize.py 由来 13 件（`human_accepted=None`）を巻き込まないことが要件。
    """

    def _legacy(self, **over):
        entry = {
            "id": "rule_apply_bd2cf304dc0a3f6f",
            "skill_name": "model-routing.md",
            "scope": "global_rule",
            "relative_path": "model-routing.md",
            "revert_schema_version": 1,
            "revert_before_b64": "eJxLyy/KTQUABm4Ceg==",
            "timestamp": "2026-08-18T01:24:31.298898+00:00",
        }
        entry.update(over)
        return entry

    def test_legacy_rule_apply_is_accepted(self):
        assert results_board.classify_decision(self._legacy()) == "accepted"

    def test_project_rule_scope_also_accepted(self):
        entry = self._legacy(scope="project_rule")
        assert results_board.classify_decision(entry) == "accepted"

    def test_optimize_history_none_flag_stays_pending(self):
        """陽性対照: 同じく決定フラグが bool でない optimize.py 形は pending のまま。"""
        entry = {
            "best_fitness": 0.82, "corrections_used": 3, "fitness_func": "default",
            "human_accepted": None, "rejection_reason": None, "run_id": "r1",
            "strategy": "direct", "target": "skills/foo/SKILL.md",
            "timestamp": "2026-07-17T07:50:37.904158",
        }
        assert results_board.classify_decision(entry) == "pending"

    def test_other_id_prefix_not_accepted(self):
        """id prefix が違う entry を巻き込まない（条件を scope だけに広げない）。"""
        entry = self._legacy(id="skill_diff_abc123")
        assert results_board.classify_decision(entry) == "pending"

    def test_non_rule_scope_not_accepted(self):
        entry = self._legacy(scope="skill")
        assert results_board.classify_decision(entry) == "pending"

    def test_missing_revert_schema_version_not_accepted(self):
        """revert 記録として未完成な entry は救済しない。"""
        entry = self._legacy()
        del entry["revert_schema_version"]
        assert results_board.classify_decision(entry) == "pending"

    def test_explicit_false_flag_wins_over_legacy_branch(self):
        """明示的な判定がある entry では legacy 分岐を通さない（順序が壊れていない）。"""
        entry = self._legacy(human_accepted=False)
        assert results_board.classify_decision(entry) == "rejected"

    def test_fitness_ineligible_still_excluded(self):
        """#376 の無効化フラグは legacy 分岐より優先される。"""
        entry = self._legacy(fitness_eligible=False)
        assert results_board.classify_decision(entry) == "excluded"

    def test_test_polluted_still_excluded(self):
        entry = self._legacy(skill_name="/T/tmpabc123/test-rule.md")
        assert results_board.classify_decision(entry) == "excluded"

    def test_missing_id_does_not_crash(self):
        entry = self._legacy()
        del entry["id"]
        assert results_board.classify_decision(entry) == "pending"

    def test_revert_event_is_not_classified_accepted(self):
        """陽性対照: revert イベント（`evolve_revert/_apply.py` が書く）を採用に化けさせない。

        scope / relative_path / skill_name を持ち決定フラグを持たない点は legacy shape と
        同じで、`revert_schema_version` と id prefix の 2 条件だけが両者を分ける。
        ここが崩れると「戻した事実」が「採用」として数えられる。
        """
        event = {
            "event_type": "revert",
            "reverted_entry_id": "rule_apply_bd2cf304dc0a3f6f",
            "revert_event_id": "rev_1",
            "revert_generation": 1,
            "scope": "global_rule",
            "repo_id": None,
            "relative_path": "model-routing.md",
            "skill_name": "model-routing.md",
            "timestamp": "2026-08-18T02:00:00+00:00",
        }
        assert results_board.classify_decision(event) == "pending"


# ── canonical writer 契約テスト（codex #398 Must 1）─────────────────
#
# survey 段階の前提「source=None → approved で判定」は誤りだった。
# optimize.py の save_history_entry は source を一切書かず human_accepted で
# 判定するため、旧ロジックは全件 pending に落ちていた（実データでは
# human_accepted が常に None のケースしか無く顕在化しなかったが、
# human_accepted=True/False が来ると誤判定する構造的バグだった）。
# 各 fixture は実コードを read して得た**実際の emit 形**（該当行を明記）。


class TestClassifyDecisionOptimizePyWriterContract:
    """skills/genetic-prompt-optimizer/scripts/optimize.py の save_history_entry が
    append する実際のエントリ形（source キー無し・human_accepted で判定）。
    """

    def _entry(self, **overrides) -> dict:
        # optimize.py:317-327 の save_history_entry が組み立てる dict と同一の
        # キー集合（source は含まれない）。
        base = {
            "run_id": "20260810_120000",
            "target": "skills/foo/SKILL.md",
            "timestamp": "2026-08-10T12:00:00+00:00",
            "strategy": "llm_improve",
            "corrections_used": 3,
            "fitness_func": "default",
            "best_fitness": 0.7,
            "human_accepted": None,
            "rejection_reason": None,
        }
        base.update(overrides)
        return base

    def test_human_accepted_true_is_accepted(self):
        entry = self._entry(human_accepted=True)
        assert results_board.classify_decision(entry) == "accepted"

    def test_human_accepted_false_is_rejected(self):
        entry = self._entry(human_accepted=False, rejection_reason="score not improved")
        assert results_board.classify_decision(entry) == "rejected"

    def test_human_accepted_none_is_pending(self):
        """dry-run 生成のみ・未決定（実データで確認した実際の状態）。"""
        entry = self._entry(human_accepted=None)
        assert results_board.classify_decision(entry) == "pending"


class TestClassifyDecisionEvolveLoopWriterContract:
    """skills/evolve-loop-orchestrator/scripts/run_loop.py が append する
    loop_result の実際の emit 形（run_loop.py:649-664・approved で判定・source/
    human_accepted キーなし）。
    """

    def _entry(self, **overrides) -> dict:
        base = {
            "loop": 0,
            "run_id": "20260810_120000",
            "target": "skills/foo/SKILL.md",
            "baseline_score": 0.65,
            "best_score": 0.7,
            "improvement": 0.05,
            "verdict": "IMPROVED",
            "global_best_score": 0.7,
            "best_axes": {"technical": 0.7},
            "pareto_dominates": True,
            "approved": True,
            "dry_run": False,
            "timestamp": "2026-08-10T12:00:00+00:00",
            "variants_count": 1,
        }
        base.update(overrides)
        return base

    def test_approved_true_is_accepted(self):
        entry = self._entry(approved=True)
        assert results_board.classify_decision(entry) == "accepted"

    def test_approved_false_is_rejected(self):
        entry = self._entry(approved=False, verdict="STABLE")
        assert results_board.classify_decision(entry) == "rejected"


class TestClassifyDecisionEvolveRemediationWriterContract:
    """skills/evolve-fitness/scripts/fitness_evolution.py の
    record_evolve_diff_decision が append する実際のエントリ形
    （fitness_evolution.py:164-176・source="evolve_remediation"・human_accepted で判定）。
    """

    def _entry(self, **overrides) -> dict:
        base = {
            "id": "evolve_diff_abc123",
            "source": "evolve_remediation",
            "skill_name": "queue",
            "diff_summary": "evolve diff accepted: skill_evolve:medium",
            "timestamp": "2026-08-10T12:00:00+00:00",
            "fitness_func": "skill_quality",
            "best_fitness": 0.6,
            "human_accepted": True,
            "rejection_reason": None,
            "run_id": None,
            "decision_source": "explicit_accept",
        }
        base.update(overrides)
        return base

    def test_human_accepted_true_is_accepted(self):
        entry = self._entry(human_accepted=True)
        assert results_board.classify_decision(entry) == "accepted"

    def test_human_accepted_false_is_rejected(self):
        entry = self._entry(human_accepted=False, decision_source="explicit_reject")
        assert results_board.classify_decision(entry) == "rejected"


# ── build_results_board ─────────────────────────────────────────────


_NOW = datetime(2026, 8, 10, 12, 0, 0, tzinfo=timezone.utc)


def _iso(days_ago: float) -> str:
    return (_NOW - timedelta(days=days_ago)).isoformat()


@pytest.fixture
def stub_history(monkeypatch):
    """optimize_history_store.load_effective_history をスタブに差し替える（#402 段階4）。"""
    def _set(entries):
        monkeypatch.setattr(results_board, "load_effective_history", lambda slug: entries)
    return _set


@pytest.fixture
def stub_revert_events(monkeypatch):
    """optimize_history_store.load_revert_events をスタブに差し替える（#402 段階4 §3）。"""
    def _set(events):
        monkeypatch.setattr(results_board, "load_revert_events", lambda slug: events)
    return _set


@pytest.fixture
def stub_correction_rate(monkeypatch):
    """correction_rate.build_correction_rate_summary をスタブに差し替える（ADR-054 §7.2.1）。"""
    def _set(summary):
        monkeypatch.setattr(results_board, "build_correction_rate_summary", lambda **kw: summary)
    return _set


def _closed_gate_summary():
    return {
        "gate": {
            "gate_open": False, "display_start_week": None, "required": 4, "best_run_length": 0,
            "point_week": None, "current_run_length": 0,
        },
        "displayed_weeks": [],
        "latest_coverage": None,
        "diagnostics": {},
        "generated_at": None,
    }


def _point_week(week_id="2026-W33", judged=629, tp=63, pj_breakdown=None):
    """#508 状態(ii) の point_week fixture（compute_display_gate 出力と同型）。"""
    rate = (tp / judged) if judged else None
    return {
        "week_id": week_id,
        "judged_count": judged,
        "tp_count": tp,
        "coverage": 1.0,
        "measured": True,
        "rate": rate,
        "failure_reasons": [],
        "pj_breakdown": pj_breakdown if pj_breakdown is not None else {
            "evolve-anything": {"total": judged, "judged": judged, "tp": tp, "coverage": 1.0, "rate": rate},
        },
    }


def _point_gate_summary(point_week=None, current_run_length=1, latest_coverage=None):
    pw = point_week if point_week is not None else _point_week()
    return {
        "gate": {
            "gate_open": False, "display_start_week": None, "required": 4, "best_run_length": 1,
            "point_week": pw, "current_run_length": current_run_length,
        },
        "displayed_weeks": [],
        "latest_coverage": latest_coverage if latest_coverage is not None else {
            "week_id": pw["week_id"], "judged": pw["judged_count"], "total": pw["judged_count"],
            "failure_reasons": [],
        },
        "diagnostics": {},
        "generated_at": _NOW.isoformat(),
    }


class TestBuildResultsBoardCorrectionRate:
    """ADR-054 §7.2.1 柱3(a)「指摘率」の統合（旧 rework 表示の置換先）。

    集計ロジック自体（週次 join / freeze / gate）は test_correction_rate.py が担う。
    ここは build_results_board が build_correction_rate_summary を正しく配線し、
    read 失敗を握りつぶさず安全な既定値へフォールバックすることだけを検証する。
    """

    def test_summary_is_passed_through_verbatim(self, stub_history, stub_correction_rate):
        stub_history([])
        summary = {
            "gate": {"gate_open": True, "display_start_week": "2026-W10", "required": 4, "best_run_length": 4},
            "displayed_weeks": [
                {"week_id": "2026-W10", "rate": 0.1, "judged_count": 10, "tp_count": 1,
                 "coverage": 1.0, "measured": True, "pj_breakdown": {}, "is_worsening": False,
                 "top3_examples": []},
            ],
            "latest_coverage": {"week_id": "2026-W10", "judged": 10, "total": 10},
            "diagnostics": {},
            "generated_at": _NOW.isoformat(),
        }
        stub_correction_rate(summary)

        board = results_board.build_results_board("evolve-anything", now=_NOW)

        assert board["correction_rate"] == summary

    def test_read_failure_falls_back_to_closed_gate(self, stub_history, monkeypatch):
        """build_correction_rate_summary が例外を投げても KeyError にならず安全側（gate閉）に倒れる。"""
        stub_history([])

        def _boom(**kw):
            raise RuntimeError("boom")

        monkeypatch.setattr(results_board, "build_correction_rate_summary", _boom)

        board = results_board.build_results_board("evolve-anything", now=_NOW)

        assert board["correction_rate"]["gate"]["gate_open"] is False
        assert board["correction_rate"]["displayed_weeks"] == []


class TestBuildResultsBoardDecisions:
    def test_counts_by_bucket_within_window(self, stub_history, stub_correction_rate):
        stub_correction_rate(_closed_gate_summary())
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

    def test_entries_outside_window_not_counted(self, stub_history, stub_correction_rate):
        stub_correction_rate(_closed_gate_summary())
        stub_history([
            {"source": None, "approved": True, "skill_name": "old", "timestamp": _iso(40)},
        ])

        board = results_board.build_results_board("evolve-anything", now=_NOW)

        assert board["decisions"]["accepted"] == 0

    def test_excluded_always_present_even_when_zero(self, stub_history, stub_correction_rate):
        """silence != evaluated — excluded=0 でもキー自体は必ず出す。"""
        stub_correction_rate(_closed_gate_summary())
        stub_history([{"source": None, "approved": True, "skill_name": "a", "timestamp": _iso(1)}])

        board = results_board.build_results_board("evolve-anything", now=_NOW)

        assert board["decisions"]["excluded"] == 0

    def test_excluded_reasons_breakdown_computed(self, stub_history, stub_correction_rate):
        """ADR-054 §2.6-7: excluded の理由（fitness 無効化 / テスト汚染）を内訳として計算する。"""
        stub_correction_rate(_closed_gate_summary())
        stub_history([
            {
                "source": "evolve_remediation", "human_accepted": True,
                "fitness_eligible": False, "skill_name": "d", "timestamp": _iso(1),
            },
            {
                "source": None, "approved": True,
                "target": "/private/var/folders/gg/x/T/pytest-of-user/pytest-1/x.md",
                "timestamp": _iso(2),
            },
        ])

        board = results_board.build_results_board("evolve-anything", now=_NOW)

        assert board["excluded_reasons"] == {"fitness_ineligible": 1, "test_polluted": 1}

    def test_history_load_failure_is_graceful(self, stub_correction_rate, monkeypatch):
        stub_correction_rate(_closed_gate_summary())

        def _boom(slug):
            raise RuntimeError("boom")

        monkeypatch.setattr(results_board, "load_effective_history", _boom)

        board = results_board.build_results_board("evolve-anything", now=_NOW)

        assert board["decisions"] == {"accepted": 0, "rejected": 0, "pending": 0, "excluded": 0}


class TestBuildResultsBoardAcceptedList:
    def test_capped_at_ten_sorted_newest_first(self, stub_history, stub_correction_rate):
        stub_correction_rate(_closed_gate_summary())
        entries = [
            {"source": None, "approved": True, "skill_name": f"s{i}", "timestamp": _iso(i)}
            for i in range(15)
        ]
        stub_history(entries)

        board = results_board.build_results_board("evolve-anything", now=_NOW)

        assert len(board["accepted_list"]) == 10
        assert board["accepted_list"][0]["skill_name"] == "s0"  # 最新（days_ago=0）が先頭

    def test_falls_back_to_target_when_skill_name_missing(self, stub_history, stub_correction_rate):
        stub_correction_rate(_closed_gate_summary())
        stub_history([
            {"source": None, "approved": True, "target": "skills/foo/SKILL.md", "timestamp": _iso(1)},
        ])

        board = results_board.build_results_board("evolve-anything", now=_NOW)

        assert board["accepted_list"][0]["skill_name"] == "skills/foo/SKILL.md"

    def test_sort_uses_parsed_datetime_not_raw_string(self, stub_history, stub_correction_rate):
        """#398 Should 3: 生文字列の辞書順比較は tz offset 混在で誤順序になる
        （ISO8601 辞書順比較の既知 pitfall と同型）。

        "s_early"（+05:00 オフセット・実 UTC は早い時刻）と "s_late"（-05:00 オフセット・
        実 UTC は10時間後）を用意する。生文字列を辞書順比較すると "23:00+05:00" の方が
        "19:00-05:00" より大きく見えるため s_early が誤って先頭に来るが、実際の UTC 換算では
        s_late の方が新しい。_parse_timestamp でパースした aware datetime を比較すれば
        正しく s_late が先頭に来る。
        """
        stub_correction_rate(_closed_gate_summary())
        stub_history([
            {
                "source": None, "approved": True, "skill_name": "s_early",
                "timestamp": "2026-08-07T23:00:00+05:00",  # UTC 2026-08-07T18:00:00
            },
            {
                "source": None, "approved": True, "skill_name": "s_late",
                "timestamp": "2026-08-07T19:00:00-05:00",  # UTC 2026-08-08T00:00:00（実は後）
            },
        ])

        board = results_board.build_results_board("evolve-anything", now=_NOW)

        names = [e["skill_name"] for e in board["accepted_list"]]
        assert names == ["s_late", "s_early"]


class TestBuildResultsBoardWithdrawalCandidates:
    def test_accepted_and_regressed_is_withdrawal_candidate(self, stub_history, stub_correction_rate):
        stub_correction_rate(_closed_gate_summary())
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
        self, stub_history, stub_correction_rate
    ):
        """withdrawal candidate は accepted のみが対象（rejected は最初から不採用）。"""
        stub_correction_rate(_closed_gate_summary())
        stub_history([
            {
                "source": None, "approved": False, "verdict": "REGRESSED",
                "skill_name": "never-applied", "timestamp": _iso(1),
            },
        ])

        board = results_board.build_results_board("evolve-anything", now=_NOW)

        assert board["withdrawal_candidates"] == []


class TestBuildResultsBoardWithdrawalRevertFields(object):
    """#402 段階4 §3: withdrawal candidate に entry_id/revert_available/
    revert_unavailable_reason/reverted を構造化結果まで運ぶ。"""

    def test_pre_extension_entry_is_not_revert_available(
        self, stub_history, stub_correction_rate, stub_revert_events
    ):
        """revert_schema_version の無い entry（記録拡張前・非対応 writer）は
        pre_extension として revert_available=False になる。"""
        stub_correction_rate(_closed_gate_summary())
        stub_revert_events([])
        stub_history([
            {
                "id": "p1", "source": None, "approved": True, "verdict": "REGRESSED",
                "skill_name": "bad-apply", "timestamp": _iso(1),
            },
        ])

        board = results_board.build_results_board("evolve-anything", now=_NOW)

        c = board["withdrawal_candidates"][0]
        assert c["entry_id"] == "p1"
        assert c["revert_available"] is False
        assert c["revert_unavailable_reason"] == "pre_extension"
        assert c["reverted"] is False

    def test_full_revert_fields_entry_is_revert_available(
        self, stub_history, stub_correction_rate, stub_revert_events
    ):
        stub_correction_rate(_closed_gate_summary())
        stub_revert_events([])
        stub_history([
            {
                "id": "p2", "source": None, "approved": True, "verdict": "REGRESSED",
                "skill_name": "bad-apply2", "timestamp": _iso(1),
                "revert_schema_version": 1, "revert_before_b64": "eJw...",
                "revert_unavailable_reason": None, "scope": "project",
                "repo_id": "/repo", "relative_path": "skills/x/SKILL.md",
                "after_sha": "deadbeef",
            },
        ])

        board = results_board.build_results_board("evolve-anything", now=_NOW)

        c = board["withdrawal_candidates"][0]
        assert c["entry_id"] == "p2"
        assert c["revert_available"] is True
        assert c["revert_unavailable_reason"] is None
        assert c["reverted"] is False

    def test_before_too_large_entry(self, stub_history, stub_correction_rate, stub_revert_events):
        stub_correction_rate(_closed_gate_summary())
        stub_revert_events([])
        stub_history([
            {
                "id": "p3", "source": None, "approved": True, "verdict": "REGRESSED",
                "skill_name": "big-skill", "timestamp": _iso(1),
                "revert_schema_version": 1, "revert_before_b64": None,
                "revert_unavailable_reason": "before_too_large", "scope": "project",
                "repo_id": "/repo", "relative_path": "skills/big/SKILL.md",
            },
        ])

        board = results_board.build_results_board("evolve-anything", now=_NOW)

        c = board["withdrawal_candidates"][0]
        assert c["revert_available"] is False
        assert c["revert_unavailable_reason"] == "before_too_large"

    def test_revert_events_load_failure_is_graceful(
        self, stub_history, stub_correction_rate, monkeypatch
    ):
        """load_revert_events が例外を投げても reverted=False にフォールバックする。"""
        stub_correction_rate(_closed_gate_summary())
        stub_history([
            {
                "id": "p1", "source": None, "approved": True, "verdict": "REGRESSED",
                "skill_name": "bad-apply", "timestamp": _iso(1),
            },
        ])

        def _boom(slug):
            raise RuntimeError("boom")

        monkeypatch.setattr(results_board, "load_revert_events", _boom)

        board = results_board.build_results_board("evolve-anything", now=_NOW)

        assert board["withdrawal_candidates"][0]["reverted"] is False


# ── render_results_board ────────────────────────────────────────────


class TestRenderResultsBoard:
    def _board(self, **overrides):
        base = {
            "slug": "evolve-anything",
            "generated_at": _NOW.isoformat(),
            "correction_rate": _closed_gate_summary(),
            "decisions": {"accepted": 1, "rejected": 2, "pending": 1, "excluded": 4},
            "accepted_list": [{"skill_name": "queue", "timestamp": _iso(1)}],
            "withdrawal_candidates": [],
        }
        base.update(overrides)
        return base

    def test_header_present(self):
        lines = results_board.render_results_board(self._board())
        assert lines[0] == "## 🏆 戦果ボード"

    def test_gate_closed_shows_not_measured_with_coverage(self):
        board = self._board(correction_rate={
            **_closed_gate_summary(),
            "latest_coverage": {"week_id": "2026-W34", "judged": 82, "total": 90},
        })
        lines = results_board.render_results_board(board)
        text = "\n".join(lines)
        assert "未測定" in text
        assert "82/90" in text
        assert "2026-W34" in text

    def test_gate_closed_no_finalized_week_shows_generic_message(self):
        board = self._board(correction_rate=_closed_gate_summary())
        lines = results_board.render_results_board(board)
        text = "\n".join(lines)
        assert "未測定" in text
        assert "確定週データなし" in text

    def test_exclusion_diagnostics_shown_with_zero_counts(self):
        """#466: 分母から除外した件数は0件でも必ず表示する（silence != evaluated）。"""
        board = self._board(correction_rate={
            **_closed_gate_summary(),
            "diagnostics": {
                "excluded_untracked_total": 0,
                "excluded_before_cutoff_total": 0,
                "excluded_home_dir_total": 0,
                "excluded_total": 0,
            },
        })
        lines = results_board.render_results_board(board)
        text = "\n".join(lines)
        assert "分母から除外: 0 件" in text
        assert "tracked外 0 件" in text
        assert "90日超 0 件" in text
        assert "ホーム起動 0 件" in text

    def test_exclusion_diagnostics_shown_with_nonzero_counts(self):
        board = self._board(correction_rate={
            **_closed_gate_summary(),
            "diagnostics": {
                "excluded_untracked_total": 5,
                "excluded_before_cutoff_total": 3,
                "excluded_home_dir_total": 12,
                "excluded_total": 20,
            },
        })
        lines = results_board.render_results_board(board)
        text = "\n".join(lines)
        assert "分母から除外: 20 件" in text
        assert "tracked外 5 件" in text
        assert "90日超 3 件" in text
        assert "ホーム起動 12 件" in text

    def test_exclusion_diagnostics_shown_when_gate_open_too(self):
        """gate 開閉に関わらず常に表示する。"""
        board = self._board(correction_rate={
            "gate": {"gate_open": True, "display_start_week": "2026-W10", "required": 4, "best_run_length": 4},
            "displayed_weeks": [
                {"week_id": "2026-W10", "rate": 0.1, "judged_count": 10, "tp_count": 1,
                 "coverage": 1.0, "measured": True, "pj_breakdown": {}, "is_worsening": False,
                 "top3_examples": []},
            ],
            "latest_coverage": {"week_id": "2026-W10", "judged": 10, "total": 10},
            "diagnostics": {
                "excluded_untracked_total": 1,
                "excluded_before_cutoff_total": 0,
                "excluded_home_dir_total": 0,
                "excluded_total": 1,
            },
            "generated_at": _NOW.isoformat(),
        })
        lines = results_board.render_results_board(board)
        text = "\n".join(lines)
        assert "分母から除外: 1 件" in text

    def test_gate_open_lists_weeks_newest_first_with_rate(self):
        board = self._board(correction_rate={
            "gate": {"gate_open": True, "display_start_week": "2026-W10", "required": 4, "best_run_length": 4},
            "displayed_weeks": [
                {"week_id": "2026-W10", "rate": 0.1, "judged_count": 10, "tp_count": 1,
                 "coverage": 1.0, "measured": True, "pj_breakdown": {}, "is_worsening": False,
                 "top3_examples": []},
                {"week_id": "2026-W11", "rate": 0.2, "judged_count": 10, "tp_count": 2,
                 "coverage": 1.0, "measured": True, "pj_breakdown": {}, "is_worsening": True,
                 "top3_examples": [{"text": "四国めたんじゃなくて", "reason": "呼称の訂正", "idiom": ""}]},
            ],
            "latest_coverage": {"week_id": "2026-W11", "judged": 10, "total": 10},
            "diagnostics": {},
            "generated_at": _NOW.isoformat(),
        })
        lines = results_board.render_results_board(board)
        text = "\n".join(lines)
        # 新しい週（W11）が先頭に来る
        assert text.index("2026-W11") < text.index("2026-W10")
        assert "10.0%" in text
        assert "20.0%" in text

    def test_worsening_week_shows_top3_examples(self):
        board = self._board(correction_rate={
            "gate": {"gate_open": True, "display_start_week": "2026-W10", "required": 4, "best_run_length": 4},
            "displayed_weeks": [
                {"week_id": "2026-W10", "rate": 0.2, "judged_count": 10, "tp_count": 2,
                 "coverage": 1.0, "measured": True, "pj_breakdown": {}, "is_worsening": True,
                 "top3_examples": [{"text": "四国めたんじゃなくて", "reason": "呼称の訂正", "idiom": ""}]},
            ],
            "latest_coverage": {"week_id": "2026-W10", "judged": 10, "total": 10},
            "diagnostics": {},
            "generated_at": _NOW.isoformat(),
        })
        lines = results_board.render_results_board(board)
        text = "\n".join(lines)
        assert "四国めたんじゃなくて" in text
        assert "呼称の訂正" in text

    def test_improving_week_does_not_show_top3(self):
        board = self._board(correction_rate={
            "gate": {"gate_open": True, "display_start_week": "2026-W10", "required": 4, "best_run_length": 4},
            "displayed_weeks": [
                {"week_id": "2026-W10", "rate": 0.1, "judged_count": 10, "tp_count": 1,
                 "coverage": 1.0, "measured": True, "pj_breakdown": {}, "is_worsening": False,
                 "top3_examples": []},
            ],
            "latest_coverage": {"week_id": "2026-W10", "judged": 10, "total": 10},
            "diagnostics": {},
            "generated_at": _NOW.isoformat(),
        })
        lines = results_board.render_results_board(board)
        text = "\n".join(lines)
        assert "気になった直近の指摘" not in text

    def test_pj_breakdown_below_floor_shows_counts_only(self):
        """1桁分母の PJ 別 rate は非表示（件数のみ・Simpson 防御）。"""
        board = self._board(correction_rate={
            "gate": {"gate_open": True, "display_start_week": "2026-W10", "required": 4, "best_run_length": 4},
            "displayed_weeks": [
                {"week_id": "2026-W10", "rate": 0.1, "judged_count": 10, "tp_count": 1,
                 "coverage": 1.0, "measured": True,
                 "pj_breakdown": {
                     "big-pj": {"total": 10, "judged": 10, "tp": 1, "coverage": 1.0, "rate": 0.1},
                     "tiny-pj": {"total": 2, "judged": 2, "tp": 1, "coverage": 1.0, "rate": None},
                 },
                 "is_worsening": False, "top3_examples": []},
            ],
            "latest_coverage": {"week_id": "2026-W10", "judged": 10, "total": 10},
            "diagnostics": {},
            "generated_at": _NOW.isoformat(),
        })
        lines = results_board.render_results_board(board)
        text = "\n".join(lines)
        assert "big-pj 1/10（10.0%）" in text
        assert "tiny-pj 1/2（件数のみ・分母不足）" in text

    # ── #400 A5: カテゴリ内訳（設計 §2.6「表示の形」）────────────────────

    def test_category_breakdown_shows_composition_and_top_example(self):
        board = self._board(correction_rate={
            "gate": {"gate_open": True, "display_start_week": "2026-W10", "required": 4, "best_run_length": 4},
            "displayed_weeks": [
                {"week_id": "2026-W10", "rate": 0.1, "judged_count": 10, "tp_count": 3,
                 "coverage": 1.0, "measured": True, "pj_breakdown": {}, "is_worsening": False,
                 "top3_examples": [],
                 "category_breakdown": {
                     "measured": True,
                     "counts": {"presentation": 2, "factual": 1},
                     "unclassified_count": 0,
                     "conflict_keys": 0,
                     "top_category": "presentation",
                     "top_category_example": {
                         "text": "P6のデザインが違うんだけど", "reason": "見た目の指摘",
                         "idiom": "", "pj_slug": "evolve-anything",
                     },
                 }},
            ],
            "latest_coverage": {"week_id": "2026-W10", "judged": 10, "total": 10},
            "diagnostics": {},
            "generated_at": _NOW.isoformat(),
        })
        lines = results_board.render_results_board(board)
        text = "\n".join(lines)
        assert "presentation" in text and "2件" in text
        assert "factual" in text and "1件" in text
        assert "P6のデザインが違うんだけど" in text
        assert "見た目の指摘" in text
        # task-mix 交絡の注記が出る
        assert "その週に何をやったか" in text

    def test_category_breakdown_hidden_when_not_measured(self):
        """conflict で当該週のカテゴリ内訳が未測定なら構成比は表示しない。

        ただし**痕跡なく消しはしない**（P4: silence != evaluated）。構成比の代わりに
        「測定不能 + 衝突件数」を出し、判定の重複記録という測定バグの手がかりを残す
        （下の test_category_breakdown_conflict_surfaces_reason が固定）。
        """
        board = self._board(correction_rate={
            "gate": {"gate_open": True, "display_start_week": "2026-W10", "required": 4, "best_run_length": 4},
            "displayed_weeks": [
                {"week_id": "2026-W10", "rate": 0.1, "judged_count": 10, "tp_count": 1,
                 "coverage": 1.0, "measured": True, "pj_breakdown": {}, "is_worsening": False,
                 "top3_examples": [],
                 "category_breakdown": {
                     "measured": False, "counts": {}, "unclassified_count": 0,
                     "conflict_keys": 1, "top_category": None, "top_category_example": None,
                 }},
            ],
            "latest_coverage": {"week_id": "2026-W10", "judged": 10, "total": 10},
            "diagnostics": {},
            "generated_at": _NOW.isoformat(),
        })
        lines = results_board.render_results_board(board)
        text = "\n".join(lines)
        assert "カテゴリ構成" not in text

    def test_category_breakdown_conflict_surfaces_reason(self):
        """衝突で内訳を落とした週は理由を surface する（P4: silence != evaluated）。

        内訳行が痕跡なく消えると「category を持たない週」と区別が付かず、同一発話への
        複数カテゴリ付与＝判定の重複記録という測定バグの手がかりを失う。
        """
        board = self._board(correction_rate={
            "gate": {"gate_open": True, "display_start_week": "2026-W10", "required": 4, "best_run_length": 4},
            "displayed_weeks": [
                {"week_id": "2026-W10", "rate": 0.1, "judged_count": 10, "tp_count": 1,
                 "coverage": 1.0, "measured": True, "pj_breakdown": {}, "is_worsening": False,
                 "top3_examples": [],
                 "category_breakdown": {
                     "measured": False, "counts": {}, "unclassified_count": 0,
                     "conflict_keys": 3, "top_category": None, "top_category_example": None,
                 }},
            ],
            "latest_coverage": {"week_id": "2026-W10", "judged": 10, "total": 10},
            "diagnostics": {},
            "generated_at": _NOW.isoformat(),
        })
        text = "\n".join(results_board.render_results_board(board))
        assert "カテゴリ内訳: 測定不能" in text
        assert "3 件" in text

    def test_category_breakdown_absent_key_does_not_break_rendering(self):
        """category_breakdown キー自体が無い週（旧 summary・A5 以前）でも壊れない。"""
        board = self._board(correction_rate={
            "gate": {"gate_open": True, "display_start_week": "2026-W10", "required": 4, "best_run_length": 4},
            "displayed_weeks": [
                {"week_id": "2026-W10", "rate": 0.1, "judged_count": 10, "tp_count": 1,
                 "coverage": 1.0, "measured": True, "pj_breakdown": {}, "is_worsening": False,
                 "top3_examples": []},
            ],
            "latest_coverage": {"week_id": "2026-W10", "judged": 10, "total": 10},
            "diagnostics": {},
            "generated_at": _NOW.isoformat(),
        })
        lines = results_board.render_results_board(board)  # 例外を投げない
        text = "\n".join(lines)
        assert "カテゴリ構成" not in text

    def test_excluded_count_always_shown_even_when_zero(self):
        board = self._board(decisions={"accepted": 1, "rejected": 0, "pending": 0, "excluded": 0})
        lines = results_board.render_results_board(board)
        text = "\n".join(lines)
        assert "excluded" in text and "0" in text

    def test_excluded_reason_breakdown_shown_when_present(self):
        """ADR-054 §2.6-7: excluded の理由内訳（テスト汚染/legacy無効化）を画面に出す。"""
        board = self._board(
            decisions={"accepted": 0, "rejected": 0, "pending": 0, "excluded": 4},
            excluded_reasons={"fitness_ineligible": 3, "test_polluted": 1},
        )
        lines = results_board.render_results_board(board)
        text = "\n".join(lines)
        assert "legacy無効化 3 件" in text
        assert "テスト汚染 1 件" in text

    def test_no_excluded_reason_line_when_excluded_zero(self):
        board = self._board(
            decisions={"accepted": 1, "rejected": 0, "pending": 0, "excluded": 0},
            excluded_reasons={},
        )
        lines = results_board.render_results_board(board)
        text = "\n".join(lines)
        assert "内訳" not in text

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

    def test_revert_available_prints_two_step_command(self):
        """#402 段階4 §3(S4): revert_available=true の行には実行コマンドそのものを
        印字する（既定 dry-run なので2段案内）。"""
        board = self._board(withdrawal_candidates=[
            {
                "skill_name": "bad-apply", "timestamp": _iso(1), "verdict": "REGRESSED",
                "entry_id": "p1", "revert_available": True,
                "revert_unavailable_reason": None, "reverted": False,
            },
        ])
        lines = results_board.render_results_board(board)
        text = "\n".join(lines)
        assert "bin/evolve-revert p1" in text
        assert "bin/evolve-revert p1 --apply" in text

    def test_revert_unavailable_prints_japanese_reason(self):
        """コード（機械用）でなく日本語1行（人間用）を表示する。"""
        board = self._board(withdrawal_candidates=[
            {
                "skill_name": "old-apply", "timestamp": _iso(1), "verdict": "REGRESSED",
                "entry_id": "p2", "revert_available": False,
                "revert_unavailable_reason": "pre_extension", "reverted": False,
            },
        ])
        lines = results_board.render_results_board(board)
        text = "\n".join(lines)
        assert "pre_extension" not in text
        assert "戻す機能の導入前に採用されたため" in text

    def test_reverted_entry_shows_reverted_note_not_command(self):
        board = self._board(withdrawal_candidates=[
            {
                "skill_name": "already-reverted", "timestamp": _iso(1), "verdict": "REGRESSED",
                "entry_id": "p3", "revert_available": True,
                "revert_unavailable_reason": None, "reverted": True,
            },
        ])
        lines = results_board.render_results_board(board)
        text = "\n".join(lines)
        assert "bin/evolve-revert p3" not in text
        assert "戻し済み" in text

    def test_is_read_only_no_disk_write(self, tmp_path):
        before = set(tmp_path.rglob("*"))
        results_board.render_results_board(self._board())
        after = set(tmp_path.rglob("*"))
        assert before == after


# ── #508: 状態(ii) 点表示（指摘率を「確定した1週分の点」として先に出す） ────
#
# 設計正典 docs/decisions/drafts/508-single-week-rate-point.md §4。
# 「バイト等値」の範囲は指摘率ブロック全体（除外 diagnostics 行を含む・次の見出しまで）
# なので、_render_correction_rate を直接呼んでブロック単体を検証する。


class TestRenderCorrectionRatePointState:
    # ── 陽性対照（緑のまま・P1〜P3。陰性試験と混ぜて数えない） ──────────

    def test_p1_state_i_exact_match_unaffected(self):
        """P1/I1: 確定週0件 → 変更前 golden と等値。"""
        lines = results_board._render_correction_rate(_closed_gate_summary())
        assert lines == [
            "分母から除外: 0 件（tracked外 0 件・90日超 0 件・ホーム起動 0 件）",
            "",
            "**指摘率: 未測定（確定週データなし）**",
            "全量判定の確定週が 4 週連続で揃うまで系列は表示しません。",
            "",
        ]

    def test_p2_state_iii_exact_match_unaffected(self):
        """P2/I5: 連続 run が k 以上（gate_open）→ 変更前 golden と等値。"""
        summary = {
            "gate": {
                "gate_open": True, "display_start_week": "2026-W10",
                "required": 4, "best_run_length": 4,
            },
            "displayed_weeks": [
                {"week_id": "2026-W10", "rate": 0.1, "judged_count": 10, "tp_count": 1,
                 "coverage": 1.0, "measured": True, "pj_breakdown": {}, "is_worsening": False,
                 "top3_examples": []},
            ],
            "latest_coverage": {"week_id": "2026-W10", "judged": 10, "total": 10},
            "diagnostics": {},
            "generated_at": _NOW.isoformat(),
        }
        lines = results_board._render_correction_rate(summary)
        assert lines == [
            "分母から除外: 0 件（tracked外 0 件・90日超 0 件・ホーム起動 0 件）",
            "",
            "**指摘率**（判定カバレッジ100%の確定週のみ・新しい順）",
            "分子は LLM judge の意味判定です"
            "（実測 precision 80% ＝ 分子の2割は誤りを含む前提で読んでください）。",
            "",
            "- 2026-W10: 10.0%（判定 10 件中 TP 1 件・カバレッジ100%）",
            "",
        ]

    def test_p3_point_state_pj_floor_boundary(self):
        """P3/I7(c): 点表示でも PJ 別 floor（judged<MIN_PJ_RATE_DENOM=10 は rate 非表示）が
        従来どおり効く。境界値 judged=9（非表示）と judged=10（表示）を突き合わせる。"""
        pw = _point_week(week_id="2026-W20", judged=19, tp=2, pj_breakdown={
            "small": {"total": 9, "judged": 9, "tp": 1, "coverage": 1.0, "rate": None},
            "big": {"total": 10, "judged": 10, "tp": 1, "coverage": 1.0, "rate": 0.1},
        })
        summary = _point_gate_summary(point_week=pw, current_run_length=1)
        text = "\n".join(results_board._render_correction_rate(summary))
        assert "small 1/9（件数のみ・分母不足）" in text
        assert "big 1/10（10.0%）" in text

    # ── 陰性試験（赤くなるべき・I2/I6/I7/I8/I9） ──────────────────────

    def test_i2_point_block_exact_match(self):
        """I2/N5: (a)〜(f) がすべて含まれることを完全一致で固定する
        （分子/分母の欠落・数値の入替を検出する）。"""
        pw = _point_week(week_id="2026-W33", judged=100, tp=10, pj_breakdown={
            "evolve-anything": {"total": 100, "judged": 100, "tp": 10, "coverage": 1.0, "rate": 0.1},
        })
        summary = _point_gate_summary(point_week=pw, current_run_length=1)
        lines = results_board._render_correction_rate(summary)
        assert lines == [
            "分母から除外: 0 件（tracked外 0 件・90日超 0 件・ホーム起動 0 件）",
            "",
            "**指摘率（2026-W33）: 10.0%**（1週分。推移は 4 週連続で表示）",
            "判定 100 件中 TP 10 件・カバレッジ100%",
            "連続 run の進捗: 1/4 週連続",
            "PJ別: evolve-anything 10/100（10.0%）",
            "",
        ]

    def test_i4_unmeasured_only_weeks_stay_state_i(self):
        """I4/N1: measured=False の週しか無ければ点表示の候補に入らず状態(i)に留まる。"""
        summary = _closed_gate_summary()  # point_week=None（measured 週なし相当）
        text = "\n".join(results_board._render_correction_rate(summary))
        assert "指摘率（" not in text
        assert "未測定" in text

    def test_i6_newer_unmeasured_candidate_surfaced(self):
        """I6/N3: 点の対象週より新しい確定候補週が未測定なら、week_id・カバレッジ・
        未測定理由を隠さず添える。"""
        pw = _point_week(week_id="2026-W33", judged=100, tp=10)
        latest = {
            "week_id": "2026-W34", "judged": 50, "total": 90,
            "failure_reasons": ["tp_conflict"],
        }
        summary = _point_gate_summary(point_week=pw, current_run_length=1, latest_coverage=latest)
        text = "\n".join(results_board._render_correction_rate(summary))
        assert "最新候補週 2026-W34: 判定カバレッジ 50/90・未測定・理由: tp_conflict" in text

    def test_i7ab_all_pj_listed_and_sums_match_total(self):
        """I7(a)(b)/N6: judged>=1 の PJ を全件列挙し、PJ 別合計が全体分母/分子と一致する。"""
        pj_breakdown = {
            "pj-a": {"total": 60, "judged": 60, "tp": 6, "coverage": 1.0, "rate": 0.1},
            "pj-b": {"total": 40, "judged": 40, "tp": 4, "coverage": 1.0, "rate": 0.1},
        }
        pw = _point_week(week_id="2026-W20", judged=100, tp=10, pj_breakdown=pj_breakdown)
        assert sum(v["judged"] for v in pj_breakdown.values()) == pw["judged_count"]
        assert sum(v["tp"] for v in pj_breakdown.values()) == pw["tp_count"]
        summary = _point_gate_summary(point_week=pw, current_run_length=1)
        text = "\n".join(results_board._render_correction_rate(summary))
        assert "pj-a 6/60（10.0%）" in text
        assert "pj-b 4/40（10.0%）" in text

    def test_i7d_empty_pj_breakdown_falls_back_to_state_i(self):
        """I7(d)/防御: PJ別内訳が空なら点表示そのものを行わず状態(i)に落ちる。"""
        pw = _point_week(pj_breakdown={})
        summary = _point_gate_summary(point_week=pw, current_run_length=1)
        text = "\n".join(results_board._render_correction_rate(summary))
        assert "未測定" in text
        assert "PJ別" not in text
        assert f"指摘率（{pw['week_id']}）" not in text

    def test_i8_progress_reflects_current_run_length(self):
        """I8/N4: n/k の n は現在の連続 run 長（表示専用・非連続な確定週の総数ではない）。"""
        summary = _point_gate_summary(current_run_length=3)
        text = "\n".join(results_board._render_correction_rate(summary))
        assert "連続 run の進捗: 3/4 週連続" in text

    def test_n7_state_i_never_shows_zero_percent(self):
        """N7: 確定週0件のときに率 0% を出さない（未測定と 0 の混同防止）。"""
        text = "\n".join(results_board._render_correction_rate(_closed_gate_summary()))
        assert "0%" not in text
        assert "0.0%" not in text

    # ── I3（系列を騙らない） ────────────────────────────────────────

    def test_i3_single_week_id_when_no_newer_candidate(self):
        text = "\n".join(results_board._render_correction_rate(_point_gate_summary()))
        assert text.count("2026-W") == 1

    def test_i3_newer_candidate_line_carries_no_second_rate_value(self):
        """I6 で最新候補週の week_id が追加で出ても、それは率を伴う第2のデータ点ではない
        （%記号が付かない＝系列を騙らない）。"""
        pw = _point_week(week_id="2026-W33", judged=100, tp=10)
        latest = {"week_id": "2026-W34", "judged": 50, "total": 90, "failure_reasons": []}
        summary = _point_gate_summary(point_week=pw, current_run_length=1, latest_coverage=latest)
        lines = results_board._render_correction_rate(summary)
        text = "\n".join(lines)
        assert "→" not in text and "↑" not in text and "↓" not in text
        candidate_line = next(line for line in lines if line.startswith("最新候補週"))
        assert "%" not in candidate_line

    # ── §2-1-(c) 実行文脈: gate 側で非ソート入力を正しく解決した後の配線確認 ──

    def test_current_run_length_and_point_week_wired_from_gate(self):
        """compute_display_gate が非ソート入力から正しく解決した point_week /
        current_run_length を、results_board がそのまま表示に使うことを配線として確認する
        （collect ロジック自体は test_correction_rate.py が担う）。"""
        import correction_rate as cr

        weeks = [
            {"week_id": "2026-W12", "measured": True, "rate": 0.1, "judged_count": 12,
             "tp_count": 1, "total_population": 12, "coverage": 1.0,
             "pj_breakdown": {"evolve-anything": {"total": 12, "judged": 12, "tp": 1,
                                                    "coverage": 1.0, "rate": None}},
             "top3_examples": [], "failure_reasons": []},
            {"week_id": "2026-W10", "measured": True, "rate": 0.2, "judged_count": 10,
             "tp_count": 2, "total_population": 10, "coverage": 1.0,
             "pj_breakdown": {"evolve-anything": {"total": 10, "judged": 10, "tp": 2,
                                                    "coverage": 1.0, "rate": 0.2}},
             "top3_examples": [], "failure_reasons": []},
            {"week_id": "2026-W11", "measured": True, "rate": 0.15, "judged_count": 11,
             "tp_count": 1, "total_population": 11, "coverage": 1.0,
             "pj_breakdown": {"evolve-anything": {"total": 11, "judged": 11, "tp": 1,
                                                    "coverage": 1.0, "rate": 0.09}},
             "top3_examples": [], "failure_reasons": []},
        ]  # わざと非ソートで渡す（§2-1-c）
        gate = cr.compute_display_gate(weeks)
        summary = {
            "gate": gate,
            "displayed_weeks": [],
            "latest_coverage": {
                "week_id": "2026-W12", "judged": 12, "total": 12, "failure_reasons": [],
            },
            "diagnostics": {},
            "generated_at": _NOW.isoformat(),
        }
        text = "\n".join(results_board._render_correction_rate(summary))
        assert "指摘率（2026-W12）" in text
        assert "連続 run の進捗: 3/4 週連続" in text
