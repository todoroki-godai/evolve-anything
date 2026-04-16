"""run_benchmark.py と output_evaluator.py のユニットテスト (TDD)。

API 実呼び出しを含むテストは @pytest.mark.bench でマーク。
通常の CI では -m "not bench" で除外する。
"""
from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import asdict
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bench"))
from output_evaluator import AxisScores, OutputEvaluator
from run_benchmark import (
    CONSIDERATION_SKILLS,
    BenchmarkResult,
    BenchmarkRunner,
    _compute_harness_hash,
    _load_previous_score,
)

# ─────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────

def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n",
        encoding="utf-8",
    )


def _mock_haiku_technical(prompt: str) -> str:
    return json.dumps({
        "clarity": 0.8, "completeness": 0.7, "consistency": 0.8,
        "edge_cases": 0.6, "testability": 0.65, "total": 0.72,
        "rationale": "mock technical",
    })


def _mock_haiku_domain(prompt: str) -> str:
    return json.dumps({
        "data_grounding": 0.85, "diagnostic_accuracy": 0.80,
        "proposal_utility": 0.75, "scope_fit": 0.80, "total": 0.80,
        "rationale": "mock domain",
    })


def _mock_haiku_structure(prompt: str) -> str:
    return json.dumps({
        "format": 0.85, "length": 0.70, "examples": 0.75,
        "completeness": 0.80, "total": 0.775,
        "rationale": "mock structure",
    })


def _make_subprocess_mock(outputs: list[str]):
    """複数の haiku レスポンスを順番に返す subprocess.run モック。"""
    call_results = [
        mock.MagicMock(returncode=0, stdout=o, stderr="")
        for o in outputs
    ]
    return mock.patch(
        "subprocess.run",
        side_effect=call_results,
    )


# ─────────────────────────────────────────────────
# BenchmarkResult dataclass
# ─────────────────────────────────────────────────

class TestBenchmarkResult:
    def test_creation(self):
        r = BenchmarkResult(
            skill_name="evolve",
            session_id="sess-1",
            score=7.5,
            score_pre=None,
            delta=None,
            harness_hash="sha256:abc",
            mutation_id="null",
            timestamp="2026-04-16T00:00:00Z",
            model="claude-haiku-4-5",
        )
        assert r.skill_name == "evolve"
        assert r.score == 7.5

    def test_serialization_has_all_fields(self):
        r = BenchmarkResult(
            skill_name="reflect",
            session_id="sess-2",
            score=6.2,
            score_pre=5.8,
            delta=0.4,
            harness_hash="sha256:xyz",
            mutation_id="null",
            timestamp="2026-04-16T00:00:00Z",
            model="claude-haiku-4-5",
        )
        d = asdict(r)
        expected_keys = {
            "skill_name", "session_id", "score", "score_pre", "delta",
            "harness_hash", "mutation_id", "timestamp", "model",
        }
        assert set(d.keys()) == expected_keys

    def test_score_range_0_to_10(self):
        r = BenchmarkResult("evolve", "s", 10.0, None, None, "h", "null", "t", "m")
        assert 0.0 <= r.score <= 10.0

    def test_delta_is_none_when_no_previous(self):
        r = BenchmarkResult("evolve", "s", 7.0, None, None, "h", "null", "t", "m")
        assert r.delta is None
        assert r.score_pre is None


# ─────────────────────────────────────────────────
# _compute_harness_hash
# ─────────────────────────────────────────────────

class TestHarnessHash:
    def test_returns_sha256_prefix(self):
        h = _compute_harness_hash("some context")
        assert h.startswith("sha256:")

    def test_stable_for_same_content(self):
        assert _compute_harness_hash("ctx") == _compute_harness_hash("ctx")

    def test_different_content_different_hash(self):
        assert _compute_harness_hash("ctx-a") != _compute_harness_hash("ctx-b")

    def test_hash_length(self):
        h = _compute_harness_hash("test")
        assert len(h) == len("sha256:") + 64  # SHA-256 hex = 64 chars


# ─────────────────────────────────────────────────
# _load_previous_score
# ─────────────────────────────────────────────────

class TestLoadPreviousScore:
    def test_returns_none_when_no_file(self, tmp_path):
        score = _load_previous_score(tmp_path / "none.jsonl", "evolve", "sess-A")
        assert score is None

    def test_returns_none_when_no_matching_record(self, tmp_path):
        path = tmp_path / "results.jsonl"
        _write_jsonl(path, [
            {"skill_name": "reflect", "session_id": "sess-A", "score": 5.0},
        ])
        score = _load_previous_score(path, "evolve", "sess-A")
        assert score is None

    def test_returns_latest_score_for_matching(self, tmp_path):
        path = tmp_path / "results.jsonl"
        _write_jsonl(path, [
            {"skill_name": "evolve", "session_id": "sess-A", "score": 5.0, "timestamp": "2026-04-01T00:00:00Z"},
            {"skill_name": "evolve", "session_id": "sess-A", "score": 7.2, "timestamp": "2026-04-10T00:00:00Z"},
        ])
        score = _load_previous_score(path, "evolve", "sess-A")
        assert score == 7.2  # 最新（最後）の score を返す

    def test_ignores_different_session(self, tmp_path):
        path = tmp_path / "results.jsonl"
        _write_jsonl(path, [
            {"skill_name": "evolve", "session_id": "sess-B", "score": 9.0},
        ])
        score = _load_previous_score(path, "evolve", "sess-A")
        assert score is None


# ─────────────────────────────────────────────────
# AxisScores
# ─────────────────────────────────────────────────

class TestAxisScores:
    def test_creation(self):
        s = AxisScores(technical=0.7, domain=0.8, structure=0.75)
        assert s.technical == 0.7

    def test_integrated_score(self):
        s = AxisScores(technical=0.7, domain=0.8, structure=0.75)
        # 0.7*0.4 + 0.8*0.4 + 0.75*0.2 = 0.28 + 0.32 + 0.15 = 0.75
        assert abs(s.integrated() - 0.75) < 1e-9

    def test_to_score_10(self):
        s = AxisScores(technical=0.7, domain=0.8, structure=0.75)
        assert abs(s.to_score_10() - 7.5) < 1e-9

    def test_parse_error_gives_min_score(self):
        """parse error 時は 0.05 最低値（0.0 ではない）を返す。"""
        s = AxisScores(technical=0.0, domain=0.0, structure=0.0, has_error=True)
        # integrated = 0.0 but min clamped
        assert s.integrated(min_on_error=0.05) == 0.05


# ─────────────────────────────────────────────────
# OutputEvaluator
# ─────────────────────────────────────────────────

SYSTEM_CTX = "# CLAUDE.md stub"

class TestOutputEvaluator:
    def test_evaluate_returns_axis_scores(self):
        evaluator = OutputEvaluator(system_context=SYSTEM_CTX)
        with _make_subprocess_mock([
            _mock_haiku_technical(""),
            _mock_haiku_domain(""),
            _mock_haiku_structure(""),
        ]):
            scores = evaluator.evaluate("evolve", "sample output text")
        assert isinstance(scores, AxisScores)
        assert 0.0 <= scores.technical <= 1.0
        assert 0.0 <= scores.domain <= 1.0
        assert 0.0 <= scores.structure <= 1.0

    def test_integrated_score_in_range(self):
        evaluator = OutputEvaluator(system_context=SYSTEM_CTX)
        with _make_subprocess_mock([
            _mock_haiku_technical(""),
            _mock_haiku_domain(""),
            _mock_haiku_structure(""),
        ]):
            scores = evaluator.evaluate("reflect", "some output")
        assert 0.0 <= scores.integrated() <= 1.0

    def test_parse_error_fallback(self):
        """haiku が JSON でない返答をした場合、has_error=True で最低スコア。"""
        evaluator = OutputEvaluator(system_context=SYSTEM_CTX)
        with _make_subprocess_mock(["not json", "not json", "not json"]):
            scores = evaluator.evaluate("evolve", "output")
        assert scores.has_error is True
        assert scores.integrated(min_on_error=0.05) == 0.05

    def test_api_call_count(self):
        """evaluate 1回で haiku を3回呼ぶ（3軸）。"""
        evaluator = OutputEvaluator(system_context=SYSTEM_CTX)
        with _make_subprocess_mock([
            _mock_haiku_technical(""),
            _mock_haiku_domain(""),
            _mock_haiku_structure(""),
        ]) as m:
            evaluator.evaluate("evolve", "output")
        assert m.call_count == 3


# ─────────────────────────────────────────────────
# BenchmarkRunner
# ─────────────────────────────────────────────────

from golden_extractor import GoldenCase


def _make_case(skill_name: str, session_id: str, correction_count: int = 0) -> GoldenCase:
    return GoldenCase(
        skill_name=skill_name,
        user_prompt="",
        system_context=SYSTEM_CTX,
        correction_count=correction_count,
        session_id=session_id,
    )


class TestBenchmarkRunner:
    def _make_runner(self, tmp_path: Path, max_api_calls: int = 100) -> BenchmarkRunner:
        return BenchmarkRunner(
            output_file=tmp_path / "benchmark_results.jsonl",
            system_context=SYSTEM_CTX,
            max_api_calls=max_api_calls,
        )

    def test_run_produces_results(self, tmp_path):
        runner = self._make_runner(tmp_path)
        cases = [_make_case("evolve", "sess-A")]
        # generation call (1) + 3-axis evaluation (3) = 4 calls
        with _make_subprocess_mock([
            "mock evolve output",               # generation
            _mock_haiku_technical(""),          # technical
            _mock_haiku_domain(""),             # domain
            _mock_haiku_structure(""),          # structure
        ]):
            results = runner.run(cases)
        assert len(results) == 1
        assert results[0].skill_name == "evolve"
        assert results[0].session_id == "sess-A"

    def test_run_saves_to_file(self, tmp_path):
        runner = self._make_runner(tmp_path)
        cases = [_make_case("evolve", "sess-A")]
        with _make_subprocess_mock([
            "mock output",
            _mock_haiku_technical(""),
            _mock_haiku_domain(""),
            _mock_haiku_structure(""),
        ]):
            runner.run(cases)
        out = tmp_path / "benchmark_results.jsonl"
        assert out.exists()
        lines = [l for l in out.read_text().splitlines() if l.strip()]
        assert len(lines) == 1

    def test_score_is_0_to_10(self, tmp_path):
        runner = self._make_runner(tmp_path)
        cases = [_make_case("reflect", "sess-B")]
        with _make_subprocess_mock([
            "output",
            _mock_haiku_technical(""),
            _mock_haiku_domain(""),
            _mock_haiku_structure(""),
        ]):
            results = runner.run(cases)
        assert 0.0 <= results[0].score <= 10.0

    def test_harness_hash_stable(self, tmp_path):
        runner = self._make_runner(tmp_path)
        cases = [_make_case("evolve", "sess-A"), _make_case("evolve", "sess-B")]
        with _make_subprocess_mock([
            "out1", _mock_haiku_technical(""), _mock_haiku_domain(""), _mock_haiku_structure(""),
            "out2", _mock_haiku_technical(""), _mock_haiku_domain(""), _mock_haiku_structure(""),
        ]):
            results = runner.run(cases)
        # 同一 system_context → 同一ハッシュ
        assert results[0].harness_hash == results[1].harness_hash

    def test_mutation_id_is_null_for_normal_run(self, tmp_path):
        runner = self._make_runner(tmp_path)
        cases = [_make_case("evolve", "sess-A")]
        with _make_subprocess_mock([
            "out", _mock_haiku_technical(""), _mock_haiku_domain(""), _mock_haiku_structure(""),
        ]):
            results = runner.run(cases)
        assert results[0].mutation_id == "null"

    def test_score_pre_from_previous_results(self, tmp_path):
        """前回の benchmark_results.jsonl があれば score_pre を設定する。"""
        out = tmp_path / "benchmark_results.jsonl"
        _write_jsonl(out, [
            {"skill_name": "evolve", "session_id": "sess-A", "score": 6.5,
             "timestamp": "2026-04-15T00:00:00Z"},
        ])
        runner = self._make_runner(tmp_path)
        cases = [_make_case("evolve", "sess-A")]
        with _make_subprocess_mock([
            "out", _mock_haiku_technical(""), _mock_haiku_domain(""), _mock_haiku_structure(""),
        ]):
            results = runner.run(cases)
        assert results[0].score_pre == 6.5
        assert results[0].delta is not None

    def test_max_api_calls_limit(self, tmp_path):
        """max_api_calls を超えたらそれ以上実行しない。"""
        # 4 calls per case (1 gen + 3 eval). max=4 → 1ケースのみ
        runner = self._make_runner(tmp_path, max_api_calls=4)
        cases = [
            _make_case("evolve", "sess-A"),
            _make_case("reflect", "sess-B"),  # これは実行されない
        ]
        with _make_subprocess_mock([
            "out1", _mock_haiku_technical(""), _mock_haiku_domain(""), _mock_haiku_structure(""),
            # sess-B 分は呼ばれない
        ]):
            results = runner.run(cases)
        assert len(results) == 1
        assert results[0].session_id == "sess-A"

    def test_dry_run_no_api_calls(self, tmp_path):
        """dry_run=True なら API 呼び出しをせず None を返す。"""
        runner = BenchmarkRunner(
            output_file=tmp_path / "out.jsonl",
            system_context=SYSTEM_CTX,
            max_api_calls=100,
            dry_run=True,
        )
        cases = [_make_case("evolve", "sess-A"), _make_case("reflect", "sess-B")]
        with mock.patch("subprocess.run") as m:
            results = runner.run(cases)
        m.assert_not_called()
        assert results == []

    def test_dry_run_prints_plan(self, tmp_path, capsys):
        runner = BenchmarkRunner(
            output_file=tmp_path / "out.jsonl",
            system_context=SYSTEM_CTX,
            max_api_calls=100,
            dry_run=True,
        )
        cases = [_make_case("evolve", "sess-A"), _make_case("reflect", "sess-B")]
        runner.run(cases)
        captured = capsys.readouterr()
        assert "dry_run" in captured.out.lower() or "DRY" in captured.out

    def test_haiku_generation_failure_skipped(self, tmp_path):
        """haiku 出力生成が失敗した場合、そのケースはスキップ。"""
        runner = self._make_runner(tmp_path)
        cases = [_make_case("evolve", "sess-A")]
        fail = mock.MagicMock(returncode=1, stdout="", stderr="error")
        with mock.patch("subprocess.run", return_value=fail):
            results = runner.run(cases)
        assert results == []

    def test_filter_consideration_skills(self, tmp_path):
        """action 系スキル（ship/browse/qa）はオフライン評価対象外。"""
        runner = self._make_runner(tmp_path)
        cases = [
            _make_case("evolve", "sess-A"),   # 対象
            _make_case("ship", "sess-B"),     # 対象外
            _make_case("reflect", "sess-C"),  # 対象
        ]
        with _make_subprocess_mock([
            "out1", _mock_haiku_technical(""), _mock_haiku_domain(""), _mock_haiku_structure(""),
            "out2", _mock_haiku_technical(""), _mock_haiku_domain(""), _mock_haiku_structure(""),
        ]):
            results = runner.run(cases)
        skill_names = [r.skill_name for r in results]
        assert "ship" not in skill_names
        assert "evolve" in skill_names
        assert "reflect" in skill_names


# ─────────────────────────────────────────────────
# CONSIDERATION_SKILLS 定数
# ─────────────────────────────────────────────────

class TestConsiderationSkills:
    def test_includes_core_skills(self):
        for s in ("evolve", "reflect", "optimize", "audit"):
            assert s in CONSIDERATION_SKILLS

    def test_excludes_action_skills(self):
        for s in ("ship", "browse", "qa"):
            assert s not in CONSIDERATION_SKILLS
