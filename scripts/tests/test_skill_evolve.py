#!/usr/bin/env python3
"""skill_evolve.py のテスト（コア: スコアリング/分類/アンチパターン/assess_single_skill/検証系/workflow）。

proposal 生成・適用は test_skill_evolve_proposal.py、remediation 統合・rejected_stats は
test_skill_evolve_remediation.py、denylist/batch guard/judgment 2相は
test_skill_evolve_batch_guard.py、batch トークン見積もりは test_skill_evolve_batch_estimate.py に分離。
"""
import json
import sys
from pathlib import Path
from unittest import mock

_lib_dir = Path(__file__).resolve().parent.parent.parent / "scripts" / "lib"
sys.path.insert(0, str(_lib_dir))

from skill_evolve import (
    BAND_AID_THRESHOLD,
    HIGH_SUITABILITY_THRESHOLD,
    _count_external_keywords,
    _score_execution_frequency,
    _score_external_dependency,
    _score_failure_diversity,
    _score_output_evaluability,
    assess_single_skill,
    classify_suitability,
    detect_anti_patterns,
    is_self_evolved_skill,
    is_verification_skill,
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


# --- #354⑧: judgment_complexity 静的指標近似 ---


def test_judgment_complexity_static_low():
    """条件分岐が少ない（2件未満）場合は 1（低）を返す。"""
    from skill_evolve.llm_scoring import _score_judgment_complexity_static
    content = "# Simple Skill\n\nDoes exactly one thing, no conditions."
    assert _score_judgment_complexity_static(content) == 1


def test_judgment_complexity_static_medium():
    """条件分岐が 3-7 件の場合は 2（中）を返す。"""
    from skill_evolve.llm_scoring import _score_judgment_complexity_static
    # 3件の条件分岐語
    content = "if user says yes, then proceed. elif condition, do something else. when error, retry."
    result = _score_judgment_complexity_static(content)
    assert result == 2


def test_judgment_complexity_static_high():
    """条件分岐が 8 件以上の場合は 3（高）を返す。"""
    from skill_evolve.llm_scoring import _score_judgment_complexity_static
    # 8件以上の条件分岐語
    content = (
        "if A: do X\n"
        "elif B: do Y\n"
        "when C occurs: handle it\n"
        "if D: check E\n"
        "unless F: skip\n"
        "if G and H: complex action\n"
        "when I: escalate\n"
        "if J: fallback\n"
    )
    result = _score_judgment_complexity_static(content)
    assert result == 3


def test_judgment_complexity_static_deterministic():
    """同じ入力は常に同じ値を返す（決定論）。"""
    from skill_evolve.llm_scoring import _score_judgment_complexity_static
    content = "if condition: do X\nelif other: do Y\nwhen error: retry"
    result1 = _score_judgment_complexity_static(content)
    result2 = _score_judgment_complexity_static(content)
    assert result1 == result2


def test_judgment_complexity_static_returns_valid_range():
    """返り値は常に 1-3 の範囲内。"""
    from skill_evolve.llm_scoring import _score_judgment_complexity_static
    for content in [
        "",
        "# Empty skill",
        "if" * 20,  # 20個の条件分岐
        "複雑な判断が多数ある場合の分岐: 条件1, 条件2, 判断3, 場合4, 判断5, 条件6, 場合7, 条件8",
    ]:
        result = _score_judgment_complexity_static(content)
        assert result in (1, 2, 3), f"Invalid range {result} for content: {content[:50]}"


def test_judgment_complexity_excludes_markdown_heading_numbers():
    """markdown 見出し番号（### N.）は steps に数えない（#354 review fix）。

    見出しだらけで分岐ゼロの文書を complexity=3 に張り付かせない。
    """
    from skill_evolve.llm_scoring import _score_judgment_complexity_static
    # 10 個の "### N." 見出しのみ。番号付きリスト手順でも分岐でもない。
    heading_only = "\n".join(f"### {i}. Section title" for i in range(1, 11))
    assert _score_judgment_complexity_static(heading_only) == 1
    # 同じ番号でもリスト手順（行頭が数字）なら steps として数える（ただし cap で頭打ち）。
    list_steps = "\n".join(f"{i}. do the thing" for i in range(1, 11))
    assert _score_judgment_complexity_static(list_steps) == 2


def test_judgment_complexity_steps_capped():
    """steps は STEPS_SIGNAL_CAP で頭打ちになり、単独では 3 に到達しない（#354 follow-up）。

    番号付きリストが長いだけの線形チェックリスト（agent-brushup/spec-keeper 等、steps 20-26）が
    complexity=3 に張り付く問題への対処。
    """
    from skill_evolve.llm_scoring import (
        _score_judgment_complexity_static,
        STEPS_SIGNAL_CAP,
    )
    # 30 手順の長いチェックリスト（分岐・判断委譲ゼロ）。
    long_checklist = "\n".join(f"{i}. step {i}" for i in range(1, 31))
    score = _score_judgment_complexity_static(long_checklist)
    assert score == 2, f"長い線形チェックリストは cap で 2 になるべき: {score}"
    # cap 未満では到達できない（cap*1 < 8）ことを保証。
    assert STEPS_SIGNAL_CAP < 8
    # branches / ask_user が加われば 3 に到達できる（判断委譲が駆動）。
    with_judgment = long_checklist + "\n" + "\n".join(
        "AskUserQuestion" for _ in range(3)
    )
    assert _score_judgment_complexity_static(with_judgment) == 3


def test_judgment_complexity_ask_user_is_weighted():
    """AskUserQuestion は重み付けされ、少数でも判断委譲として効く（#354 review fix）。"""
    from skill_evolve.llm_scoring import _score_judgment_complexity_static, ASK_USER_WEIGHT
    assert ASK_USER_WEIGHT >= 2
    # AskUserQuestion 2 回のみ（branches/steps ゼロ）。重み付けで 2*W >= 3 → 2 以上。
    content = "Ask via AskUserQuestion here.\nLater ask AskUserQuestion again."
    assert _score_judgment_complexity_static(content) >= 2


def test_compute_llm_scores_uses_static_judgment_on_cache_miss(tmp_path, monkeypatch):
    """キャッシュミス時に _score_judgment_complexity_static が使われる（#354 配線確認）。"""
    from skill_evolve import compute_llm_scores
    skill_dir = tmp_path / "my-skill"
    skill_dir.mkdir()
    # 明確に高複雑さの内容（8件以上の条件分岐）
    skill_content = (
        "if A: x\nelif B: y\nwhen C: z\nif D: w\nunless E: v\n"
        "if F: u\nwhen G: t\nif H: s\n"
    )
    (skill_dir / "SKILL.md").write_text(skill_content)

    cache_file = tmp_path / "cache.json"
    monkeypatch.setattr("skill_evolve.CACHE_FILE", cache_file)

    result = compute_llm_scores("my-skill", skill_dir)
    assert result["judgment_complexity"] == 3, "8+分岐なのに high(3) にならなかった"
    assert result["judgment_source"] == "static"
    assert result["cached"] is False
