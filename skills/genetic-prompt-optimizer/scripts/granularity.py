"""Adaptive Granularity: ファイルサイズに応じたセクション分割レベルの自動調整"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Literal
import re

# 定数
SPLIT_THRESHOLD_SMALL: int = 60
SPLIT_THRESHOLD_LARGE: int = 200
MIN_SECTION_LINES: int = 10


@dataclass
class Section:
    id: str                    # "h2-3" 等の一意識別子
    heading: str               # 見出しテキスト（"## Troubleshooting" 等）
    lines: list[str]           # セクション本文（見出し行含む）
    parent_id: str | None      # 親セクションID（h3 の場合は所属 h2）
    depth: int                 # 見出し深度（2 = ##, 3 = ###）


def determine_split_level(file_lines: int) -> Literal["none", "h2_h3", "h2_only"]:
    """ファイル行数に基づき分割レベルを決定。
    < 60 → "none", 60-200 → "h2_h3", > 200 → "h2_only"
    """
    if file_lines < SPLIT_THRESHOLD_SMALL:
        return "none"
    elif file_lines <= SPLIT_THRESHOLD_LARGE:
        return "h2_h3"
    else:
        return "h2_only"


def split_sections(content: str, level: Literal["none", "h2_h3", "h2_only"]) -> list[Section]:
    """Markdown を指定レベルの見出しでセクション分割。

    - level="none": ファイル全体を1セクション
    - level="h2_h3": ## と ### で分割
    - level="h2_only": ## のみで分割
    - 見出しが1つもない場合: "none"と同等（1セクション）
    - 不正な level → ValueError
    """
    if level not in ("none", "h2_h3", "h2_only"):
        raise ValueError(f"Invalid level: {level!r}. Must be 'none', 'h2_h3', or 'h2_only'")

    lines = content.split("\n")

    if level == "none":
        return [Section(id="h2-0", heading="", lines=lines, parent_id=None, depth=0)]

    # 見出しパターン
    if level == "h2_only":
        heading_re = re.compile(r"^(#{2})\s+(.+)$")  # ## のみ
    else:  # h2_h3
        heading_re = re.compile(r"^(#{2,3})\s+(.+)$")  # ## or ###

    sections: list[Section] = []
    current_lines: list[str] = []
    current_heading = ""
    current_depth = 0
    h2_index = 0
    h3_index = 0
    current_parent_id: str | None = None
    current_id = "preamble"

    for line in lines:
        m = heading_re.match(line)
        if m:
            # 前セクションを保存
            if current_lines:
                sections.append(Section(
                    id=current_id,
                    heading=current_heading,
                    lines=current_lines,
                    parent_id=current_parent_id,
                    depth=current_depth,
                ))

            hashes = m.group(1)
            depth = len(hashes)
            heading_text = line

            if depth == 2:
                current_id = f"h2-{h2_index}"
                current_parent_id = None
                current_depth = 2
                h2_index += 1
                h3_index = 0
            else:  # depth == 3
                parent = f"h2-{h2_index - 1}" if h2_index > 0 else None
                current_id = f"h3-{h3_index}"
                current_parent_id = parent
                current_depth = 3
                h3_index += 1

            current_heading = heading_text
            current_lines = [line]
        else:
            current_lines.append(line)

    # 最後のセクションを保存
    if current_lines:
        sections.append(Section(
            id=current_id,
            heading=current_heading,
            lines=current_lines,
            parent_id=current_parent_id,
            depth=current_depth,
        ))

    # 見出しが1つもなかった → 1セクションとして返す
    if len(sections) == 1 and sections[0].id == "preamble":
        sections[0].id = "h2-0"

    return sections


def merge_small_sections(sections: list[Section], min_lines: int = MIN_SECTION_LINES) -> list[Section]:
    """min_lines 未満のセクションを直前のセクションに統合。

    - 先頭セクション（preamble）は統合対象外
    - 統合時、元の見出し行は保持
    - 統合後にセクション数が0になる場合 → 統合前を返す
    """
    if not sections:
        return sections

    merged: list[Section] = [sections[0]]  # 先頭は常に保持

    for section in sections[1:]:
        if len(section.lines) < min_lines and merged:
            # 直前のセクションに統合
            merged[-1].lines.extend(section.lines)
        else:
            merged.append(section)

    # 統合後に空になった場合（ありえないが安全策）
    if not merged:
        return sections

    return merged
