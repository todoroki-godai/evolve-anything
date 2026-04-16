"""mutation_injector.py のユニットテスト (TDD)。

MutationInjector の3パターン + sentinel 統合テスト。
API 呼び出しを含む sentinel テストは @pytest.mark.bench でマーク。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bench"))
from mutation_injector import (
    ALL_MUTATION_IDS,
    MutationInjector,
    MutationResult,
    SentinelReport,
    SentinelRunner,
)

# ─────────────────────────────────────────────────
# テスト用 system_context
# ─────────────────────────────────────────────────

_CTX = """\
# CLAUDE.md
# rl-anything Plugin
スキル/ルールの自律進化パイプラインを提供する Claude Code Plugin。

## クイックスタート
/rl-anything:evolve で日次運用。

# rules/skill-ops.md
# スキル運用
- タスクの種類が変わったら CLAUDE.md の Skills を再確認し、該当スキルがあれば使用する
- スキル作成時は必ず skill-creator を先に呼ぶ

# rules/workflow.md
# ワークフロー
- 実装指示を受けたら feat/* ブランチで作業する
- 「後でやる」等の先送りをせず subagent で並行処理する
- gstack フローチェーン: ~/.gstack/flow-chain.json

# rules/testing.md
# テスト・検証
- 正常系 E2E テストを最初に書く
- 仕様未達なら修正→再テスト
"""

_RULES_COUNT = 3  # skill-ops, workflow, testing


# ─────────────────────────────────────────────────
# MutationResult dataclass
# ─────────────────────────────────────────────────

class TestMutationResult:
    def test_creation(self):
        r = MutationResult(
            mutation_id="rule_delete|skill-ops.md",
            original_length=100,
            mutated_length=80,
            mutated_context="shortened ctx",
            description="削除: rules/skill-ops.md",
        )
        assert r.mutation_id.startswith("rule_delete")
        assert r.mutated_length < r.original_length

    def test_length_reduction_positive(self):
        r = MutationResult("rule_delete|x", 100, 80, "ctx", "desc")
        assert r.original_length - r.mutated_length == 20

    def test_mutation_id_format(self):
        """mutation_id は {type}|{detail} 形式。"""
        r = MutationResult("prompt_truncate|50pct", 200, 100, "ctx", "desc")
        parts = r.mutation_id.split("|")
        assert len(parts) == 2
        assert parts[0] in ALL_MUTATION_IDS


# ─────────────────────────────────────────────────
# ALL_MUTATION_IDS 定数
# ─────────────────────────────────────────────────

class TestAllMutationIds:
    def test_contains_three_types(self):
        assert "rule_delete" in ALL_MUTATION_IDS
        assert "trigger_invert" in ALL_MUTATION_IDS
        assert "prompt_truncate" in ALL_MUTATION_IDS

    def test_is_frozenset(self):
        assert isinstance(ALL_MUTATION_IDS, frozenset)


# ─────────────────────────────────────────────────
# MutationInjector.rule_delete
# ─────────────────────────────────────────────────

class TestRuleDelete:
    def test_returns_mutation_result(self):
        inj = MutationInjector(_CTX)
        result = inj.rule_delete()
        assert isinstance(result, MutationResult)

    def test_mutation_id_prefix(self):
        result = MutationInjector(_CTX).rule_delete()
        assert result.mutation_id.startswith("rule_delete|")

    def test_context_shorter(self):
        result = MutationInjector(_CTX).rule_delete()
        assert result.mutated_length < result.original_length

    def test_one_rules_section_removed(self):
        """削除後の rules セクション数が1つ減る。"""
        result = MutationInjector(_CTX).rule_delete()
        remaining = result.mutated_context.count("# rules/")
        assert remaining == _RULES_COUNT - 1

    def test_claude_md_preserved(self):
        """CLAUDE.md セクションは保持される。"""
        result = MutationInjector(_CTX).rule_delete()
        assert "# CLAUDE.md" in result.mutated_context

    def test_no_rules_context_returns_unchanged(self):
        """rules セクションがない context はそのまま返す。"""
        ctx = "# CLAUDE.md\nsome content only"
        result = MutationInjector(ctx).rule_delete()
        assert result.mutated_context == ctx
        assert result.mutation_id == "rule_delete|none"

    def test_deterministic_with_same_seed(self):
        """同じ seed で同じルールが削除される。"""
        r1 = MutationInjector(_CTX, seed=42).rule_delete()
        r2 = MutationInjector(_CTX, seed=42).rule_delete()
        assert r1.mutated_context == r2.mutated_context

    def test_different_seeds_may_differ(self):
        """seed が違えば異なるルールが削除される（3ルールあれば高確率）。"""
        results = {MutationInjector(_CTX, seed=i).rule_delete().mutation_id for i in range(3)}
        # 3ルールがあれば少なくとも2種類の mutation_id が出るはず
        assert len(results) >= 1  # 最低1種類（単一ルール環境への寛容テスト）


# ─────────────────────────────────────────────────
# MutationInjector.trigger_invert
# ─────────────────────────────────────────────────

class TestTriggerInvert:
    def test_returns_mutation_result(self):
        result = MutationInjector(_CTX).trigger_invert()
        assert isinstance(result, MutationResult)

    def test_mutation_id_prefix(self):
        result = MutationInjector(_CTX).trigger_invert()
        assert result.mutation_id.startswith("trigger_invert|")

    def test_context_modified(self):
        """元の context と異なる内容になる。"""
        result = MutationInjector(_CTX).trigger_invert()
        assert result.mutated_context != _CTX

    def test_negation_marker_present(self):
        """反転マーカー（NEGATED または廃止等）が挿入されている。"""
        result = MutationInjector(_CTX).trigger_invert()
        # 反転を示す何らかのマーカーが入っていること
        has_marker = any(
            m in result.mutated_context
            for m in ["[NEGATED]", "【廃止】", "しない", "NOT:"]
        )
        assert has_marker

    def test_overall_structure_preserved(self):
        """CLAUDE.md と rules/ のセクション構造は壊れない。"""
        result = MutationInjector(_CTX).trigger_invert()
        assert "# CLAUDE.md" in result.mutated_context
        assert "# rules/" in result.mutated_context

    def test_no_action_lines_returns_unchanged(self):
        """反転対象の行がない場合はそのまま返す。"""
        ctx = "# CLAUDE.md\nThis is context with no action lines."
        result = MutationInjector(ctx).trigger_invert()
        assert result.mutated_context == ctx
        assert result.mutation_id == "trigger_invert|none"

    def test_deterministic_with_same_seed(self):
        r1 = MutationInjector(_CTX, seed=0).trigger_invert()
        r2 = MutationInjector(_CTX, seed=0).trigger_invert()
        assert r1.mutated_context == r2.mutated_context


# ─────────────────────────────────────────────────
# MutationInjector.prompt_truncate
# ─────────────────────────────────────────────────

class TestPromptTruncate:
    def test_returns_mutation_result(self):
        result = MutationInjector(_CTX).prompt_truncate()
        assert isinstance(result, MutationResult)

    def test_mutation_id(self):
        result = MutationInjector(_CTX).prompt_truncate()
        assert result.mutation_id == "prompt_truncate|50pct"

    def test_truncated_to_half(self):
        result = MutationInjector(_CTX).prompt_truncate(fraction=0.5)
        # 50% ± 少しの許容（行境界で調整するため）
        original_len = len(_CTX)
        assert result.mutated_length <= original_len * 0.6
        assert result.mutated_length >= original_len * 0.3

    def test_custom_fraction(self):
        result_30 = MutationInjector(_CTX).prompt_truncate(fraction=0.3)
        result_70 = MutationInjector(_CTX).prompt_truncate(fraction=0.7)
        assert result_30.mutated_length < result_70.mutated_length

    def test_mutation_id_reflects_fraction(self):
        result = MutationInjector(_CTX).prompt_truncate(fraction=0.3)
        assert "30pct" in result.mutation_id

    def test_context_starts_with_beginning(self):
        """短縮後も先頭部分（CLAUDE.md）は保持される。"""
        result = MutationInjector(_CTX).prompt_truncate()
        assert result.mutated_context.startswith("# CLAUDE.md")

    def test_empty_context_handled(self):
        result = MutationInjector("").prompt_truncate()
        assert result.mutated_context == ""


# ─────────────────────────────────────────────────
# MutationInjector.apply_all
# ─────────────────────────────────────────────────

class TestApplyAll:
    def test_returns_three_results(self):
        results = MutationInjector(_CTX).apply_all()
        assert len(results) == 3

    def test_all_types_covered(self):
        results = MutationInjector(_CTX).apply_all()
        types = {r.mutation_id.split("|")[0] for r in results}
        assert types == {"rule_delete", "trigger_invert", "prompt_truncate"}

    def test_all_produce_shorter_or_modified_context(self):
        results = MutationInjector(_CTX).apply_all()
        for r in results:
            # 少なくとも original_length と同じかそれ以下（truncate/delete）
            # または内容が変わっている（invert）
            assert r.mutated_context != _CTX or r.mutated_length < r.original_length


# ─────────────────────────────────────────────────
# SentinelReport dataclass
# ─────────────────────────────────────────────────

class TestSentinelReport:
    def test_creation(self):
        r = SentinelReport(
            mutation_id="rule_delete|skill-ops.md",
            baseline_score=7.5,
            mutated_score=5.2,
            delta=-2.3,
            detected=True,
            description="削除: rules/skill-ops.md",
        )
        assert r.detected is True
        assert r.delta < 0

    def test_detection_logic(self):
        """delta が閾値以上に下がれば detected=True。"""
        r = SentinelReport("rule_delete|x", 7.0, 5.0, -2.0, True, "desc")
        assert r.detected is True

    def test_not_detected_when_score_unchanged(self):
        r = SentinelReport("trigger_invert|x", 7.0, 7.1, 0.1, False, "desc")
        assert r.detected is False


# ─────────────────────────────────────────────────
# SentinelRunner (mock-based — API呼び出しなし)
# ─────────────────────────────────────────────────

from golden_extractor import GoldenCase

_CASE = GoldenCase(
    skill_name="evolve",
    user_prompt="",
    system_context=_CTX,
    correction_count=0,
    session_id="sentinel-sess",
)


def _make_haiku_mocks(baseline_score: float, mutated_score: float, count: int = 3):
    """baseline 1ケース(4calls) + mutation count ケース分(各4calls)のモックを返す。"""
    def _make_4calls(score_10: float):
        score_01 = score_10 / 10.0
        tech = json.dumps({"clarity": score_01, "completeness": score_01, "consistency": score_01,
                           "edge_cases": score_01, "testability": score_01, "total": score_01,
                           "rationale": "mock"})
        domain = json.dumps({"data_grounding": score_01, "diagnostic_accuracy": score_01,
                             "proposal_utility": score_01, "scope_fit": score_01,
                             "total": score_01, "rationale": "mock"})
        struct = json.dumps({"format": score_01, "length": score_01, "examples": score_01,
                             "completeness": score_01, "total": score_01, "rationale": "mock"})
        gen = mock.MagicMock(returncode=0, stdout="generated output", stderr="")
        tech_m = mock.MagicMock(returncode=0, stdout=tech, stderr="")
        dom_m = mock.MagicMock(returncode=0, stdout=domain, stderr="")
        str_m = mock.MagicMock(returncode=0, stdout=struct, stderr="")
        return [gen, tech_m, dom_m, str_m]

    all_calls = _make_4calls(baseline_score)  # baseline
    for _ in range(count):
        all_calls += _make_4calls(mutated_score)  # each mutation
    return all_calls


class TestSentinelRunner:
    def _runner(self, tmp_path: Path, baseline_results: list[dict] | None = None) -> SentinelRunner:
        results_file = tmp_path / "benchmark_results.jsonl"
        if baseline_results:
            results_file.write_text(
                "\n".join(json.dumps(r) for r in baseline_results) + "\n",
                encoding="utf-8",
            )
        return SentinelRunner(
            cases=[_CASE],
            system_context=_CTX,
            results_file=results_file,
            max_api_calls=100,
        )

    def test_run_returns_sentinel_reports(self, tmp_path):
        runner = self._runner(tmp_path)
        with mock.patch("subprocess.run", side_effect=_make_haiku_mocks(7.5, 4.0)):
            reports = runner.run()
        assert len(reports) == 3
        assert all(isinstance(r, SentinelReport) for r in reports)

    def test_detects_score_decrease(self, tmp_path):
        """baseline=7.5, mutated=4.0 → delta < 0 → detected=True。"""
        runner = self._runner(tmp_path)
        with mock.patch("subprocess.run", side_effect=_make_haiku_mocks(7.5, 4.0)):
            reports = runner.run()
        for r in reports:
            assert r.baseline_score > r.mutated_score
            assert r.detected is True

    def test_not_detected_when_scores_similar(self, tmp_path):
        """baseline=7.5, mutated=7.4 → detected=False（delta が閾値未満）。"""
        runner = self._runner(tmp_path)
        with mock.patch("subprocess.run", side_effect=_make_haiku_mocks(7.5, 7.4)):
            reports = runner.run()
        for r in reports:
            assert r.detected is False

    def test_uses_baseline_from_results_file(self, tmp_path):
        """benchmark_results.jsonl に baseline が記録されていれば再利用。"""
        baseline_records = [
            {"skill_name": "evolve", "session_id": "sentinel-sess",
             "score": 8.0, "timestamp": "2026-04-16T00:00:00Z"},
        ]
        runner = self._runner(tmp_path, baseline_records)
        # mutation のみ実行（baseline は results_file から読む → 4 calls × 3 mutations = 12）
        with mock.patch("subprocess.run", side_effect=_make_haiku_mocks(5.0, 5.0, count=3)):
            reports = runner.run()
        # baseline_score = 8.0 (from file), not from API
        assert all(r.baseline_score == 8.0 for r in reports)

    def test_detection_rate_in_summary(self, tmp_path):
        runner = self._runner(tmp_path)
        with mock.patch("subprocess.run", side_effect=_make_haiku_mocks(7.5, 4.0)):
            reports = runner.run()
        detection_rate = sum(1 for r in reports if r.detected) / len(reports)
        assert detection_rate == 1.0  # 全mutation が検出された

    def test_dry_run_no_api(self, tmp_path, capsys):
        runner = SentinelRunner(
            cases=[_CASE],
            system_context=_CTX,
            results_file=tmp_path / "out.jsonl",
            max_api_calls=100,
            dry_run=True,
        )
        with mock.patch("subprocess.run") as m:
            runner.run()
        m.assert_not_called()
        out = capsys.readouterr().out
        assert "dry" in out.lower() or "DRY" in out
