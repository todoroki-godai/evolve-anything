#!/usr/bin/env python3
"""loop_ablation.py のテスト（#234 PR3: 設計文脈 vs naive 生成比較の較正実験、opt-in CLI）。

LLM 呼び出し（generate_candidate / run_loop._score_variant_axes）は必ず mock する
（no-llm-in-tests）。mock 位置は loop_ablation モジュール自身の名前空間
（`from optimize_core import generate_candidate` で束縛された名前・
`import run_loop as _run_loop` で束縛されたモジュール参照）で、
variant_generation.py のテストと同型（「テストする層の1つ下」を mock）。
"""
import importlib.util
import io
import json
from pathlib import Path
from unittest.mock import patch

import pytest

# loop_ablation.py を importlib で読み込む（test_variant_generation.py と同じパターン）
spec = importlib.util.spec_from_file_location(
    "loop_ablation",
    Path(__file__).parent.parent / "scripts" / "loop_ablation.py",
)
loop_ablation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(loop_ablation)


SKILL_CONTENT = (
    "---\ndescription: テストスキル\n---\n\n# テストスキル\n"
    "## Usage\nテスト内容です。\n"
)


def _make_skill(tmp_path: Path, name: str = "SKILL.md", content: str = SKILL_CONTENT) -> Path:
    skill_file = tmp_path / name
    skill_file.write_text(content, encoding="utf-8")
    return skill_file


@pytest.fixture(autouse=True)
def _isolate_corrections(tmp_path, monkeypatch):
    """既定では corrections.jsonl を「存在しないパス」に向け、実環境の
    corrections データがテストに紛れ込まないようにする
    （variant_generation.py のテストと同じ防御パターン）。
    """
    monkeypatch.setattr(loop_ablation, "_CORRECTIONS_PATH", tmp_path / "no-corrections.jsonl")


def _passed_candidate():
    """optimize_core.generate_candidate の合格戻り値を模す。"""
    return {"content": "# Improved\n改善\n", "passed": True, "gate_reason": None, "fitness": None}


def _failed_candidate():
    return {"content": None, "passed": False, "gate_reason": "bad", "fitness": None}


def _fake_axes(base: float):
    return {"technical": base, "domain": base, "structure": base, "integrated": round(base, 4)}


class TestDryRunNoLLM:
    """keystone regression test: dry-run で LLM 呼び出しゼロ。"""

    def test_dry_run_no_generate_candidate_or_scoring_calls(self, tmp_path):
        skill_file = _make_skill(tmp_path)
        with patch.object(loop_ablation, "generate_candidate") as mock_gen, \
             patch.object(loop_ablation._run_loop, "_score_variant_axes") as mock_score:
            result = loop_ablation.run_ablation(str(skill_file), n=3, run=False)
        assert mock_gen.call_count == 0
        assert mock_score.call_count == 0
        assert result["dry_run"] is True

    def test_dry_run_shows_comparability_result(self, tmp_path):
        skill_file = _make_skill(tmp_path)
        out = io.StringIO()
        result = loop_ablation.run_ablation(str(skill_file), n=3, run=False, out=out)
        assert "comparability" in result
        assert "comparable" in result["comparability"]
        assert "比較可能性" in out.getvalue()

    def test_dry_run_shows_cost_estimate(self, tmp_path):
        skill_file = _make_skill(tmp_path)
        result = loop_ablation.run_ablation(str(skill_file), n=3, run=False)
        assert result["cost"]["generation_calls"] == 6
        assert result["cost"]["scoring_calls"] == 18


class TestCollectCalledOnce:
    """collect_corrections/collect_context が designed/naive で重複せず1回のみ呼ばれる。"""

    def test_collect_functions_called_once_in_dry_run(self, tmp_path):
        skill_file = _make_skill(tmp_path)
        with patch.object(
            loop_ablation, "collect_corrections", wraps=loop_ablation.collect_corrections
        ) as mock_corr, patch.object(
            loop_ablation, "collect_context", wraps=loop_ablation.collect_context
        ) as mock_ctx:
            loop_ablation.run_ablation(str(skill_file), n=3, run=False)
        assert mock_corr.call_count == 1
        assert mock_ctx.call_count == 1


class TestComparabilityGate:
    def test_run_without_force_aborts_when_incomparable(self, tmp_path):
        skill_file = _make_skill(tmp_path)
        incomparable = {
            "comparable": False,
            "prompt_diff_chars": 0,
            "corrections_count": 0,
            "context_signals": [],
            "reason": "designed prompt と naive prompt が実質同一",
        }
        with patch.object(
            loop_ablation._stats, "assess_comparability", return_value=incomparable
        ), patch.object(loop_ablation, "generate_candidate") as mock_gen, patch.object(
            loop_ablation._run_loop, "_score_variant_axes"
        ) as mock_score:
            result = loop_ablation.run_ablation(str(skill_file), n=3, run=True, force=False)
        assert result["aborted"] is True
        assert mock_gen.call_count == 0
        assert mock_score.call_count == 0

    def test_run_with_force_proceeds_and_marks_forced(self, tmp_path):
        skill_file = _make_skill(tmp_path)
        incomparable = {
            "comparable": False,
            "prompt_diff_chars": 0,
            "corrections_count": 0,
            "context_signals": [],
            "reason": "designed prompt と naive prompt が実質同一",
        }
        with patch.object(
            loop_ablation._stats, "assess_comparability", return_value=incomparable
        ), patch.object(
            loop_ablation, "generate_candidate", return_value=_passed_candidate()
        ) as mock_gen, patch.object(
            loop_ablation._run_loop, "_score_variant_axes", return_value=_fake_axes(0.7)
        ):
            result = loop_ablation.run_ablation(str(skill_file), n=2, run=True, force=True)
        assert result["forced"] is True
        assert mock_gen.call_count == 4  # designed x2 + naive x2


class TestSingleVariableIsolation:
    """designed/naive で prompt 引数のみ差替え、他の generate_candidate 引数は完全同一。"""

    def test_designed_naive_call_counts_and_prompt_isolation(self, tmp_path):
        skill_file = _make_skill(tmp_path)
        marker = "CORRECTION_MARKER_XYZ"
        corrections = [{"message": marker, "correction_type": "t", "extracted_learning": ""}]

        with patch.object(
            loop_ablation, "collect_corrections", return_value=corrections
        ) as mock_corr, patch.object(
            loop_ablation, "collect_context", return_value={}
        ) as mock_ctx, patch.object(
            loop_ablation, "generate_candidate", return_value=_passed_candidate()
        ) as mock_gen, patch.object(
            loop_ablation._run_loop, "_score_variant_axes", return_value=_fake_axes(0.7)
        ):
            result = loop_ablation.run_ablation(str(skill_file), n=2, run=True, force=True)

        assert mock_corr.call_count == 1
        assert mock_ctx.call_count == 1
        assert mock_gen.call_count == 4
        assert result["designed_passed"] == 2
        assert result["naive_passed"] == 2

        designed_calls = [c for c in mock_gen.call_args_list if marker in c.args[0]]
        naive_calls = [c for c in mock_gen.call_args_list if marker not in c.args[0]]
        assert len(designed_calls) == 2
        assert len(naive_calls) == 2

        # 他の引数（original_content/claude_cwd/max_lines/pitfall_path/max_chars）は
        # designed/naive 両条件で完全同一（単一変数分離: 違いは prompt 引数だけ）。
        other_args = {c.args[1:] for c in mock_gen.call_args_list}
        assert len(other_args) == 1


class TestGateFiltering:
    def test_failed_candidates_excluded_from_scoring(self, tmp_path):
        skill_file = _make_skill(tmp_path)
        call_count = [0]

        def fake_generate(*args, **kwargs):
            call_count[0] += 1
            # 8回中2回（3,6番目）を不合格にする（何番目が designed/naive のどの候補に
            # 割り当たるかはスレッド順で変わるが、不合格数は常に2件で決定論的）。
            if call_count[0] % 3 == 0:
                return _failed_candidate()
            return _passed_candidate()

        with patch.object(loop_ablation, "generate_candidate", side_effect=fake_generate), \
             patch.object(
                 loop_ablation._run_loop, "_score_variant_axes", return_value=_fake_axes(0.7)
             ) as mock_score:
            result = loop_ablation.run_ablation(str(skill_file), n=4, run=True, force=True)

        total_passed = result["designed_passed"] + result["naive_passed"]
        assert total_passed == 6
        assert mock_score.call_count == 6


class TestZeroPassed:
    def test_zero_passed_both_conditions_no_crash(self, tmp_path):
        skill_file = _make_skill(tmp_path)
        with patch.object(
            loop_ablation, "generate_candidate", return_value=_failed_candidate()
        ), patch.object(loop_ablation._run_loop, "_score_variant_axes") as mock_score:
            result = loop_ablation.run_ablation(str(skill_file), n=3, run=True, force=True)
        assert result["designed_passed"] == 0
        assert result["naive_passed"] == 0
        mock_score.assert_not_called()
        assert result["comparison"]["verdict"] == "inconclusive"


class TestReadOnly:
    def test_target_file_unchanged_after_run(self, tmp_path):
        skill_file = _make_skill(tmp_path)
        original = skill_file.read_text(encoding="utf-8")
        with patch.object(
            loop_ablation, "generate_candidate", return_value=_passed_candidate()
        ), patch.object(
            loop_ablation._run_loop, "_score_variant_axes", return_value=_fake_axes(0.7)
        ):
            loop_ablation.run_ablation(str(skill_file), n=2, run=True, force=True)
        assert skill_file.read_text(encoding="utf-8") == original


class TestJSONOutput:
    def test_json_output_parses_and_has_keys(self, tmp_path, capsys):
        skill_file = _make_skill(tmp_path)
        with patch.object(
            loop_ablation, "generate_candidate", return_value=_passed_candidate()
        ), patch.object(
            loop_ablation._run_loop, "_score_variant_axes", return_value=_fake_axes(0.7)
        ):
            loop_ablation.main(
                ["--target", str(skill_file), "--n", "2", "--run", "--force", "--json"]
            )
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert "comparability" in data
        assert "cost" in data
        assert "comparison" in data
        assert "forced" in data


class TestErrorHandling:
    def test_missing_target_returns_error_dict(self, tmp_path):
        missing = tmp_path / "does-not-exist.md"
        result = loop_ablation.run_ablation(str(missing), n=2, run=False)
        assert "error" in result

    def test_main_exits_nonzero_on_missing_target(self, tmp_path, capsys):
        missing = tmp_path / "does-not-exist.md"
        with pytest.raises(SystemExit) as exc_info:
            loop_ablation.main(["--target", str(missing)])
        assert exc_info.value.code == 1
