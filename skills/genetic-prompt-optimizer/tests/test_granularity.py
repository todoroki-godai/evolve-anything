"""Tests for granularity module."""
from __future__ import annotations
import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from granularity import (
    Section,
    determine_split_level,
    split_sections,
    merge_small_sections,
    MIN_SECTION_LINES,
)


# --- determine_split_level ---

@pytest.mark.parametrize("lines,expected", [
    (0, "none"),
    (59, "none"),
    (60, "h2_h3"),
    (100, "h2_h3"),
    (200, "h2_h3"),
    (201, "h2_only"),
    (500, "h2_only"),
])
def test_determine_split_level(lines: int, expected: str) -> None:
    assert determine_split_level(lines) == expected


# --- split_sections level="none" ---

def test_split_sections_none_returns_single_section() -> None:
    content = "line1\nline2\nline3"
    result = split_sections(content, "none")
    assert len(result) == 1
    assert result[0].id == "h2-0"
    assert result[0].heading == ""
    assert result[0].depth == 0
    assert result[0].parent_id is None
    assert result[0].lines == ["line1", "line2", "line3"]


# --- split_sections level="h2_h3" ---

def test_split_sections_h2_h3() -> None:
    content = "\n".join([
        "preamble text",
        "## Section A",
        "content a",
        "### Sub A1",
        "content a1",
        "## Section B",
        "content b",
    ])
    result = split_sections(content, "h2_h3")

    # preamble + 2 h2 + 1 h3 = 4 sections
    assert len(result) == 4

    # preamble
    assert result[0].id == "preamble"
    assert result[0].depth == 0

    # h2-0: Section A
    assert result[1].id == "h2-0"
    assert result[1].heading == "## Section A"
    assert result[1].depth == 2
    assert result[1].parent_id is None
    assert result[1].lines == ["## Section A", "content a"]

    # h3-0: Sub A1
    assert result[2].id == "h3-0"
    assert result[2].heading == "### Sub A1"
    assert result[2].depth == 3
    assert result[2].parent_id == "h2-0"
    assert result[2].lines == ["### Sub A1", "content a1"]

    # h2-1: Section B
    assert result[3].id == "h2-1"
    assert result[3].heading == "## Section B"
    assert result[3].depth == 2
    assert result[3].parent_id is None


# --- split_sections level="h2_only" ---

def test_split_sections_h2_only_ignores_h3() -> None:
    content = "\n".join([
        "preamble",
        "## First",
        "text",
        "### Ignored Sub",
        "sub text",
        "## Second",
        "more text",
    ])
    result = split_sections(content, "h2_only")

    # preamble + 2 h2 = 3 sections (### is NOT split)
    assert len(result) == 3

    assert result[0].id == "preamble"
    assert result[1].id == "h2-0"
    assert result[1].heading == "## First"
    # h3 line should be inside h2-0's lines
    assert "### Ignored Sub" in result[1].lines
    assert "sub text" in result[1].lines

    assert result[2].id == "h2-1"
    assert result[2].heading == "## Second"


# --- split_sections: no headings ---

def test_split_sections_no_headings_returns_single() -> None:
    content = "just plain text\nno headings here"
    result = split_sections(content, "h2_h3")
    assert len(result) == 1
    assert result[0].id == "h2-0"
    assert result[0].lines == ["just plain text", "no headings here"]


# --- split_sections: invalid level ---

def test_split_sections_invalid_level_raises() -> None:
    with pytest.raises(ValueError, match="Invalid level"):
        split_sections("text", "h4")  # type: ignore


# --- merge_small_sections ---

def test_merge_small_sections_merges_short() -> None:
    sections = [
        Section(id="h2-0", heading="## A", lines=["## A"] + [f"line{i}" for i in range(15)], parent_id=None, depth=2),
        Section(id="h2-1", heading="## B", lines=["## B"] + [f"line{i}" for i in range(7)], parent_id=None, depth=2),
        Section(id="h2-2", heading="## C", lines=["## C"] + [f"line{i}" for i in range(20)], parent_id=None, depth=2),
    ]
    result = merge_small_sections(sections)

    # B (8 lines < 10) merged into A
    assert len(result) == 2
    assert result[0].id == "h2-0"
    assert len(result[0].lines) == 16 + 8  # A's 16 + B's 8
    assert result[1].id == "h2-2"


def test_merge_small_sections_consecutive_small() -> None:
    sections = [
        Section(id="h2-0", heading="## A", lines=["## A"] + ["x"] * 12, parent_id=None, depth=2),
        Section(id="h2-1", heading="## B", lines=["## B"] + ["x"] * 3, parent_id=None, depth=2),
        Section(id="h2-2", heading="## C", lines=["## C"] + ["x"] * 4, parent_id=None, depth=2),
        Section(id="h2-3", heading="## D", lines=["## D"] + ["x"] * 15, parent_id=None, depth=2),
    ]
    result = merge_small_sections(sections)

    # B (4 lines) merged into A, C (5 lines) merged into merged-A
    assert len(result) == 2
    assert result[0].id == "h2-0"
    assert result[1].id == "h2-3"


def test_merge_small_sections_preserves_first() -> None:
    sections = [
        Section(id="preamble", heading="", lines=["x"] * 3, parent_id=None, depth=0),
    ]
    result = merge_small_sections(sections)
    assert len(result) == 1
    assert result[0].id == "preamble"


def test_merge_small_sections_empty() -> None:
    assert merge_small_sections([]) == []


# --- realistic markdown ---

def _build_realistic_markdown() -> str:
    """150行、## 3個、### 5個の実際的なMarkdown"""
    parts: list[str] = []
    # Preamble (10 lines)
    parts.extend([f"preamble line {i}" for i in range(10)])
    # ## Section 1 (30 lines) with ### Sub 1.1, ### Sub 1.2
    parts.append("## Section 1")
    parts.extend([f"s1 line {i}" for i in range(10)])
    parts.append("### Sub 1.1")
    parts.extend([f"s1.1 line {i}" for i in range(8)])
    parts.append("### Sub 1.2")
    parts.extend([f"s1.2 line {i}" for i in range(9)])
    # ## Section 2 (40 lines) with ### Sub 2.1, ### Sub 2.2, ### Sub 2.3
    parts.append("## Section 2")
    parts.extend([f"s2 line {i}" for i in range(8)])
    parts.append("### Sub 2.1")
    parts.extend([f"s2.1 line {i}" for i in range(10)])
    parts.append("### Sub 2.2")
    parts.extend([f"s2.2 line {i}" for i in range(8)])
    parts.append("### Sub 2.3")
    parts.extend([f"s2.3 line {i}" for i in range(10)])
    # ## Section 3 (remaining lines)
    parts.append("## Section 3")
    remaining = 150 - len(parts)
    parts.extend([f"s3 line {i}" for i in range(remaining)])
    return "\n".join(parts)


def test_realistic_markdown_h2_h3() -> None:
    content = _build_realistic_markdown()
    lines = content.split("\n")
    assert len(lines) == 150

    result = split_sections(content, "h2_h3")
    # preamble + 3 h2 + 5 h3 = 9 sections
    assert len(result) == 9

    h2_sections = [s for s in result if s.depth == 2]
    h3_sections = [s for s in result if s.depth == 3]
    assert len(h2_sections) == 3
    assert len(h3_sections) == 5


def test_realistic_markdown_h2_only() -> None:
    content = _build_realistic_markdown()
    result = split_sections(content, "h2_only")
    # preamble + 3 h2 = 4 sections (### are not split)
    assert len(result) == 4

    h2_sections = [s for s in result if s.depth == 2]
    assert len(h2_sections) == 3
