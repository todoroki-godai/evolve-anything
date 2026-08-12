"""evolve_revert._entry — apply 対象 entry の検索（#402 段階3 §2 手順1）。

entry 検索は **raw × alias 集合**で行う（M-A）。revert 済み entry は
``load_effective_history`` の出力から除外されるため、冪等パス（同じ entry_id で
revert を再実行したときに前回のイベントが既に記録済みかの判定・C6）には raw が要る。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Set

import optimize_history_store as _store


@dataclass(frozen=True)
class EntryLookup:
    """``find_entry`` の結果。

    entry:     見つかった raw entry（accept レコード）。未発見なら ``None``。
    duplicate: 同一 ``id`` が複数 source（data-dir × alias）に存在した（C1・§1 手順4の
               優先順位で1件を採っているが、不整合の可能性を明示するためのフラグ）。
    slug:      実際に解決に使った slug（``slug`` 省略時は ``resolve_slug()`` の結果）。
    """

    entry: Optional[Dict[str, Any]]
    duplicate: bool
    slug: str


def find_entry(entry_id: str, slug: Optional[str] = None) -> EntryLookup:
    """entry_id を raw × alias 集合から引く（設計正典 §2 手順1・C1）。

    現 project slug だけに限定すると、旧 slug（PJ rename 前）にしか存在しない entry_id
    を指定した revert が「見つからない」で失敗する（v2 round3 codex [Must]）ため、
    ``load_raw_history_with_aliases``（§1 と同じ alias 6段階集約）で引く。

    同一 id が複数 source に存在した場合は優先順位（canonical 優先）で1件を採用しつつ、
    ``duplicate=True`` で不整合の可能性を明示する。これは必ずしも異常ではない——同一
    内容の accept → revert → 再 accept のループでも同じ id（``proposal_id`` は
    repo_id/relative_path/before_sha 由来で revert_generation を含まない）が複数
    source に現れうる——ため、拒否はしない。
    """
    if slug is None:
        slug = _store.resolve_slug()
    duplicate_ids: Set[Any] = set()
    records = _store.load_raw_history_with_aliases(slug, duplicate_ids=duplicate_ids)
    entry = next((r for r in records if r.get("id") == entry_id), None)
    return EntryLookup(entry=entry, duplicate=entry_id in duplicate_ids, slug=slug)
