"""MEMORY.md 索引孤児の決定論検出（#127, advisory）。

MEMORY.md（memory ディレクトリの索引ファイル）本文の ``[text](file.md)`` リンク集合と、
``memory/*.md`` の実ファイル集合を突合し、両者の差分を検出する:

- ``unindexed_files``: 実ファイルはあるが索引にリンクが無い（索引から不可視＝想起されない）。
- ``indexed_missing``: 索引にリンクはあるが実体が無い（stale リンク）。

スコープ: memory dir を **引数で受ける** 単一ディレクトリのみ（``memory_capability`` と同じく
``MEMORY.md`` は索引であって memory 実体でないため実ファイル集合から除外する）。実 ~/.claude を
直読みしないため、テスト・呼び出し側は tmp / 実 memory dir のどちらでも渡せる。

決定論・LLM 非依存。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Set

from memory_capability import _INDEX_FILENAME, _memory_files

# ``[text](target.md)`` の target 部分（相対パス可）を捕捉する。
# 末尾 ``.md`` のリンクのみを索引エントリとみなす（アンカー ``#sec`` 付きは対象外に倒す）。
_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)\s]+?\.md)\)")


@dataclass
class IndexOrphanReport:
    """索引孤児レポート。"""

    has_index: bool
    unindexed_files: List[str] = field(default_factory=list)
    indexed_missing: List[str] = field(default_factory=list)

    @property
    def has_findings(self) -> bool:
        return bool(self.unindexed_files or self.indexed_missing)


def _extract_index_links(index_path: Path) -> Set[str]:
    """MEMORY.md 本文の ``[..](x.md)`` リンク先を basename 集合で返す。"""
    try:
        text = index_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return set()
    out: Set[str] = set()
    for match in _LINK_RE.finditer(text):
        target = match.group(1).strip()
        base = Path(target).name  # 相対パスでも basename に正規化して突合する
        if base and base != _INDEX_FILENAME:
            out.add(base)
    return out


def detect_index_orphans(memory_dir: Path) -> IndexOrphanReport:
    """memory dir の索引孤児（unindexed / stale リンク）を検出する。

    MEMORY.md が無ければ検査対象外（``has_index=False``・findings なし）。
    """
    memory_dir = Path(memory_dir)
    index_path = memory_dir / _INDEX_FILENAME
    if not index_path.is_file():
        return IndexOrphanReport(has_index=False)

    indexed = _extract_index_links(index_path)
    actual = {p.name for p in _memory_files(memory_dir)}
    return IndexOrphanReport(
        has_index=True,
        unindexed_files=sorted(actual - indexed),
        indexed_missing=sorted(indexed - actual),
    )
