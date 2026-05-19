#!/usr/bin/env python3
"""直接パッチプロンプト最適化のユニットテスト"""

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(
    0, str(Path(__file__).parent.parent / "scripts")
)

from optimize import (
    DirectPatchOptimizer,
    BACKUP_SUFFIX,
    MAX_CORRECTIONS_PER_PATCH,
    _check_deprecated_options,
    detect_scope,
)
from optimize_core import (
    build_patch_prompt,
    collect_corrections,
    determine_strategy,
    extract_markdown,
    format_gate_reason,
    record_pitfall,
    restore_frontmatter_if_lost,
    run_regression_gate,
)


# --- フィクスチャ ---

@pytest.fixture
def temp_dir():
    d = tempfile.mkdtemp()
    yield Path(d)
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def sample_skill(temp_dir):
    skill_path = temp_dir / "test-skill.md"
    skill_path.write_text(
        "---\nname: test\ndescription: テスト用スキル\n---\n\n"
        "# テストスキル\n\nこれはテスト用のスキルです。\n",
        encoding="utf-8",
    )
    return skill_path


@pytest.fixture
def corrections_file(temp_dir):
    """テスト用 corrections.jsonl"""
    path = temp_dir / "corrections.jsonl"
    records = [
        {
            "message": "スキルの出力が長すぎる",
            "last_skill": "test-skill",
            "correction_type": "output_format",
            "extracted_learning": "出力を簡潔にする",
            "confidence": 0.8,
            "reflect_status": "pending",
        },
        {
            "message": "エラーハンドリングが不足",
            "last_skill": "test-skill",
            "correction_type": "missing_handling",
            "extracted_learning": "エラーケースを追加",
            "confidence": 0.9,
            "reflect_status": "pending",
        },
    ]
    lines = [json.dumps(r, ensure_ascii=False) for r in records]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


# --- detect_scope テスト ---

class TestDetectScope:
    def test_project_scope(self, temp_dir):
        skill = temp_dir / "SKILL.md"
        skill.touch()
        assert detect_scope(skill) == "project"

    def test_global_scope(self):
        global_path = Path.home() / ".claude" / "skills" / "test" / "SKILL.md"
        assert detect_scope(global_path) == "global"


# --- collect_corrections テスト ---

class TestCollectCorrections:
    def test_corrections_あり(self, corrections_file):
        result = collect_corrections("test-skill", corrections_file, MAX_CORRECTIONS_PER_PATCH)
        assert len(result) == 2
        assert result[0]["correction_type"] == "output_format"

    def test_corrections_なし(self, temp_dir):
        empty_path = temp_dir / "empty.jsonl"
        empty_path.write_text("", encoding="utf-8")
        result = collect_corrections("test-skill", empty_path, MAX_CORRECTIONS_PER_PATCH)
        assert result == []

    def test_corrections_ファイル不在(self, temp_dir):
        missing_path = temp_dir / "missing.jsonl"
        result = collect_corrections("test-skill", missing_path, MAX_CORRECTIONS_PER_PATCH)
        assert result == []

    def test_corrections_大量_制限(self, temp_dir):
        path = temp_dir / "many.jsonl"
        records = []
        for i in range(20):
            records.append(json.dumps({
                "message": f"修正 {i}",
                "last_skill": "test-skill",
                "correction_type": "fix",
                "reflect_status": "pending",
            }, ensure_ascii=False))
        path.write_text("\n".join(records) + "\n", encoding="utf-8")
        result = collect_corrections("test-skill", path, MAX_CORRECTIONS_PER_PATCH)
        assert len(result) == MAX_CORRECTIONS_PER_PATCH

    def test_last_skill_が_None(self, temp_dir):
        """Issue #24: last_skill が null の場合に AttributeError にならないこと"""
        path = temp_dir / "none_skill.jsonl"
        records = [
            json.dumps({
                "message": "last_skill が None",
                "last_skill": None,
                "correction_type": "fix",
                "reflect_status": "pending",
            }, ensure_ascii=False),
            json.dumps({
                "message": "正常レコード",
                "last_skill": "test-skill",
                "correction_type": "fix",
                "reflect_status": "pending",
            }, ensure_ascii=False),
        ]
        path.write_text("\n".join(records) + "\n", encoding="utf-8")
        result = collect_corrections("test-skill", path, MAX_CORRECTIONS_PER_PATCH)
        assert len(result) == 1
        assert result[0]["message"] == "正常レコード"

    def test_last_skill_キー不在(self, temp_dir):
        """last_skill キー自体がないレコードでもエラーにならないこと"""
        path = temp_dir / "no_key.jsonl"
        records = [
            json.dumps({
                "message": "キーなし",
                "correction_type": "fix",
                "reflect_status": "pending",
            }, ensure_ascii=False),
            json.dumps({
                "message": "正常レコード",
                "last_skill": "test-skill",
                "correction_type": "fix",
                "reflect_status": "pending",
            }, ensure_ascii=False),
        ]
        path.write_text("\n".join(records) + "\n", encoding="utf-8")
        result = collect_corrections("test-skill", path, MAX_CORRECTIONS_PER_PATCH)
        assert len(result) == 1
        assert result[0]["message"] == "正常レコード"

    def test_applied_は除外(self, temp_dir):
        path = temp_dir / "applied.jsonl"
        records = [
            json.dumps({
                "message": "適用済み",
                "last_skill": "test-skill",
                "correction_type": "fix",
                "reflect_status": "applied",
            }, ensure_ascii=False),
            json.dumps({
                "message": "未適用",
                "last_skill": "test-skill",
                "correction_type": "fix",
                "reflect_status": "pending",
            }, ensure_ascii=False),
        ]
        path.write_text("\n".join(records) + "\n", encoding="utf-8")
        result = collect_corrections("test-skill", path, MAX_CORRECTIONS_PER_PATCH)
        assert len(result) == 1
        assert result[0]["message"] == "未適用"


# --- build_patch_prompt テスト ---

class TestBuildPatchPrompt:
    def test_error_guided_prompt(self):
        corrections = [
            {"message": "出力が長い", "correction_type": "output_format", "extracted_learning": "簡潔に"},
        ]
        prompt = build_patch_prompt("# Test Skill", corrections, {}, "error_guided", False, 200)
        assert "修正すべき問題点" in prompt
        assert "出力が長い" in prompt
        assert "簡潔に" in prompt

    def test_llm_improve_prompt(self):
        prompt = build_patch_prompt("# Test Skill", [], {}, "llm_improve", False, 200)
        assert "改善方針" in prompt
        assert "具体的な例" in prompt

    def test_context_included(self):
        context = {
            "workflow_hint": "このスキルは週3回使用",
            "audit_issues": [{"type": "line_limit", "file": "test.md", "detail": "100行超過"}],
            "pitfalls": "| gate | forbidden_pattern(X) | 0.00 |",
        }
        prompt = build_patch_prompt("# Test", [], context, "llm_improve", False, 200)
        assert "ワークフロー分析" in prompt
        assert "週3回" in prompt
        assert "構造的問題" in prompt
        assert "失敗パターン" in prompt

    def test_frontmatter_preservation_note_added_when_frontmatter_exists(self):
        """frontmatter を持つスキルには保持指示が追加されること。"""
        skill_with_fm = "---\nname: test\n---\n\n# Test"
        prompt = build_patch_prompt(skill_with_fm, [], {}, "llm_improve", False, 200)
        assert "YAML frontmatter" in prompt
        assert "保持" in prompt

    def test_frontmatter_preservation_note_absent_when_no_frontmatter(self):
        """frontmatter のないスキルには保持指示が追加されないこと。"""
        skill_no_fm = "# Test Skill\n\nContent here."
        prompt = build_patch_prompt(skill_no_fm, [], {}, "llm_improve", False, 200)
        assert "YAML frontmatter" not in prompt


# --- restore_frontmatter_if_lost テスト ---

class TestRestoreFrontmatterIfLost:
    def test_restores_when_candidate_lacks_frontmatter(self):
        """LLM が frontmatter を消した場合に元の frontmatter を補完すること。"""
        original = "---\nname: test\ndescription: テスト\n---\n\n# Test Skill"
        candidate = "# Test Skill (improved)"
        result = restore_frontmatter_if_lost(candidate, original)
        assert result.startswith("---\nname: test")
        assert "# Test Skill (improved)" in result

    def test_no_change_when_candidate_has_frontmatter(self):
        """candidate に frontmatter がある場合は変更しないこと。"""
        original = "---\nname: test\n---\n\n# Original"
        candidate = "---\nname: test\n---\n\n# Improved"
        result = restore_frontmatter_if_lost(candidate, original)
        assert result == candidate

    def test_no_change_when_original_has_no_frontmatter(self):
        """original に frontmatter がない場合は何もしないこと。"""
        original = "# No frontmatter"
        candidate = "# Improved without frontmatter"
        result = restore_frontmatter_if_lost(candidate, original)
        assert result == candidate

    def test_restored_content_passes_regression_gate(self):
        """auto-restore 後のコンテンツが regression gate を通過すること。"""
        from regression_gate import check_gates  # optimize_core import 時に sys.path 追加済み
        original = "---\nname: test\ndescription: テスト\n---\n\n# Test Skill\n\nContent."
        candidate_without_fm = "# Test Skill\n\nImproved content."
        restored = restore_frontmatter_if_lost(candidate_without_fm, original)
        result = check_gates(candidate=restored, original=original, max_lines=200)
        assert result.passed, f"gate failed: {result.reason}"

    def test_restores_when_original_has_crlf_line_endings(self):
        """`\r\n` 改行のファイルでも frontmatter を復元できること。"""
        original = "---\r\nname: test\r\ndescription: テスト\r\n---\r\n\r\n# Test Skill"
        candidate = "# Test Skill (improved)"
        result = restore_frontmatter_if_lost(candidate, original)
        assert "name: test" in result
        assert "# Test Skill (improved)" in result

    def test_no_change_when_original_has_crlf_but_no_closing_delimiter(self):
        """`\r\n` でも閉じ `---` がない場合は変更しないこと。"""
        original = "---\r\nname: test\r\n"  # 閉じなし（malformed）
        candidate = "# Improved"
        result = restore_frontmatter_if_lost(candidate, original)
        assert result == candidate


# --- DirectPatchOptimizer コアテスト (Task 3.4) ---

class TestDirectPatchOptimizer:
    def test_dry_run(self, sample_skill, temp_dir):
        optimizer = DirectPatchOptimizer(target_path=str(sample_skill), dry_run=True)
        optimizer.run_dir = temp_dir / "test_run"

        with patch("optimize._CORRECTIONS_PATH", temp_dir / "missing.jsonl"):
            result = optimizer.run()

        assert result["dry_run"] is True
        assert result["strategy"] == "llm_improve"
        assert result["best_individual"]["content"] is not None

    def test_error_guided_正常系(self, sample_skill, temp_dir, corrections_file):
        optimizer = DirectPatchOptimizer(target_path=str(sample_skill), mode="auto")
        optimizer.run_dir = temp_dir / "test_run"

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "```markdown\n---\nname: test\ndescription: 改善済み\n---\n\n# Improved Skill\n\nBetter content.\n```"

        with patch("optimize._CORRECTIONS_PATH", corrections_file), \
             patch("optimize_core.subprocess.run", return_value=mock_result):
            result = optimizer.run()

        assert result["strategy"] == "error_guided"
        assert result["corrections_used"] == 2
        assert "Improved Skill" in result["best_individual"]["content"]

    def test_llm_improve_正常系(self, sample_skill, temp_dir):
        optimizer = DirectPatchOptimizer(target_path=str(sample_skill), mode="llm_improve")
        optimizer.run_dir = temp_dir / "test_run"

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "```markdown\n# Better Skill\n\nImproved.\n```"

        with patch("optimize._CORRECTIONS_PATH", temp_dir / "missing.jsonl"), \
             patch("optimize_core.subprocess.run", return_value=mock_result):
            result = optimizer.run()

        assert result["strategy"] == "llm_improve"
        assert result["corrections_used"] == 0

    def test_regression_gate_行数超過(self, sample_skill, temp_dir):
        optimizer = DirectPatchOptimizer(target_path=str(sample_skill))
        optimizer.run_dir = temp_dir / "test_run"

        # 行数超過のパッチを返すモック
        long_content = "\n".join([f"line {i}" for i in range(1000)])
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = f"```markdown\n{long_content}\n```"

        with patch("optimize._CORRECTIONS_PATH", temp_dir / "missing.jsonl"), \
             patch("optimize_core.subprocess.run", return_value=mock_result):
            result = optimizer.run()

        assert result.get("gate_rejected") is True
        assert "line_limit_exceeded" in result.get("gate_reason", "")

    def test_regression_gate_空コンテンツ(self, sample_skill):
        passed, reason = run_regression_gate("", None, 500, None)
        assert passed is False
        assert reason == "empty"

    def test_regression_gate_禁止パターン(self, sample_skill):
        passed, reason = run_regression_gate("# Skill\n\nTODO: fix this", None, 500, None)
        assert passed is False
        assert "forbidden_pattern(TODO)" in reason

    def test_regression_gate_正常通過(self, sample_skill):
        passed, reason = run_regression_gate("# Good Skill\n\nThis is fine.", None, 500, None)
        assert passed is True
        assert reason is None

    def test_regression_gate_frontmatter_保持(self):
        original = "---\nname: test\n---\n\n# Original"
        passed, reason = run_regression_gate("---\nname: test\n---\n\n# Improved", original, 500, None)
        assert passed is True
        assert reason is None

    def test_regression_gate_frontmatter_消失(self):
        """ゲート自体は frontmatter 消失を検出すること（auto-restore より下のレイヤー）。"""
        original = "---\nname: test\n---\n\n# Original"
        passed, reason = run_regression_gate("# Improved without frontmatter", original, 500, None)
        assert passed is False
        assert reason == "frontmatter_lost"

    def test_regression_gate_frontmatter_なし_スキップ(self):
        original = "# No frontmatter skill"
        passed, reason = run_regression_gate("# Updated skill", original, 500, None)
        assert passed is True
        assert reason is None

    def test_gate_rejected_rule_行数超過_分離提案あり(self, temp_dir):
        """rule ファイルが行数超過で gate リジェクト時に suggestion が含まれる。"""
        rules_dir = temp_dir / ".claude" / "rules"
        rules_dir.mkdir(parents=True)
        rule_path = rules_dir / "my-rule.md"
        rule_path.write_text("line1\nline2\nline3", encoding="utf-8")

        optimizer = DirectPatchOptimizer(target_path=str(rule_path))
        optimizer.run_dir = temp_dir / "test_run"

        # MAX_RULE_LINES=10 を超える 11 行のパッチを返すモック
        long_content = "\n".join([f"line {i}" for i in range(11)])
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = f"```markdown\n{long_content}\n```"

        with patch("optimize._CORRECTIONS_PATH", temp_dir / "missing.jsonl"), \
             patch("optimize_core.subprocess.run", return_value=mock_result):
            result = optimizer.run()

        assert result.get("gate_rejected") is True
        assert result.get("suggestion") is not None
        assert "references" in result["suggestion"]

    def test_gate_rejected_skill_行数超過_分離提案なし(self, temp_dir):
        """skill ファイルが行数超過で gate リジェクト時に suggestion は None。"""
        skill_path = temp_dir / "SKILL.md"
        skill_path.write_text("# Skill", encoding="utf-8")

        optimizer = DirectPatchOptimizer(target_path=str(skill_path))
        optimizer.run_dir = temp_dir / "test_run"

        long_content = "\n".join([f"line {i}" for i in range(1000)])
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = f"```markdown\n{long_content}\n```"

        with patch("optimize._CORRECTIONS_PATH", temp_dir / "missing.jsonl"), \
             patch("optimize_core.subprocess.run", return_value=mock_result):
            result = optimizer.run()

        assert result.get("gate_rejected") is True
        assert result.get("suggestion") is None

    def test_format_gate_reason_frontmatter_lost(self):
        msg = format_gate_reason("frontmatter_lost")
        assert "frontmatter" in msg
        assert "消失" in msg

    def test_llm_コール失敗(self, sample_skill, temp_dir):
        optimizer = DirectPatchOptimizer(target_path=str(sample_skill))
        optimizer.run_dir = temp_dir / "test_run"

        with patch("optimize._CORRECTIONS_PATH", temp_dir / "missing.jsonl"), \
             patch("optimize_core.subprocess.run", side_effect=FileNotFoundError):
            result = optimizer.run()

        assert result.get("error") is not None
        assert "claude CLI" in result["error"]

    def test_error_guided_corrections_0件_フォールバック(self):
        strategy = determine_strategy("error_guided", [])
        assert strategy == "llm_improve"

    def test_auto_mode_corrections_あり(self):
        strategy = determine_strategy("auto", [{"message": "fix"}])
        assert strategy == "error_guided"

    def test_auto_mode_corrections_なし(self):
        strategy = determine_strategy("auto", [])
        assert strategy == "llm_improve"


# --- バックアップ/復元テスト ---

class TestBackupRestore:
    def test_backup_作成(self, sample_skill):
        optimizer = DirectPatchOptimizer(target_path=str(sample_skill), dry_run=True)
        optimizer.backup_original()
        backup_path = sample_skill.with_suffix(sample_skill.suffix + BACKUP_SUFFIX)
        assert backup_path.exists()

    def test_restore(self, sample_skill):
        original_content = sample_skill.read_text(encoding="utf-8")
        backup_path = sample_skill.with_suffix(sample_skill.suffix + BACKUP_SUFFIX)
        shutil.copy2(sample_skill, backup_path)

        sample_skill.write_text("modified content", encoding="utf-8")
        DirectPatchOptimizer.restore(str(sample_skill))

        assert sample_skill.read_text(encoding="utf-8") == original_content
        assert not backup_path.exists()


# --- History テスト (Task 4.2, 4.4) ---

class TestHistory:
    def test_history_entry_format(self, sample_skill, temp_dir):
        optimizer = DirectPatchOptimizer(target_path=str(sample_skill), dry_run=True)
        optimizer.run_dir = temp_dir / "test_run"

        result = {
            "run_id": "test_run",
            "target": str(sample_skill),
            "strategy": "error_guided",
            "corrections_used": 3,
            "fitness_func": "default",
            "best_individual": {"fitness": 0.8},
        }

        history_path = optimizer.save_history_entry(result)
        assert history_path.exists()

        entry = json.loads(history_path.read_text(encoding="utf-8").strip())
        assert entry["strategy"] == "error_guided"
        assert entry["corrections_used"] == 3
        assert entry["human_accepted"] is None

    def test_record_human_decision(self, temp_dir):
        history_file = temp_dir / "history.jsonl"
        entry = {
            "run_id": "test",
            "strategy": "llm_improve",
            "corrections_used": 0,
            "human_accepted": None,
        }
        history_file.write_text(json.dumps(entry, ensure_ascii=False) + "\n", encoding="utf-8")

        DirectPatchOptimizer.record_human_decision(str(temp_dir / "run1"), human_accepted=True)

        # history.jsonl は run_dir.parent なので、temp_dir 直下にあるはず
        # ここでは temp_dir の構造を合わせる必要がある
        # record_human_decision は run_dir の parent に history.jsonl を探す
        run_dir = temp_dir / "run1"
        run_dir.mkdir(exist_ok=True)
        parent_history = temp_dir / "history.jsonl"

        # history_file をリロード
        updated = json.loads(parent_history.read_text(encoding="utf-8").strip())
        assert updated["human_accepted"] is True


# --- 廃止オプションテスト (Task 4.4) ---

class TestDeprecatedOptions:
    def test_generations_deprecated(self):
        result = _check_deprecated_options(["--generations", "3"])
        assert result is not None
        assert "廃止されました" in result

    def test_population_deprecated(self):
        result = _check_deprecated_options(["--population", "5"])
        assert result is not None

    def test_budget_deprecated(self):
        result = _check_deprecated_options(["--budget", "30"])
        assert result is not None

    def test_cascade_deprecated(self):
        result = _check_deprecated_options(["--cascade", "config.yaml"])
        assert result is not None

    def test_parallel_deprecated(self):
        result = _check_deprecated_options(["--parallel", "4"])
        assert result is not None

    def test_strategy_deprecated(self):
        result = _check_deprecated_options(["--strategy", "budget_mpo"])
        assert result is not None

    def test_valid_options(self):
        result = _check_deprecated_options(["--target", "test.md", "--mode", "auto"])
        assert result is None


# --- Pitfall テスト ---

class TestPitfalls:
    def test_record_pitfall(self, temp_dir):
        skill_path = temp_dir / "SKILL.md"
        skill_path.touch()
        record_pitfall(str(skill_path), "gate", "empty", 0.0)

        pitfalls = temp_dir / "references" / "pitfalls.md"
        assert pitfalls.exists()
        content = pitfalls.read_text(encoding="utf-8")
        assert "empty" in content

    def test_pitfall_重複排除(self, temp_dir):
        skill_path = temp_dir / "SKILL.md"
        skill_path.touch()
        record_pitfall(str(skill_path), "gate", "empty", 0.0)
        record_pitfall(str(skill_path), "gate", "empty", 0.0)

        pitfalls = temp_dir / "references" / "pitfalls.md"
        content = pitfalls.read_text(encoding="utf-8")
        assert content.count("empty") == 1


# --- extract_markdown テスト ---

class TestExtractMarkdown:
    def test_markdown_block(self):
        text = "Some text\n```markdown\n# Title\n\nContent\n```\nMore text"
        result = extract_markdown(text)
        assert result == "# Title\n\nContent"

    def test_plain_code_block(self):
        text = "```\n# Title\n```"
        result = extract_markdown(text)
        assert result == "# Title"

    def test_no_block(self):
        text = "# Just plain text"
        result = extract_markdown(text)
        assert result == "# Just plain text"

    def test_empty(self):
        result = extract_markdown("")
        assert result is None

    def test_multiple_blocks_returns_longest(self):
        text = (
            "Here is the result:\n"
            "```markdown\nshort\n```\n\n"
            "Full improved version:\n"
            "```markdown\n# Full Skill\n\nThis is the complete improved skill.\n\n## Section\n\nMore content.\n```\n"
        )
        result = extract_markdown(text)
        assert "Full Skill" in result
        assert "More content" in result
        assert len(result) > len("short")
