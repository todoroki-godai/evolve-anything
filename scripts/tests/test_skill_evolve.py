#!/usr/bin/env python3
"""skill_evolve.py のテスト。"""
import json
import sys
from pathlib import Path
from unittest import mock

import pytest

_lib_dir = Path(__file__).resolve().parent.parent.parent / "scripts" / "lib"
sys.path.insert(0, str(_lib_dir))

from skill_evolve.proposal import count_diff_lines as _count_diff_lines
from skill_evolve import (
    ANTI_PATTERN_REJECTION_COUNT,
    BAND_AID_THRESHOLD,
    HIGH_SUITABILITY_THRESHOLD,
    MEDIUM_SUITABILITY_THRESHOLD,
    VERIFICATION_SKILL_KEYWORDS,
    _count_external_keywords,
    _score_execution_frequency,
    _score_external_dependency,
    _score_failure_diversity,
    _score_output_evaluability,
    apply_evolve_proposal,
    assess_single_skill,
    classify_suitability,
    detect_anti_patterns,
    evolve_skill_proposal,
    is_self_evolved_skill,
    is_verification_skill,
)
from issue_schema import (
    TOOL_USAGE_RULE_CANDIDATE,
    TOOL_USAGE_HOOK_CANDIDATE,
    SKILL_EVOLVE_CANDIDATE,
    SE_SKILL_NAME,
    SE_SKILL_DIR,
    SE_SUITABILITY,
    SE_TOTAL_SCORE,
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


# --- is_self_evolved_skill ---


def test_self_evolved_with_both(tmp_path):
    skill_dir = tmp_path / "my-skill"
    skill_dir.mkdir()
    refs = skill_dir / "references"
    refs.mkdir()
    (refs / "pitfalls.md").write_text("# Pitfalls\n")
    (skill_dir / "SKILL.md").write_text(
        "# My Skill\n\n## Failure-triggered Learning\n\nsome content\n"
    )
    assert is_self_evolved_skill(skill_dir) is True


def test_self_evolved_missing_pitfalls(tmp_path):
    skill_dir = tmp_path / "my-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "# My Skill\n\n## Failure-triggered Learning\n"
    )
    assert is_self_evolved_skill(skill_dir) is False


def test_self_evolved_missing_section(tmp_path):
    skill_dir = tmp_path / "my-skill"
    skill_dir.mkdir()
    refs = skill_dir / "references"
    refs.mkdir()
    (refs / "pitfalls.md").write_text("# Pitfalls\n")
    (skill_dir / "SKILL.md").write_text("# My Skill\n\nNo special section\n")
    assert is_self_evolved_skill(skill_dir) is False


# --- テレメトリスコアリング ---


def test_frequency_scoring():
    assert _score_execution_frequency(0) == 1
    assert _score_execution_frequency(3) == 1
    assert _score_execution_frequency(4) == 2
    assert _score_execution_frequency(15) == 2
    assert _score_execution_frequency(16) == 3
    assert _score_execution_frequency(100) == 3


def test_diversity_scoring():
    assert _score_failure_diversity(set()) == 1
    assert _score_failure_diversity({"a"}) == 1
    assert _score_failure_diversity({"a", "b"}) == 2
    assert _score_failure_diversity({"a", "b", "c"}) == 2
    assert _score_failure_diversity({"a", "b", "c", "d"}) == 3


def test_evaluability_scoring():
    assert _score_output_evaluability(0, 0) == 1
    assert _score_output_evaluability(10, 0) == 1  # 100% success = hard to eval
    assert _score_output_evaluability(10, 3) == 2  # 70% success
    assert _score_output_evaluability(10, 6) == 3  # 40% success


# --- LLM軸（静的解析部分） ---


def test_external_dependency_scoring():
    assert _score_external_dependency("simple local tool") == 1
    assert _score_external_dependency("uses API and HTTP calls with deploy") == 2
    content_heavy = "AWS CDK deploy Lambda S3 SNS SQS DynamoDB Bedrock API HTTP MCP"
    assert _score_external_dependency(content_heavy) == 3


def test_external_keyword_count():
    assert _count_external_keywords("no keywords here") == 0
    assert _count_external_keywords("deploy to AWS using API") >= 3


# --- 分類 ---


def test_classify_suitability():
    assert classify_suitability(5) == "low"
    assert classify_suitability(7) == "low"
    assert classify_suitability(8) == "medium"
    assert classify_suitability(11) == "medium"
    assert classify_suitability(12) == "high"
    assert classify_suitability(15) == "high"


# --- アンチパターン検出 ---


def test_noise_collector_detection(tmp_path):
    skill_dir = tmp_path / "test-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# Simple\n")
    # error_count > 0 のときのみ Noise Collector を検出
    patterns = detect_anti_patterns(
        {"diversity": 1, "frequency": 2, "judgment_complexity": 2, "error_count": 3},
        skill_dir,
    )
    assert any(p["pattern"] == "Noise Collector" for p in patterns)


def test_noise_collector_not_triggered_without_errors(tmp_path):
    """エラーデータなし（テレメトリ不在）では Noise Collector は検出しない。"""
    skill_dir = tmp_path / "test-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# Simple\n")
    patterns = detect_anti_patterns(
        {"diversity": 1, "frequency": 2, "judgment_complexity": 2, "error_count": 0},
        skill_dir,
    )
    assert not any(p["pattern"] == "Noise Collector" for p in patterns)


def test_context_bloat_detection(tmp_path):
    skill_dir = tmp_path / "test-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# Simple\n")
    patterns = detect_anti_patterns(
        {"diversity": 2, "frequency": 3, "judgment_complexity": 1, "error_count": 5},
        skill_dir,
    )
    assert any(p["pattern"] == "Context Bloat" for p in patterns)


def test_band_aid_detection(tmp_path):
    skill_dir = tmp_path / "test-skill"
    skill_dir.mkdir()
    # Band-Aid は references/ のみカウント（SKILL.md のステップは除外）
    refs = skill_dir / "references"
    refs.mkdir()
    items = "\n".join(f"- item {i}" for i in range(BAND_AID_THRESHOLD + 1))
    (refs / "troubleshoot.md").write_text(f"# Troubleshoot\n\n{items}\n")
    (skill_dir / "SKILL.md").write_text("# Skill\n")
    patterns = detect_anti_patterns(
        {"diversity": 2, "frequency": 2, "judgment_complexity": 2, "error_count": 5},
        skill_dir,
    )
    assert any(p["pattern"] == "Band-Aid" for p in patterns)


def test_band_aid_not_triggered_by_skill_md_steps(tmp_path):
    """SKILL.md の手順リストだけでは Band-Aid は検出されない。"""
    skill_dir = tmp_path / "test-skill"
    skill_dir.mkdir()
    items = "\n".join(f"- step {i}" for i in range(50))
    (skill_dir / "SKILL.md").write_text(f"# Skill\n\n{items}\n")
    patterns = detect_anti_patterns(
        {"diversity": 2, "frequency": 2, "judgment_complexity": 2, "error_count": 5},
        skill_dir,
    )
    assert not any(p["pattern"] == "Band-Aid" for p in patterns)


def test_no_anti_patterns(tmp_path):
    skill_dir = tmp_path / "test-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# Simple\n- one\n- two\n")
    patterns = detect_anti_patterns(
        {"diversity": 3, "frequency": 2, "judgment_complexity": 2, "error_count": 5},
        skill_dir,
    )
    assert len(patterns) == 0


# --- キャッシュ ---


def test_llm_cache_hit(tmp_path, monkeypatch):
    from skill_evolve import compute_llm_scores, _file_hash, CACHE_FILE

    skill_dir = tmp_path / "my-skill"
    skill_dir.mkdir()
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text("# Test Skill\n")

    # キャッシュを事前設定
    cache = {
        "my-skill": {
            "hash": _file_hash(skill_md),
            "external_dependency": 2,
            "judgment_complexity": 3,
        }
    }
    cache_file = tmp_path / "cache.json"
    cache_file.write_text(json.dumps(cache))

    monkeypatch.setattr("skill_evolve.CACHE_FILE", cache_file)
    result = compute_llm_scores("my-skill", skill_dir)
    assert result["cached"] is True
    assert result["external_dependency"] == 2
    assert result["judgment_complexity"] == 3


# --- evolve_skill_proposal ---


def test_proposal_template_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "skill_evolve._plugin_root",
        tmp_path / "nonexistent",
    )
    result = evolve_skill_proposal("test-skill", tmp_path / "test-skill")
    assert result["error"] is not None
    assert "テンプレートファイルが見つかりません" in result["error"]


def test_proposal_with_templates(tmp_path, monkeypatch):
    # テンプレートディレクトリを作成
    templates_dir = tmp_path / "skills" / "evolve" / "templates"
    templates_dir.mkdir(parents=True)
    (templates_dir / "self-evolve-sections.md").write_text(
        "## Pre-flight Check\n\nCheck pitfalls.\n\n"
        "## Failure-triggered Learning\n\n| Trigger | Action |\n"
    )
    (templates_dir / "pitfalls.md").write_text(
        "## Active Pitfalls\n\n## Candidate Pitfalls\n\n## Graduated Pitfalls\n"
    )

    # スキルディレクトリ
    skill_dir = tmp_path / "test-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# Test Skill\n")

    monkeypatch.setattr("skill_evolve._plugin_root", tmp_path)

    # [ADR-037] Phase 1c: LLM-free。決定論フォールバック（テンプレそのまま）を返す
    result = evolve_skill_proposal("test-skill", skill_dir)

    assert result["error"] is None
    assert "Pre-flight" in result["sections_to_add"]
    assert "Active Pitfalls" in result["pitfalls_template"]


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


# --- assess_single_skill ---


def test_assess_single_skill_already_evolved(tmp_path):
    """既に自己進化済みのスキルは suitability='already_evolved' を返す。"""
    skill_dir = tmp_path / "my-skill"
    skill_dir.mkdir()
    refs = skill_dir / "references"
    refs.mkdir()
    (refs / "pitfalls.md").write_text("# Pitfalls\n")
    (skill_dir / "SKILL.md").write_text(
        "# My Skill\n\n## Failure-triggered Learning\n\nsome content\n"
    )
    result = assess_single_skill("my-skill", skill_dir)
    assert result["suitability"] == "already_evolved"
    assert result["already_evolved"] is True


@mock.patch("skill_evolve.compute_llm_scores")
@mock.patch("skill_evolve.compute_telemetry_scores")
def test_assess_single_skill_high(mock_telemetry, mock_llm, tmp_path):
    """高適性スキルの判定。"""
    skill_dir = tmp_path / "my-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# My Skill\n")

    mock_telemetry.return_value = {
        "frequency": 3, "diversity": 3, "evaluability": 3,
        "usage_count": 20, "error_count": 8,
        "error_categories": ["a", "b", "c", "d"],
    }
    mock_llm.return_value = {
        "external_dependency": 2, "judgment_complexity": 3, "cached": True,
    }
    result = assess_single_skill("my-skill", skill_dir)
    assert result["suitability"] == "high"
    assert result["total_score"] >= HIGH_SUITABILITY_THRESHOLD


@mock.patch("skill_evolve.compute_llm_scores")
@mock.patch("skill_evolve.compute_telemetry_scores")
def test_assess_single_skill_medium(mock_telemetry, mock_llm, tmp_path):
    """中適性スキルの判定。"""
    skill_dir = tmp_path / "my-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# My Skill\n")

    mock_telemetry.return_value = {
        "frequency": 2, "diversity": 2, "evaluability": 1,
        "usage_count": 10, "error_count": 0,
        "error_categories": ["a", "b"],
    }
    mock_llm.return_value = {
        "external_dependency": 1, "judgment_complexity": 2, "cached": True,
    }
    result = assess_single_skill("my-skill", skill_dir)
    # total = 2+2+1+1+2+0 = 8 → medium (>= 8, < 12)
    assert result["suitability"] == "medium"


@mock.patch("skill_evolve.compute_llm_scores")
@mock.patch("skill_evolve.compute_telemetry_scores")
def test_assess_single_skill_low(mock_telemetry, mock_llm, tmp_path):
    """低適性スキルの判定。"""
    skill_dir = tmp_path / "my-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# My Skill\n")

    mock_telemetry.return_value = {
        "frequency": 1, "diversity": 1, "evaluability": 1,
        "usage_count": 2, "error_count": 0,
        "error_categories": [],
    }
    mock_llm.return_value = {
        "external_dependency": 1, "judgment_complexity": 1, "cached": True,
    }
    result = assess_single_skill("my-skill", skill_dir)
    assert result["suitability"] == "low"


@mock.patch("skill_evolve.compute_llm_scores")
@mock.patch("skill_evolve.compute_telemetry_scores")
def test_assess_single_skill_rejected(mock_telemetry, mock_llm, tmp_path):
    """アンチパターン2件以上で rejected。"""
    skill_dir = tmp_path / "my-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# My Skill\n")
    # Band-Aid 用の references/ を作成
    refs = skill_dir / "references"
    refs.mkdir()
    items = "\n".join(f"- item {i}" for i in range(BAND_AID_THRESHOLD + 1))
    (refs / "troubleshoot.md").write_text(f"# Troubleshoot\n\n{items}\n")

    # Noise Collector (diversity=1, error_count>0) + Band-Aid
    mock_telemetry.return_value = {
        "frequency": 3, "diversity": 1, "evaluability": 3,
        "usage_count": 20, "error_count": 5,
        "error_categories": ["a"],
    }
    mock_llm.return_value = {
        "external_dependency": 1, "judgment_complexity": 1, "cached": True,
    }
    result = assess_single_skill("my-skill", skill_dir)
    assert result["suitability"] == "rejected"


# --- is_verification_skill ---


def test_is_verification_skill_by_name():
    """スキル名にverify/check/validate/lint/test/qa等が含まれる場合にTrueを返す。"""
    for name in ["godot-verify", "pre-check", "validate-schema", "lint-code", "qa", "qa-only"]:
        assert is_verification_skill(name, Path("/dummy")) is True, f"{name} should be verification"


def test_is_verification_skill_not_matched():
    """verify系キーワードを含まないスキル名はFalseを返す。"""
    for name in ["deploy", "commit", "design-review", "office-hours"]:
        assert is_verification_skill(name, Path("/dummy")) is False, f"{name} should NOT be verification"


def test_is_verification_skill_by_content(tmp_path):
    """スキル名はマッチしないが、SKILL.md内容にverify系キーワードがある場合にTrueを返す。"""
    skill_dir = tmp_path / "my-tool"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "# My Tool\n\nThis skill validates and verifies deployment results.\n"
    )
    assert is_verification_skill("my-tool", skill_dir) is True


def test_is_verification_skill_content_no_match(tmp_path):
    """スキル名もSKILL.md内容もマッチしない場合はFalseを返す。"""
    skill_dir = tmp_path / "my-tool"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# My Tool\n\nThis skill creates documents.\n")
    assert is_verification_skill("my-tool", skill_dir) is False


@mock.patch("skill_evolve.compute_llm_scores")
@mock.patch("skill_evolve.compute_telemetry_scores")
def test_assess_single_skill_verification_bypass(mock_telemetry, mock_llm, tmp_path):
    """verify系スキルはテレメトリが低くてもmediumに昇格する。"""
    skill_dir = tmp_path / "godot-verify"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# Godot Verify\n")

    mock_telemetry.return_value = {
        "frequency": 1, "diversity": 1, "evaluability": 1,
        "usage_count": 0, "error_count": 0,
        "error_categories": [],
    }
    mock_llm.return_value = {
        "external_dependency": 1, "judgment_complexity": 1, "cached": True,
    }
    result = assess_single_skill("godot-verify", skill_dir)
    assert result["suitability"] == "medium"
    assert "verification_bypass" in result
    assert result["verification_bypass"] is True
    assert "検証系" in result["recommendation"]


@mock.patch("skill_evolve.compute_llm_scores")
@mock.patch("skill_evolve.compute_telemetry_scores")
def test_assess_single_skill_verification_high_unchanged(mock_telemetry, mock_llm, tmp_path):
    """verify系スキルでもhigh以上なら昇格不要でそのまま。"""
    skill_dir = tmp_path / "godot-verify"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# Godot Verify\n")

    mock_telemetry.return_value = {
        "frequency": 3, "diversity": 3, "evaluability": 3,
        "usage_count": 20, "error_count": 8,
        "error_categories": ["a", "b", "c", "d"],
    }
    mock_llm.return_value = {
        "external_dependency": 2, "judgment_complexity": 3, "cached": True,
    }
    result = assess_single_skill("godot-verify", skill_dir)
    assert result["suitability"] == "high"
    # high のまま — verification_bypass は付かない or False
    assert not result.get("verification_bypass", False)


# --- apply_evolve_proposal ---


def test_apply_evolve_proposal_success(tmp_path, monkeypatch):
    """正常適用: SKILL.md にセクション追記、references/pitfalls.md 作成、バックアップ作成。"""
    skill_dir = tmp_path / "my-skill"
    skill_dir.mkdir()
    original_content = "# My Skill\n\nOriginal content.\n"
    (skill_dir / "SKILL.md").write_text(original_content)

    proposal = {
        "skill_name": "my-skill",
        "sections_to_add": "## Pre-flight Check\n\n## Failure-triggered Learning\n",
        "pitfalls_template": "## Active Pitfalls\n\n## Graduated Pitfalls\n",
        "skill_md_path": str(skill_dir / "SKILL.md"),
        "pitfalls_path": str(skill_dir / "references" / "pitfalls.md"),
        "error": None,
    }

    result = apply_evolve_proposal(proposal)
    assert result["applied"] is True
    assert result["error"] is None

    # SKILL.md にセクション追記されている
    updated = (skill_dir / "SKILL.md").read_text()
    assert "Pre-flight Check" in updated
    assert "Original content" in updated

    # references/pitfalls.md が作成されている
    pitfalls = skill_dir / "references" / "pitfalls.md"
    assert pitfalls.exists()
    assert "Active Pitfalls" in pitfalls.read_text()

    # バックアップが作成されている
    backup = skill_dir / "SKILL.md.pre-evolve-backup"
    assert backup.exists()
    assert backup.read_text() == original_content


def test_apply_evolve_proposal_creates_references_dir(tmp_path):
    """references/ ディレクトリがなくても自動作成される。"""
    skill_dir = tmp_path / "my-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# My Skill\n")

    proposal = {
        "skill_name": "my-skill",
        "sections_to_add": "## Failure-triggered Learning\n",
        "pitfalls_template": "## Active Pitfalls\n",
        "skill_md_path": str(skill_dir / "SKILL.md"),
        "pitfalls_path": str(skill_dir / "references" / "pitfalls.md"),
        "error": None,
    }

    result = apply_evolve_proposal(proposal)
    assert result["applied"] is True
    assert (skill_dir / "references").is_dir()
    assert (skill_dir / "references" / "pitfalls.md").exists()


def test_apply_evolve_proposal_with_error():
    """proposal にエラーがある場合は適用しない。"""
    proposal = {
        "skill_name": "my-skill",
        "error": "テンプレートが見つかりません",
    }

    result = apply_evolve_proposal(proposal)
    assert result["applied"] is False
    assert result["error"] == "テンプレートが見つかりません"


def test_apply_evolve_proposal_backup_path_in_result(tmp_path):
    """結果に backup_path が含まれる。"""
    skill_dir = tmp_path / "my-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# My Skill\n")

    proposal = {
        "skill_name": "my-skill",
        "sections_to_add": "## Failure-triggered Learning\n",
        "pitfalls_template": "## Active Pitfalls\n",
        "skill_md_path": str(skill_dir / "SKILL.md"),
        "pitfalls_path": str(skill_dir / "references" / "pitfalls.md"),
        "error": None,
    }

    result = apply_evolve_proposal(proposal)
    assert "backup_path" in result
    assert result["backup_path"].endswith(".pre-evolve-backup")


# --- workflow_checkpoints in assess_single_skill ---


@mock.patch("skill_evolve.compute_llm_scores")
@mock.patch("skill_evolve.compute_telemetry_scores")
def test_assess_workflow_skill_has_checkpoints(mock_telemetry, mock_llm, tmp_path):
    """ワークフロースキルの assessment に workflow_checkpoints が含まれる。"""
    # .claude/skills/my-skill/ 構造を作成
    project_dir = tmp_path / "project"
    claude_dir = project_dir / ".claude" / "skills" / "my-skill"
    claude_dir.mkdir(parents=True)
    (claude_dir / "SKILL.md").write_text(
        "---\ntype: workflow\n---\n\n# My Skill\n\n1. Step 1: Check\n2. Step 2: Do\n3. Step 3: Report\n"
    )

    mock_telemetry.return_value = {
        "frequency": 2, "diversity": 2, "evaluability": 2,
        "usage_count": 10, "error_count": 3,
        "error_categories": ["a", "b"],
    }
    mock_llm.return_value = {
        "external_dependency": 1, "judgment_complexity": 1, "cached": True,
    }
    result = assess_single_skill("my-skill", claude_dir)
    assert "workflow_checkpoints" in result
    # workflow_checkpoints はリスト（空でも可）
    assert isinstance(result["workflow_checkpoints"], list)


@mock.patch("skill_evolve.compute_llm_scores")
@mock.patch("skill_evolve.compute_telemetry_scores")
def test_assess_non_workflow_skill_has_none_checkpoints(mock_telemetry, mock_llm, tmp_path):
    """非ワークフロースキルでは workflow_checkpoints が None。"""
    skill_dir = tmp_path / "my-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# Simple Utility\n\nDoes one thing.\n")

    mock_telemetry.return_value = {
        "frequency": 2, "diversity": 2, "evaluability": 2,
        "usage_count": 10, "error_count": 3,
        "error_categories": ["a", "b"],
    }
    mock_llm.return_value = {
        "external_dependency": 1, "judgment_complexity": 1, "cached": True,
    }
    result = assess_single_skill("my-skill", skill_dir)
    assert result["workflow_checkpoints"] is None


def test_assess_already_evolved_has_none_checkpoints(tmp_path):
    """already_evolved のスキルでは workflow_checkpoints が None。"""
    skill_dir = tmp_path / "my-skill"
    skill_dir.mkdir()
    refs = skill_dir / "references"
    refs.mkdir()
    (refs / "pitfalls.md").write_text("# Pitfalls\n")
    (skill_dir / "SKILL.md").write_text(
        "# My Skill\n\n## Failure-triggered Learning\n\nsome content\n"
    )
    result = assess_single_skill("my-skill", skill_dir)
    assert result["workflow_checkpoints"] is None


# --- _count_diff_lines (#196) ---


def test_count_diff_lines_small_change():
    """少ない変更行数は正確にカウントされる。"""
    original = "line1\nline2\nline3\n"
    modified = "line1\nline2_changed\nline3\n"
    count = _count_diff_lines(original, modified)
    assert count == 2  # 1 removed + 1 added


def test_count_diff_lines_no_change():
    """変更なしは 0 を返す。"""
    text = "line1\nline2\nline3\n"
    assert _count_diff_lines(text, text) == 0


def test_count_diff_lines_many_changes():
    """多数の変更行数が正確にカウントされる。"""
    original = "\n".join(f"line{i}" for i in range(40))
    modified = "\n".join(f"changed{i}" for i in range(40))
    count = _count_diff_lines(original, modified)
    # 40 removed + 40 added = 80
    assert count == 80


# --- difflib bounded edit gate in _parse_customization_response (#196, #199, [ADR-037] Phase 1c) ---


def test_parse_customization_within_budget(tmp_path, monkeypatch):
    """diff 行数がバジェット以内なら Phase B 出力をそのまま返す。"""
    template = "## Pre-flight Check\n\n## Failure-triggered Learning\n"
    # Phase B が 2 行変更した出力（budget=30 以内）
    customized_output = "## Pre-flight Check (custom)\n\n## Failure-triggered Learning\n"

    from skill_evolve.proposal import _parse_customization_response
    result = _parse_customization_response(customized_output, template, budget=30)

    assert "custom" in result


def test_parse_customization_exceeds_budget_fallback(tmp_path, monkeypatch):
    """diff 行数がバジェットを超えた場合はテンプレートにフォールバックする (#196)。"""
    template = "## Pre-flight Check\n\n## Failure-triggered Learning\n"
    # Phase B が多数の行を変更した出力（budget=5 を超える）
    many_lines = "\n".join(f"changed line {i}" for i in range(20))

    from skill_evolve.proposal import _parse_customization_response
    result = _parse_customization_response(many_lines, template, budget=5)

    # フォールバックでテンプレートがそのまま返る
    assert result == template


def test_parse_customization_budget_override(tmp_path):
    """budget=10 が正しく判定に使われる (#199)。"""
    template = "original line 1\noriginal line 2\noriginal line 3\n"
    # 11 行変更 (budget=10 を 1 超)
    changed_lines = "\n".join(f"changed {i}" for i in range(11))

    from skill_evolve.proposal import _parse_customization_response
    result = _parse_customization_response(changed_lines, template, budget=10)

    assert result == template  # fallback


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


# --- reason_refs in apply_evolve_proposal (#201) ---


def test_apply_evolve_proposal_reason_refs_in_frontmatter(tmp_path, monkeypatch):
    """apply_evolve_proposal 後の SKILL.md frontmatter に reason_refs が含まれる (#201)。"""
    skill_dir = tmp_path / "my-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# My Skill\n\nOriginal content.\n")

    corrections_file = tmp_path / "corrections.jsonl"
    corrections_file.write_text(
        json.dumps({"id": "corr-001", "last_skill": "my-skill", "timestamp": "2026-05-01T00:00:00+00:00"}) + "\n" +
        json.dumps({"id": "corr-002", "last_skill": "my-skill", "timestamp": "2026-05-01T01:00:00+00:00"}) + "\n"
    )

    proposal = {
        "skill_name": "my-skill",
        "sections_to_add": "## Pre-flight Check\n\n## Failure-triggered Learning\n",
        "pitfalls_template": "## Active Pitfalls\n",
        "skill_md_path": str(skill_dir / "SKILL.md"),
        "pitfalls_path": str(skill_dir / "references" / "pitfalls.md"),
        "error": None,
        "correction_ids": ["corr-001", "corr-002"],
    }

    result = apply_evolve_proposal(proposal)
    assert result["applied"] is True

    updated = (skill_dir / "SKILL.md").read_text()
    assert "reason_refs" in updated
    assert "corr-001" in updated
    assert "corr-002" in updated


def test_apply_evolve_proposal_no_reason_refs_when_empty(tmp_path):
    """correction_ids が空またはない場合は reason_refs なしでも正常適用できる (#201)。"""
    skill_dir = tmp_path / "my-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# My Skill\n\nOriginal content.\n")

    proposal = {
        "skill_name": "my-skill",
        "sections_to_add": "## Pre-flight Check\n\n## Failure-triggered Learning\n",
        "pitfalls_template": "## Active Pitfalls\n",
        "skill_md_path": str(skill_dir / "SKILL.md"),
        "pitfalls_path": str(skill_dir / "references" / "pitfalls.md"),
        "error": None,
        # correction_ids なし
    }

    result = apply_evolve_proposal(proposal)
    assert result["applied"] is True


# --- apply_evolve_proposal skipped guard (#P1) ---


def test_apply_evolve_proposal_skipped_returns_early():
    """proposal が status:skipped の場合は KeyError なく早期リターンする。"""
    result = apply_evolve_proposal({"status": "skipped", "reason": "rejected_rate=35%"})
    assert result["applied"] is False
    assert result["skipped"] is True
    assert result["reason"] == "rejected_rate=35%"


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


# --- denylist ---


_denylist_lib = Path(__file__).resolve().parent.parent.parent / "scripts" / "lib"


class TestDenylist:
    def _import(self, monkeypatch, tmp_path):
        import importlib
        monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path))
        import skill_evolve.denylist as dl_mod
        importlib.reload(dl_mod)
        return dl_mod

    def test_denylist_load_empty(self, monkeypatch, tmp_path):
        dl = self._import(monkeypatch, tmp_path)
        assert dl.get_denied_skill_names() == set()

    def test_denylist_add_and_get(self, monkeypatch, tmp_path):
        dl = self._import(monkeypatch, tmp_path)
        dl.add_to_denylist(["skill-a", "skill-b"])
        denied = dl.get_denied_skill_names()
        assert "skill-a" in denied
        assert "skill-b" in denied

    def test_denylist_persist(self, monkeypatch, tmp_path):
        dl = self._import(monkeypatch, tmp_path)
        dl.add_to_denylist(["persistent-skill"])
        data = dl.load_denylist()
        assert "persistent-skill" in data["skills"]
        assert "reason" in data["skills"]["persistent-skill"]
        assert "denied_at" in data["skills"]["persistent-skill"]

    def test_remove_from_denylist(self, monkeypatch, tmp_path):
        dl = self._import(monkeypatch, tmp_path)
        dl.add_to_denylist(["skill-x", "skill-y"])
        dl.remove_from_denylist(["skill-x"])
        denied = dl.get_denied_skill_names()
        assert "skill-x" not in denied
        assert "skill-y" in denied


# --- assessment batch guard ---


def _make_skill_path(tmp_path, name, subdir=".claude/skills"):
    skill_dir = tmp_path / subdir / name
    skill_dir.mkdir(parents=True)
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text(f"# {name}\n")
    return skill_md


class TestBatchGuardAssessment:
    def test_batch_guard_returns_meta_when_over_limit(self, monkeypatch, tmp_path):
        """11件の custom スキルで batch_guard_trigger sentinel が返る。"""
        import importlib
        monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path))
        import skill_evolve.denylist as dl_mod
        importlib.reload(dl_mod)

        skill_paths = [_make_skill_path(tmp_path, f"skill-{i}") for i in range(11)]
        origins = ["custom"] * 11

        cfg_mock = mock.MagicMock()
        cfg_mock.get.return_value = ""

        with mock.patch("skill_evolve.assessment.find_artifacts", return_value={"skills": skill_paths}), \
             mock.patch("skill_evolve.assessment.classify_artifact_origin", side_effect=lambda p: origins[skill_paths.index(p)]), \
             mock.patch("skill_evolve.assessment.load_user_config", return_value=cfg_mock), \
             mock.patch("skill_evolve.denylist.DATA_DIR", tmp_path):
            from skill_evolve.assessment import skill_evolve_assessment
            result = skill_evolve_assessment(tmp_path)

        sentinel = next((r for r in result if r.get("_meta") == "batch_guard_trigger"), None)
        assert sentinel is not None, "batch_guard_trigger sentinel が返されるべき"
        assert sentinel["total_effective"] == 11

    def test_batch_guard_groups_structure(self, monkeypatch, tmp_path):
        """sentinel の groups が origin 別に構造化される。"""
        import importlib
        monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path))
        import skill_evolve.denylist as dl_mod
        importlib.reload(dl_mod)

        custom_paths = [_make_skill_path(tmp_path, f"custom-{i}") for i in range(8)]
        global_paths = [_make_skill_path(tmp_path, f"global-{i}", ".claude/skills") for i in range(4)]
        all_paths = custom_paths + global_paths

        cfg_mock = mock.MagicMock()
        cfg_mock.get.return_value = ",".join(f"global-{i}" for i in range(4))

        def origin_fn(p):
            name = p.parent.name
            if name.startswith("global-"):
                return "global"
            return "custom"

        with mock.patch("skill_evolve.assessment.find_artifacts", return_value={"skills": all_paths}), \
             mock.patch("skill_evolve.assessment.classify_artifact_origin", side_effect=origin_fn), \
             mock.patch("skill_evolve.assessment.load_user_config", return_value=cfg_mock), \
             mock.patch("skill_evolve.denylist.DATA_DIR", tmp_path):
            from skill_evolve.assessment import skill_evolve_assessment
            result = skill_evolve_assessment(tmp_path)

        sentinel = next((r for r in result if r.get("_meta") == "batch_guard_trigger"), None)
        assert sentinel is not None
        group_origins = {g["origin"] for g in sentinel["groups"]}
        assert "custom" in group_origins
        assert "global" in group_origins
        for g in sentinel["groups"]:
            assert "skills" in g
            assert "estimated_tokens" in g
            assert "skill_count" in g
            # truncate 後プロンプト長ベース（#337）。旧 47,000/skill の桁違い過大を解消。
            # 各スキルは truncate 上限（2000字 + scaffold）以下に収まる
            assert 0 < g["estimated_tokens"] < g["skill_count"] * 2_000

    def test_assessment_filters_denied(self, monkeypatch, tmp_path):
        """denylist にあるスキルは effective_targets から除外される。"""
        import importlib
        monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path))
        import skill_evolve.denylist as dl_mod
        importlib.reload(dl_mod)
        dl_mod.add_to_denylist([f"skill-{i}" for i in range(5)])

        skill_paths = [_make_skill_path(tmp_path, f"skill-{i}") for i in range(11)]
        cfg_mock = mock.MagicMock()
        cfg_mock.get.return_value = ""
        telemetry_ret = {
            "frequency": 1, "diversity": 1, "evaluability": 1,
            "error_count": 0, "usage_count": 1, "error_categories": {},
        }
        llm_ret = {"external_dependency": 1, "judgment_complexity": 1, "cached": False}

        with mock.patch("skill_evolve.assessment.find_artifacts", return_value={"skills": skill_paths}), \
             mock.patch("skill_evolve.assessment.classify_artifact_origin", return_value="custom"), \
             mock.patch("skill_evolve.assessment.load_user_config", return_value=cfg_mock), \
             mock.patch("skill_evolve.denylist.DATA_DIR", tmp_path), \
             mock.patch("skill_evolve.compute_telemetry_scores", return_value=telemetry_ret), \
             mock.patch("skill_evolve.compute_llm_scores", return_value=llm_ret), \
             mock.patch("skill_evolve.is_self_evolved_skill", return_value=False):
            from skill_evolve.assessment import skill_evolve_assessment
            result = skill_evolve_assessment(tmp_path)

        # 5件 denied → effective 6件 → guard トリガーしない
        sentinel = next((r for r in result if r.get("_meta") == "batch_guard_trigger"), None)
        assert sentinel is None, "denied 後は guard トリガーしないはず"

    def test_skip_skills_param(self, monkeypatch, tmp_path):
        """skip_skills を渡すと一時除外される。"""
        import importlib
        monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path))
        import skill_evolve.denylist as dl_mod
        importlib.reload(dl_mod)

        skill_paths = [_make_skill_path(tmp_path, f"skill-{i}") for i in range(11)]
        cfg_mock = mock.MagicMock()
        cfg_mock.get.return_value = ""
        telemetry_ret = {
            "frequency": 1, "diversity": 1, "evaluability": 1,
            "error_count": 0, "usage_count": 1, "error_categories": {},
        }
        llm_ret = {"external_dependency": 1, "judgment_complexity": 1, "cached": False}

        skip = {f"skill-{i}" for i in range(5)}

        with mock.patch("skill_evolve.assessment.find_artifacts", return_value={"skills": skill_paths}), \
             mock.patch("skill_evolve.assessment.classify_artifact_origin", return_value="custom"), \
             mock.patch("skill_evolve.assessment.load_user_config", return_value=cfg_mock), \
             mock.patch("skill_evolve.denylist.DATA_DIR", tmp_path), \
             mock.patch("skill_evolve.compute_telemetry_scores", return_value=telemetry_ret), \
             mock.patch("skill_evolve.compute_llm_scores", return_value=llm_ret), \
             mock.patch("skill_evolve.is_self_evolved_skill", return_value=False):
            from skill_evolve.assessment import skill_evolve_assessment
            result = skill_evolve_assessment(tmp_path, skip_skills=skip)

        sentinel = next((r for r in result if r.get("_meta") == "batch_guard_trigger"), None)
        assert sentinel is None, "skip_skills で effective が減り guard トリガーしないはず"

    def test_denied_reduces_effective_below_limit(self, monkeypatch, tmp_path):
        """denylist で effective が 10件以下になれば guard トリガーしない。"""
        import importlib
        monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path))
        import skill_evolve.denylist as dl_mod
        importlib.reload(dl_mod)
        dl_mod.add_to_denylist(["skill-10"])

        skill_paths = [_make_skill_path(tmp_path, f"skill-{i}") for i in range(11)]
        cfg_mock = mock.MagicMock()
        cfg_mock.get.return_value = ""

        telemetry_ret = {
            "frequency": 1, "diversity": 1, "evaluability": 1,
            "error_count": 0, "usage_count": 1, "error_categories": {},
        }
        llm_ret = {"external_dependency": 1, "judgment_complexity": 1, "cached": False}

        with mock.patch("skill_evolve.assessment.find_artifacts", return_value={"skills": skill_paths}), \
             mock.patch("skill_evolve.assessment.classify_artifact_origin", return_value="custom"), \
             mock.patch("skill_evolve.assessment.load_user_config", return_value=cfg_mock), \
             mock.patch("skill_evolve.denylist.DATA_DIR", tmp_path), \
             mock.patch("skill_evolve.compute_telemetry_scores", return_value=telemetry_ret), \
             mock.patch("skill_evolve.compute_llm_scores", return_value=llm_ret), \
             mock.patch("skill_evolve.is_self_evolved_skill", return_value=False):
            from skill_evolve.assessment import skill_evolve_assessment
            result = skill_evolve_assessment(tmp_path)

        sentinel = next((r for r in result if r.get("_meta") == "batch_guard_trigger"), None)
        assert sentinel is None, "1件 denied で effective=10 → guard トリガーしないはず"

    def test_confirmed_batch_bypasses_guard_in_assessment(self, monkeypatch, tmp_path):
        """confirmed_batch=True のとき assessment.py の guard 条件が実際にスキップされる。"""
        import importlib
        monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path))
        import skill_evolve.denylist as dl_mod
        importlib.reload(dl_mod)

        skill_paths = [_make_skill_path(tmp_path, f"skill-{i}") for i in range(11)]
        origins = ["custom"] * 11
        cfg_mock = mock.MagicMock()
        cfg_mock.get.return_value = ""
        telemetry_ret = {
            "frequency": 1, "diversity": 1, "evaluability": 1,
            "error_count": 0, "usage_count": 1, "error_categories": {},
        }
        llm_ret = {"external_dependency": 1, "judgment_complexity": 1, "cached": False}

        with mock.patch("skill_evolve.assessment.find_artifacts", return_value={"skills": skill_paths}), \
             mock.patch("skill_evolve.assessment.classify_artifact_origin", side_effect=lambda p: origins[skill_paths.index(p)]), \
             mock.patch("skill_evolve.assessment.load_user_config", return_value=cfg_mock), \
             mock.patch("skill_evolve.denylist.DATA_DIR", tmp_path), \
             mock.patch("skill_evolve.compute_telemetry_scores", return_value=telemetry_ret), \
             mock.patch("skill_evolve.compute_llm_scores", return_value=llm_ret), \
             mock.patch("skill_evolve.is_self_evolved_skill", return_value=False):
            from skill_evolve.assessment import skill_evolve_assessment
            result = skill_evolve_assessment(tmp_path, confirmed_batch=True)

        # 11件あっても confirmed_batch=True なら sentinel が返らず通常評価が走る
        sentinel = next((r for r in result if r.get("_meta") == "batch_guard_trigger"), None)
        assert sentinel is None, "confirmed_batch=True では guard をスキップすべき"
        non_meta = [r for r in result if not r.get("_meta")]
        assert len(non_meta) == 11, "全 11 件が評価対象になるべき"


# ============================================================
# [ADR-037] Phase 1c: claude -p 全廃 — ファイルベース2相テスト
# ============================================================


# --- _parse_judgment_response の信頼境界（int/str/dict 寛容） ---


def test_parse_judgment_response_int():
    from skill_evolve import _parse_judgment_response
    assert _parse_judgment_response(2) == 2


def test_parse_judgment_response_str():
    from skill_evolve import _parse_judgment_response
    assert _parse_judgment_response("評価: 3 です") == 3


def test_parse_judgment_response_dict():
    from skill_evolve import _parse_judgment_response
    assert _parse_judgment_response({"judgment_complexity": 2}) == 2
    assert _parse_judgment_response({"score": 1}) == 1


def test_parse_judgment_response_none_and_bool_and_out_of_range():
    from skill_evolve import _parse_judgment_response
    assert _parse_judgment_response(None) is None
    assert _parse_judgment_response(True) is None  # bool は数値扱いしない
    assert _parse_judgment_response(5) is None      # 範囲外
    assert _parse_judgment_response("no digit") is None


# --- compute_llm_scores が LLM-free（cache-miss は static フォールバック） ---


def test_compute_llm_scores_cache_miss_is_static(tmp_path, monkeypatch):
    from skill_evolve import compute_llm_scores
    skill_dir = tmp_path / "my-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# Skill\n\nif foo: ...\nelse: ...\n条件 判断 場合\n")

    cache_file = tmp_path / "cache.json"
    monkeypatch.setattr("skill_evolve.CACHE_FILE", cache_file)
    monkeypatch.setattr("skill_evolve.DATA_DIR", tmp_path)

    result = compute_llm_scores("my-skill", skill_dir)
    assert result["cached"] is False
    assert result["judgment_source"] == "static"
    assert result["judgment_complexity"] in (1, 2, 3)
    # cache に static として確定保存される
    saved = json.loads(cache_file.read_text())
    assert saved["my-skill"]["judgment_source"] == "static"


# --- emit_judgment_requests / ingest_judgment_scores ---


def _make_skill(tmp_path, name, content="# S\n\nif x: ...\n"):
    d = tmp_path / name
    d.mkdir()
    (d / "SKILL.md").write_text(content)
    return d


def test_emit_judgment_requests_shape_and_meta(tmp_path, monkeypatch):
    from skill_evolve import emit_judgment_requests
    sd = _make_skill(tmp_path, "alpha")
    cache_file = tmp_path / "cache.json"
    monkeypatch.setattr("skill_evolve.CACHE_FILE", cache_file)

    out = emit_judgment_requests(tmp_path, [sd])
    assert len(out["requests"]) == 1
    req = out["requests"][0]
    assert req["id"] == "alpha"
    assert "判断の複雑さ" in req["prompt"]
    assert "hash" in req["meta"]
    assert "external_dependency" in req["meta"]
    assert "_content" not in req["meta"]  # 内部フィールドは meta から除去


def test_emit_judgment_skips_fresh_llm_but_not_static(tmp_path, monkeypatch):
    from skill_evolve import emit_judgment_requests, _file_hash
    sd_llm = _make_skill(tmp_path, "llmskill")
    sd_static = _make_skill(tmp_path, "staticskill")
    cache_file = tmp_path / "cache.json"
    cache = {
        "llmskill": {"hash": _file_hash(sd_llm / "SKILL.md"),
                     "judgment_source": "llm", "judgment_complexity": 2,
                     "external_dependency": 1},
        "staticskill": {"hash": _file_hash(sd_static / "SKILL.md"),
                        "judgment_source": "static", "judgment_complexity": 1,
                        "external_dependency": 1},
    }
    cache_file.write_text(json.dumps(cache))
    monkeypatch.setattr("skill_evolve.CACHE_FILE", cache_file)

    out = emit_judgment_requests(tmp_path, [sd_llm, sd_static])
    ids = {r["id"] for r in out["requests"]}
    assert ids == {"staticskill"}  # fresh-llm は除外、static は emit

    # refresh=True なら両方 emit
    out2 = emit_judgment_requests(tmp_path, [sd_llm, sd_static], refresh=True)
    assert {r["id"] for r in out2["requests"]} == {"llmskill", "staticskill"}


def test_ingest_judgment_scores_updates_cache_as_llm(tmp_path, monkeypatch):
    from skill_evolve import emit_judgment_requests, ingest_judgment_scores
    sd = _make_skill(tmp_path, "beta")
    cache_file = tmp_path / "cache.json"
    monkeypatch.setattr("skill_evolve.CACHE_FILE", cache_file)
    monkeypatch.setattr("skill_evolve.DATA_DIR", tmp_path)

    out = emit_judgment_requests(tmp_path, [sd])
    responses = {"beta": "3"}
    result = ingest_judgment_scores(tmp_path, out["requests"], responses)
    assert result == {"beta": 3}
    saved = json.loads(cache_file.read_text())
    assert saved["beta"]["judgment_complexity"] == 3
    assert saved["beta"]["judgment_source"] == "llm"
    assert saved["beta"]["external_dependency"] >= 1  # meta から補完


def test_ingest_judgment_leaves_static_when_unparseable(tmp_path, monkeypatch):
    from skill_evolve import emit_judgment_requests, ingest_judgment_scores
    sd = _make_skill(tmp_path, "gamma")
    cache_file = tmp_path / "cache.json"
    monkeypatch.setattr("skill_evolve.CACHE_FILE", cache_file)
    monkeypatch.setattr("skill_evolve.DATA_DIR", tmp_path)

    out = emit_judgment_requests(tmp_path, [sd])
    # 応答漏れ（None）→ 据え置き、cache 更新なし
    result = ingest_judgment_scores(tmp_path, out["requests"], {})
    assert result == {}
    assert not cache_file.exists()  # result 空なら save しない


# --- _parse_customization_response の信頼境界 ---


def test_parse_customization_none_returns_template():
    from skill_evolve.proposal import _parse_customization_response
    template = "## Pre-flight Check\n## Failure-triggered Learning\n"
    assert _parse_customization_response(None, template) == template


def test_parse_customization_strips_code_fence():
    from skill_evolve.proposal import _parse_customization_response
    template = "## Pre-flight Check\n## Failure-triggered Learning\n"
    raw = "```\n## Pre-flight Check (X)\n## Failure-triggered Learning\n```"
    result = _parse_customization_response(raw, template, budget=30)
    assert "```" not in result
    assert "(X)" in result


# --- emit_customize_request / ingest_customized_proposal ---


def _setup_templates(tmp_path):
    templates_dir = tmp_path / "skills" / "evolve" / "templates"
    templates_dir.mkdir(parents=True)
    (templates_dir / "self-evolve-sections.md").write_text(
        "## Pre-flight Check\n## Failure-triggered Learning\n"
    )
    (templates_dir / "pitfalls.md").write_text("## Active Pitfalls\n")


def test_emit_customize_request_shape(tmp_path, monkeypatch):
    from skill_evolve import emit_customize_request
    _setup_templates(tmp_path)
    monkeypatch.setattr("skill_evolve._plugin_root", tmp_path)
    sd = _make_skill(tmp_path, "delta", content="# Delta Skill\n")

    out = emit_customize_request("delta", sd)
    assert len(out["requests"]) == 1
    assert out["requests"][0]["id"] == "delta"
    assert "カスタマイズ" in out["requests"][0]["prompt"]
    assert "_template" not in out["requests"][0]["meta"]


def test_emit_customize_request_template_missing(tmp_path, monkeypatch):
    from skill_evolve import emit_customize_request
    monkeypatch.setattr("skill_evolve._plugin_root", tmp_path / "nope")
    sd = _make_skill(tmp_path, "eps")
    out = emit_customize_request("eps", sd)
    assert out["requests"] == []
    assert "error" in out


def test_ingest_customized_proposal_builds_proposal(tmp_path, monkeypatch):
    from skill_evolve import emit_customize_request, ingest_customized_proposal
    _setup_templates(tmp_path)
    monkeypatch.setattr("skill_evolve._plugin_root", tmp_path)
    sd = _make_skill(tmp_path, "zeta", content="# Zeta\n")

    with mock.patch("skill_evolve.proposal.get_rejected_stats",
                    return_value={"rejected_rate": 0.0}):
        out = emit_customize_request("zeta", sd)
        responses = {"zeta": "## Pre-flight Check (zeta)\n## Failure-triggered Learning\n"}
        proposal = ingest_customized_proposal("zeta", sd, out["requests"], responses)

    assert proposal["error"] is None
    assert "(zeta)" in proposal["sections_to_add"]
    assert "Active Pitfalls" in proposal["pitfalls_template"]


def test_ingest_customized_proposal_fallback_on_missing_response(tmp_path, monkeypatch):
    from skill_evolve import emit_customize_request, ingest_customized_proposal
    _setup_templates(tmp_path)
    monkeypatch.setattr("skill_evolve._plugin_root", tmp_path)
    sd = _make_skill(tmp_path, "eta", content="# Eta\n")

    with mock.patch("skill_evolve.proposal.get_rejected_stats",
                    return_value={"rejected_rate": 0.0}):
        out = emit_customize_request("eta", sd)
        proposal = ingest_customized_proposal("eta", sd, out["requests"], {})

    # 応答欠損 → テンプレそのままにフォールバック
    assert proposal["error"] is None
    assert "Pre-flight Check" in proposal["sections_to_add"]


# --- #336: skill_dir は str で渡しても TypeError にならない（Path/str 契約統一） ---


def test_emit_customize_request_accepts_str_dir(tmp_path, monkeypatch):
    """skill_dir を str で渡しても `skill_dir / "SKILL.md"` で落ちない（#336）。

    assess_single_skill は str を受け入れるのに emit_* が Path 前提で TypeError に
    なる契約不整合を塞ぐ。入口で Path() 正規化されていれば str でも動く。
    """
    from skill_evolve import emit_customize_request
    _setup_templates(tmp_path)
    monkeypatch.setattr("skill_evolve._plugin_root", tmp_path)
    sd = _make_skill(tmp_path, "theta", content="# Theta Skill\n")

    out = emit_customize_request("theta", str(sd))  # str で渡す
    assert len(out["requests"]) == 1
    assert out["requests"][0]["id"] == "theta"


def test_ingest_customized_proposal_accepts_str_dir(tmp_path, monkeypatch):
    """ingest_customized_proposal も str の skill_dir を受け入れる（#336）。"""
    from skill_evolve import emit_customize_request, ingest_customized_proposal
    _setup_templates(tmp_path)
    monkeypatch.setattr("skill_evolve._plugin_root", tmp_path)
    sd = _make_skill(tmp_path, "iota", content="# Iota\n")

    with mock.patch("skill_evolve.proposal.get_rejected_stats",
                    return_value={"rejected_rate": 0.0}):
        out = emit_customize_request("iota", str(sd))
        responses = {"iota": "## Pre-flight Check (iota)\n## Failure-triggered Learning\n"}
        proposal = ingest_customized_proposal("iota", str(sd), out["requests"], responses)

    assert proposal["error"] is None
    assert "(iota)" in proposal["sections_to_add"]
