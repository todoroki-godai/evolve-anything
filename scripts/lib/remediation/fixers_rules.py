"""rule / line_limit / skill_evolve / verification_rule / stale_memory / pitfall_archive 系 fix 関数 (旧 remediation.py 由来)。

remediation/__init__.py から re-export される（後方互換）。
"""
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from issue_schema import (
    SE_SKILL_DIR,
    SE_SKILL_NAME,
    SKILL_EVOLVE_CANDIDATE,
    VERIFICATION_RULE_CANDIDATE,
    VRC_RULE_FILENAME,
    VRC_RULE_TEMPLATE,
)


def _is_rule_file(file_path: str) -> bool:
    """rule ファイルかどうかを判定する。"""
    return ".claude/rules/" in file_path


def _fix_rule_by_separation(
    issue: Dict[str, Any],
    path: Path,
    original_content: str,
    limit: int,
) -> Dict[str, Any]:
    """rule ファイルの行数超過を references への分離で修正する。

    [ADR-037] Phase 1d-ii: claude -p を全廃。決定論フォールバック（proposable 降格）で完走する。
    LLM 品質は emit_separation_request / ingest_separation の2相（SKILL 駆動）で回復する。
    """
    issue["category"] = "proposable"
    return {
        "issue": issue, "original_content": original_content,
        "fixed": False, "error": "separation_deferred_to_2phase",
    }


def fix_line_limit_violation(
    issues: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """行数制限違反を修正する。

    [ADR-037] Phase 1d-ii: claude -p を全廃。決定論フォールバック（proposable 降格）で完走する。
    rule ファイル / 非 rule ファイルとも proposable 降格（fixed=False）を返す。
    LLM 品質は emit_compression_request / ingest_compression および
    emit_separation_request / ingest_separation の2相（SKILL 駆動）で回復する。
    """
    results = []
    for issue in issues:
        if issue["type"] != "line_limit_violation":
            continue

        file_path = issue["file"]
        detail = issue.get("detail", {})
        limit = detail.get("limit", 3)
        path = Path(file_path)

        try:
            original_content = path.read_text(encoding="utf-8")
        except OSError as e:
            results.append({
                "issue": issue, "original_content": "", "fixed": False,
                "error": str(e),
            })
            continue

        if _is_rule_file(file_path):
            results.append(_fix_rule_by_separation(issue, path, original_content, limit))
            continue

        # 非 rule ファイル: proposable 降格（LLM 圧縮は2相で回復）
        issue["category"] = "proposable"
        results.append({
            "issue": issue, "original_content": original_content,
            "fixed": False, "error": "compression_deferred_to_2phase",
        })

    return results


def fix_skill_evolve(
    issues: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """スキルに自己進化パターンを適用する。"""
    import sys
    from plugin_root import PLUGIN_ROOT

    sys.path.insert(0, str(PLUGIN_ROOT / "scripts" / "lib"))
    from skill_evolve import apply_evolve_proposal, evolve_skill_proposal

    results = []
    for issue in issues:
        if issue["type"] != SKILL_EVOLVE_CANDIDATE:
            continue
        detail = issue.get("detail", {})
        skill_name = detail.get(SE_SKILL_NAME, "")
        skill_dir = Path(detail.get(SE_SKILL_DIR, ""))

        if not skill_dir.exists():
            results.append({
                "issue": issue, "original_content": "", "fixed": False,
                "error": f"skill_dir not found: {skill_dir}",
            })
            continue

        proposal = evolve_skill_proposal(skill_name, skill_dir)
        if proposal.get("error"):
            results.append({
                "issue": issue, "original_content": "", "fixed": False,
                "error": proposal["error"],
            })
            continue

        skill_md = Path(proposal["skill_md_path"])
        try:
            original_content = skill_md.read_text(encoding="utf-8") if skill_md.exists() else ""
        except OSError:
            original_content = ""

        apply_result = apply_evolve_proposal(proposal)
        results.append({
            "issue": issue, "original_content": original_content,
            "fixed": apply_result["applied"], "error": apply_result["error"],
        })

    return results


def fix_verification_rule(
    issues: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """検証知見ルールをプロジェクトに作成する。"""
    results = []
    for issue in issues:
        if issue["type"] != VERIFICATION_RULE_CANDIDATE:
            continue
        detail = issue.get("detail", {})
        rule_filename = detail.get(VRC_RULE_FILENAME, "")
        rule_template = detail.get(VRC_RULE_TEMPLATE, "")
        if not rule_filename or not rule_template:
            results.append({
                "issue": issue, "original_content": "", "fixed": False,
                "error": "missing rule_filename or rule_template",
            })
            continue

        file_path = Path(issue["file"])
        rules_dir = file_path.parent
        rules_dir.mkdir(parents=True, exist_ok=True)

        try:
            original_content = file_path.read_text(encoding="utf-8") if file_path.exists() else ""
        except OSError:
            original_content = ""

        try:
            content = rule_template
            if not content.endswith("\n"):
                content += "\n"
            file_path.write_text(content, encoding="utf-8")
            results.append({
                "issue": issue, "original_content": original_content,
                "fixed": True, "error": None,
            })
        except OSError as e:
            results.append({
                "issue": issue, "original_content": original_content,
                "fixed": False, "error": str(e),
            })

    return results


def fix_stale_memory(
    issues: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """MEMORY.md からstaleエントリのポインタ行を削除する。"""
    by_file: Dict[str, List[Dict[str, Any]]] = {}
    for issue in issues:
        if issue["type"] != "stale_memory":
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
            ref_path = issue.get("detail", {}).get("path", "")
            if not ref_path:
                continue
            for i, line in enumerate(lines):
                if ref_path in line:
                    lines_to_remove.add(i)

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


def fix_pitfall_archive(
    issues: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """pitfall Cold層（Graduated/Candidate/New）を pitfalls-archive.md にアーカイブする。

    cap_exceeded: Active超過分をCold層から優先順にアーカイブ
    line_guard: 行数が閾値以下になるまでCold層からアーカイブ
    """
    from pitfall_manager import (
        ACTIVE_PITFALL_CAP,
        PITFALL_MAX_LINES,
        parse_pitfalls,
        render_pitfalls,
    )

    results = []
    for issue in issues:
        if issue["type"] not in ("cap_exceeded", "line_guard"):
            continue

        detail = issue.get("detail", {})
        pitfalls_path = Path(issue["file"])

        if not pitfalls_path.exists():
            results.append({
                "issue": issue, "original_content": "", "fixed": False,
                "error": "pitfalls.md not found",
            })
            continue

        try:
            original_content = pitfalls_path.read_text(encoding="utf-8")
        except OSError as e:
            results.append({
                "issue": issue, "original_content": "", "fixed": False,
                "error": str(e),
            })
            continue

        sections = parse_pitfalls(original_content)

        cold_items: List[Dict[str, Any]] = []
        for item in sections.get("graduated", []):
            cold_items.append({"item": item, "section": "graduated", "priority": 0})
        for item in sections.get("candidate", []):
            cold_items.append({"item": item, "section": "candidate", "priority": 1})
        for item in sections.get("active", []):
            if item["fields"].get("Status") == "New":
                cold_items.append({"item": item, "section": "active", "priority": 2})
        cold_items.sort(key=lambda x: x["priority"])

        if not cold_items:
            results.append({
                "issue": issue, "original_content": original_content, "fixed": False,
                "remaining": "Cold層にアーカイブ対象がありません。Active pitfallの手動レビューが必要です",
                "error": None,
            })
            continue

        to_archive: List[Dict[str, Any]] = []
        if issue["type"] == "cap_exceeded":
            active_count = detail.get("active_count", 0)
            cap = detail.get("cap", ACTIVE_PITFALL_CAP)
            need = active_count - cap
            for ci in cold_items:
                if len(to_archive) >= need:
                    break
                to_archive.append(ci)
        else:  # line_guard
            current_lines = len(original_content.splitlines())
            target = PITFALL_MAX_LINES
            removed_lines = 0
            for ci in cold_items:
                if current_lines - removed_lines <= target:
                    break
                raw_lines = len(ci["item"].get("raw", "").splitlines())
                to_archive.append(ci)
                removed_lines += raw_lines

        if not to_archive:
            results.append({
                "issue": issue, "original_content": original_content, "fixed": False,
                "error": None, "remaining": "アーカイブ対象が不足しています",
            })
            continue

        archive_path = pitfalls_path.parent / "pitfalls-archive.md"
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        archive_entries = []
        for ci in to_archive:
            archive_entries.append(
                f"\n{ci['item']['raw']}\n- **Archived-date**: {now}\n"
            )

        archive_content = ""
        if archive_path.exists():
            archive_content = archive_path.read_text(encoding="utf-8")
        if not archive_content.strip():
            archive_content = "# Pitfalls Archive\n"
        archive_content += "\n".join(archive_entries)
        archive_path.write_text(archive_content, encoding="utf-8")

        titles = [ci["item"]["title"] for ci in to_archive]
        for ci in to_archive:
            section_key = ci["section"]
            sections[section_key] = [
                item for item in sections[section_key]
                if item["title"] != ci["item"]["title"]
            ]

        pitfalls_path.write_text(render_pitfalls(sections), encoding="utf-8")

        results.append({
            "issue": issue, "original_content": original_content,
            "fixed": True, "error": None,
            "archived_titles": titles,
        })

    return results
