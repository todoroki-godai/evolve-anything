#!/usr/bin/env python3
"""パターン発見スクリプト。

usage.jsonl、errors.jsonl、sessions.jsonl、history.jsonl から
繰り返しパターンを検出し、スキル/ルール候補を生成する。
"""
import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional

DATA_DIR = Path.home() / ".claude" / "rl-anything"

# パッケージ化後 (Phase 2): __file__ は scripts/lib/discover/__init__.py のため
# scripts/lib/ を sys.path に追加して plugin_root / line_limit / similarity 等を解決
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from plugin_root import PLUGIN_ROOT
_plugin_root = PLUGIN_ROOT

HISTORY_DIR = PLUGIN_ROOT / "skills" / "genetic-prompt-optimizer" / "scripts" / "generations"

# 閾値
BEHAVIOR_THRESHOLD = 5   # 行動パターン検出閾値
ERROR_THRESHOLD = 3       # エラーパターン検出閾値
REJECTION_THRESHOLD = 3   # 却下理由検出閾値
MISSED_SKILL_THRESHOLD = 2  # missed skill 検出閾値（セッション数）

# 構造的制約は共通モジュールから取得
sys.path.insert(0, str(PLUGIN_ROOT / "scripts" / "lib"))
from agent_classifier import classify_agent_type
from line_limit import MAX_RULE_LINES, MAX_SKILL_LINES
from similarity import jaccard_coefficient, tokenize
from skill_triggers import extract_skill_triggers, normalize_skill_name

# Jaccard 照合閾値
JACCARD_THRESHOLD = 0.15

# Discover 振動防止用抑制リスト
SUPPRESSION_FILE = DATA_DIR / "discover-suppression.jsonl"


# 抑制リスト / JSONL ローダ / バリデータ / トークン抽出は discover/suppression.py に集約済み（後方互換のため再エクスポート）
from .suppression import (  # noqa: E402, F401
    load_jsonl,
    load_suppression_list,
    load_merge_suppression,
    add_merge_suppression,
    add_to_suppression_list,
    validate_skill_content,
    validate_rule_content,
    load_claude_reflect_data,
    _load_skill_tokens,
    _load_classify_usage_skill,
)


# 行動パターン検出 + Agent prompt 分類 + missed skill 検出は discover/patterns.py に集約済み（後方互換のため再エクスポート）
from .patterns import (  # noqa: E402, F401
    detect_behavior_patterns,
    detect_missed_skills,
    _classify_agent_prompts,
)



# エラー / 繰り返し correction / rejection 検出 + scope 判定は discover/errors.py に集約済み（後方互換のため再エクスポート）
from .errors import (  # noqa: E402, F401
    HOOK_CANDIDATE_THRESHOLD,
    detect_error_patterns,
    detect_repeated_correction_patterns,
    detect_rejection_patterns,
    determine_scope,
)


# Jaccard 照合 (enrich) は discover/enrich.py に集約済み（後方互換のため再エクスポート）
from .enrich import _enrich_patterns  # noqa: E402, F401


# 推奨 artifact 一覧 + 導入状態判定 + mitigation_metrics は discover/artifacts.py に集約済み（後方互換のため再エクスポート）
from .artifacts import (  # noqa: E402, F401
    RECOMMENDED_ARTIFACTS,
    detect_recommended_artifacts,
    detect_installed_artifacts,
    _compute_mitigation_metrics,
)



def run_discover(
    *,
    project_root: Optional[Path] = None,
    include_unknown: bool = False,
    tool_usage: bool = False,
) -> Dict[str, Any]:
    """Discover を実行して候補を返す。enrich 統合済み。"""
    behavior = detect_behavior_patterns(
        project_root=project_root, include_unknown=include_unknown,
    )
    errors = detect_error_patterns(
        project_root=project_root, include_unknown=include_unknown,
    )
    rejections = detect_rejection_patterns()
    reflect_data = load_claude_reflect_data()

    # missed skill 検出
    missed_result = detect_missed_skills(
        project_root=project_root,
        include_unknown=include_unknown,
    )

    result: Dict[str, Any] = {
        "behavior_patterns": behavior,
        "error_patterns": errors,
        "rejection_patterns": rejections,
        "reflect_data_count": len(reflect_data),
    }

    # missed skill opportunities をレポートに含める
    if missed_result["missed"]:
        result["missed_skill_opportunities"] = missed_result["missed"]
    if missed_result["message"]:
        result["missed_skill_message"] = missed_result["message"]

    # スコープ判断
    all_patterns = behavior + errors + rejections
    for p in all_patterns:
        p["scope"] = determine_scope(p)

    result["total_candidates"] = len(all_patterns)

    # enrich 統合: Jaccard 照合
    active_patterns = errors + rejections if (errors or rejections) else behavior
    if active_patterns:
        enrich_result = _enrich_patterns(active_patterns, project_dir=project_root)
        result["matched_skills"] = enrich_result["matched_skills"]
        result["unmatched_patterns"] = enrich_result["unmatched_patterns"]

    # 検証知見カタログの検出
    try:
        from verification_catalog import detect_verification_needs
        proj = project_root or Path.cwd()
        verification_needs = detect_verification_needs(proj)
        if verification_needs:
            result["verification_needs"] = verification_needs
    except Exception as e:
        result["verification_needs_error"] = str(e)

    tool_result = None
    if tool_usage:
        from tool_usage_analyzer import analyze_tool_usage
        tool_result = analyze_tool_usage(project_root=project_root)
        if tool_result["total_tool_calls"] > 0:
            result["tool_usage_patterns"] = tool_result

    # 推奨アーティファクト未導入チェック（tool_usage データを証拠として付加）
    recommended_missing = detect_recommended_artifacts(
        tool_usage_patterns=tool_result,
    )
    if recommended_missing:
        result["recommended_artifacts"] = recommended_missing

    # 導入済みアーティファクトの状態
    installed = detect_installed_artifacts(
        tool_usage_patterns=tool_result,
    )
    if installed:
        result["installed_artifacts"] = installed

    # pitfall 自動検出
    try:
        _lib_path = PLUGIN_ROOT / "scripts" / "lib"
        if str(_lib_path) not in sys.path:
            sys.path.insert(0, str(_lib_path))
        from pitfall_manager import extract_pitfall_candidates
        from telemetry_query import query_corrections, query_errors
        proj = project_root or Path.cwd()
        proj_name = proj.name
        corrections_data = query_corrections(project=proj_name)
        errors_data = query_errors(project=proj_name)
        pitfall_result = extract_pitfall_candidates(corrections_data, errors=errors_data)
        if pitfall_result["candidates"]:
            result["pitfall_candidates"] = pitfall_result["candidates"]

        # hook 候補検出: 同じ corrections パターンが N 回繰り返されたもの (#41)
        hook_candidates = detect_repeated_correction_patterns(corrections_data)
        if hook_candidates:
            result["hook_candidates"] = hook_candidates
    except Exception as e:
        result["pitfall_candidates_error"] = str(e)

    # instruction violation 検出 (issue #39)
    try:
        from critical_instruction_extractor import (
            extract_critical_lines,
            detect_instruction_violation,
        )
        from telemetry_query import query_corrections
        from issue_schema import make_instruction_violation_issue

        proj = project_root or Path.cwd()
        proj_name = proj.name
        corrections_data = query_corrections(project=proj_name)

        # last_skill が設定されている corrections のみ対象
        skill_corrections = [
            c for c in corrections_data if c.get("last_skill")
        ]

        violations = []
        for corr in skill_corrections:
            skill_name = corr["last_skill"]
            # スキルの SKILL.md を探す
            skill_dirs = list(Path.home().glob(f".claude/skills/{skill_name}/SKILL.md"))
            pj_skill_dirs = list((proj / ".claude" / "skills" / skill_name / "SKILL.md").parent.glob("SKILL.md")) if (proj / ".claude" / "skills" / skill_name).exists() else []
            all_skill_mds = skill_dirs + [d for d in pj_skill_dirs if d not in skill_dirs]

            for skill_md in all_skill_mds:
                content = skill_md.read_text(encoding="utf-8")
                instructions = extract_critical_lines(content)
                if not instructions:
                    continue
                violation = detect_instruction_violation(corr, instructions)
                if violation:
                    violations.append(
                        make_instruction_violation_issue(
                            skill_name=skill_name,
                            skill_path=str(skill_md),
                            instruction_text=violation.instruction.original,
                            correction_message=violation.correction_message,
                            match_type=violation.match_type,
                            confidence=violation.confidence,
                            reason=violation.reason,
                            needs_review=violation.needs_review,
                        )
                    )
                break  # 最初にマッチしたスキルのみ

        if violations:
            result["instruction_violations"] = violations
    except Exception as e:
        result["instruction_violations_error"] = str(e)

    # 停滞→リカバリパターン検出
    try:
        from tool_usage_analyzer import (
            extract_tool_calls_by_session,
            detect_stall_recovery_patterns,
            STALL_RECOVERY_RECENCY_DAYS,
        )
        session_commands = extract_tool_calls_by_session(
            project_root,
            max_age_days=STALL_RECOVERY_RECENCY_DAYS,
        )
        stall_patterns = detect_stall_recovery_patterns(session_commands)
        result["stall_recovery_patterns"] = stall_patterns
    except Exception as e:
        result["stall_recovery_patterns"] = []
        result["stall_recovery_error"] = str(e)

    # ワークフローチェックポイントギャップ走査
    try:
        from workflow_checkpoint import is_workflow_skill, detect_checkpoint_gaps
        proj = project_root or Path.cwd()
        skills_dir = proj / ".claude" / "skills"
        workflow_gaps = []
        if skills_dir.is_dir():
            for skill_dir in sorted(skills_dir.iterdir()):
                if not skill_dir.is_dir():
                    continue
                if not is_workflow_skill(skill_dir):
                    continue
                gaps = detect_checkpoint_gaps(skill_dir.name, skill_dir, proj)
                if gaps:
                    workflow_gaps.append({
                        "skill_name": skill_dir.name,
                        "gaps": gaps,
                    })
        if workflow_gaps:
            result["workflow_checkpoint_gaps"] = workflow_gaps
    except Exception as e:
        result["workflow_checkpoint_gaps_error"] = str(e)

    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="パターン発見スクリプト")
    parser.add_argument(
        "--project-dir",
        default=None,
        help="プロジェクトディレクトリ（指定時はそのプロジェクトのレコードのみ集計）",
    )
    parser.add_argument(
        "--include-unknown",
        action="store_true",
        help="project が null のレコードも集計に含める",
    )
    parser.add_argument(
        "--tool-usage",
        action="store_true",
        help="セッション JSONL からツール利用パターンを分析する",
    )
    args = parser.parse_args()

    project_root = Path(args.project_dir) if args.project_dir else None
    result = run_discover(
        project_root=project_root,
        include_unknown=args.include_unknown,
        tool_usage=args.tool_usage,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
