#!/usr/bin/env python3
"""Remediation エンジン。

audit の検出結果を受け取り、confidence_score / impact_scope ベースで
auto_fixable / proposable / manual_required に動的分類し、
修正アクション生成・検証・テレメトリ記録を行う。
"""
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from plugin_root import PLUGIN_ROOT
_plugin_root = PLUGIN_ROOT
sys.path.insert(0, str(PLUGIN_ROOT / "scripts" / "lib"))
from skill_origin import (  # noqa: E402
    is_protected_skill,
    suggest_local_alternative,
    generate_protection_warning,
)
from issue_schema import (  # noqa: E402
    TOOL_USAGE_RULE_CANDIDATE,
    TOOL_USAGE_HOOK_CANDIDATE,
    SKILL_EVOLVE_CANDIDATE,
    VERIFICATION_RULE_CANDIDATE,
    RULE_FILENAME,
    RULE_CONTENT,
    RULE_TARGET_COMMANDS,
    RULE_ALTERNATIVE_TOOLS,
    RULE_TOTAL_COUNT,
    HOOK_SCRIPT_PATH,
    HOOK_SCRIPT_CONTENT,
    HOOK_SETTINGS_DIFF,
    HOOK_TOTAL_COUNT,
    SE_SKILL_NAME,
    SE_SKILL_DIR,
    SE_SUITABILITY,
    SE_TOTAL_SCORE,
    SE_SCORES,
    VRC_CATALOG_ID,
    VRC_RULE_FILENAME,
    VRC_RULE_TEMPLATE,
    VRC_DESCRIPTION,
    VRC_EVIDENCE,
    VRC_DETECTION_CONFIDENCE,
    WORKFLOW_CHECKPOINT_CANDIDATE,
    WCC_SKILL_NAME,
    WCC_CATEGORY,
    WCC_EVIDENCE_COUNT,
    WCC_CONFIDENCE,
    WCC_TEMPLATE,
    WCC_DESCRIPTION,
    MISSING_EFFORT_CANDIDATE,
    MEC_SKILL_NAME,
    MEC_SKILL_PATH,
    MEC_PROPOSED_EFFORT,
    MEC_CONFIDENCE,
    MEC_REASON,
    SKILL_QUALITY_PATTERN_GAP,
    INSTRUCTION_VIOLATION_CANDIDATE,
    IVC_SKILL_NAME,
    IVC_INSTRUCTION_TEXT,
    IVC_CORRECTION_MESSAGE,
    IVC_MATCH_TYPE,
    IVC_CONFIDENCE,
    IVC_REASON,
    IVC_NEEDS_REVIEW,
    SQP_SKILL_NAME,
    SQP_SKILL_PATH,
    SQP_DOMAIN,
    SQP_MISSING_REQUIRED,
    SQP_MISSING_RECOMMENDED,
    SQP_PATTERN_SCORE,
    SQP_OVERALL_SCORE,
)

# 分類閾値
AUTO_FIX_CONFIDENCE = 0.9
PROPOSABLE_CONFIDENCE = 0.5
MAJOR_EXCESS_RATIO = 1.6  # 行数が制限値の160%以上 → manual_required
DUPLICATE_PROPOSABLE_SIMILARITY = 0.75  # duplicate の proposable 昇格閾値
DUPLICATE_PROPOSABLE_CONFIDENCE = 0.60  # similarity >= 閾値時の confidence

# impact_scope の判定に使うパス
_GLOBAL_SCOPE_PATTERNS = {"CLAUDE.md"}
_PROJECT_SCOPE_PATTERNS = {".claude/"}

DATA_DIR = Path.home() / ".claude" / "rl-anything"

# 原則ベース判断 + FP 除外 + 独立検証は remediation/principles.py に集約済み（後方互換のため再エクスポート）
from .principles import (  # noqa: E402, F401
    REMEDIATION_PRINCIPLES,
    FP_EXCLUSIONS,
    _apply_principles,
    _should_exclude_fp,
    _independent_verify,
)




# confidence_score / impact_scope 算出 + classify_issue / classify_issues は
# remediation/confidence.py に集約済み（後方互換のため再エクスポート）
from .confidence import (  # noqa: E402, F401
    compute_impact_scope,
    _load_calibration_overrides,
    compute_confidence_score,
    classify_issue,
    classify_issues,
)




# rationale 生成は remediation/rationale.py に集約済み（後方互換のため再エクスポート）
from .rationale import _RATIONALE_TEMPLATES, generate_rationale  # noqa: E402, F401



# 基本 fix 関数群は remediation/fixers_basic.py に集約済み（後方互換のため再エクスポート）
from .fixers_basic import (  # noqa: E402, F401
    fix_stale_references,
    fix_stale_rules,
    fix_claudemd_phantom_refs,
    fix_claudemd_missing_section,
    fix_global_rule,
    fix_hook_scaffold,
    fix_untagged_reference,
)




# rule / line_limit / skill_evolve / verification_rule / stale_memory / pitfall_archive 系 fix 関数は
# remediation/fixers_rules.py に集約済み（後方互換のため再エクスポート）
from .fixers_rules import (  # noqa: E402, F401
    _is_rule_file,
    _fix_rule_by_separation,
    fix_line_limit_violation,
    fix_skill_evolve,
    fix_verification_rule,
    fix_stale_memory,
    fix_pitfall_archive,
)




# quality 系 fix 関数 + FIX_DISPATCH + generate_proposals は
# remediation/fixers_quality.py に集約済み（後方互換のため再エクスポート）
from .fixers_quality import (  # noqa: E402, F401
    fix_split_candidate,
    fix_preflight_scriptification,
    fix_workflow_checkpoint,
    fix_skill_quality_pattern_gap,
    fix_missing_effort,
    _verify_missing_effort,
    fix_instruction_violation,
    generate_proposals,
    generate_auto_fix_summaries,
)
from .fixers_quality import _build_fix_dispatch as _build_fix_dispatch  # noqa: E402

FIX_DISPATCH: Dict[str, Any] = _build_fix_dispatch()




# 検証エンジン + VERIFY_DISPATCH + check_regression / rollback_fix / record_outcome は
# remediation/verify.py に集約済み（後方互換のため再エクスポート）
from .verify import (  # noqa: E402, F401
    _verify_stale_ref,
    _verify_line_limit_violation,
    _verify_stale_rule,
    _verify_claudemd_phantom_ref,
    _verify_claudemd_missing_section,
    _verify_stale_memory,
    _verify_global_rule,
    _verify_hook_scaffold,
    _verify_untagged_reference,
    _verify_skill_evolve,
    _verify_verification_rule,
    _verify_pitfall_archive,
    _verify_split_candidate,
    _verify_preflight_scriptification,
    _verify_workflow_checkpoint,
    _verify_skill_quality_pattern_gap,
    _verify_instruction_violation,
    verify_fix,
    check_regression,
    rollback_fix,
    record_outcome,
)
from .verify import _build_verify_dispatch as _build_verify_dispatch  # noqa: E402

VERIFY_DISPATCH: Dict[str, Any] = _build_verify_dispatch()
