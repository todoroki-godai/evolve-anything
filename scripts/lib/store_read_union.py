"""correction-family ストアの read 層 union + slug alias 共有ヘルパー（#46 read 層拡張）。

PJ rename（rl-anything→evolve-anything）で legacy ``~/.claude/rl-anything`` に取り残された
個人辞書 / 判定進捗 / weak_signals が canonical-only reader から見えない問題を、**物理 merge
せず read 層だけで**解消する。``correction_semantic.store`` と ``weak_signals.store``（Phase 2）が
この 1 モジュールを共有し、union/alias ロジックを二重実装しない（normalize_idiom_text を
autopromote/cross_pj で共有するのと同方針・単一ソース）。

設計の不変条件:
- 明示 path 指定時はこの union を**使わない**（テスト isolation / write round-trip の hermetic 性）
- union は canonical 先頭（呼び出し側が idiom_key / key で dedup・canonical 先頭勝ち）
- alias は read 専用（write は現 slug 固定）

決定論・LLM 非依存。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, List


def iter_read_store_paths(name: str) -> List[Path]:
    """default 解決時の read 候補パス（canonical + legacy + plugins-data の union）。

    capture_rate / ``rl_common.iter_read_data_dirs`` と同方針で全候補 dir を union する
    （canonical 先頭・存在するもののみ）。``iter_read_data_dirs`` は呼び出しのたびに参照する
    （hook/tool 文脈の patch とテストの monkeypatch に追従するため）。import 不能環境は
    canonical 既定にフォールバック。
    """
    try:
        import rl_common
        return [Path(d) / name for d in rl_common.iter_read_data_dirs()]
    except Exception:
        return [Path.home() / ".claude" / "evolve-anything" / name]


def pj_slug_match(rec_slug: Any, target_slug: Any) -> bool:
    """record の pj_slug が当 PJ（read 層 alias 込み）に一致するか。

    PJ rename の legacy record は旧 slug タグのまま残るため、``capture_rate._normalize_pj`` と
    同方針で ``canonical_pj_slug`` を通して畳んでから突合する（alias は read 専用・write は
    現 slug 固定・``pj_slug.PJ_SLUG_ALIASES`` が単一ソース）。別名の無い通常 slug は
    canonical_pj_slug が恒等なので exact 一致を保つ。import 不能環境は exact 一致にフォールバック。
    """
    try:
        from pj_slug import canonical_pj_slug
        return canonical_pj_slug(rec_slug) == canonical_pj_slug(target_slug)
    except Exception:
        return rec_slug == target_slug
