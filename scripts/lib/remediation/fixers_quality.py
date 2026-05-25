"""quality 系 fix 関数 + FIX_DISPATCH + generate_proposals (旧 remediation.py 由来)。

remediation/__init__.py から re-export される（後方互換）。
FIX_DISPATCH は他 slice の fix 関数を遅延参照するため、テーブルは package 経由で構築する。
"""
from pathlib import Path
from typing import Any, Dict, List, Tuple

from issue_schema import (
    HOOK_SCRIPT_PATH,
    INSTRUCTION_VIOLATION_CANDIDATE,
    IVC_INSTRUCTION_TEXT,
    IVC_MATCH_TYPE,
    IVC_REASON,
    IVC_SKILL_NAME,
    MEC_PROPOSED_EFFORT,
    MEC_REASON,
    MEC_SKILL_NAME,
    MEC_SKILL_PATH,
    MISSING_EFFORT_CANDIDATE,
    RULE_FILENAME,
    RULE_TARGET_COMMANDS,
    SE_SKILL_NAME,
    SE_TOTAL_SCORE,
    SKILL_EVOLVE_CANDIDATE,
    SKILL_QUALITY_PATTERN_GAP,
    SQP_DOMAIN,
    SQP_MISSING_RECOMMENDED,
    SQP_MISSING_REQUIRED,
    SQP_SKILL_NAME,
    TOOL_USAGE_HOOK_CANDIDATE,
    TOOL_USAGE_RULE_CANDIDATE,
    VERIFICATION_RULE_CANDIDATE,
    VRC_DESCRIPTION,
    VRC_EVIDENCE,
    VRC_RULE_FILENAME,
    WCC_CATEGORY,
    WCC_SKILL_NAME,
    WCC_TEMPLATE,
    WORKFLOW_CHECKPOINT_CANDIDATE,
)


def fix_split_candidate(
    issues: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """スキル分割案を LLM で生成して提案テキストを返す（ファイル変更なし）。"""
    import subprocess

    results = []
    for issue in issues:
        if issue["type"] != "split_candidate":
            continue
        detail = issue.get("detail", {})
        skill_name = detail.get("skill_name", "unknown")
        file_path = issue["file"]
        path = Path(file_path)

        if not path.exists():
            results.append({
                "issue": issue, "original_content": "", "fixed": False,
                "error": f"SKILL.md not found: {file_path}",
            })
            continue

        try:
            content = path.read_text(encoding="utf-8")
        except OSError as e:
            results.append({
                "issue": issue, "original_content": "", "fixed": False,
                "error": str(e),
            })
            continue

        line_count = detail.get("line_count", len(content.splitlines()))
        threshold = detail.get("threshold", 300)

        prompt = (
            f"以下のスキル SKILL.md ({line_count}行、閾値{threshold}行) を分析し、"
            f"references/ に切り出すべきセクションを特定してください。\n"
            f"出力形式:\n"
            f"- 分割先ファイル名と各ファイルの概要\n"
            f"- 推定削減行数\n"
            f"- SKILL.md に残す内容の概要\n\n"
            f"```\n{content[:3000]}```"
        )

        try:
            result_proc = subprocess.run(
                ["claude", "--print", "-p", prompt],
                capture_output=True, text=True, timeout=60,
            )
            if result_proc.returncode != 0:
                proposal_text = (
                    f"スキル「{skill_name}」({line_count}行) の分割を検討してください。"
                    f"references/ にセクションを切り出し、SKILL.md を {threshold}行以下に削減することを推奨します。"
                )
            else:
                proposal_text = result_proc.stdout.strip()
        except (subprocess.TimeoutExpired, OSError):
            proposal_text = (
                f"スキル「{skill_name}」({line_count}行) の分割を検討してください。"
                f"references/ にセクションを切り出し、SKILL.md を {threshold}行以下に削減することを推奨します。"
            )

        results.append({
            "issue": issue, "original_content": "", "fixed": True,
            "error": None, "proposal_text": proposal_text,
        })

    return results


def fix_preflight_scriptification(
    issues: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Pre-flight スクリプト化提案をテンプレート付きで返す（ファイル変更なし）。"""
    results = []
    for issue in issues:
        if issue["type"] != "preflight_scriptification":
            continue
        detail = issue.get("detail", {})
        pitfall_title = detail.get("pitfall_title", "unknown")
        category = detail.get("category", "generic")
        template_path = detail.get("template_path", "")

        template_content = ""
        if template_path:
            tp = Path(template_path)
            if tp.exists():
                try:
                    template_content = tp.read_text(encoding="utf-8")
                except OSError:
                    pass

        proposal_text = (
            f"pitfall「{pitfall_title}」のPre-flightスクリプト化を提案します。\n"
            f"カテゴリ: {category}\n"
        )
        if template_content:
            proposal_text += f"テンプレート ({Path(template_path).name}):\n```bash\n{template_content}```\n"

        results.append({
            "issue": issue, "original_content": "", "fixed": True,
            "error": None, "proposal_text": proposal_text,
        })

    return results


def fix_workflow_checkpoint(
    issues: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """ワークフロースキルにチェックポイント追記を提案する（人間承認必須）。"""
    results = []
    for issue in issues:
        detail = issue.get("detail", {})
        skill_name = detail.get(WCC_SKILL_NAME, "")
        category = detail.get(WCC_CATEGORY, "")
        template = detail.get(WCC_TEMPLATE, "")

        proposal_text = (
            f"スキル「{skill_name}」にチェックポイント「{category}」の追加を提案します。\n\n"
            f"追加ステップ:\n{template}\n"
        )

        results.append({
            "issue": issue,
            "original_content": "",
            "fixed": True,
            "error": None,
            "proposal_text": proposal_text,
        })

    return results


def fix_skill_quality_pattern_gap(
    issues: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """スキル品質パターンギャップの修正提案を行う。"""
    results = []
    for issue in issues:
        if issue["type"] != SKILL_QUALITY_PATTERN_GAP:
            continue
        detail = issue.get("detail", {})
        skill_name = detail.get(SQP_SKILL_NAME, "unknown")
        domain = detail.get(SQP_DOMAIN, "default")
        missing_required = detail.get(SQP_MISSING_REQUIRED, [])
        missing_recommended = detail.get(SQP_MISSING_RECOMMENDED, [])

        suggestions = []
        pattern_templates = {
            "gotchas": "## Gotchas\n\n- [ ] (環境固有の注意点を記載)",
            "output_template": "## Output Format\n\n```\n(期待する出力形式を記載)\n```",
            "checklist": "## Steps\n\n1. (手順1)\n2. (手順2)\n3. (手順3)",
            "validation_loop": "## Validation\n\n1. 実行\n2. 結果を検証\n3. 問題があれば修正して再実行",
            "plan_validate_execute": "## Safety\n\n1. Plan: 変更内容を確認\n2. Validate: dry-run で検証\n3. Execute: 問題なければ実行",
            "progressive_disclosure": "## References\n\n詳細は `references/` を参照。",
            "defaults_first": "(選択肢を提示する際は推奨を明記: 「推奨: X」)",
        }

        for pattern in missing_required:
            template = pattern_templates.get(pattern, f"## {pattern}\n\n(内容を記載)")
            suggestions.append(f"[REQUIRED] {pattern}: {template}")
        for pattern in missing_recommended:
            template = pattern_templates.get(pattern, f"## {pattern}\n\n(内容を記載)")
            suggestions.append(f"[RECOMMENDED] {pattern}: {template}")

        results.append({
            "issue": issue,
            "fixed": False,
            "proposal": f"Skill '{skill_name}' (domain: {domain}) に以下のパターン追加を推奨:\n"
                        + "\n".join(suggestions),
            "changed_files": [],
        })

    return results


def fix_missing_effort(
    issues: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """effort frontmatter が未設定のスキルに追加提案を行う。"""
    from frontmatter import update_frontmatter

    results = []
    for issue in issues:
        if issue["type"] != MISSING_EFFORT_CANDIDATE:
            continue
        detail = issue.get("detail", {})
        skill_path = Path(detail.get(MEC_SKILL_PATH, ""))
        proposed = detail.get(MEC_PROPOSED_EFFORT, "medium")
        reason = detail.get(MEC_REASON, "")

        if not skill_path.is_file():
            results.append({
                "issue": issue,
                "fixed": False,
                "error": f"file not found: {skill_path}",
            })
            continue

        success, err = update_frontmatter(skill_path, {"effort": proposed})
        results.append({
            "issue": issue,
            "fixed": success,
            "error": err if not success else None,
            "changed_files": [str(skill_path)] if success else [],
            "proposal_text": (
                f"effort: {proposed} を {detail.get(MEC_SKILL_NAME, '')} に追加"
                f" (reason: {reason})"
            ),
        })

    return results


def _verify_missing_effort(result: Dict[str, Any]) -> Tuple[bool, str]:
    """effort frontmatter が正しく追加されたか検証する。"""
    from frontmatter import parse_frontmatter

    changed = result.get("changed_files", [])
    if not changed:
        return False, "no changed files"
    path = Path(changed[0])
    if not path.is_file():
        return False, f"file not found: {path}"
    fm = parse_frontmatter(path)
    if fm.get("effort"):
        return True, ""
    return False, "effort not found in frontmatter after fix"


def fix_instruction_violation(
    issues: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """instruction violation を pitfall として記録する proposable ハンドラ。"""
    results = []
    for issue in issues:
        if issue["type"] != INSTRUCTION_VIOLATION_CANDIDATE:
            continue
        detail = issue.get("detail", {})
        skill_name = detail.get(IVC_SKILL_NAME, "unknown")
        instruction_text = detail.get(IVC_INSTRUCTION_TEXT, "")
        match_type = detail.get(IVC_MATCH_TYPE, "")
        reason = detail.get(IVC_REASON, "")
        results.append({
            "issue": issue,
            "original_content": "",
            "fixed": True,
            "error": None,
            "proposal": (
                f"スキル「{skill_name}」の指示違反を pitfall に記録: "
                f"{instruction_text[:60]}... ({match_type})"
            ),
            "pitfall_root_cause": f"instruction — {reason}",
        })
    return results


def _build_fix_dispatch() -> Dict[str, Any]:
    """package 経由で各 fix 関数を遅延解決して FIX_DISPATCH を構築する。"""
    from . import (  # noqa: PLC0415
        fix_claudemd_missing_section,
        fix_claudemd_phantom_refs,
        fix_global_rule,
        fix_hook_scaffold,
        fix_line_limit_violation,
        fix_pitfall_archive,
        fix_skill_evolve,
        fix_stale_memory,
        fix_stale_references,
        fix_stale_rules,
        fix_untagged_reference,
        fix_verification_rule,
    )

    return {
        "stale_ref": fix_stale_references,
        "stale_memory": fix_stale_memory,
        "cap_exceeded": fix_pitfall_archive,
        "line_guard": fix_pitfall_archive,
        "split_candidate": fix_split_candidate,
        "preflight_scriptification": fix_preflight_scriptification,
        "stale_rule": fix_stale_rules,
        "line_limit_violation": fix_line_limit_violation,
        "untagged_reference_candidates": fix_untagged_reference,
        "claudemd_phantom_ref": fix_claudemd_phantom_refs,
        "claudemd_missing_section": fix_claudemd_missing_section,
        TOOL_USAGE_RULE_CANDIDATE: fix_global_rule,
        TOOL_USAGE_HOOK_CANDIDATE: fix_hook_scaffold,
        SKILL_EVOLVE_CANDIDATE: fix_skill_evolve,
        VERIFICATION_RULE_CANDIDATE: fix_verification_rule,
        WORKFLOW_CHECKPOINT_CANDIDATE: fix_workflow_checkpoint,
        MISSING_EFFORT_CANDIDATE: fix_missing_effort,
        SKILL_QUALITY_PATTERN_GAP: fix_skill_quality_pattern_gap,
        INSTRUCTION_VIOLATION_CANDIDATE: fix_instruction_violation,
    }


def generate_proposals(
    issues: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """行数制限違反や肥大化警告に対する修正案を rationale 付きで生成する。"""
    from . import generate_rationale  # noqa: PLC0415

    proposals = []
    for issue in issues:
        category = issue.get("category", "proposable")
        rationale = generate_rationale(issue, category)

        if issue["type"] == "stale_ref":
            detail = issue.get("detail", {})
            path = detail.get("path", "unknown")
            proposal = f"{issue['file']} 内の陳腐化参照「{path}」を削除"
        elif issue["type"] == "stale_rule":
            detail = issue.get("detail", {})
            path = detail.get("path", "unknown")
            proposal = f"ルール {issue['file']} 内の不存在参照「{path}」を更新/削除"
        elif issue["type"] == "claudemd_phantom_ref":
            detail = issue.get("detail", {})
            ref_type = detail.get("ref_type", "skill")
            name = detail.get("name", "unknown")
            proposal = f"CLAUDE.md 内の存在しない{ref_type}参照「{name}」を削除"
        elif issue["type"] == "claudemd_missing_section":
            detail = issue.get("detail", {})
            section = detail.get("section", "skills")
            proposal = f"CLAUDE.md に {section} セクションを追加"
        elif issue["type"] == "line_limit_violation":
            detail = issue.get("detail", {})
            lines = detail.get("lines", 0)
            limit = detail.get("limit", 1)
            proposal = (
                f"ファイル {issue['file']} ({lines}/{limit} 行) を分析し、"
                f"最も行数の多いセクションを references/ に切り出す提案です。"
            )
        elif issue["type"] == "near_limit":
            detail = issue.get("detail", {})
            proposal = (
                f"ファイル {issue['file']} ({detail.get('pct', 0)}%) を"
                f"トピック別ファイルに分割する提案です。"
            )
        elif issue["type"] == "orphan_rule":
            detail = issue.get("detail", {})
            name = detail.get("name", Path(issue["file"]).stem)
            proposal = f"ルール「{name}」の削除"
        elif issue["type"] == "stale_memory":
            detail = issue.get("detail", {})
            path = detail.get("path", "unknown")
            proposal = f"MEMORY.md の「{path}」エントリの更新/削除"
        elif issue["type"] == "memory_duplicate":
            detail = issue.get("detail", {})
            sections = detail.get("sections", ["unknown", "unknown"])
            a = sections[0] if len(sections) > 0 else "unknown"
            b = sections[1] if len(sections) > 1 else "unknown"
            proposal = f"セクション「{a}」と「{b}」の統合"
        elif issue["type"] == TOOL_USAGE_RULE_CANDIDATE:
            detail = issue.get("detail", {})
            cmds = ", ".join(detail.get(RULE_TARGET_COMMANDS, []))
            proposal = f"~/.claude/rules/{detail.get(RULE_FILENAME, 'avoid-bash-builtin.md')} に Bash {cmds} 禁止ルールを作成"
        elif issue["type"] == TOOL_USAGE_HOOK_CANDIDATE:
            detail = issue.get("detail", {})
            proposal = f"{detail.get(HOOK_SCRIPT_PATH, '~/.claude/hooks/check-bash-builtin.py')} に PreToolUse hook を生成"
        elif issue["type"] == "untagged_reference_candidates":
            detail = issue.get("detail", {})
            skill_name = detail.get("skill_name", "unknown")
            proposal = f"スキル「{skill_name}」の frontmatter に `type: reference` を追加"
        elif issue["type"] == SKILL_EVOLVE_CANDIDATE:
            detail = issue.get("detail", {})
            skill_name = detail.get(SE_SKILL_NAME, "unknown")
            score = detail.get(SE_TOTAL_SCORE, 0)
            proposal = f"スキル「{skill_name}」({score}/15点) に自己進化パターンを組み込み"
        elif issue["type"] == VERIFICATION_RULE_CANDIDATE:
            detail = issue.get("detail", {})
            filename = detail.get(VRC_RULE_FILENAME, "unknown")
            proposal = f".claude/rules/{filename} に検証ルール「{detail.get(VRC_DESCRIPTION, '')}」を作成"
        elif issue["type"] == "cap_exceeded":
            detail = issue.get("detail", {})
            proposal = f"Active pitfall ({detail.get('active_count', 0)}件) の超過分をCold層からアーカイブ"
        elif issue["type"] == "line_guard":
            detail = issue.get("detail", {})
            proposal = f"pitfalls.md ({detail.get('line_count', 0)}行) のCold層をアーカイブして閾値以下に削減"
        elif issue["type"] == "split_candidate":
            detail = issue.get("detail", {})
            skill_name = detail.get("skill_name", "unknown")
            proposal = f"スキル「{skill_name}」({detail.get('line_count', 0)}行) の references/ 分割提案"
        elif issue["type"] == "preflight_scriptification":
            detail = issue.get("detail", {})
            proposal = f"pitfall「{detail.get('pitfall_title', 'unknown')}」のPre-flightスクリプト化提案"
        elif issue["type"] == "duplicate":
            detail = issue.get("detail", {})
            proposal = f"アーティファクト「{detail.get('name', 'unknown')}」の統合提案"
        else:
            proposal = f"{issue['type']} に対する修正案を検討してください。"

        entry = {
            "issue": issue,
            "proposal": proposal,
            "rationale": rationale,
        }

        if issue["type"] in (TOOL_USAGE_RULE_CANDIDATE, VERIFICATION_RULE_CANDIDATE):
            detail = issue.get("detail", {})
            evidence = detail.get(VRC_EVIDENCE, "") or detail.get("evidence", "")
            if evidence:
                try:
                    from reflect_utils import suggest_paths_frontmatter
                    ps = suggest_paths_frontmatter(evidence, Path.cwd())
                    if ps is not None:
                        entry["paths_suggestion"] = {
                            "patterns": ps.patterns,
                            "confidence": ps.confidence,
                        }
                except ImportError:
                    pass

        proposals.append(entry)

    return proposals


def generate_auto_fix_summaries(
    issues: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """auto_fixable に分類された issue を1件ずつ rationale 付きで列挙する。

    evolve の Remediation フェーズで「一括修正しますか？」と尋ねる前に、
    各 issue が「何をなぜどう直すのか」を1件単位で提示するために使う。
    auto_fixable 以外の category は除外する。

    Returns:
        [{"issue": <classified issue>, "proposal": str, "rationale": str}, ...]
    """
    auto_fixable = [
        issue for issue in issues if issue.get("category") == "auto_fixable"
    ]
    return generate_proposals(auto_fixable)
