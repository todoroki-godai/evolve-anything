"""Pitfall markdown パーサ + レンダリング + 3層コンテキスト分類。

`pitfalls.md` を Active / Candidate / Graduated の3セクションに分解し、
Hot / Warm / Cold の3層コンテキストを取り出す純粋関数群。
"""
import re
from typing import Any, Dict, List, Optional

from skill_evolve import HOT_TIER_MAX_ITEMS

_PITFALL_HEADER_RE = re.compile(r"^###\s+(.+)$", re.MULTILINE)
_FIELD_RE = re.compile(r"^-\s+\*\*(\w[\w-]*)\*\*:\s*(.+)$", re.MULTILINE)


def parse_pitfalls(content: str) -> Dict[str, List[Dict[str, Any]]]:
    """pitfalls.md をパースして3セクションに分離する。

    Returns:
        {"active": [...], "candidate": [...], "graduated": [...]}
        各要素: {"title": str, "fields": {key: value}, "raw": str}
    """
    sections: Dict[str, List[Dict[str, Any]]] = {
        "active": [],
        "candidate": [],
        "graduated": [],
    }

    current_section = "active"
    current_item: Optional[Dict[str, Any]] = None
    current_lines: List[str] = []

    for line in content.splitlines():
        # セクションヘッダ
        if re.match(r"^##\s+Active\s+Pitfalls", line, re.IGNORECASE):
            _flush_item(current_item, current_lines, sections, current_section)
            current_section = "active"
            current_item = None
            current_lines = []
            continue
        if re.match(r"^##\s+Candidate\s+Pitfalls", line, re.IGNORECASE):
            _flush_item(current_item, current_lines, sections, current_section)
            current_section = "candidate"
            current_item = None
            current_lines = []
            continue
        if re.match(r"^##\s+Graduated\s+Pitfalls", line, re.IGNORECASE):
            _flush_item(current_item, current_lines, sections, current_section)
            current_section = "graduated"
            current_item = None
            current_lines = []
            continue

        # 項目ヘッダ (### タイトル)
        m = _PITFALL_HEADER_RE.match(line)
        if m:
            _flush_item(current_item, current_lines, sections, current_section)
            current_item = {"title": m.group(1).strip(), "fields": {}}
            current_lines = [line]
            continue

        if current_item is not None:
            current_lines.append(line)
            fm = _FIELD_RE.match(line)
            if fm:
                current_item["fields"][fm.group(1)] = fm.group(2).strip()

    _flush_item(current_item, current_lines, sections, current_section)
    return sections


def _flush_item(
    item: Optional[Dict[str, Any]],
    lines: List[str],
    sections: Dict[str, List[Dict[str, Any]]],
    section: str,
) -> None:
    """現在のアイテムをセクションに追加する。"""
    if item is not None:
        item["raw"] = "\n".join(lines)
        sections[section].append(item)


def render_pitfalls(sections: Dict[str, List[Dict[str, Any]]]) -> str:
    """パース済み pitfalls を markdown に復元する。"""
    parts = ["# Pitfalls\n"]

    parts.append("\n## Active Pitfalls\n")
    for item in sections.get("active", []):
        parts.append("")
        parts.append(item["raw"])

    parts.append("\n## Candidate Pitfalls\n")
    for item in sections.get("candidate", []):
        parts.append("")
        parts.append(item["raw"])

    parts.append("\n## Graduated Pitfalls\n")
    for item in sections.get("graduated", []):
        parts.append("")
        parts.append(item["raw"])

    return "\n".join(parts) + "\n"


def get_hot_tier(sections: Dict[str, List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """Hot 層: Active + Pre-flight対応=Yes の上位5件を返す。"""
    hot = [
        item for item in sections.get("active", [])
        if item["fields"].get("Status") == "Active"
        and item["fields"].get("Pre-flight対応", "").lower().startswith("yes")
    ]
    # Last-seen 降順でソート
    hot.sort(key=lambda x: x["fields"].get("Last-seen", ""), reverse=True)
    return hot[:HOT_TIER_MAX_ITEMS]


def get_warm_tier(sections: Dict[str, List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """Warm 層: New + Hot 層に入らなかった Active を返す。"""
    hot_titles = {item["title"] for item in get_hot_tier(sections)}
    warm = [
        item for item in sections.get("active", [])
        if item["title"] not in hot_titles
    ]
    return warm


def get_cold_tier(sections: Dict[str, List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """Cold 層: Graduated + Candidate + New を返す。

    アーカイブ優先順: Graduated > Candidate > New
    """
    cold = list(sections.get("graduated", []))
    cold.extend(sections.get("candidate", []))
    # New は active セクション内で Status=New のもの
    for item in sections.get("active", []):
        if item["fields"].get("Status") == "New":
            cold.append(item)
    return cold
