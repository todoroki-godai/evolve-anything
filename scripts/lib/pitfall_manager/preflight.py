"""Pre-flight スクリプト提案 + 行数ガード。

Cold 層自動アーカイブと Pre-flight 対応 pitfall のテンプレート選定を担う。
"""
from pathlib import Path
from typing import Any, Dict, List, Optional

from skill_evolve import PITFALL_MAX_LINES

# `<repo>/scripts/lib/pitfall_manager/preflight.py` → `<repo>/scripts`
_plugin_root = Path(__file__).resolve().parent.parent.parent


def _compute_line_guard(
    sections: Dict[str, List[Dict[str, Any]]],
    content: str,
) -> Dict[str, Any]:
    """行数ガード: PITFALL_MAX_LINES 超過時に Cold 層から削除候補を生成。

    Returns:
        {"line_count": int, "line_guard_candidates": [...], "warning": str|None}
    """
    line_count = len(content.splitlines())
    if line_count <= PITFALL_MAX_LINES:
        return {"line_count": line_count, "line_guard_candidates": [], "warning": None}

    excess = line_count - PITFALL_MAX_LINES

    # Cold 層を古い順にソート
    cold_items: List[Dict[str, Any]] = []

    # Graduated: Graduated-date 古い順
    for item in sections.get("graduated", []):
        cold_items.append({
            "title": item["title"],
            "sort_key": item["fields"].get("Graduated-date", "9999-99-99"),
            "category": "graduated",
            "raw_lines": len(item.get("raw", "").splitlines()),
        })

    # Candidate: First-seen 古い順
    for item in sections.get("candidate", []):
        cold_items.append({
            "title": item["title"],
            "sort_key": item["fields"].get("First-seen", "9999-99-99"),
            "category": "candidate",
            "raw_lines": len(item.get("raw", "").splitlines()),
        })

    cold_items.sort(key=lambda x: x["sort_key"])

    candidates: List[Dict[str, Any]] = []
    removed_lines = 0
    for ci in cold_items:
        if removed_lines >= excess:
            break
        candidates.append({
            "title": ci["title"],
            "category": ci["category"],
            "lines": ci["raw_lines"],
        })
        removed_lines += ci["raw_lines"]

    warning = None
    if removed_lines < excess:
        warning = "Active/New 項目の手動レビューが必要"

    return {
        "line_count": line_count,
        "line_guard_candidates": candidates,
        "warning": warning,
    }


# --- Pre-flight スクリプト提案 ---


_CATEGORY_TEMPLATE_MAP = {
    "action": "action.sh",
    "tool_use": "tool_use.sh",
    "output": "output.sh",
}


def suggest_preflight_script(
    pitfall: Dict[str, Any],
    templates_dir: Optional[Path] = None,
) -> Optional[Dict[str, Any]]:
    """Pre-flight 対応 pitfall にテンプレートパスを提案する。

    Returns:
        {"pitfall_title": str, "category": str, "template_path": str} or None
    """
    if pitfall["fields"].get("Pre-flight対応", "").lower() != "yes":
        return None
    if pitfall["fields"].get("Status") != "Active":
        return None

    root_cause = pitfall["fields"].get("Root-cause", "")
    category = root_cause.split("—")[0].strip().lower() if "—" in root_cause else "generic"

    tmpl_dir = templates_dir or (_plugin_root.parent / "skills" / "evolve" / "templates" / "preflight")
    template_name = _CATEGORY_TEMPLATE_MAP.get(category)
    if template_name is None:
        template_name = "generic.sh"
        category = "generic"

    template_path = tmpl_dir / template_name

    # フォールバック: ファイルが存在しない場合
    if not template_path.exists():
        template_path = tmpl_dir / "generic.sh"
        template_name = "generic.sh"
        category = "generic"

    return {
        "pitfall_title": pitfall["title"],
        "category": category,
        "template_path": str(template_path),
    }
