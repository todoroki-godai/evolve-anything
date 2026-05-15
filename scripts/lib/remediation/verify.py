"""検証エンジン + VERIFY_DISPATCH + check_regression / rollback_fix / record_outcome (旧 remediation.py 由来)。

remediation/__init__.py から re-export される（後方互換）。
VERIFY_DISPATCH は同 slice 内で定義された _verify_* と他 slice の `_verify_missing_effort`
を package 経由で遅延参照する。
"""
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from issue_schema import (
    HOOK_SCRIPT_PATH,
    INSTRUCTION_VIOLATION_CANDIDATE,
    MISSING_EFFORT_CANDIDATE,
    SKILL_EVOLVE_CANDIDATE,
    SKILL_QUALITY_PATTERN_GAP,
    TOOL_USAGE_HOOK_CANDIDATE,
    TOOL_USAGE_RULE_CANDIDATE,
    VERIFICATION_RULE_CANDIDATE,
    WORKFLOW_CHECKPOINT_CANDIDATE,
)


def _verify_stale_ref(fixed_file: str, detail: Dict[str, Any]) -> Dict[str, Any]:
    """stale_ref の検証。"""
    import sys
    from plugin_root import PLUGIN_ROOT
    sys.path.insert(0, str(PLUGIN_ROOT / "scripts" / "lib"))
    from audit import _extract_paths_outside_codeblocks

    content = Path(fixed_file).read_text(encoding="utf-8")
    extracted = _extract_paths_outside_codeblocks(content)
    ref_path = detail.get("path", "")
    for _, found_path in extracted:
        if found_path == ref_path:
            return {"resolved": False, "remaining": f"参照「{ref_path}」がまだ存在します"}
    return {"resolved": True, "remaining": None}


def _verify_line_limit_violation(fixed_file: str, detail: Dict[str, Any]) -> Dict[str, Any]:
    """line_limit_violation の検証。分離モードの場合は分離先ファイルの存在も確認する。"""
    from . import _is_rule_file  # noqa: PLC0415

    content = Path(fixed_file).read_text(encoding="utf-8")
    current_lines = content.count("\n") + 1
    limit = detail.get("limit", 0)
    if current_lines > limit:
        return {
            "resolved": False,
            "remaining": f"行数 {current_lines}/{limit} — まだ超過しています",
        }
    if _is_rule_file(fixed_file):
        import sys
        from plugin_root import PLUGIN_ROOT
        sys.path.insert(0, str(PLUGIN_ROOT / "scripts" / "lib"))
        from line_limit import _resolve_reference_path
        ref_path = _resolve_reference_path(Path(fixed_file))
        if not ref_path.exists():
            return {
                "resolved": False,
                "remaining": f"分離先ファイル {ref_path} が存在しません",
            }
    return {"resolved": True, "remaining": None}


def _verify_stale_rule(fixed_file: str, detail: Dict[str, Any]) -> Dict[str, Any]:
    """stale_rule の検証: 参照パスが消えているか。"""
    content = Path(fixed_file).read_text(encoding="utf-8")
    ref_path = detail.get("path", "")
    if ref_path and ref_path in content:
        return {"resolved": False, "remaining": f"参照「{ref_path}」がまだ存在します"}
    return {"resolved": True, "remaining": None}


def _verify_claudemd_phantom_ref(fixed_file: str, detail: Dict[str, Any]) -> Dict[str, Any]:
    """claudemd_phantom_ref の検証: 当該スキル/ルール名が消えているか。"""
    content = Path(fixed_file).read_text(encoding="utf-8")
    name = detail.get("name", "")
    if not name:
        return {"resolved": True, "remaining": None}
    for line in content.splitlines():
        if name in line and line.strip().startswith("-"):
            return {"resolved": False, "remaining": f"「{name}」のリスト項目がまだ存在します"}
    return {"resolved": True, "remaining": None}


def _verify_claudemd_missing_section(fixed_file: str, detail: Dict[str, Any]) -> Dict[str, Any]:
    """claudemd_missing_section の検証: Skills セクションが存在するか。"""
    content = Path(fixed_file).read_text(encoding="utf-8")
    if re.search(r"^##\s+Skills", content, re.MULTILINE):
        return {"resolved": True, "remaining": None}
    return {"resolved": False, "remaining": "Skills セクションが追加されていません"}


def _verify_stale_memory(fixed_file: str, detail: Dict[str, Any]) -> Dict[str, Any]:
    """stale_memory の検証: 当該モジュール参照が消えているか。"""
    content = Path(fixed_file).read_text(encoding="utf-8")
    path = detail.get("path", "")
    if path and path in content:
        return {"resolved": False, "remaining": f"「{path}」への参照がまだ存在します"}
    return {"resolved": True, "remaining": None}


def _verify_global_rule(fixed_file: str, detail: Dict[str, Any]) -> Dict[str, Any]:
    """global rule ファイルの存在確認 + 行数検証。"""
    path = Path(fixed_file)
    if not path.exists():
        return {"resolved": False, "remaining": "rule ファイルが存在しません"}
    content = path.read_text(encoding="utf-8")
    lines = content.count("\n") + (1 if content and not content.endswith("\n") else 0)
    if lines > 3:
        return {"resolved": False, "remaining": f"rule が3行制限を超過しています ({lines}行)"}
    return {"resolved": True, "remaining": None}


def _verify_hook_scaffold(fixed_file: str, detail: Dict[str, Any]) -> Dict[str, Any]:
    """hook スクリプトの存在確認。"""
    script_path = detail.get(HOOK_SCRIPT_PATH, fixed_file)
    path = Path(script_path)
    if not path.exists():
        return {"resolved": False, "remaining": "hook スクリプトが存在しません"}
    return {"resolved": True, "remaining": None}


def _verify_untagged_reference(fixed_file: str, detail: Dict[str, Any]) -> Dict[str, Any]:
    """untagged_reference_candidates の検証: type: reference が frontmatter に存在するか。"""
    import sys
    from plugin_root import PLUGIN_ROOT
    sys.path.insert(0, str(PLUGIN_ROOT / "scripts" / "lib"))
    from frontmatter import parse_frontmatter

    fm = parse_frontmatter(Path(fixed_file))
    if fm.get("type") == "reference":
        return {"resolved": True, "remaining": None}
    return {"resolved": False, "remaining": "frontmatter に type: reference が存在しません"}


def _verify_skill_evolve(fixed_file: str, detail: Dict[str, Any]) -> Dict[str, Any]:
    """skill_evolve_candidate の検証。"""
    skill_dir = Path(detail.get("skill_dir", Path(fixed_file).parent))

    pitfalls = skill_dir / "references" / "pitfalls.md"
    if not pitfalls.exists():
        return {"resolved": False, "remaining": "references/pitfalls.md が存在しません"}

    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        return {"resolved": False, "remaining": "SKILL.md が存在しません"}

    content = skill_md.read_text(encoding="utf-8")
    missing = []
    if not re.search(r"(?i)failure[- ]triggered\s+learning", content):
        missing.append("Failure-triggered Learning")
    if not re.search(r"(?i)pre[- ]flight", content):
        missing.append("Pre-flight Check")
    if missing:
        return {"resolved": False, "remaining": f"セクション欠落: {', '.join(missing)}"}

    return {"resolved": True, "remaining": None}


def _verify_verification_rule(fixed_file: str, detail: Dict[str, Any]) -> Dict[str, Any]:
    """verification_rule_candidate の検証: ルールファイルが存在するか。"""
    path = Path(fixed_file)
    if not path.exists():
        return {"resolved": False, "remaining": "ルールファイルが存在しません"}
    content = path.read_text(encoding="utf-8")
    if len(content.strip()) == 0:
        return {"resolved": False, "remaining": "ルールファイルが空です"}
    return {"resolved": True, "remaining": None}


def _verify_pitfall_archive(fixed_file: str, detail: Dict[str, Any]) -> Dict[str, Any]:
    """pitfall archive の検証: Active件数/行数が閾値以下か + archive先の存在確認。"""
    from pitfall_manager import ACTIVE_PITFALL_CAP, PITFALL_MAX_LINES, parse_pitfalls

    path = Path(fixed_file)
    if not path.exists():
        return {"resolved": False, "remaining": "pitfalls.md が存在しません"}

    content = path.read_text(encoding="utf-8")
    sections = parse_pitfalls(content)

    archive_path = path.parent / "pitfalls-archive.md"
    if not archive_path.exists():
        return {"resolved": False, "remaining": "pitfalls-archive.md が存在しません"}

    active_count = sum(
        1 for item in sections.get("active", [])
        if item["fields"].get("Status") == "Active"
    )
    cap = detail.get("cap", ACTIVE_PITFALL_CAP)
    if active_count > cap:
        return {"resolved": False, "remaining": f"Active pitfall ({active_count}) がまだ cap ({cap}) を超過しています"}

    line_count = len(content.splitlines())
    if line_count > PITFALL_MAX_LINES:
        return {"resolved": False, "remaining": f"行数 ({line_count}) がまだ閾値 ({PITFALL_MAX_LINES}) を超過しています"}

    return {"resolved": True, "remaining": None}


def _verify_split_candidate(fixed_file: str, detail: Dict[str, Any]) -> Dict[str, Any]:
    return {"resolved": True, "remaining": None}


def _verify_preflight_scriptification(fixed_file: str, detail: Dict[str, Any]) -> Dict[str, Any]:
    return {"resolved": True, "remaining": None}


def _verify_workflow_checkpoint(fixed_file: str, detail: Dict[str, Any]) -> Dict[str, Any]:
    return {"resolved": True, "remaining": None}


def _verify_skill_quality_pattern_gap(fixed_file: str, detail: Dict[str, Any]) -> Dict[str, Any]:
    return {"resolved": True, "remaining": None}


def _verify_instruction_violation(fixed_file: str, detail: Dict[str, Any]) -> Dict[str, Any]:
    return {"resolved": True, "remaining": None}


def _build_verify_dispatch() -> Dict[str, Any]:
    """package 経由で _verify_missing_effort を解決し VERIFY_DISPATCH を構築する。"""
    from . import _verify_missing_effort  # noqa: PLC0415

    return {
        "stale_ref": _verify_stale_ref,
        "line_limit_violation": _verify_line_limit_violation,
        "stale_rule": _verify_stale_rule,
        "claudemd_phantom_ref": _verify_claudemd_phantom_ref,
        "claudemd_missing_section": _verify_claudemd_missing_section,
        "stale_memory": _verify_stale_memory,
        "untagged_reference_candidates": _verify_untagged_reference,
        "cap_exceeded": _verify_pitfall_archive,
        "line_guard": _verify_pitfall_archive,
        "split_candidate": _verify_split_candidate,
        "preflight_scriptification": _verify_preflight_scriptification,
        TOOL_USAGE_RULE_CANDIDATE: _verify_global_rule,
        TOOL_USAGE_HOOK_CANDIDATE: _verify_hook_scaffold,
        SKILL_EVOLVE_CANDIDATE: _verify_skill_evolve,
        VERIFICATION_RULE_CANDIDATE: _verify_verification_rule,
        WORKFLOW_CHECKPOINT_CANDIDATE: _verify_workflow_checkpoint,
        MISSING_EFFORT_CANDIDATE: _verify_missing_effort,
        SKILL_QUALITY_PATTERN_GAP: _verify_skill_quality_pattern_gap,
        INSTRUCTION_VIOLATION_CANDIDATE: _verify_instruction_violation,
    }


def verify_fix(fixed_file: str, original_issue: Dict[str, Any]) -> Dict[str, Any]:
    """修正されたファイルに対して該当する検出関数を再実行し、元の問題の解消を確認する。"""
    from . import VERIFY_DISPATCH  # noqa: PLC0415

    path = Path(fixed_file)
    if not path.exists():
        return {"resolved": False, "remaining": "ファイルが存在しません"}

    issue_type = original_issue["type"]
    detail = original_issue.get("detail", {})

    verify_fn = VERIFY_DISPATCH.get(issue_type)
    if verify_fn is not None:
        return verify_fn(fixed_file, detail)

    import sys
    print(f"  [warn] verify_fix: 未登録の issue type「{issue_type}」をスキップ", file=sys.stderr)
    return {"resolved": True, "remaining": None}


def check_regression(fixed_file: str, original_content: str) -> Dict[str, Any]:
    """修正が副作用を起こしていないか検証する。"""
    path = Path(fixed_file)
    if not path.exists():
        return {"passed": False, "issues": ["ファイルが存在しません"]}

    new_content = path.read_text(encoding="utf-8")
    issues = []

    original_headings = re.findall(r"^(#{1,6}\s+.+)$", original_content, re.MULTILINE)
    new_headings = re.findall(r"^(#{1,6}\s+.+)$", new_content, re.MULTILINE)
    if original_headings != new_headings:
        removed_headings = set(original_headings) - set(new_headings)
        if removed_headings:
            issues.append(f"見出しが削除されました: {', '.join(removed_headings)}")

    original_fences = original_content.count("```")
    new_fences = new_content.count("```")
    if new_fences % 2 != 0:
        issues.append("コードブロックの開始/終了が不対応です")

    if not new_content.strip():
        issues.append("ファイルが空になりました")

    if ".claude/rules/" in fixed_file:
        import sys
        from plugin_root import PLUGIN_ROOT
        sys.path.insert(0, str(PLUGIN_ROOT / "scripts" / "lib"))
        from line_limit import MAX_RULE_LINES

        line_count = new_content.count("\n") + (1 if new_content and not new_content.endswith("\n") else 0)
        if line_count > MAX_RULE_LINES:
            issues.append(
                f"Rules ファイルが行数制限を超過しています ({line_count}行)"
            )

    return {
        "passed": len(issues) == 0,
        "issues": issues,
    }


def rollback_fix(fixed_file: str, original_content: str) -> bool:
    """修正前の内容に復元する。"""
    try:
        Path(fixed_file).write_text(original_content, encoding="utf-8")
        return True
    except OSError:
        return False


def record_outcome(
    issue: Dict[str, Any],
    category: str,
    action: str,
    result: str,
    user_decision: str,
    rationale: str,
    *,
    dry_run: bool = False,
    fix_detail: Optional[Dict[str, Any]] = None,
    verify_result: Optional[Dict[str, Any]] = None,
    duration_ms: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    """修正結果を remediation-outcomes.jsonl に記録する。"""
    from . import DATA_DIR  # noqa: PLC0415

    if dry_run:
        return None

    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "issue_type": issue.get("type", "unknown"),
        "category": category,
        "confidence_score": issue.get("confidence_score", 0.0),
        "impact_scope": issue.get("impact_scope", "unknown"),
        "action": action,
        "result": result,
        "user_decision": user_decision,
        "rationale": rationale,
        "file": issue.get("file", ""),
    }

    if fix_detail is not None:
        record["fix_detail"] = fix_detail
    if verify_result is not None:
        record["verify_result"] = verify_result
    if duration_ms is not None:
        record["duration_ms"] = duration_ms

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    outcomes_file = DATA_DIR / "remediation-outcomes.jsonl"
    with open(outcomes_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")

    return record
