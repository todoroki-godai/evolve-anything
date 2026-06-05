#!/usr/bin/env python3
"""skill_evolve の remediation データフロー統合 + rejected_stats プリフライトのテスト。

test_skill_evolve.py から分離。
- skill_evolve / tool_usage の assessment 結果が issue_schema factory 経由で remediation に注入される
- get_rejected_stats（jsonl 集計 / パス境界 substring 誤マッチ防止）
- evolve_skill_proposal の rejected_rate プリフライト（skip / proceed）
"""
import json
import sys
from pathlib import Path
from unittest import mock

_lib_dir = Path(__file__).resolve().parent.parent.parent / "scripts" / "lib"
sys.path.insert(0, str(_lib_dir))

from skill_evolve import evolve_skill_proposal
from issue_schema import (
    TOOL_USAGE_RULE_CANDIDATE,
    TOOL_USAGE_HOOK_CANDIDATE,
    SKILL_EVOLVE_CANDIDATE,
    SE_SUITABILITY,
    RULE_FILENAME,
    RULE_CONTENT,
    RULE_TARGET_COMMANDS,
    RULE_ALTERNATIVE_TOOLS,
    RULE_TOTAL_COUNT,
    HOOK_SCRIPT_PATH,
    HOOK_SCRIPT_CONTENT,
    HOOK_SETTINGS_DIFF,
    HOOK_TOTAL_COUNT,
    make_rule_candidate_issue,
    make_hook_candidate_issue,
    make_skill_evolve_issue,
)


# --- evolve.py データフロー統合テスト ---


def test_skill_evolve_issues_injected_into_remediation(tmp_path):
    """skill_evolve_assessment の結果が remediation の issues に注入されることを確認。"""
    evolve_scripts = Path(__file__).resolve().parent.parent.parent / "skills" / "evolve" / "scripts"
    sys.path.insert(0, str(evolve_scripts))
    from remediation import classify_issues as real_classify

    fake_assessment = [
        {
            "skill_name": "test-skill",
            "skill_dir": str(tmp_path / "test-skill"),
            "already_evolved": False,
            "suitability": "medium",
            "total_score": 10,
        },
    ]

    # skill_evolve 結果を変換（issue_schema factory 経由）
    issues = []
    for assessment in fake_assessment:
        suitability = assessment.get("suitability", "low")
        if suitability in ("high", "medium"):
            skill_md_path = str(Path(assessment["skill_dir"]) / "SKILL.md")
            issues.append(make_skill_evolve_issue(assessment, skill_md_path))

    assert len(issues) == 1
    assert issues[0]["type"] == SKILL_EVOLVE_CANDIDATE
    assert issues[0]["detail"][SE_SUITABILITY] == "medium"

    # classify_issues に渡せることを確認
    classified = real_classify(issues)
    # medium suitability → confidence 0.60 → proposable
    assert len(classified["proposable"]) == 1
    assert classified["proposable"][0]["confidence_score"] == 0.60


def test_tool_usage_issues_injected_into_remediation():
    """discover の tool_usage 結果が remediation の issues に注入されることを確認。

    tool_usage_analyzer の出力スキーマ → issue_schema factory → remediation classify。
    """
    from remediation import classify_issues as real_classify

    # tool_usage_analyzer が返す実際のスキーマ
    rule_candidates = [
        {
            RULE_FILENAME: "avoid-bash-builtin.md",
            RULE_CONTENT: "# test rule\ngrep は Grep。\nパイプは OK。\n",
            RULE_TARGET_COMMANDS: ["grep", "rg"],
            RULE_ALTERNATIVE_TOOLS: ["Grep"],
            RULE_TOTAL_COUNT: 5,
        }
    ]
    hook_candidate = {
        HOOK_SCRIPT_PATH: str(Path.home() / ".claude" / "hooks" / "check-bash-builtin.py"),
        HOOK_SCRIPT_CONTENT: "#!/usr/bin/env python3\n# hook",
        HOOK_SETTINGS_DIFF: '{"hooks": {}}',
        "target_commands": ["grep", "rg"],
    }

    rules_dir_str = str(Path.home() / ".claude" / "rules")
    issues = []
    for rc in rule_candidates:
        issues.append(make_rule_candidate_issue(rc, rules_dir_str=rules_dir_str))
    total_count = 5
    issues.append(make_hook_candidate_issue(hook_candidate, total_count))

    assert len(issues) == 2
    assert issues[0]["type"] == TOOL_USAGE_RULE_CANDIDATE
    assert issues[1]["type"] == TOOL_USAGE_HOOK_CANDIDATE

    # detail フィールドが正しく設定されていることを確認
    assert issues[0]["detail"][RULE_FILENAME] == "avoid-bash-builtin.md"
    assert issues[0]["detail"][RULE_TOTAL_COUNT] == 5
    assert issues[1]["detail"][HOOK_SCRIPT_PATH] != ""
    assert issues[1]["detail"][HOOK_TOTAL_COUNT] == 5

    # classify_issues に渡せることを確認
    classified = real_classify(issues)
    # global scope → proposable
    assert len(classified["proposable"]) == 2


def test_low_suitability_not_injected():
    """low suitability のスキルは issue に変換されないことを確認。"""
    fake_assessment = [
        {
            "skill_name": "simple-skill",
            "skill_dir": "/tmp/simple-skill",
            "already_evolved": False,
            "suitability": "low",
            "total_score": 6,
        },
    ]

    issues = []
    for assessment in fake_assessment:
        suitability = assessment.get("suitability", "low")
        if suitability in ("high", "medium"):
            skill_md_path = str(Path(assessment["skill_dir"]) / "SKILL.md")
            issues.append(make_skill_evolve_issue(assessment, skill_md_path))

    assert len(issues) == 0


# --- get_rejected_stats (#200) ---


def test_get_rejected_stats_no_file(tmp_path, monkeypatch):
    """remediation-outcomes.jsonl が存在しない場合は graceful degradation。"""
    import trigger_engine.self_evolution as se_mod
    import trigger_engine as te_mod

    # DATA_DIR は lazy lookup で `from . import DATA_DIR` を使う。
    # trigger_engine パッケージの DATA_DIR をパッチする。
    monkeypatch.setattr(te_mod, "DATA_DIR", tmp_path)
    stats = se_mod.get_rejected_stats("my-skill")

    assert stats["rejected_count"] == 0
    assert stats["total_count"] == 0
    assert stats["rejected_rate"] == 0.0


def test_get_rejected_stats_with_data(tmp_path, monkeypatch):
    """rejected_rate が正しく計算される。"""
    import trigger_engine.self_evolution as se_mod
    import trigger_engine as te_mod

    outcomes_file = tmp_path / "remediation-outcomes.jsonl"
    records = []
    # 4 rejected, 6 total → rate 0.40
    for i in range(4):
        records.append(json.dumps({
            "issue_type": "skill_evolve_candidate",
            "file": ".claude/skills/my-skill/SKILL.md",
            "user_decision": "rejected",
            "result": "rejected",
            "timestamp": "2026-05-01T00:00:00+00:00",
        }))
    for i in range(6):
        records.append(json.dumps({
            "issue_type": "skill_evolve_candidate",
            "file": ".claude/skills/my-skill/SKILL.md",
            "user_decision": "approved",
            "result": "success",
            "timestamp": "2026-05-01T00:00:00+00:00",
        }))
    outcomes_file.write_text("\n".join(records))

    monkeypatch.setattr(te_mod, "DATA_DIR", tmp_path)
    stats = se_mod.get_rejected_stats("my-skill")

    assert stats["rejected_count"] == 4
    assert stats["total_count"] == 10
    assert abs(stats["rejected_rate"] - 0.4) < 0.01


# --- get_rejected_stats substring match fix (#P2) ---


def test_get_rejected_stats_no_substring_match(tmp_path, monkeypatch):
    """'review' スキルが 'code-review' のレコードにマッチしないことを確認 (#P2)。"""
    import trigger_engine.self_evolution as se_mod
    import trigger_engine as te_mod

    outcomes_file = tmp_path / "remediation-outcomes.jsonl"
    # "code-review" のレコードのみ存在（"review" スキルを検索しても 0 件になるべき）
    records = [
        json.dumps({
            "issue_type": "skill_evolve_candidate",
            "file": ".claude/skills/code-review/SKILL.md",
            "user_decision": "rejected",
            "timestamp": "2026-05-01T00:00:00+00:00",
        }),
    ]
    outcomes_file.write_text("\n".join(records))

    monkeypatch.setattr(te_mod, "DATA_DIR", tmp_path)
    stats = se_mod.get_rejected_stats("review")

    # "review" は "code-review" のパスにサブストリングとしては含まれるが
    # パス境界チェックで除外されるべき
    assert stats["total_count"] == 0
    assert stats["rejected_count"] == 0
    assert stats["rejected_rate"] == 0.0


# --- rejected pre-flight in evolve_skill_proposal (#200) ---


def test_evolve_skill_proposal_skip_when_high_rejected_rate(tmp_path, monkeypatch):
    """rejected_rate > 30% のスキルは evolve をスキップする (#200)。"""
    templates_dir = tmp_path / "skills" / "evolve" / "templates"
    templates_dir.mkdir(parents=True)
    (templates_dir / "self-evolve-sections.md").write_text(
        "## Pre-flight Check\n\n## Failure-triggered Learning\n"
    )
    (templates_dir / "pitfalls.md").write_text("## Active Pitfalls\n")

    skill_dir = tmp_path / "test-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# Test Skill\n")

    monkeypatch.setattr("skill_evolve._plugin_root", tmp_path)

    with mock.patch(
        "skill_evolve.proposal.get_rejected_stats",
        return_value={"rejected_rate": 0.40, "rejected_count": 4, "total_count": 10},
    ):
        result = evolve_skill_proposal("test-skill", skill_dir)

    assert result.get("status") == "skipped"
    assert "rejected_rate" in result.get("reason", "")


def test_evolve_skill_proposal_proceed_when_low_rejected_rate(tmp_path, monkeypatch):
    """rejected_rate <= 30% なら通常通り処理する (#200)。"""
    templates_dir = tmp_path / "skills" / "evolve" / "templates"
    templates_dir.mkdir(parents=True)
    (templates_dir / "self-evolve-sections.md").write_text(
        "## Pre-flight Check\n\n## Failure-triggered Learning\n"
    )
    (templates_dir / "pitfalls.md").write_text("## Active Pitfalls\n")

    skill_dir = tmp_path / "test-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# Test Skill\n")

    monkeypatch.setattr("skill_evolve._plugin_root", tmp_path)

    with mock.patch(
        "skill_evolve.proposal.get_rejected_stats",
        return_value={"rejected_rate": 0.20, "rejected_count": 2, "total_count": 10},
    ):
        result = evolve_skill_proposal("test-skill", skill_dir)

    assert result.get("error") is None
    assert result.get("status") != "skipped"


def test_evolve_skill_proposal_proceed_when_no_stats(tmp_path, monkeypatch):
    """jsonl 不在 (stats 全0) でも skip にならない (#200 graceful degradation)。"""
    templates_dir = tmp_path / "skills" / "evolve" / "templates"
    templates_dir.mkdir(parents=True)
    (templates_dir / "self-evolve-sections.md").write_text(
        "## Pre-flight Check\n\n## Failure-triggered Learning\n"
    )
    (templates_dir / "pitfalls.md").write_text("## Active Pitfalls\n")

    skill_dir = tmp_path / "test-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# Test Skill\n")

    monkeypatch.setattr("skill_evolve._plugin_root", tmp_path)

    with mock.patch(
        "skill_evolve.proposal.get_rejected_stats",
        return_value={"rejected_rate": 0.0, "rejected_count": 0, "total_count": 0},
    ):
        result = evolve_skill_proposal("test-skill", skill_dir)

    # skipped にならない
    assert result.get("status") != "skipped"
