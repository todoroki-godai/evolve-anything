#!/usr/bin/env python3
"""coherence.py のユニットテスト"""

import json
import sys
import tempfile
from pathlib import Path

import pytest

_test_dir = Path(__file__).resolve().parent
_rl_dir = _test_dir.parent
_plugin_root = _rl_dir.parent.parent
sys.path.insert(0, str(_rl_dir))
sys.path.insert(0, str(_plugin_root / "scripts" / "lib"))
sys.path.insert(0, str(_plugin_root / "scripts"))
sys.path.insert(0, str(_plugin_root / "skills" / "audit" / "scripts"))

import importlib.util

_coherence_path = _rl_dir / "fitness" / "coherence.py"
_spec = importlib.util.spec_from_file_location("coherence", _coherence_path)
coherence = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(coherence)

THRESHOLDS = coherence.THRESHOLDS
WEIGHTS = coherence.WEIGHTS
compute_coherence_score = coherence.compute_coherence_score
score_completeness = coherence.score_completeness
score_consistency = coherence.score_consistency
score_coverage = coherence.score_coverage
score_efficiency = coherence.score_efficiency


# --- ヘルパー ---

def _make_project(tmp_path, *, claude_md=True, rules=True, skills=True,
                   memory=True, hooks=True, skills_section=True,
                   claude_md_content=None):
    """テスト用プロジェクトディレクトリを作成する。"""
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir(parents=True, exist_ok=True)

    if claude_md:
        content = claude_md_content or "# Project\n\n## Overview\n\nSample project.\n"
        if skills_section and "## Skills" not in content:
            content += "\n## Skills\n\n- sample-skill: A sample skill\n"
        (tmp_path / "CLAUDE.md").write_text(content, encoding="utf-8")

    if rules:
        rules_dir = claude_dir / "rules"
        rules_dir.mkdir(parents=True, exist_ok=True)
        (rules_dir / "sample.md").write_text("# Rule\nDo this.\n", encoding="utf-8")

    if skills:
        skill_dir = claude_dir / "skills" / "sample-skill"
        skill_dir.mkdir(parents=True, exist_ok=True)
        # 50行以上のスキルファイル
        skill_content = "# Sample Skill\n\n## Usage\n\nUse this skill when...\n\n## Steps\n\n"
        skill_content += "\n".join([f"Step {i}: Do something {i}" for i in range(50)])
        (skill_dir / "SKILL.md").write_text(skill_content, encoding="utf-8")

    if memory:
        mem_dir = claude_dir / "memory"
        mem_dir.mkdir(parents=True, exist_ok=True)
        (mem_dir / "MEMORY.md").write_text("# Memory\n\n## Notes\n\nSome notes.\n", encoding="utf-8")

    if hooks:
        settings = {"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": ["echo test"]}]}}
        (claude_dir / "settings.json").write_text(json.dumps(settings), encoding="utf-8")

    return tmp_path


# ============================================================
# 1. Coverage テスト
# ============================================================

class TestScoreCoverage:
    def test_all_layers_present(self, tmp_path):
        """全レイヤーが揃っている場合、score=1.0"""
        project = _make_project(tmp_path)
        score, details = score_coverage(project)
        assert score == 1.0
        assert all(details.values())

    def test_hooks_missing(self, tmp_path):
        """Hooks 未設定の場合、score < 1.0"""
        project = _make_project(tmp_path, hooks=False)
        score, details = score_coverage(project)
        assert score < 1.0
        assert details["hooks"] is False
        assert details["claude_md"] is True

    def test_claude_md_only(self, tmp_path):
        """CLAUDE.md のみの場合、score <= 0.2"""
        project = _make_project(
            tmp_path, rules=False, skills=False, memory=False,
            hooks=False, skills_section=False
        )
        score, details = score_coverage(project)
        # claude_md=True のみ → 1/6 ≈ 0.1667
        assert score <= 0.2

    def test_no_claude_dir(self, tmp_path):
        """`.claude/` も CLAUDE.md も存在しない場合、score=0.0"""
        score, details = score_coverage(tmp_path)
        assert score == 0.0


# ============================================================
# 2. Consistency テスト
# ============================================================

class TestScoreConsistency:
    def test_skill_exists(self, tmp_path):
        """CLAUDE.md で言及された Skill がすべて実在する場合"""
        project = _make_project(tmp_path)
        score, details = score_consistency(project)
        assert details["skill_existence"]["pass"] is True

    def test_skill_missing(self, tmp_path):
        """CLAUDE.md で言及された Skill が存在しない場合"""
        claude_content = "# Project\n\n## Skills\n\n- nonexistent-skill: Does not exist\n"
        project = _make_project(tmp_path, claude_md_content=claude_content)
        score, details = score_consistency(project)
        assert details["skill_existence"]["pass"] is False
        assert "nonexistent-skill" in details["skill_existence"]["missing"]

    def test_trigger_duplicates(self, tmp_path):
        """トリガーワード重複チェック"""
        claude_content = (
            "# Project\n\n## Skills\n\n"
            "- skill-a: First skill\n  トリガーワード: deploy, build\n"
            "- skill-b: Second skill\n  トリガーワード: deploy, test\n"
        )
        project = _make_project(tmp_path, claude_md_content=claude_content)
        # skill ディレクトリも作成
        (tmp_path / ".claude" / "skills" / "skill-a").mkdir(parents=True, exist_ok=True)
        (tmp_path / ".claude" / "skills" / "skill-b").mkdir(parents=True, exist_ok=True)
        score, details = score_consistency(project)
        assert details["trigger_duplicates"]["pass"] is False


# ============================================================
# 3. Completeness テスト
# ============================================================

class TestScoreCompleteness:
    def test_all_complete(self, tmp_path):
        """全 Skill が基準を満たす場合"""
        project = _make_project(tmp_path)
        score, details = score_completeness(project)
        # skill_quality, rule_compliance, claude_md_size, hardcoded_values のすべて pass
        assert details["skill_quality"]["pass"] is True
        assert details["rule_compliance"]["pass"] is True

    def test_empty_skill(self, tmp_path):
        """行数が少ない Skill がある場合"""
        project = _make_project(tmp_path)
        # 短いスキルを追加
        empty_skill_dir = tmp_path / ".claude" / "skills" / "empty-skill"
        empty_skill_dir.mkdir(parents=True, exist_ok=True)
        (empty_skill_dir / "SKILL.md").write_text("# Empty\nShort.\n", encoding="utf-8")
        score, details = score_completeness(project)
        assert details["skill_quality"]["pass"] is False


# ============================================================
# 4. Efficiency テスト
# ============================================================

class TestScoreEfficiency:
    def test_clean_environment(self, tmp_path):
        """重複なし・near-limit なしの環境"""
        project = _make_project(tmp_path)
        # data_dir を空の tmp に指定し usage.jsonl が存在しない状態をシミュレート
        empty_data = tmp_path / "_data"
        empty_data.mkdir()
        score, details = score_efficiency(project, data_dir=empty_data)
        # duplicate は pass（重複なし）
        assert details["duplicate_skills"]["pass"] is True

    def test_unused_skill_skipped_without_usage(self, tmp_path):
        """usage.jsonl が存在しない場合、未使用チェックは skip"""
        project = _make_project(tmp_path)
        empty_data = tmp_path / "_data"
        empty_data.mkdir()
        score, details = score_efficiency(project, data_dir=empty_data)
        assert details["unused_skills"]["skipped"] is True


# ============================================================
# 5. 統合テスト
# ============================================================

class TestComputeCoherenceScore:
    def test_return_keys(self, tmp_path):
        """戻り値に必要なキーが含まれる"""
        project = _make_project(tmp_path)
        result = compute_coherence_score(project)
        assert "overall" in result
        assert "coverage" in result
        assert "consistency" in result
        assert "completeness" in result
        assert "efficiency" in result
        assert "details" in result

    def test_weighted_average(self, tmp_path):
        """重み付き平均の計算が正しい"""
        project = _make_project(tmp_path)
        result = compute_coherence_score(project)
        expected = (
            WEIGHTS["coverage"] * result["coverage"]
            + WEIGHTS["consistency"] * result["consistency"]
            + WEIGHTS["completeness"] * result["completeness"]
            + WEIGHTS["efficiency"] * result["efficiency"]
        )
        assert abs(result["overall"] - round(expected, 4)) < 0.001

    def test_no_claude_dir(self, tmp_path):
        """`.claude/` が存在しない場合"""
        result = compute_coherence_score(tmp_path)
        assert result["coverage"] == 0.0

    def test_consistency_low_overall(self, tmp_path):
        """Consistency のみ低い場合の overall 計算"""
        # consistency=0.5 をシミュレートするため、不在スキルを参照
        claude_content = (
            "# Project\n\n## Skills\n\n"
            "- missing-a: Does not exist\n"
            "- sample-skill: Exists\n"
        )
        project = _make_project(tmp_path, claude_md_content=claude_content)
        result = compute_coherence_score(project)
        # consistency が低いことを確認
        assert result["consistency"] < 1.0
