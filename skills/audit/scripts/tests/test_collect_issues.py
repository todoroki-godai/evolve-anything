"""collect_issues() のユニットテスト。"""
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_plugin_root = Path(__file__).resolve().parent.parent.parent.parent.parent
sys.path.insert(0, str(_plugin_root / "skills" / "audit" / "scripts"))
sys.path.insert(0, str(_plugin_root / "scripts" / "lib"))
sys.path.insert(0, str(_plugin_root / "scripts"))

from audit import collect_issues, find_artifacts


def _find_artifacts_local_only(project_dir):
    """グローバル artifacts を除外し、プロジェクトローカルのみ返す。"""
    result = {"skills": [], "rules": [], "memory": [], "claude_md": []}
    claude_dir = project_dir / ".claude"
    claude_md = project_dir / "CLAUDE.md"
    if claude_md.exists():
        result["claude_md"].append(claude_md)
    skills_dir = claude_dir / "skills"
    if skills_dir.exists():
        for skill_md in skills_dir.rglob("SKILL.md"):
            result["skills"].append(skill_md)
    rules_dir = claude_dir / "rules"
    if rules_dir.exists():
        for rule_file in rules_dir.glob("*.md"):
            result["rules"].append(rule_file)
    memory_dir = claude_dir / "memory"
    if memory_dir.exists():
        for mem_file in memory_dir.glob("*.md"):
            result["memory"].append(mem_file)
    return result


@pytest.fixture
def project_dir(tmp_path):
    """基本的なプロジェクト構造を作成する。"""
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    (claude_dir / "skills").mkdir()
    (claude_dir / "rules").mkdir()
    memory_dir = claude_dir / "memory"
    memory_dir.mkdir()
    return tmp_path


def test_violations(project_dir):
    """行数超過の issue が返される（MEMORY.md の制限超過）。"""
    memory_dir = project_dir / ".claude" / "memory"
    memory_file = memory_dir / "MEMORY.md"
    memory_file.write_text("\n".join([f"line {i}" for i in range(250)]))

    with patch("audit.read_auto_memory", return_value=[]):
        issues = collect_issues(project_dir)

    violation_issues = [i for i in issues if i["type"] == "line_limit_violation"]
    assert len(violation_issues) >= 1
    # MEMORY.md の violation を確認
    memory_violations = [v for v in violation_issues if "MEMORY.md" in v["file"]]
    assert len(memory_violations) >= 1
    assert memory_violations[0]["detail"]["lines"] == 250
    assert memory_violations[0]["detail"]["limit"] == 200
    assert memory_violations[0]["source"] == "check_line_limits"


def test_stale_refs(project_dir):
    """存在しないパス参照が stale_ref として返される。"""
    memory_dir = project_dir / ".claude" / "memory"
    memory_file = memory_dir / "MEMORY.md"
    memory_file.write_text("# Memory\n\n- See skills/nonexistent/SKILL.md\n")

    with patch("audit.read_auto_memory", return_value=[]):
        issues = collect_issues(project_dir)

    stale_issues = [i for i in issues if i["type"] == "stale_ref"]
    assert len(stale_issues) >= 1
    assert "nonexistent" in stale_issues[0]["detail"]["path"]


def test_near_limits(project_dir):
    """肥大化警告の issue が返される。"""
    memory_dir = project_dir / ".claude" / "memory"
    memory_file = memory_dir / "MEMORY.md"
    # MEMORY.md の制限は 200 行、80% = 160 行以上で near_limit
    memory_file.write_text("\n".join([f"line {i}" for i in range(170)]))

    with patch("audit.read_auto_memory", return_value=[]):
        issues = collect_issues(project_dir)

    near_issues = [i for i in issues if i["type"] == "near_limit"]
    assert len(near_issues) >= 1
    assert near_issues[0]["detail"]["pct"] >= 80


def test_duplicates(project_dir):
    """重複候補の issue が返される。"""
    skills_dir = project_dir / ".claude" / "skills"
    for name in ["my-skill", "myskill"]:
        d = skills_dir / name
        d.mkdir()
        (d / "SKILL.md").write_text(f"# {name}\ncontent\n")

    with patch("audit.read_auto_memory", return_value=[]):
        issues = collect_issues(project_dir)

    dup_issues = [i for i in issues if i["type"] == "duplicate"]
    assert len(dup_issues) >= 1
    assert dup_issues[0]["source"] == "detect_duplicates_simple"


def test_hardcoded_value_in_skill(project_dir):
    """skill ファイル内のハードコード値が hardcoded_value として返される。"""
    skills_dir = project_dir / ".claude" / "skills" / "my-skill"
    skills_dir.mkdir(parents=True)
    skill_md = skills_dir / "SKILL.md"
    skill_md.write_text("# My Skill\nslack_app_id: A04K8RZLM3Q\n")

    with patch("audit.read_auto_memory", return_value=[]):
        issues = collect_issues(project_dir)

    hv_issues = [i for i in issues if i["type"] == "hardcoded_value"]
    assert len(hv_issues) >= 1
    assert hv_issues[0]["detail"]["pattern_type"] == "slack_id"
    assert hv_issues[0]["detail"]["matched"] == "A04K8RZLM3Q"
    assert hv_issues[0]["source"] == "detect_hardcoded_values"


def test_no_hardcoded_value(project_dir):
    """ハードコード値がなければ hardcoded_value issue は返さない。"""
    skills_dir = project_dir / ".claude" / "skills" / "clean-skill"
    skills_dir.mkdir(parents=True)
    skill_md = skills_dir / "SKILL.md"
    skill_md.write_text("# Clean Skill\nNo hardcoded values here.\n")

    with patch("audit.read_auto_memory", return_value=[]), \
         patch("audit.find_artifacts", side_effect=_find_artifacts_local_only):
        issues = collect_issues(project_dir)

    hv_issues = [i for i in issues if i["type"] == "hardcoded_value"]
    assert len(hv_issues) == 0


def test_plugin_skill_excluded_from_line_limit(project_dir, tmp_path, monkeypatch):
    """plugin / global 由来スキルは行数超過でも violation に含まれない（custom のみ対象）。"""
    import audit as audit_mod

    # plugin 由来: plugins cache 配下
    plugins_cache = tmp_path / "plugins" / "cache"
    plugin_skill_md = plugins_cache / "gstack" / "skills" / "browse" / "SKILL.md"
    plugin_skill_md.parent.mkdir(parents=True)
    plugin_skill_md.write_text("\n".join(["line"] * 501))
    monkeypatch.setenv("CLAUDE_PLUGINS_DIR", str(plugins_cache))

    # global 由来: classify_artifact_origin が "global" を返すよう、
    # _plugin_skill_map_cache のインライン分岐に乗せる。
    # `~/.claude/skills/` 直下相当の path で "global" 判定をシミュレート。
    global_skill_md = Path.home() / ".claude" / "skills" / "__test_fake_global_skill__" / "SKILL.md"

    artifacts = _find_artifacts_local_only(project_dir)
    artifacts["skills"].extend([plugin_skill_md])

    orig_cache = audit_mod.classification._plugin_skill_map_cache
    audit_mod.classification._plugin_skill_map_cache = {"browse": "gstack"}
    try:
        from audit import check_line_limits, classify_artifact_origin
        # plugin 分類確認
        assert classify_artifact_origin(plugin_skill_md) == "plugin"
        violations = check_line_limits(artifacts)
    finally:
        audit_mod.classification._plugin_skill_map_cache = orig_cache

    assert len([v for v in violations if "browse" in v.get("file", "")]) == 0


def test_no_issues(project_dir):
    """問題がなければ空リストを返す。"""
    with patch("audit.read_auto_memory", return_value=[]), \
         patch("audit.find_artifacts", side_effect=_find_artifacts_local_only):
        issues = collect_issues(project_dir)

    assert issues == []


def test_claudemd_violation_excluded(project_dir):
    """CLAUDE.md の行数超過は violation ではなく warning のみ（collect_issues に含まれない）。"""
    claude_md = project_dir / "CLAUDE.md"
    claude_md.write_text("\n".join([f"line {i}" for i in range(250)]))

    with patch("audit.read_auto_memory", return_value=[]):
        issues = collect_issues(project_dir)

    violation_issues = [i for i in issues if i["type"] == "line_limit_violation"]
    # CLAUDE.md は warning_only なので violation に含まれない
    claudemd_violations = [i for i in violation_issues if "CLAUDE.md" in i["file"]]
    assert len(claudemd_violations) == 0


def test_rule_with_frontmatter_no_violation(project_dir):
    """frontmatter 付きルールは frontmatter 除外でカウントし、制限内なら violation にならない。"""
    rules_dir = project_dir / ".claude" / "rules"
    rule_file = rules_dir / "test-rule.md"
    # 4行 frontmatter + 5行コンテンツ = 全体9行、コンテンツ5行 <= project_rules 5
    rule_file.write_text('---\npaths:\n  - "**/*.py"\n---\nLine 1\nLine 2\nLine 3\nLine 4\nLine 5')

    with patch("audit.read_auto_memory", return_value=[]), \
         patch("audit.find_artifacts", side_effect=_find_artifacts_local_only):
        issues = collect_issues(project_dir)

    rule_violations = [
        i for i in issues
        if i["type"] == "line_limit_violation" and "test-rule.md" in i["file"]
    ]
    assert len(rule_violations) == 0


def test_rule_with_frontmatter_violation(project_dir):
    """frontmatter 除外でもコンテンツ超過のルールは violation になる。"""
    rules_dir = project_dir / ".claude" / "rules"
    rule_file = rules_dir / "over-rule.md"
    # 4行 frontmatter + 11行コンテンツ → コンテンツ 11 > project_rules 10
    body = "\n".join([f"Line {i}" for i in range(11)])
    rule_file.write_text(f'---\npaths:\n  - "**/*.py"\n---\n{body}')

    with patch("audit.read_auto_memory", return_value=[]):
        issues = collect_issues(project_dir)

    rule_violations = [
        i for i in issues
        if i["type"] == "line_limit_violation" and "over-rule.md" in i["file"]
    ]
    assert len(rule_violations) >= 1
    assert rule_violations[0]["detail"]["lines"] == 11
    assert rule_violations[0]["detail"]["limit"] == 10


def test_rule_with_frontmatter_and_blank_line_no_violation(project_dir):
    """frontmatter + 空行 + コンテンツ5行 → 空行除外で5行、violation にならない (#47)。"""
    rules_dir = project_dir / ".claude" / "rules"
    rule_file = rules_dir / "blank-rule.md"
    rule_file.write_text('---\npaths:\n  - "**/*.py"\n---\n\nLine 1\nLine 2\nLine 3\nLine 4\nLine 5')

    with patch("audit.read_auto_memory", return_value=[]), \
         patch("audit.find_artifacts", side_effect=_find_artifacts_local_only):
        issues = collect_issues(project_dir)

    rule_violations = [
        i for i in issues
        if i["type"] == "line_limit_violation" and "blank-rule.md" in i["file"]
    ]
    assert len(rule_violations) == 0


def test_untagged_reference_excludes_claudemd_skills(project_dir):
    """CLAUDE.md Skills セクションに記載のスキルは untagged_reference にならない (#47)。"""
    from audit import detect_untagged_reference_candidates

    # CLAUDE.md にスキル記載
    claude_md = project_dir / "CLAUDE.md"
    claude_md.write_text("# Project\n\n## Skills\n\n- `/my-skill` — does stuff\n")

    # type 未設定、usage ゼロのスキル
    skills_dir = project_dir / ".claude" / "skills" / "my-skill"
    skills_dir.mkdir(parents=True)
    (skills_dir / "SKILL.md").write_text("# My Skill\nTrigger: my-skill\n使用タイミング: ...\n")

    artifacts = _find_artifacts_local_only(project_dir)
    usage = {}

    with patch("audit.classify_artifact_origin", return_value="project"):
        candidates = detect_untagged_reference_candidates(
            artifacts, usage, project_dir=project_dir,
        )

    skill_names = [c["skill_name"] for c in candidates]
    assert "my-skill" not in skill_names


def test_untagged_reference_excludes_bold_label_backtick_skills(project_dir):
    """`- **ラベル**: `/skill`` 形式の CLAUDE.md 記載スキルも除外される (#295)。"""
    from audit import detect_untagged_reference_candidates

    claude_md = project_dir / "CLAUDE.md"
    claude_md.write_text(
        "# Project\n\n## Skills\n\n"
        "- **AWSデプロイ**: `/aws-deploy` - `.claude/skills/aws-deploy/SKILL.md`\n"
    )
    # 参照型に見える（heuristic では弾けない）が CLAUDE.md 記載のスキル
    skills_dir = project_dir / ".claude" / "skills" / "aws-deploy"
    skills_dir.mkdir(parents=True)
    (skills_dir / "SKILL.md").write_text(
        "# AWS Deploy\nThis is a reference guide for deployment specifications.\n"
    )

    artifacts = _find_artifacts_local_only(project_dir)
    with patch("audit.classify_artifact_origin", return_value="project"):
        candidates = detect_untagged_reference_candidates(
            artifacts, {}, project_dir=project_dir,
        )

    assert "aws-deploy" not in [c["skill_name"] for c in candidates]


def test_claude_md_unparseable_helper(project_dir):
    """claude_md_unparseable: CLAUDE.md の在/不在 × trigger 抽出 0 を判定する (#295)。"""
    from audit import claude_md_unparseable

    # CLAUDE.md が無い → False（正規の no-CLAUDE.md PJ。検出は走らせる）
    assert claude_md_unparseable(project_dir) is False

    # CLAUDE.md は在るが Skills セクション無し（trigger 0）→ True
    (project_dir / "CLAUDE.md").write_text("# Project\n\nNo skills section here.\n")
    assert claude_md_unparseable(project_dir) is True

    # CLAUDE.md に parse 可能な Skills 記載 → False
    (project_dir / "CLAUDE.md").write_text(
        "# Project\n\n## Skills\n\n- /s: x. Trigger: t\n"
    )
    assert claude_md_unparseable(project_dir) is False


def test_collect_issues_suppresses_untagged_when_claude_md_unparseable(project_dir):
    """CLAUDE.md は在るが trigger 抽出 0 のとき untagged 構造化 issue を積まない (#295)。"""
    # Skills セクションが無い CLAUDE.md（= trigger 抽出 0、unparseable）
    (project_dir / "CLAUDE.md").write_text("# Project\n\nNo skills section.\n")
    # 参照型に見えるスキル（本来なら untagged_reference になる）
    skills_dir = project_dir / ".claude" / "skills" / "design-guide"
    skills_dir.mkdir(parents=True)
    (skills_dir / "SKILL.md").write_text(
        "# Design Guide\nThis is a reference guide for design system specifications.\n"
    )

    with patch("audit.read_auto_memory", return_value=[]), \
         patch("audit.find_artifacts", side_effect=_find_artifacts_local_only), \
         patch("audit.classify_artifact_origin", return_value="project"):
        issues = collect_issues(project_dir)

    untagged = [i for i in issues if i["type"] == "untagged_reference_candidates"]
    assert untagged == []


def test_untagged_reference_excludes_user_invocable_heuristic(project_dir):
    """トリガーワードや使用タイミング記載のスキルは除外 (#47)。"""
    from audit import detect_untagged_reference_candidates

    skills_dir = project_dir / ".claude" / "skills" / "generate-docs"
    skills_dir.mkdir(parents=True)
    (skills_dir / "SKILL.md").write_text(
        "# Generate Docs\nTrigger: generate-docs, ドキュメント生成\n使用タイミング: ドキュメント更新時\n"
    )

    artifacts = _find_artifacts_local_only(project_dir)
    usage = {}

    with patch("audit.classify_artifact_origin", return_value="project"):
        candidates = detect_untagged_reference_candidates(
            artifacts, usage, project_dir=project_dir,
        )

    skill_names = [c["skill_name"] for c in candidates]
    assert "generate-docs" not in skill_names


def test_untagged_reference_detects_actual_reference(project_dir):
    """参照型スキルは引き続き検出される (#47)。"""
    from audit import detect_untagged_reference_candidates

    skills_dir = project_dir / ".claude" / "skills" / "design-guide"
    skills_dir.mkdir(parents=True)
    (skills_dir / "SKILL.md").write_text(
        "# Design Guide\nThis is a reference guide for design system specifications.\n"
    )

    artifacts = _find_artifacts_local_only(project_dir)
    usage = {}

    with patch("audit.classify_artifact_origin", return_value="project"):
        candidates = detect_untagged_reference_candidates(
            artifacts, usage, project_dir=project_dir,
        )

    skill_names = [c["skill_name"] for c in candidates]
    assert "design-guide" in skill_names


def test_untagged_reference_excludes_code_block_skills(project_dir):
    """コードブロックを含むスキルは action 型とみなして除外する。"""
    from audit import detect_untagged_reference_candidates

    skills_dir = project_dir / ".claude" / "skills" / "manage-repo"
    skills_dir.mkdir(parents=True)
    (skills_dir / "SKILL.md").write_text(
        "# Manage Repo\n\nRepos workflow.\n\n```bash\ngit status\n```\n"
    )

    artifacts = _find_artifacts_local_only(project_dir)
    usage = {}

    with patch("audit.classify_artifact_origin", return_value="project"):
        candidates = detect_untagged_reference_candidates(
            artifacts, usage, project_dir=project_dir,
        )

    skill_names = [c["skill_name"] for c in candidates]
    assert "manage-repo" not in skill_names


def test_untagged_reference_excludes_neutral_content(project_dir):
    """action/reference 信号が同スコア（両ゼロ含む）の場合、action 型とみなして除外（安全側）。"""
    from audit import detect_untagged_reference_candidates

    skills_dir = project_dir / ".claude" / "skills" / "docs-qa"
    skills_dir.mkdir(parents=True)
    (skills_dir / "SKILL.md").write_text(
        "# Docs QA\n\nQA workflow for documentation.\n"
    )

    artifacts = _find_artifacts_local_only(project_dir)
    usage = {}

    with patch("audit.classify_artifact_origin", return_value="project"):
        candidates = detect_untagged_reference_candidates(
            artifacts, usage, project_dir=project_dir,
        )

    skill_names = [c["skill_name"] for c in candidates]
    assert "docs-qa" not in skill_names


def test_issue_format(project_dir):
    """各 issue が統一フォーマットを満たす。"""
    claude_md = project_dir / "CLAUDE.md"
    claude_md.write_text("\n".join([f"line {i}" for i in range(250)]))

    with patch("audit.read_auto_memory", return_value=[]):
        issues = collect_issues(project_dir)

    for issue in issues:
        assert "type" in issue
        assert "file" in issue
        assert "detail" in issue
        assert "source" in issue
        assert isinstance(issue["type"], str)
        assert isinstance(issue["detail"], dict)
