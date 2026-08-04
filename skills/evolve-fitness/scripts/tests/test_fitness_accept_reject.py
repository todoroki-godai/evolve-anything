"""issue #223: evolve diff 提案の accept/reject 採点蓄積のテスト。

正常系 E2E を最初に書く（複数ステップのデータ受け渡しバグ検出のため）:
  evolve diff accept → 採点ブリッジ → history.jsonl 正規記録
  → load_history → analyze_correlations が fitness_func グループで相関を取る。
"""
import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

_plugin_root = Path(__file__).resolve().parent.parent.parent.parent.parent
sys.path.insert(0, str(_plugin_root / "skills" / "evolve-fitness" / "scripts"))
sys.path.insert(0, str(_plugin_root / "scripts" / "rl" / "fitness"))

import fitness_evolution as fe


# ========== E2E: 採点ブリッジ → 記録 → 相関分析 ==========

GOOD_SKILL = """---
name: my-skill
description: Use this skill to create and build a foo. Trigger when user says foo.
---

This skill builds foo objects from bar inputs through a deterministic pipeline.
"""


class TestRecordEvolveDiffDecisionE2E:
    def test_accept_writes_normalized_history_entry(self, tmp_path):
        """diff accept → best_fitness/human_accepted/fitness_func/source が記録される。"""
        history_file = tmp_path / "history.jsonl"

        entry = fe.record_evolve_diff_decision(
            skill_name="my-skill",
            after_content=GOOD_SKILL,
            diff_summary="description を行動促進形式に変更",
            human_accepted=True,
            history_file=history_file,
        )

        assert history_file.exists()
        assert entry["fitness_func"] == "skill_quality"
        assert entry["source"] == "evolve_remediation"
        assert entry["human_accepted"] is True
        assert isinstance(entry["best_fitness"], float)
        assert 0.0 <= entry["best_fitness"] <= 1.0
        assert entry["skill_name"] == "my-skill"
        assert "timestamp" in entry
        assert "id" in entry

        # #267 C3: naive local time は queue_verify._parse_iso と JST で 9 時間ずれるため
        # aware UTC で書く（同一 instant の naive/aware 混在比較の罠を writer 側で防ぐ）。
        parsed_ts = datetime.fromisoformat(entry["timestamp"])
        assert parsed_ts.tzinfo is not None
        assert parsed_ts.utcoffset().total_seconds() == 0

        # 書き込まれた行が読み戻せる
        lines = history_file.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 1
        assert json.loads(lines[0])["best_fitness"] == entry["best_fitness"]

    def test_naive_clock_still_persisted_as_aware_utc(self, tmp_path, monkeypatch):
        """#297 fixup: この writer は append_entry を経由しない直接書込経路のため、
        naive timestamp を返す clock でも normalize_entry_timestamp を明示的に
        通っていることを回帰的に検証する（chokepoint 呼び忘れの再発防止）。
        """
        history_file = tmp_path / "history.jsonl"

        class _NaiveClock(datetime):
            @classmethod
            def now(cls, tz=None):  # noqa: ARG003 — tz を無視する壊れた clock を模す
                return datetime(2026, 7, 31, 9, 0, 0)

        monkeypatch.setattr(fe, "datetime", _NaiveClock)

        entry = fe.record_evolve_diff_decision(
            skill_name="my-skill",
            after_content=GOOD_SKILL,
            diff_summary="naive clock regression",
            human_accepted=True,
            history_file=history_file,
        )

        parsed_ts = datetime.fromisoformat(entry["timestamp"])
        assert parsed_ts.tzinfo is not None
        assert parsed_ts.utcoffset().total_seconds() == 0

        on_disk = json.loads(history_file.read_text(encoding="utf-8").splitlines()[0])
        disk_ts = datetime.fromisoformat(on_disk["timestamp"])
        assert disk_ts.tzinfo is not None
        assert disk_ts.utcoffset().total_seconds() == 0

    def test_record_carries_provenance(self, tmp_path):
        """同じ optimize_history に書く第3の producer も実行条件を残す（#309）。

        ここが欠けると、同一母集団に「条件つき」と「条件不明」のレコードが混ざり、
        後から条件別に比較できなくなる（後方互換の問題ではなく新規生成分の配線漏れ）。
        """
        history_file = tmp_path / "history.jsonl"
        entry = fe.record_evolve_diff_decision(
            skill_name="my-skill", after_content=GOOD_SKILL, diff_summary="x",
            human_accepted=True, history_file=history_file,
        )
        prov = json.loads(history_file.read_text(encoding="utf-8").splitlines()[0])["provenance"]
        assert prov == entry["provenance"]
        assert prov["schema_version"] == 1
        # skill_quality の採点は LLM を呼ばない
        assert prov["evaluation_kind"] == "deterministic"
        assert prov["producer"] == "evolve-fitness.record_evolve_diff_decision"
        assert prov["config"]["fitness_func"] == "skill_quality"
        assert "judge" not in prov

    def test_reject_records_human_accepted_false(self, tmp_path):
        history_file = tmp_path / "history.jsonl"
        entry = fe.record_evolve_diff_decision(
            skill_name="my-skill",
            after_content=GOOD_SKILL,
            diff_summary="x",
            human_accepted=False,
            rejection_reason="意味が変わる",
            history_file=history_file,
        )
        assert entry["human_accepted"] is False
        assert entry["rejection_reason"] == "意味が変わる"

    def test_run_id_is_recorded_when_provided(self, tmp_path):
        """run_id を渡すと entry / history.jsonl に純加算される（#267 Sprint 1）。"""
        history_file = tmp_path / "history.jsonl"
        entry = fe.record_evolve_diff_decision(
            skill_name="my-skill",
            after_content=GOOD_SKILL,
            diff_summary="x",
            human_accepted=True,
            history_file=history_file,
            run_id="run-abc",
        )
        assert entry["run_id"] == "run-abc"
        lines = history_file.read_text(encoding="utf-8").splitlines()
        assert json.loads(lines[0])["run_id"] == "run-abc"

    def test_decision_source_is_recorded_when_provided(self, tmp_path):
        """#376: 明示 decision イベントの出所を entry に残す（legacy レコードとの判別材料）。"""
        history_file = tmp_path / "history.jsonl"
        entry = fe.record_evolve_diff_decision(
            skill_name="my-skill",
            after_content=GOOD_SKILL,
            diff_summary="x",
            human_accepted=True,
            history_file=history_file,
            decision_source="explicit_accept",
        )
        assert entry["decision_source"] == "explicit_accept"
        lines = history_file.read_text(encoding="utf-8").splitlines()
        assert json.loads(lines[0])["decision_source"] == "explicit_accept"

    def test_decision_source_defaults_to_none_when_omitted(self, tmp_path):
        """後方互換: 未指定時はキー欠落でなく明示 None（#376 migration の判別条件に使う）。"""
        history_file = tmp_path / "history.jsonl"
        entry = fe.record_evolve_diff_decision(
            skill_name="my-skill",
            after_content=GOOD_SKILL,
            diff_summary="x",
            human_accepted=True,
            history_file=history_file,
        )
        assert entry["decision_source"] is None

    def test_run_id_defaults_to_none_when_omitted(self, tmp_path):
        """run_id 未指定（既存呼び出し元との後方互換）は None（キー欠落でなく明示 None）。"""
        history_file = tmp_path / "history.jsonl"
        entry = fe.record_evolve_diff_decision(
            skill_name="my-skill",
            after_content=GOOD_SKILL,
            diff_summary="x",
            human_accepted=True,
            history_file=history_file,
        )
        assert entry["run_id"] is None

    def test_idempotent_ingest_by_id(self, tmp_path):
        """同一 id の二重記録は1行に保たれる（冪等）。"""
        history_file = tmp_path / "history.jsonl"
        e1 = fe.record_evolve_diff_decision(
            skill_name="my-skill", after_content=GOOD_SKILL, diff_summary="x",
            human_accepted=True, history_file=history_file, entry_id="fixed-id-1",
        )
        fe.record_evolve_diff_decision(
            skill_name="my-skill", after_content=GOOD_SKILL, diff_summary="x",
            human_accepted=True, history_file=history_file, entry_id="fixed-id-1",
        )
        lines = history_file.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 1
        assert e1["id"] == "fixed-id-1"

    def test_e2e_records_feed_correlation_analysis(self, tmp_path):
        """記録 → load → analyze_correlations が grouped 構造を返す。"""
        history_file = tmp_path / "history.jsonl"
        for i in range(25):
            fe.record_evolve_diff_decision(
                skill_name=f"skill-{i}", after_content=GOOD_SKILL, diff_summary="x",
                human_accepted=(i % 2 == 0), history_file=history_file,
            )
        history = fe.load_history(history_file)
        assert len(history) == 25
        result = fe.analyze_correlations(history)
        assert "by_fitness_func" in result
        assert "skill_quality" in result["by_fitness_func"]


# ========== ADR-031: history_file 未指定時に store 経由で per-slug 解決 ==========

class TestDefaultHistoryRoutesToStore:
    def test_record_without_history_file_lands_in_slug_file(self, tmp_path, monkeypatch):
        """history_file 未指定 → store の current slug ファイルに記録される（split-brain 解消）。"""
        import optimize_history_store as store
        monkeypatch.setattr(store, "HISTORY_ROOT", tmp_path / "optimize_history")
        monkeypatch.setattr(store, "resolve_slug", lambda cwd=None: "proj-x")

        fe.record_evolve_diff_decision(
            skill_name="s", after_content=GOOD_SKILL, diff_summary="x",
            human_accepted=True,
        )
        # store の per-slug ファイルに着地し、load_history(None) でも読める
        assert (tmp_path / "optimize_history" / "proj-x.jsonl").exists()
        history = fe.load_history()
        assert len(history) == 1
        assert history[0]["human_accepted"] is True


# ========== (c) fitness_func グループ化 ==========

class TestAnalyzeCorrelationsGrouping:
    def test_groups_by_fitness_func_no_mixing(self):
        """異種 fitness_func は混合せず別グループで相関を取る。"""
        history = []
        for i in range(25):
            history.append({"best_fitness": 0.5 + i * 0.01, "human_accepted": i % 2 == 0,
                            "fitness_func": "skill_quality"})
        for i in range(25):
            history.append({"best_fitness": 0.9, "human_accepted": True,
                            "fitness_func": "default"})
        result = fe.analyze_correlations(history)
        assert set(result["by_fitness_func"].keys()) == {"skill_quality", "default"}
        # 各グループの data_points が分離されている
        assert result["by_fitness_func"]["skill_quality"]["data_points"] == 25
        assert result["by_fitness_func"]["default"]["data_points"] == 25

    def test_excludes_none_best_fitness_from_population(self):
        """(a) best_fitness=None は相関母集団に入らない。"""
        history = [{"best_fitness": None, "human_accepted": True, "fitness_func": "skill_quality"}]
        history += [{"best_fitness": 0.5, "human_accepted": True, "fitness_func": "skill_quality"}]
        result = fe.analyze_correlations(history)
        assert result["by_fitness_func"]["skill_quality"]["data_points"] == 1

    def test_missing_fitness_func_defaults_to_unknown(self):
        history = [{"best_fitness": 0.5, "human_accepted": True}]
        result = fe.analyze_correlations(history)
        assert "unknown" in result["by_fitness_func"]


# ========== format_correlation_report 整形ヘルパー ==========

class TestFormatCorrelationReport:
    def test_multiple_fitness_funcs_rendered_independently(self):
        """複数 fitness_func が各グループ独立に出力される。"""
        correlation = {
            "by_fitness_func": {
                "skill_quality": {"data_points": 25, "correlation": 0.72, "sufficient_data": True},
                "default": {"data_points": 22, "correlation": 0.61, "sufficient_data": True},
            }
        }
        report = fe.format_correlation_report(correlation)
        assert "[skill_quality]" in report
        assert "[default]" in report
        assert "0.720" in report
        assert "0.610" in report
        # 高相関グループには警告が出ない
        assert "警告" not in report

    def test_empty_by_fitness_func_returns_no_data(self):
        assert fe.format_correlation_report({"by_fitness_func": {}}) == "相関データなし"
        assert fe.format_correlation_report({}) == "相関データなし"

    def test_low_correlation_group_emits_warning(self):
        """相関 < 0.50 のグループのみ警告（グループ単位）。"""
        correlation = {
            "by_fitness_func": {
                "skill_quality": {
                    "data_points": 25, "correlation": 0.30, "sufficient_data": True,
                    "warning": "score-acceptance 相関が 0.300 (< 0.5) に低下。評価関数の再キャリブレーション推奨。",
                },
                "default": {"data_points": 22, "correlation": 0.80, "sufficient_data": True},
            }
        }
        report = fe.format_correlation_report(correlation)
        lines = report.splitlines()
        # skill_quality に警告、default には付かない
        sq_idx = next(i for i, l in enumerate(lines) if "[skill_quality]" in l)
        assert "警告" in lines[sq_idx + 1]
        default_idx = next(i for i, l in enumerate(lines) if "[default]" in l)
        # default の次行は別グループか末尾で、警告ではない
        assert default_idx == len(lines) - 1 or "警告" not in lines[default_idx + 1]

    def test_none_correlation_shown_as_insufficient(self):
        """correlation=None（データ不足）は N/A 表示、警告は出さない。"""
        correlation = {
            "by_fitness_func": {
                "skill_quality": {"data_points": 5, "correlation": None, "sufficient_data": False},
            }
        }
        report = fe.format_correlation_report(correlation)
        assert "N/A" in report
        assert "警告" not in report


# ========== (a) insufficient_data メッセージ ==========

class TestInsufficientDataMessage:
    def test_message_names_population_sources(self):
        result = fe.run_fitness_evolution(history=[])
        assert result["status"] == "insufficient_data"
        assert "optimize" in result["details"]["message"]
        assert "evolve" in result["details"]["message"]


# ========== #376 AC6: fitness_eligible=False は母集団から除外 ==========

class TestFitnessEligibleExclusion:
    def test_fitness_ineligible_records_excluded_from_data_count(self):
        """無効化済み（legacy hash-proxy false positive）レコードは母集団に数えない。"""
        history = [
            {"best_fitness": 0.5, "human_accepted": True, "fitness_func": "skill_quality",
             "fitness_eligible": False},
        ] * 7  # #376 実測の legacy 7 件を模す
        result = fe.run_fitness_evolution(history=history)
        assert result["status"] == "insufficient_data"
        assert result["data_count"] == 0

    def test_fitness_eligible_true_records_still_counted(self):
        history = [
            {"best_fitness": 0.5, "human_accepted": True, "fitness_func": "skill_quality",
             "fitness_eligible": True},
        ] * 5
        result = fe.run_fitness_evolution(history=history)
        assert result["data_count"] == 5

    def test_records_without_fitness_eligible_key_default_to_included(self):
        """既存レコードの大半（このフィールド導入前）は後方互換で母集団に残る。"""
        history = [
            {"best_fitness": 0.5, "human_accepted": True, "fitness_func": "skill_quality"},
        ] * 5
        result = fe.run_fitness_evolution(history=history)
        assert result["data_count"] == 5
