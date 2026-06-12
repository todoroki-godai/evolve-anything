#!/usr/bin/env python3
"""#477-3: line_limit_violation の行カウント基準明示 + confidence の超過率スケール。

- 実ファイル40行超でも rule は frontmatter 除外のコンテンツ行のみカウント
  （count_content_lines）。何を数えているかをレポート（rationale）に明示する。
- 「1行超過」に confidence 0.95 は過剰。超過率（excess / limit）で confidence を
  スケールし、わずかな超過の自動修正確信度を抑える。
"""
import sys
from pathlib import Path

_lib = Path(__file__).resolve().parent.parent.parent / "scripts" / "lib"
sys.path.insert(0, str(_lib))

from remediation import compute_confidence_score, generate_rationale  # noqa: E402


def _issue(lines, limit=10):
    return {
        "type": "line_limit_violation",
        "file": ".claude/rules/foo.md",
        "detail": {"lines": lines, "limit": limit},
    }


class TestConfidenceScaling:
    def test_one_line_over_is_not_overconfident(self):
        """1行超過（11/10）は超過率 10% で 0.95 まで上げない。"""
        score = compute_confidence_score(_issue(11, 10))
        assert score < 0.95, f"1行超過で 0.95 は過剰: {score}"
        assert score >= 0.5  # proposable には残す

    def test_larger_excess_higher_confidence_within_range(self):
        """超過率が大きいほど（manual 帯未満の範囲で）confidence は単調増加する。"""
        s1 = compute_confidence_score(_issue(11, 10))   # 10% over
        s2 = compute_confidence_score(_issue(13, 10))   # 30% over
        assert s2 >= s1

    def test_major_excess_still_manual(self):
        """160% 以上の大幅超過は従来どおり低 confidence（manual_required 行き）。"""
        score = compute_confidence_score(_issue(20, 10))  # 200% over
        assert score <= 0.4

    def test_confidence_in_unit_range(self):
        for lines in (11, 12, 13, 14, 15):
            score = compute_confidence_score(_issue(lines, 10))
            assert 0.0 <= score <= 1.0


class TestRationaleCountBasis:
    def test_rationale_states_count_basis_for_rule(self):
        """rule の rationale は「frontmatter 除外のコンテンツ行」基準を明示する。"""
        issue = _issue(11, 10)
        text = generate_rationale(issue, "proposable")
        assert "コンテンツ行" in text, f"カウント基準の明示がない: {text}"

    def test_rationale_includes_lines_and_limit(self):
        issue = _issue(11, 10)
        text = generate_rationale(issue, "proposable")
        assert "11" in text and "10" in text
