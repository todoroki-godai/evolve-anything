#!/usr/bin/env python3
"""variant_generation.py のテスト（#234 PR1: 配線drift修理）

背景: 旧 generate_variants() は optimize.py を
`--generations 1 --population <N>` で subprocess 呼び出ししていたが、
これらのオプションは optimize.py 側で廃止済み (`_DEPRECATED_OPTIONS`) のため
dry-run 含め常時失敗していた。既存テストは generate_variants 自体を mock して
いたためこのバグは検出されずに埋もれていた。本テストは低レベル関数
(collect_corrections/collect_context/determine_strategy/build_patch_prompt/
generate_candidate) を直接 import する新実装を検証する。
"""
import importlib.util
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# variant_generation.py を importlib で読み込む（test_loop.py と同じパターン）
spec = importlib.util.spec_from_file_location(
    "variant_generation",
    Path(__file__).parent.parent / "scripts" / "variant_generation.py",
)
variant_generation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(variant_generation)


SKILL_CONTENT = "---\ndescription: テストスキル\n---\n\n# テストスキル\nテスト内容です。\n"


def _make_skill(tmp_path: Path, name: str = "SKILL.md", content: str = SKILL_CONTENT) -> Path:
    skill_file = tmp_path / name
    skill_file.write_text(content, encoding="utf-8")
    return skill_file


@pytest.fixture(autouse=True)
def _isolate_corrections(tmp_path, monkeypatch):
    """既定では corrections.jsonl を「存在しないパス」に向け、実環境の
    corrections データがテストに紛れ込まないようにする
    （optimize.py 自身のテスト群と同じ防御パターン）。
    """
    monkeypatch.setattr(variant_generation, "_CORRECTIONS_PATH", tmp_path / "no-corrections.jsonl")


class TestDryRunNoSubprocess:
    """keystone regression test: dry_run=True で subprocess.run が0回呼ばれる。"""

    def test_no_subprocess_calls(self, tmp_path):
        skill_file = _make_skill(tmp_path)
        with patch("subprocess.run") as mock_run:
            result = variant_generation.generate_variants(
                str(skill_file), population=3, dry_run=True
            )
        assert mock_run.call_count == 0
        assert result["n_candidates"] == 3
        assert result["dry_run"] is True


class TestDryRunCandidates:
    def test_population_matches_candidate_count(self, tmp_path):
        skill_file = _make_skill(tmp_path)
        result = variant_generation.generate_variants(
            str(skill_file), population=4, dry_run=True
        )
        assert len(result["candidates"]) == 4

    def test_candidates_differ_from_each_other(self, tmp_path):
        skill_file = _make_skill(tmp_path)
        result = variant_generation.generate_variants(
            str(skill_file), population=3, dry_run=True
        )
        contents = [c["content"] for c in result["candidates"]]
        assert len(set(contents)) == len(contents)

    def test_frontmatter_preserved_in_all_candidates(self, tmp_path):
        skill_file = _make_skill(tmp_path)
        result = variant_generation.generate_variants(
            str(skill_file), population=3, dry_run=True
        )
        for c in result["candidates"]:
            assert c["content"].startswith("---\n")

    def test_candidate_ids_ordered_no_duplicates(self, tmp_path):
        skill_file = _make_skill(tmp_path)
        result = variant_generation.generate_variants(
            str(skill_file), population=3, dry_run=True
        )
        ids = [c["id"] for c in result["candidates"]]
        assert ids == ["candidate_0", "candidate_1", "candidate_2"]


class TestNonDryRun:
    def test_subprocess_called_population_times(self, tmp_path):
        skill_file = _make_skill(tmp_path)
        fake_output = "```markdown\n# Improved\n改善された内容\n```\n"
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=fake_output)
            result = variant_generation.generate_variants(
                str(skill_file), population=3, dry_run=False
            )
        assert mock_run.call_count == 3
        assert result["passed_count"] == 3
        assert result["n_candidates"] == 3
        assert len(result["candidates"]) == 3

    def test_target_file_unchanged_no_side_effect(self, tmp_path):
        """呼び出し後も対象ファイルの中身が不変（PopulationBroadcastOptimizer の
        副作用問題を解消できていることの回帰テスト）。"""
        skill_file = _make_skill(tmp_path)
        original = skill_file.read_text(encoding="utf-8")
        fake_output = "```markdown\n# Improved\n改善された内容\n```\n"
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=fake_output)
            variant_generation.generate_variants(str(skill_file), population=3, dry_run=False)
        assert skill_file.read_text(encoding="utf-8") == original

    def test_partial_gate_failure_returns_only_passed(self, tmp_path):
        skill_file = _make_skill(tmp_path)
        call_count = [0]

        def fake_run(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                # 禁止パターン (TODO) 検出 → gate 失敗
                return MagicMock(
                    returncode=0, stdout="```markdown\n# Bad\nTODO: fix this\n```\n"
                )
            return MagicMock(returncode=0, stdout="```markdown\n# Good\n内容\n```\n")

        with patch("subprocess.run", side_effect=fake_run):
            result = variant_generation.generate_variants(
                str(skill_file), population=3, dry_run=False
            )
        assert result["passed_count"] == 2
        assert len(result["candidates"]) == 2

    def test_all_gate_failure_returns_error(self, tmp_path):
        skill_file = _make_skill(tmp_path)
        with patch("subprocess.run") as mock_run:
            # 禁止パターン (TODO) 検出 → 全候補 gate 失敗
            mock_run.return_value = MagicMock(
                returncode=0, stdout="```markdown\n# Bad\nTODO: fix this\n```\n"
            )
            result = variant_generation.generate_variants(
                str(skill_file), population=3, dry_run=False
            )
        assert "error" in result
        assert result["n_candidates"] == 3
        assert result["passed_count"] == 0

    def test_one_candidate_exception_does_not_crash_others(self, tmp_path, monkeypatch):
        """1候補で例外が起きても他の候補処理はクラッシュしない。"""
        skill_file = _make_skill(tmp_path)
        original_generate_candidate = variant_generation.generate_candidate
        call_count = [0]

        def flaky_generate_candidate(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                raise RuntimeError("boom")
            return original_generate_candidate(*args, **kwargs)

        monkeypatch.setattr(variant_generation, "generate_candidate", flaky_generate_candidate)

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout="```markdown\n# Good\n内容\n```\n"
            )
            result = variant_generation.generate_variants(
                str(skill_file), population=3, dry_run=False
            )
        # 1件は例外→不合格、残り2件は通過してクラッシュしない
        assert result["passed_count"] == 2


class TestErrorHandling:
    def test_missing_target_returns_error_dict_not_exception(self, tmp_path):
        missing = tmp_path / "does-not-exist.md"
        result = variant_generation.generate_variants(
            str(missing), population=2, dry_run=True
        )
        assert "error" in result

    def test_population_zero_no_crash(self, tmp_path):
        skill_file = _make_skill(tmp_path)
        result = variant_generation.generate_variants(
            str(skill_file), population=0, dry_run=True
        )
        assert result["n_candidates"] == 0
        assert result["candidates"] == []


class TestStrategy:
    def test_error_guided_when_matching_corrections_exist(self, tmp_path, monkeypatch):
        skill_file = _make_skill(tmp_path, name="myskill.md")
        corrections_file = tmp_path / "corrections.jsonl"
        record = {
            "message": "出力が長すぎる",
            "last_skill": "myskill",
            "correction_type": "output_format",
            "extracted_learning": "簡潔にする",
            "reflect_status": "pending",
        }
        corrections_file.write_text(
            json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        monkeypatch.setattr(variant_generation, "_CORRECTIONS_PATH", corrections_file)

        result = variant_generation.generate_variants(
            str(skill_file), population=2, dry_run=True
        )
        assert result["strategy"] == "error_guided"
        assert result["corrections_used"] == 1

    def test_llm_improve_when_no_corrections(self, tmp_path):
        skill_file = _make_skill(tmp_path)
        result = variant_generation.generate_variants(
            str(skill_file), population=2, dry_run=True
        )
        assert result["strategy"] == "llm_improve"
