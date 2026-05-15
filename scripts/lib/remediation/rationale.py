"""rationale 生成 — 修正理由テキスト (旧 remediation.py 由来)。

remediation/__init__.py から re-export される（後方互換）。
"""
from typing import Any, Dict

from issue_schema import (
    HOOK_TOTAL_COUNT,
    RULE_ALTERNATIVE_TOOLS,
    RULE_TARGET_COMMANDS,
    RULE_TOTAL_COUNT,
    SE_SKILL_NAME,
    SE_SUITABILITY,
    SE_TOTAL_SCORE,
    SKILL_EVOLVE_CANDIDATE,
    TOOL_USAGE_HOOK_CANDIDATE,
    TOOL_USAGE_RULE_CANDIDATE,
    VERIFICATION_RULE_CANDIDATE,
    VRC_DESCRIPTION,
    VRC_DETECTION_CONFIDENCE,
    VRC_EVIDENCE,
)


_RATIONALE_TEMPLATES = {
    "stale_ref": "ディスク上に存在しないパス参照「{path}」を削除します。",
    "line_limit_violation_auto": "行数が制限を {excess} 行超過しています。空行除去等で制限内に収めます。",
    "line_limit_violation_propose": "行数が制限値の {pct}% ({lines}/{limit}) です。reference ファイルへの切り出しを提案します。",
    "line_limit_violation_manual": "行数が制限値の {pct}% ({lines}/{limit}) と大幅に超過しています。手動でのリファクタリングが必要です。",
    "near_limit": "行数が制限の {pct}% ({lines}/{limit}) に達しています。トピック別ファイルへの分割を提案します。",
    "duplicate": "名前が類似するアーティファクト「{name}」が {count} 箇所にあります。統合を検討してください。",
    "hardcoded_value": "ハードコード値 `{matched}` ({pattern_type}) が検出されました。プレースホルダへの置換を検討してください。",
    "orphan_rule": "ルール「{name}」はどのスキル・CLAUDE.md からも参照されていません。",
    "stale_rule": "ルール内で参照されている「{path}」が存在しません。参照の更新または削除を検討してください。",
    "stale_memory": "MEMORY.md 内の「{path}」への言及は実体が見つかりません。エントリの更新または削除を検討してください。",
    "memory_duplicate": "MEMORY.md のセクション「{section_a}」と「{section_b}」は内容が重複しています（類似度: {similarity}）。統合を検討してください。",
    "hooks_unconfigured": "hooks 設定が見つかりません。observe hooks の設定を検討してください。",
    "claudemd_phantom_ref": "CLAUDE.md 内で言及された{ref_type}「{name}」が存在しません。",
    "claudemd_missing_section": "CLAUDE.md に {section} セクションがありませんが、{skill_count} 個のスキルが存在します。セクションの追加を検討してください。",
    TOOL_USAGE_RULE_CANDIDATE: "Bash で {commands} が計 {count} 回使用されています。{alternatives} ツールで代替可能です。global rule の追加を提案します。",
    TOOL_USAGE_HOOK_CANDIDATE: "Bash での Built-in 代替可能コマンド使用（{count} 回検出）を自動検出する PreToolUse hook の追加を提案します。",
    "untagged_reference_candidates": "スキル「{skill_name}」は呼び出し実績がなく reference type が未設定です。frontmatter に `type: reference` を追加します。",
    SKILL_EVOLVE_CANDIDATE: "スキル「{skill_name}」の自己進化適性: {suitability}（{total_score}/15点）。自己進化パターン（Pre-flight Check, pitfalls.md, Failure-triggered Learning）の組み込みを提案します。",
    VERIFICATION_RULE_CANDIDATE: "{description}が {evidence_count} 箇所検出されました（confidence: {confidence}）。ルールの追加を提案します。",
    "cap_exceeded": "Active pitfall が {active_count}/{cap} 件で上限を超過しています。Cold 層（Graduated/Candidate/New）から優先順にアーカイブします。",
    "line_guard": "pitfalls.md が {line_count}/{max_lines} 行で上限を超過しています。Cold 層から優先順にアーカイブします。",
    "split_candidate": "スキル「{skill_name}」({line_count}/{threshold}行) が分割閾値を超過しています。references/ への切り出しを提案します。",
    "preflight_scriptification": "pitfall「{pitfall_title}」(カテゴリ: {category}) のPre-flightスクリプト化を提案します。",
}


def generate_rationale(issue: Dict[str, Any], category: str) -> str:
    """修正アクションに対する修正理由テキストを生成する。"""
    issue_type = issue["type"]
    detail = issue.get("detail", {})

    if issue_type == "stale_ref":
        return _RATIONALE_TEMPLATES["stale_ref"].format(
            path=detail.get("path", "unknown"),
        )

    if issue_type == "line_limit_violation":
        lines = detail.get("lines", 0)
        limit = detail.get("limit", 1)
        pct = int(lines / limit * 100) if limit > 0 else 0
        excess = lines - limit

        if category == "auto_fixable":
            return _RATIONALE_TEMPLATES["line_limit_violation_auto"].format(excess=excess)
        elif category == "proposable":
            return _RATIONALE_TEMPLATES["line_limit_violation_propose"].format(
                pct=pct, lines=lines, limit=limit,
            )
        else:
            return _RATIONALE_TEMPLATES["line_limit_violation_manual"].format(
                pct=pct, lines=lines, limit=limit,
            )

    if issue_type == "near_limit":
        return _RATIONALE_TEMPLATES["near_limit"].format(
            pct=detail.get("pct", 0),
            lines=detail.get("lines", 0),
            limit=detail.get("limit", 0),
        )

    if issue_type == "duplicate":
        return _RATIONALE_TEMPLATES["duplicate"].format(
            name=detail.get("name", "unknown"),
            count=len(detail.get("paths", [])),
        )

    if issue_type == "hardcoded_value":
        return _RATIONALE_TEMPLATES["hardcoded_value"].format(
            matched=detail.get("matched", "unknown"),
            pattern_type=detail.get("pattern_type", "unknown"),
        )

    if issue_type == "orphan_rule":
        return _RATIONALE_TEMPLATES["orphan_rule"].format(
            name=detail.get("name", "unknown"),
        )

    if issue_type == "stale_rule":
        return _RATIONALE_TEMPLATES["stale_rule"].format(
            path=detail.get("path", "unknown"),
        )

    if issue_type == "stale_memory":
        return _RATIONALE_TEMPLATES["stale_memory"].format(
            path=detail.get("path", "unknown"),
        )

    if issue_type == "memory_duplicate":
        sections = detail.get("sections", ["unknown", "unknown"])
        return _RATIONALE_TEMPLATES["memory_duplicate"].format(
            section_a=sections[0] if len(sections) > 0 else "unknown",
            section_b=sections[1] if len(sections) > 1 else "unknown",
            similarity=detail.get("similarity", 0.0),
        )

    if issue_type == "hooks_unconfigured":
        return _RATIONALE_TEMPLATES["hooks_unconfigured"]

    if issue_type == "claudemd_phantom_ref":
        return _RATIONALE_TEMPLATES["claudemd_phantom_ref"].format(
            ref_type=detail.get("ref_type", "skill"),
            name=detail.get("name", "unknown"),
        )

    if issue_type == "claudemd_missing_section":
        return _RATIONALE_TEMPLATES["claudemd_missing_section"].format(
            section=detail.get("section", "skills"),
            skill_count=detail.get("skill_count", 0),
        )

    if issue_type == TOOL_USAGE_RULE_CANDIDATE:
        return _RATIONALE_TEMPLATES[TOOL_USAGE_RULE_CANDIDATE].format(
            commands=", ".join(detail.get(RULE_TARGET_COMMANDS, ["unknown"])),
            count=detail.get(RULE_TOTAL_COUNT, 0),
            alternatives=", ".join(detail.get(RULE_ALTERNATIVE_TOOLS, ["unknown"])),
        )

    if issue_type == TOOL_USAGE_HOOK_CANDIDATE:
        return _RATIONALE_TEMPLATES[TOOL_USAGE_HOOK_CANDIDATE].format(
            count=detail.get(HOOK_TOTAL_COUNT, 0),
        )

    if issue_type == "untagged_reference_candidates":
        return _RATIONALE_TEMPLATES["untagged_reference_candidates"].format(
            skill_name=detail.get("skill_name", "unknown"),
        )

    if issue_type == SKILL_EVOLVE_CANDIDATE:
        return _RATIONALE_TEMPLATES[SKILL_EVOLVE_CANDIDATE].format(
            skill_name=detail.get(SE_SKILL_NAME, "unknown"),
            suitability=detail.get(SE_SUITABILITY, "unknown"),
            total_score=detail.get(SE_TOTAL_SCORE, 0),
        )

    if issue_type == VERIFICATION_RULE_CANDIDATE:
        return _RATIONALE_TEMPLATES[VERIFICATION_RULE_CANDIDATE].format(
            evidence_count=len(detail.get(VRC_EVIDENCE, [])),
            confidence=detail.get(VRC_DETECTION_CONFIDENCE, 0.0),
            description=detail.get(VRC_DESCRIPTION, "unknown"),
        )

    if issue_type == "cap_exceeded":
        return _RATIONALE_TEMPLATES["cap_exceeded"].format(
            active_count=detail.get("active_count", 0),
            cap=detail.get("cap", 10),
        )

    if issue_type == "line_guard":
        return _RATIONALE_TEMPLATES["line_guard"].format(
            line_count=detail.get("line_count", 0),
            max_lines=detail.get("max_lines", 500),
        )

    if issue_type == "split_candidate":
        return _RATIONALE_TEMPLATES["split_candidate"].format(
            skill_name=detail.get("skill_name", "unknown"),
            line_count=detail.get("line_count", 0),
            threshold=detail.get("threshold", 300),
        )

    if issue_type == "preflight_scriptification":
        return _RATIONALE_TEMPLATES["preflight_scriptification"].format(
            pitfall_title=detail.get("pitfall_title", "unknown"),
            category=detail.get("category", "unknown"),
        )

    return f"問題タイプ「{issue_type}」が検出されました。"
