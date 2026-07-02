"""regression_gate モジュールのユニットテスト。"""

import sys
from pathlib import Path

import pytest

_plugin_root = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(_plugin_root / "scripts" / "lib"))

from regression_gate import GateResult, check_gates


class TestCheckGates:
    """check_gates() の6シナリオテスト。"""

    def test_空コンテンツ(self):
        result = check_gates("", max_lines=500)
        assert result == GateResult(passed=False, reason="empty_content")

    def test_空白のみ(self):
        result = check_gates("   \n  ", max_lines=500)
        assert result == GateResult(passed=False, reason="empty_content")

    def test_行数制限超過(self):
        content = "\n".join(f"line {i}" for i in range(501))
        result = check_gates(content, max_lines=500)
        assert result.passed is False
        assert "line_limit_exceeded" in result.reason

    def test_禁止パターン検出(self):
        result = check_gates("# Skill\n\nTODO: fix this", max_lines=500)
        assert result == GateResult(passed=False, reason="forbidden_pattern(TODO)")

    def test_frontmatter_消失(self):
        result = check_gates(
            "# No frontmatter",
            original="---\nname: test\n---\n# Original",
            max_lines=500,
        )
        assert result == GateResult(passed=False, reason="frontmatter_lost")

    def test_pitfall_パターン検出(self, tmp_path):
        pitfalls = tmp_path / "pitfalls.md"
        pitfalls.write_text(
            "| Source | Pattern | Score |\n"
            "|--------|---------|-------|\n"
            "| gate | forbidden_pattern(BAD_WORD) | 0.0 |\n"
        )
        result = check_gates(
            "# Skill\n\nBAD_WORD here",
            max_lines=500,
            pitfall_patterns_path=str(pitfalls),
        )
        assert result.passed is False
        assert "pitfall_pattern(BAD_WORD)" in result.reason

    def test_全ゲート通過(self):
        result = check_gates(
            "---\nname: test\n---\n# Good Skill\n\nThis is fine.",
            original="---\nname: test\n---\n# Original",
            max_lines=500,
        )
        assert result == GateResult(passed=True, reason=None)

    def test_pitfall_ファイル不在時スキップ(self):
        result = check_gates(
            "# Good Skill",
            max_lines=500,
            pitfall_patterns_path="/nonexistent/pitfalls.md",
        )
        assert result.passed is True

    def test_frontmatter_なし_スキップ(self):
        result = check_gates(
            "# Updated",
            original="# No frontmatter",
            max_lines=500,
        )
        assert result.passed is True

    def test_original_None_時_frontmatter_チェックスキップ(self):
        result = check_gates("# Content", max_lines=500)
        assert result.passed is True

    def test_char_limit_超過でブロック(self):
        # #120 GEPA: 行数は少なくても 1 行が異常に長い bloat を捕捉する。
        content = "# Skill\n" + "x" * 100  # 2 行だが char は 108
        result = check_gates(content, max_lines=500, max_chars=50)
        assert result.passed is False
        assert result.reason.startswith("char_limit_exceeded")
        assert "108/50" in result.reason

    def test_char_limit_境界内は通過(self):
        content = "# Skill\n" + "x" * 40
        result = check_gates(content, max_lines=500, max_chars=100)
        assert result.passed is True

    def test_char_limit_None_はスキップ(self):
        # 既存呼び出し（max_chars 未指定）は char ゲートを適用しない後方互換。
        content = "# Skill\n" + "x" * 100000
        result = check_gates(content, max_lines=500)
        assert result.passed is True

    def test_char_limit_は行数超過より後(self):
        # 行数超過が優先（先に line_limit_exceeded を返す）。
        content = "\n".join(["line"] * 600)
        result = check_gates(content, max_lines=500, max_chars=10)
        assert result.passed is False
        assert result.reason.startswith("line_limit_exceeded")


class TestMaxCharsFor:
    """max_chars_for() の較正導出テスト（#120）。"""

    def test_skill_と_rule_で導出(self):
        from line_limit import MAX_CHARS_PER_LINE, max_chars_for

        assert max_chars_for(500) == 500 * MAX_CHARS_PER_LINE
        assert max_chars_for(10) == 10 * MAX_CHARS_PER_LINE

    def test_較正値は実ファイル誤ブロックを出さない(self):
        # (c) dry-run 較正: 当 PJ の全 skill(≤500行)/rule(≤10行) が char 上限内。
        import glob

        from line_limit import MAX_RULE_LINES, MAX_SKILL_LINES, max_chars_for

        root = Path(__file__).resolve().parents[3]
        skill_cap = max_chars_for(MAX_SKILL_LINES)
        for f in glob.glob(str(root / "skills" / "**" / "SKILL.md"), recursive=True):
            text = Path(f).read_text(encoding="utf-8")
            if text.count("\n") + 1 <= MAX_SKILL_LINES:
                assert len(text) <= skill_cap, f"{f} が skill char 上限超過"
        rule_cap = max_chars_for(MAX_RULE_LINES)
        for f in glob.glob(str(root / ".claude" / "rules" / "*.md")):
            text = Path(f).read_text(encoding="utf-8")
            if text.count("\n") + 1 <= MAX_RULE_LINES:
                assert len(text) <= rule_cap, f"{f} が rule char 上限超過"


class TestGateResult:
    """GateResult dataclass の構造テスト。"""

    def test_2フィールドのみ(self):
        r = GateResult(passed=True, reason=None)
        assert hasattr(r, "passed")
        assert hasattr(r, "reason")
        assert not hasattr(r, "score")
