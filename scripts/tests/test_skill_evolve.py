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

    # LLM をモック（テンプレートそのまま返す）
    with mock.patch("skill_evolve._customize_template") as mock_custom:
        mock_custom.return_value = (
            "## Pre-flight Check\n\nCheck pitfalls.\n\n"
            "## Failure-triggered Learning\n\n| Trigger | Action |\n"
        )
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


# --- difflib bounded edit gate in _customize_template (#196, #199) ---


def test_customize_template_within_budget(tmp_path, monkeypatch):
    """diff 行数がバジェット以内なら LLM 出力をそのまま返す。"""
    template = "## Pre-flight Check\n\n## Failure-triggered Learning\n"
    # LLM が 2 行変更した出力（budget=30 以内）
    customized_output = "## Pre-flight Check (custom)\n\n## Failure-triggered Learning\n"

    with mock.patch("subprocess.run") as mock_run:
        mock_run.return_value = mock.Mock(
            returncode=0, stdout=customized_output, stderr=""
        )
        with mock.patch("skill_evolve.proposal.get_skill_lr_budget", return_value=30):
            from skill_evolve.proposal import _customize_template
            result = _customize_template("test-skill", "", template)

    assert "custom" in result


def test_customize_template_exceeds_budget_fallback(tmp_path, monkeypatch):
    """diff 行数がバジェットを超えた場合はテンプレートにフォールバックする (#196)。"""
    template = "## Pre-flight Check\n\n## Failure-triggered Learning\n"
    # LLM が多数の行を変更した出力（budget=5 を超える）
    many_lines = "\n".join(f"changed line {i}" for i in range(20))
    llm_output = many_lines

    with mock.patch("subprocess.run") as mock_run:
        mock_run.return_value = mock.Mock(
            returncode=0, stdout=llm_output, stderr=""
        )
        with mock.patch("skill_evolve.proposal.get_skill_lr_budget", return_value=5):
            from skill_evolve.proposal import _customize_template
            result = _customize_template("test-skill", "", template)

    # フォールバックでテンプレートがそのまま返る
    assert result == template


def test_customize_template_budget_override(tmp_path):
    """skill_lr_budget=10 の userConfig override が正しく判定に使われる (#199)。"""
    template = "original line 1\noriginal line 2\noriginal line 3\n"
    # 11 行変更 (budget=10 を 1 超)
    changed_lines = "\n".join(f"changed {i}" for i in range(11))

    with mock.patch("subprocess.run") as mock_run:
        mock_run.return_value = mock.Mock(
            returncode=0, stdout=changed_lines, stderr=""
        )
        with mock.patch("skill_evolve.proposal.get_skill_lr_budget", return_value=10):
            from skill_evolve.proposal import _customize_template
            result = _customize_template("test-skill", "", template)

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
        with mock.patch("skill_evolve._customize_template") as mock_custom:
            mock_custom.return_value = (
                "## Pre-flight Check\n\n## Failure-triggered Learning\n"
            )
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
        with mock.patch("skill_evolve._customize_template") as mock_custom:
            mock_custom.return_value = (
                "## Pre-flight Check\n\n## Failure-triggered Learning\n"
            )
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
            assert g["estimated_tokens"] == g["skill_count"] * 47_000

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
