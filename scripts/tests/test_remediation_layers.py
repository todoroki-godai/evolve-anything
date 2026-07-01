#!/usr/bin/env python3
"""remediation.py の新レイヤー issue type テスト。"""
import sys
from pathlib import Path

import pytest

# remediation パッケージのパスを通す（scripts/lib/remediation/）
_lib = Path(__file__).resolve().parent.parent.parent / "scripts" / "lib"
sys.path.insert(0, str(_lib))

from remediation import (
    compute_confidence_score,
    generate_rationale,
    classify_issue,
    generate_proposals,
    generate_auto_fix_summaries,
)


# ── compute_confidence_score ──────────────────────────────


def test_confidence_orphan_rule():
    issue = {"type": "orphan_rule", "file": "rule.md", "detail": {"name": "test"}}
    score = compute_confidence_score(issue)
    assert 0.4 <= score <= 0.6


def test_confidence_stale_rule():
    issue = {"type": "stale_rule", "file": "rule.md", "detail": {"path": "missing.sh"}}
    score = compute_confidence_score(issue)
    assert score >= 0.9


def test_confidence_stale_memory():
    issue = {"type": "stale_memory", "file": "MEMORY.md", "detail": {"path": "old"}}
    score = compute_confidence_score(issue)
    assert 0.5 <= score <= 0.7


def test_confidence_memory_duplicate():
    issue = {"type": "memory_duplicate", "file": "MEMORY.md", "detail": {"similarity": 0.7}}
    score = compute_confidence_score(issue)
    assert 0.6 <= score <= 0.8


def test_confidence_hooks_unconfigured():
    issue = {"type": "hooks_unconfigured", "file": "settings.json", "detail": {}}
    score = compute_confidence_score(issue)
    assert 0.3 <= score <= 0.5


def test_confidence_claudemd_phantom_ref():
    issue = {"type": "claudemd_phantom_ref", "file": "CLAUDE.md", "detail": {"name": "x"}}
    score = compute_confidence_score(issue)
    assert score >= 0.85


def test_confidence_claudemd_missing_section():
    issue = {"type": "claudemd_missing_section", "file": "CLAUDE.md", "detail": {}}
    score = compute_confidence_score(issue)
    assert score >= 0.9


# ── generate_rationale ──────────────────────────────


def test_rationale_orphan_rule():
    issue = {"type": "orphan_rule", "file": "rule.md", "detail": {"name": "old-rule"}}
    r = generate_rationale(issue, "proposable")
    assert "old-rule" in r
    assert "参照されていません" in r


def test_rationale_stale_rule():
    issue = {"type": "stale_rule", "file": "rule.md", "detail": {"path": "scripts/deploy.sh"}}
    r = generate_rationale(issue, "auto_fixable")
    assert "scripts/deploy.sh" in r


def test_rationale_stale_memory():
    issue = {"type": "stale_memory", "file": "MEMORY.md", "detail": {"path": "obsolete_mod"}}
    r = generate_rationale(issue, "proposable")
    assert "obsolete_mod" in r


def test_rationale_memory_duplicate():
    issue = {
        "type": "memory_duplicate",
        "file": "MEMORY.md",
        "detail": {"sections": ["A Section", "B Section"], "similarity": 0.7},
    }
    r = generate_rationale(issue, "proposable")
    assert "A Section" in r
    assert "B Section" in r
    assert "0.7" in r


def test_rationale_hooks_unconfigured():
    issue = {"type": "hooks_unconfigured", "file": "settings.json", "detail": {}}
    r = generate_rationale(issue, "manual_required")
    assert "hooks" in r


def test_rationale_memory_heavy_update():
    """#104: memory_heavy_update は総称フォールバックでなく、update_count/行数を明示し
    「意図的な追記ログなら dismiss」導線を含む具体文言を返す。"""
    issue = {
        "type": "memory_heavy_update",
        "file": "/x/release_notes_last_checked.md",
        "detail": {
            "update_count": 71,
            "line_count": 174,
            "threshold": 10,
            "line_threshold": 80,
        },
    }
    r = generate_rationale(issue, "proposable")
    assert "問題タイプ" not in r  # 総称フォールバックに落ちていない
    assert "71" in r  # update_count を明示
    assert "174" in r  # line_count を明示
    assert "dismiss" in r  # 意図的ログの dismiss 導線（log 過検知の reframe）


def test_rationale_claudemd_phantom_ref():
    issue = {
        "type": "claudemd_phantom_ref",
        "file": "CLAUDE.md",
        "detail": {"name": "ghost-skill", "ref_type": "skill"},
    }
    r = generate_rationale(issue, "proposable")
    assert "ghost-skill" in r
    assert "skill" in r


def test_rationale_claudemd_missing_section():
    issue = {
        "type": "claudemd_missing_section",
        "file": "CLAUDE.md",
        "detail": {"section": "skills", "skill_count": 5},
    }
    r = generate_rationale(issue, "proposable")
    assert "skills" in r
    assert "5" in r


# ── classify_issue: 新 type の分類確認 ──────────────────


def test_classify_orphan_rule():
    issue = {"type": "orphan_rule", "file": ".claude/rules/x.md", "detail": {"name": "x"}}
    classified = classify_issue(issue)
    assert classified["category"] == "proposable"


def test_classify_hooks_unconfigured():
    issue = {"type": "hooks_unconfigured", "file": ".claude/settings.json", "detail": {}}
    classified = classify_issue(issue)
    assert classified["category"] == "manual_required"


def test_classify_claudemd_phantom_ref():
    issue = {"type": "claudemd_phantom_ref", "file": "CLAUDE.md", "detail": {"name": "x"}}
    classified = classify_issue(issue)
    # confidence=0.9, scope=project → proposable
    assert classified["category"] in ("proposable", "auto_fixable")


# ── generate_proposals: auto_fixable type の proposal/rationale ────────


def test_generate_proposals_stale_ref_has_rationale():
    """auto_fixable type stale_ref も具体的な proposal + rationale を持つ。"""
    issue = {
        "type": "stale_ref",
        "file": ".claude/skills/foo/SKILL.md",
        "category": "auto_fixable",
        "detail": {"path": "missing/path.md"},
    }
    props = generate_proposals([issue])
    assert len(props) == 1
    entry = props[0]
    # rationale には具体的なパスが含まれ、汎用フォールバックでない
    assert "missing/path.md" in entry["rationale"]
    # proposal も汎用フォールバック「修正案を検討してください」でないこと
    assert "検討してください" not in entry["proposal"]
    assert "missing/path.md" in entry["proposal"]


def test_generate_proposals_stale_rule_has_specific_proposal():
    issue = {
        "type": "stale_rule",
        "file": ".claude/rules/x.md",
        "category": "auto_fixable",
        "detail": {"path": "scripts/gone.sh"},
    }
    props = generate_proposals([issue])
    entry = props[0]
    assert "scripts/gone.sh" in entry["rationale"]
    assert "検討してください" not in entry["proposal"]


def test_generate_proposals_claudemd_phantom_ref_specific():
    issue = {
        "type": "claudemd_phantom_ref",
        "file": "CLAUDE.md",
        "category": "auto_fixable",
        "detail": {"name": "ghost-skill", "ref_type": "skill"},
    }
    props = generate_proposals([issue])
    entry = props[0]
    assert "ghost-skill" in entry["proposal"]
    assert "検討してください" not in entry["proposal"]


def test_generate_proposals_claudemd_missing_section_specific():
    issue = {
        "type": "claudemd_missing_section",
        "file": "CLAUDE.md",
        "category": "auto_fixable",
        "detail": {"section": "skills", "skill_count": 5},
    }
    props = generate_proposals([issue])
    entry = props[0]
    assert "skills" in entry["proposal"]
    assert "検討してください" not in entry["proposal"]


# ── generate_auto_fix_summaries: auto_fixable を1件ずつ列挙 ────────────


def test_generate_auto_fix_summaries_per_issue_rationale():
    """auto_fixable issue 群を渡すと各 issue ごとに rationale 付き entry を返す。"""
    issues = [
        {
            "type": "stale_ref",
            "file": ".claude/skills/a/SKILL.md",
            "category": "auto_fixable",
            "detail": {"path": "dead/link-a.md"},
        },
        {
            "type": "stale_rule",
            "file": ".claude/rules/b.md",
            "category": "auto_fixable",
            "detail": {"path": "scripts/dead-b.sh"},
        },
    ]
    summaries = generate_auto_fix_summaries(issues)
    assert len(summaries) == 2
    # 各 entry に issue / proposal / rationale が揃っている
    for s in summaries:
        assert "issue" in s
        assert "proposal" in s
        assert s["rationale"]  # 非空
    # 個別の detail が rationale に反映されている（1件ずつ独立した説明）
    assert "dead/link-a.md" in summaries[0]["rationale"]
    assert "scripts/dead-b.sh" in summaries[1]["rationale"]


def test_generate_auto_fix_summaries_filters_non_auto_fixable():
    """auto_fixable 以外の category は除外される。"""
    issues = [
        {
            "type": "stale_ref",
            "file": ".claude/skills/a/SKILL.md",
            "category": "auto_fixable",
            "detail": {"path": "dead.md"},
        },
        {
            "type": "orphan_rule",
            "file": ".claude/rules/c.md",
            "category": "proposable",
            "detail": {"name": "c"},
        },
    ]
    summaries = generate_auto_fix_summaries(issues)
    assert len(summaries) == 1
    assert summaries[0]["issue"]["type"] == "stale_ref"


# ── missing_effort: per-item proposal/rationale（件数に丸めない）────────


def test_generate_proposals_missing_effort_per_skill_specific():
    """missing_effort は各スキル名・推定 effort を持つ具体的 proposal を生成する。"""
    issue = {
        "type": "missing_effort",
        "file": ".claude/skills/generate-docs/SKILL.md",
        "category": "proposable",
        "detail": {
            "skill_name": "generate-docs",
            "proposed_effort": "low",
            "confidence": 0.75,
            "reason": "content_lines=62 < 80",
        },
    }
    props = generate_proposals([issue])
    entry = props[0]
    # 汎用フォールバックでなく、スキル名と推定 effort が proposal に出る
    assert "検討してください" not in entry["proposal"]
    assert "generate-docs" in entry["proposal"]
    assert "low" in entry["proposal"]
    # rationale に判断材料（reason の実値）が含まれる
    assert "content_lines=62 < 80" in entry["rationale"]


def test_generate_proposals_missing_effort_multiple_expands_per_item():
    """複数スキル分の missing_effort は件数に丸めず per-item に展開される。"""
    issues = [
        {
            "type": "missing_effort",
            "file": f".claude/skills/skill-{i}/SKILL.md",
            "category": "proposable",
            "detail": {
                "skill_name": f"skill-{i}",
                "proposed_effort": "medium",
                "confidence": 0.75,
                "reason": "content_lines=120, default",
            },
        }
        for i in range(3)
    ]
    props = generate_proposals(issues)
    assert len(props) == 3
    names = {p["issue"]["detail"]["skill_name"] for p in props}
    assert names == {"skill-0", "skill-1", "skill-2"}


def test_rationale_missing_effort_has_reason():
    issue = {
        "type": "missing_effort",
        "detail": {
            "skill_name": "orchestrate-x",
            "proposed_effort": "high",
            "confidence": 0.90,
            "reason": "allowed-tools includes Agent",
        },
    }
    r = generate_rationale(issue, "proposable")
    assert "orchestrate-x" in r
    assert "high" in r
    assert "allowed-tools includes Agent" in r
    assert "問題タイプ" not in r  # 汎用フォールバックでない
