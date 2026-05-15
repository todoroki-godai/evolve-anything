#!/usr/bin/env python3
"""淘汰スクリプト。

dead glob・zero invocation・重複の3基準でアーティファクトを検出し、
アーカイブを提案する。直接削除は行わない（MUST NOT）。
"""
import json
import math
import os
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from plugin_root import PLUGIN_ROOT
_plugin_root = PLUGIN_ROOT
sys.path.insert(0, str(PLUGIN_ROOT / "scripts" / "lib"))
sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))

from frontmatter import extract_description, parse_frontmatter
from similarity import filter_merge_group_pairs
from discover import load_merge_suppression
from skill_usage_stats import find_unused_global_skills, find_rarely_used_global_skills, find_nested_only_skills

from audit import (
    DATA_DIR,
    classify_artifact_origin,
    find_artifacts,
    load_usage_data,
    aggregate_usage,
    load_usage_registry,
    semantic_similarity_check,
)

ARCHIVE_DIR = DATA_DIR / "archive"

# 閾値定数 + evolve-state.json ロードは prune/config.py に集約済み（後方互換のため再エクスポート）
from .config import (  # noqa: E402, F401
    DEFAULT_DECAY_DAYS,
    DEFAULT_DECAY_THRESHOLD,
    CORRECTION_PENALTY,
    ZERO_INVOCATION_DAYS,
    DEFAULT_MERGE_SIMILARITY_THRESHOLD,
    DEFAULT_INTERACTIVE_MERGE_THRESHOLD,
    DEFAULT_DRIFT_THRESHOLD,
    load_merge_similarity_threshold,
    load_interactive_merge_threshold,
    load_decay_threshold,
    load_drift_threshold,
)


# スキル frontmatter 解析 + 推薦ラベルは prune/skill_inspect.py に集約済み（後方互換のため再エクスポート）
from .skill_inspect import (  # noqa: E402, F401
    _ARCHIVE_KEYWORDS,
    _KEEP_KEYWORDS,
    _KEEP_TRIGGER_THRESHOLD,
    _count_triggers,
    _enrich_candidate,
    extract_skill_summary,
    suggest_recommendation,
)


# corrections.jsonl の読み込み / decay-based クリーンアップは prune/corrections.py に集約済み（後方互換のため再エクスポート）
from .corrections import (  # noqa: E402, F401
    load_corrections,
    cleanup_corrections,
)


# 参照型判定 + 推定キャッシュ + 減衰スコア / pin は prune/skill_inspect.py に集約済み（後方互換のため再エクスポート）
from .skill_inspect import (  # noqa: E402, F401
    _load_skill_type_cache,
    _save_skill_type_cache,
    _resolve_skill_md,
    is_reference_skill,
    _estimate_skill_type,
    compute_decay_score,
    is_pinned,
)



# dead glob / zero invocation / global safe / duplicate / decay 検出は prune/detection.py に集約済み（後方互換のため再エクスポート）
from .detection import (  # noqa: E402, F401
    _expand_glob_pattern,
    detect_dead_globs,
    detect_zero_invocations,
    safe_global_check,
    detect_duplicates,
    detect_decay_candidates,
)





# skill 依存検査 (import / path ref) は prune/dependency.py に集約済み（後方互換のため再エクスポート）
from .dependency import (  # noqa: E402, F401
    SkillDependencyError,
    _IMPORT_RE_TEMPLATE,
    _list_skill_module_names,
    _git_grep_files,
    _is_git_repo,
    _iter_text_files,
    _python_grep_files_per_module,
    _python_grep_files,
    _is_excluded_referrer,
    check_import_dependencies,
)




# _is_skill_dir は prune/skill_inspect.py に集約済み（後方互換のため再エクスポート）
from .skill_inspect import _is_skill_dir  # noqa: E402, F401



# archive 操作 + 重複マージ提案は prune/archive.py に集約済み（後方互換のため再エクスポート）
from .archive import (  # noqa: E402, F401
    archive_file,
    restore_file,
    list_archive,
    determine_primary,
    merge_duplicates,
)




def _gather_drift_context(skill_path: Path, project_dir: Path) -> str:
    """ドリフト評価用のコンテキストを収集する。

    CLAUDE.md、rules、スキル内容から関連ファイルのコンテキストをまとめる。
    """
    context_parts = []

    # スキル内容
    resolved = _resolve_skill_md(skill_path)
    if resolved.exists():
        context_parts.append(f"=== Skill Content ({resolved.name}) ===\n{resolved.read_text(encoding='utf-8')}")

    # CLAUDE.md
    claude_md = project_dir / "CLAUDE.md"
    if claude_md.exists():
        context_parts.append(f"=== CLAUDE.md ===\n{claude_md.read_text(encoding='utf-8')}")

    # rules
    rules_dir = project_dir / ".claude" / "rules"
    if rules_dir.exists():
        for rule_file in sorted(rules_dir.glob("*.md"))[:10]:
            context_parts.append(f"=== Rule: {rule_file.name} ===\n{rule_file.read_text(encoding='utf-8')}")

    return "\n\n".join(context_parts)


def detect_reference_drift(
    artifacts: Dict[str, List[Path]],
    project_dir: Path,
) -> List[Dict[str, Any]]:
    """参照型スキルの内容とコードベースの乖離度を評価し、ドリフト候補を返す。

    サブエージェント呼び出しで乖離度を 0.0〜1.0 で評価する。
    サブエージェント失敗時はそのスキルを候補に含めない。
    非参照型スキルは評価しない。
    """
    threshold = load_drift_threshold()
    candidates = []

    for path in artifacts.get("skills", []):
        # 参照型スキルのみ対象
        if not is_reference_skill(path):
            continue

        skill_name = path.parent.name
        try:
            context = _gather_drift_context(path, project_dir)
            # サブエージェントでドリフト評価
            # 実際の実行時は Agent tool で LLM 評価を行う
            # ここではコンテキスト収集までを行い、スコアは呼び出し側で設定
            drift_result = _evaluate_drift(context, skill_name)
            if drift_result and drift_result.get("drift_score", 0) >= threshold:
                candidates.append({
                    "file": str(path),
                    "skill_name": skill_name,
                    "reason": "reference_drift",
                    "drift_score": drift_result["drift_score"],
                    "drift_reason": drift_result.get("drift_reason", ""),
                })
        except Exception as e:
            # サブエージェント失敗時は候補に含めない（安全側倒し）
            print(f"[prune] drift evaluation failed for {skill_name}: {e}", file=sys.stderr)
            continue

    return candidates


def _evaluate_drift(context: str, skill_name: str) -> Optional[Dict[str, Any]]:
    """ドリフト評価のプレースホルダ。

    実際の prune スキル実行時は Agent tool のサブエージェントで
    コンテキストを評価し、drift_score と drift_reason を返す。
    ここではテスト用にデフォルト値を返す。
    """
    # プレースホルダ実装: 実運用時はサブエージェントで置換
    return {"drift_score": 0.0, "drift_reason": ""}


def run_prune(
    project_dir: Optional[str] = None,
    reorganize_merge_groups: Optional[list] = None,
) -> Dict[str, Any]:
    """Prune を実行して候補を返す。"""
    proj = Path(project_dir) if project_dir else Path.cwd()
    artifacts = find_artifacts(proj)

    zero_invocations, plugin_unused = detect_zero_invocations(artifacts)

    candidates = {
        "dead_globs": detect_dead_globs(proj),
        "zero_invocations": zero_invocations,
        "plugin_unused": plugin_unused,
        "global_candidates": safe_global_check(artifacts),
        "duplicate_candidates": detect_duplicates(artifacts),
        "decay_candidates": detect_decay_candidates(artifacts),
        "reference_drift_candidates": detect_reference_drift(artifacts, proj),
    }

    total = sum(len(v) for v in candidates.values() if isinstance(v, list))
    candidates["total_candidates"] = total

    # rules は淘汰対象外。情報提供のみ。
    rules = artifacts.get("rules", [])
    candidates["rules_info"] = [
        {"name": p.stem, "scope": "global" if ".claude/rules" in str(p) and "projects" not in str(p) else "project"}
        for p in rules
    ]

    # corrections.jsonl クリーンアップ
    cleanup_result = cleanup_corrections()
    candidates["corrections_cleanup"] = cleanup_result

    # マージ提案を生成
    merge_result = merge_duplicates(
        candidates["duplicate_candidates"],
        reorganize_merge_groups=reorganize_merge_groups,
        project_dir=project_dir,
    )
    candidates["merge_result"] = merge_result

    return candidates


if __name__ == "__main__":
    import sys

    project = sys.argv[1] if len(sys.argv) > 1 else None
    result = run_prune(project)
    print(json.dumps(result, ensure_ascii=False, indent=2))
