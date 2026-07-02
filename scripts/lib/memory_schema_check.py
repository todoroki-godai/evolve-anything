"""auto-memory frontmatter スキーマ検証（#128, advisory）。

auto-memory の各ファイルは以下の frontmatter スキーマを持つ約束になっている:

    ---
    name: {kebab-case-slug}
    description: {一行の説明}
    metadata:
      type: {user|feedback|project|reference}
    ---

``memory/*.md`` の frontmatter を解析し、必須フィールドの欠落 / name の kebab-case 逸脱 /
metadata.type の不正値を検出する。``MEMORY.md``（索引ファイル）は frontmatter を持たない
仕様なので検証対象外（``memory_capability._memory_files`` が既に除外している）。

memory dir を **引数で受ける** ため実 ~/.claude を直読みしない。決定論・LLM 非依存。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

from frontmatter import parse_frontmatter
from memory_capability import _memory_files

# 許可される metadata.type（memory system の 4 類型）。
_VALID_TYPES = {"user", "feedback", "project", "reference"}
# kebab-case: 小文字英数字を単一ハイフンで連結（先頭末尾ハイフン不可・大文字/underscore 不可）。
_KEBAB_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


@dataclass
class SchemaViolation:
    """1 ファイルのスキーマ違反。"""

    filename: str
    issues: List[str]


@dataclass
class SchemaReport:
    """スキーマ検証レポート。"""

    violations: List[SchemaViolation] = field(default_factory=list)

    @property
    def has_findings(self) -> bool:
        return bool(self.violations)


def _check_frontmatter(fm: dict) -> List[str]:
    """1 ファイルの frontmatter dict を検証し、違反メッセージのリストを返す。"""
    if not fm:
        return ["frontmatter 欠落（name / description / metadata.type が必要）"]

    issues: List[str] = []

    name = fm.get("name")
    if not name:
        issues.append("name 欠落")
    elif not isinstance(name, str) or not _KEBAB_RE.match(name):
        issues.append(f"name が kebab-case でない: {name!r}")

    if not fm.get("description"):
        issues.append("description 欠落")

    metadata = fm.get("metadata")
    if not isinstance(metadata, dict) or "type" not in metadata:
        issues.append("metadata.type 欠落")
    else:
        type_val = metadata.get("type")
        if type_val not in _VALID_TYPES:
            issues.append(
                f"metadata.type が不正: {type_val!r}"
                "（許可: user / feedback / project / reference）"
            )
    return issues


def detect_schema_violations(memory_dir: Path) -> SchemaReport:
    """memory dir の frontmatter スキーマ違反を検出する。"""
    memory_dir = Path(memory_dir)
    report = SchemaReport()
    for path in _memory_files(memory_dir):
        fm = parse_frontmatter(path)
        issues = _check_frontmatter(fm)
        if issues:
            report.violations.append(SchemaViolation(filename=path.name, issues=issues))
    return report
