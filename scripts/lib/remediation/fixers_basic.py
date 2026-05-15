"""基本 fix 関数群 — stale_ref / stale_rule / claudemd_* / global_rule / hook_scaffold / untagged_reference (旧 remediation.py 由来)。

remediation/__init__.py から re-export される（後方互換）。
"""
import re
from pathlib import Path
from typing import Any, Dict, List

from issue_schema import (
    HOOK_SCRIPT_CONTENT,
    HOOK_SCRIPT_PATH,
    HOOK_SETTINGS_DIFF,
    RULE_CONTENT,
    RULE_FILENAME,
    TOOL_USAGE_HOOK_CANDIDATE,
    TOOL_USAGE_RULE_CANDIDATE,
)


def fix_stale_references(
    issues: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """陳腐化参照の行を削除する。修正前の内容を保持する（ロールバック用）。

    Returns:
        [{"issue": ..., "original_content": str, "fixed": bool, "error": str|None}, ...]
    """
    by_file: Dict[str, List[Dict[str, Any]]] = {}
    for issue in issues:
        if issue["type"] != "stale_ref":
            continue
        f = issue["file"]
        if f not in by_file:
            by_file[f] = []
        by_file[f].append(issue)

    results = []
    for file_path, file_issues in by_file.items():
        path = Path(file_path)
        try:
            original_content = path.read_text(encoding="utf-8")
        except OSError as e:
            for issue in file_issues:
                results.append({
                    "issue": issue,
                    "original_content": "",
                    "fixed": False,
                    "error": str(e),
                })
            continue

        lines = original_content.splitlines(keepends=True)
        lines_to_remove = set()
        for issue in file_issues:
            line_num = issue.get("detail", {}).get("line", 0)
            if 0 < line_num <= len(lines):
                lines_to_remove.add(line_num - 1)

        new_lines = [l for i, l in enumerate(lines) if i not in lines_to_remove]
        new_content = "".join(new_lines)

        try:
            path.write_text(new_content, encoding="utf-8")
            for issue in file_issues:
                results.append({
                    "issue": issue,
                    "original_content": original_content,
                    "fixed": True,
                    "error": None,
                })
        except OSError as e:
            for issue in file_issues:
                results.append({
                    "issue": issue,
                    "original_content": original_content,
                    "fixed": False,
                    "error": str(e),
                })

    return results


def fix_stale_rules(
    issues: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """ルール内の不存在パス参照行を削除する。"""
    by_file: Dict[str, List[Dict[str, Any]]] = {}
    for issue in issues:
        if issue["type"] != "stale_rule":
            continue
        f = issue["file"]
        if f not in by_file:
            by_file[f] = []
        by_file[f].append(issue)

    results = []
    for file_path, file_issues in by_file.items():
        path = Path(file_path)
        try:
            original_content = path.read_text(encoding="utf-8")
        except OSError as e:
            for issue in file_issues:
                results.append({
                    "issue": issue,
                    "original_content": "",
                    "fixed": False,
                    "error": str(e),
                })
            continue

        lines = original_content.splitlines(keepends=True)
        lines_to_remove = set()
        for issue in file_issues:
            line_num = issue.get("detail", {}).get("line", 0)
            if 0 < line_num <= len(lines):
                lines_to_remove.add(line_num - 1)

        new_lines = [l for i, l in enumerate(lines) if i not in lines_to_remove]
        new_content = "".join(new_lines)

        try:
            path.write_text(new_content, encoding="utf-8")
            for issue in file_issues:
                results.append({
                    "issue": issue,
                    "original_content": original_content,
                    "fixed": True,
                    "error": None,
                })
        except OSError as e:
            for issue in file_issues:
                results.append({
                    "issue": issue,
                    "original_content": original_content,
                    "fixed": False,
                    "error": str(e),
                })

    return results


def fix_claudemd_phantom_refs(
    issues: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """CLAUDE.md 内の phantom_ref 行を削除し、連続空行を正規化する。"""
    by_file: Dict[str, List[Dict[str, Any]]] = {}
    for issue in issues:
        if issue["type"] != "claudemd_phantom_ref":
            continue
        f = issue["file"]
        if f not in by_file:
            by_file[f] = []
        by_file[f].append(issue)

    results = []
    for file_path, file_issues in by_file.items():
        path = Path(file_path)
        try:
            original_content = path.read_text(encoding="utf-8")
        except OSError as e:
            for issue in file_issues:
                results.append({
                    "issue": issue,
                    "original_content": "",
                    "fixed": False,
                    "error": str(e),
                })
            continue

        lines = original_content.splitlines(keepends=True)
        lines_to_remove = set()
        for issue in file_issues:
            line_num = issue.get("detail", {}).get("line", 0)
            if 0 < line_num <= len(lines):
                lines_to_remove.add(line_num - 1)

        new_lines = [l for i, l in enumerate(lines) if i not in lines_to_remove]
        new_content = "".join(new_lines)
        new_content = re.sub(r"\n{3,}", "\n\n", new_content)

        try:
            path.write_text(new_content, encoding="utf-8")
            for issue in file_issues:
                results.append({
                    "issue": issue,
                    "original_content": original_content,
                    "fixed": True,
                    "error": None,
                })
        except OSError as e:
            for issue in file_issues:
                results.append({
                    "issue": issue,
                    "original_content": original_content,
                    "fixed": False,
                    "error": str(e),
                })

    return results


def fix_claudemd_missing_section(
    issues: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """CLAUDE.md に Skills セクションヘッダを追加する。"""
    results = []
    seen_files: set = set()
    for issue in issues:
        if issue["type"] != "claudemd_missing_section":
            continue
        file_path = issue["file"]
        if file_path in seen_files:
            continue
        seen_files.add(file_path)

        path = Path(file_path)
        try:
            original_content = path.read_text(encoding="utf-8")
        except OSError as e:
            results.append({
                "issue": issue,
                "original_content": "",
                "fixed": False,
                "error": str(e),
            })
            continue

        section_header = "\n\n## Skills\n\n<!-- スキル一覧をここに追加 -->\n"
        new_content = original_content.rstrip() + section_header

        try:
            path.write_text(new_content, encoding="utf-8")
            results.append({
                "issue": issue,
                "original_content": original_content,
                "fixed": True,
                "error": None,
            })
        except OSError as e:
            results.append({
                "issue": issue,
                "original_content": original_content,
                "fixed": False,
                "error": str(e),
            })

    return results


def fix_global_rule(
    issues: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """global rule ファイルを書き込む。

    issue["detail"] に {"filename": str, "content": str} が含まれる前提。
    """
    results = []
    for issue in issues:
        if issue["type"] != TOOL_USAGE_RULE_CANDIDATE:
            continue
        detail = issue.get("detail", {})
        filename = detail.get(RULE_FILENAME, "")
        content = detail.get(RULE_CONTENT, "")
        if not filename or not content:
            results.append({
                "issue": issue, "original_content": "", "fixed": False,
                "error": "filename or content missing",
            })
            continue

        rules_dir = Path.home() / ".claude" / "rules"
        rules_dir.mkdir(parents=True, exist_ok=True)
        path = rules_dir / filename

        try:
            original = path.read_text(encoding="utf-8") if path.exists() else ""
        except OSError:
            original = ""

        try:
            path.write_text(content, encoding="utf-8")
            results.append({
                "issue": issue, "original_content": original, "fixed": True,
                "error": None,
            })
        except OSError as e:
            results.append({
                "issue": issue, "original_content": original, "fixed": False,
                "error": str(e),
            })
    return results


def fix_hook_scaffold(
    issues: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """hook スクリプトを生成する。settings.json は書き換えない。

    issue["detail"] に {"script_path": str, "script_content": str, "settings_diff": str} が含まれる前提。
    """
    results = []
    for issue in issues:
        if issue["type"] != TOOL_USAGE_HOOK_CANDIDATE:
            continue
        detail = issue.get("detail", {})
        script_path = detail.get(HOOK_SCRIPT_PATH, "")
        script_content = detail.get(HOOK_SCRIPT_CONTENT, "")
        if not script_path or not script_content:
            results.append({
                "issue": issue, "original_content": "", "fixed": False,
                "error": "script_path or script_content missing",
            })
            continue

        path = Path(script_path)
        path.parent.mkdir(parents=True, exist_ok=True)

        try:
            original = path.read_text(encoding="utf-8") if path.exists() else ""
        except OSError:
            original = ""

        try:
            path.write_text(script_content, encoding="utf-8")
            path.chmod(0o755)
            results.append({
                "issue": issue, "original_content": original, "fixed": True,
                "error": None,
                "settings_diff": detail.get(HOOK_SETTINGS_DIFF, ""),
            })
        except OSError as e:
            results.append({
                "issue": issue, "original_content": original, "fixed": False,
                "error": str(e),
            })
    return results


def fix_untagged_reference(
    issues: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """untagged_reference_candidates の frontmatter に type: reference を追加する。"""
    import sys
    from plugin_root import PLUGIN_ROOT
    sys.path.insert(0, str(PLUGIN_ROOT / "scripts" / "lib"))
    from frontmatter import update_frontmatter

    results = []
    for issue in issues:
        if issue["type"] != "untagged_reference_candidates":
            continue

        file_path = issue["file"]
        path = Path(file_path)

        try:
            original_content = path.read_text(encoding="utf-8")
        except OSError as e:
            results.append({
                "issue": issue, "original_content": "", "fixed": False,
                "error": str(e),
            })
            continue

        success, error = update_frontmatter(path, {"type": "reference"})
        results.append({
            "issue": issue,
            "original_content": original_content,
            "fixed": success,
            "error": error if error else None,
        })

    return results
