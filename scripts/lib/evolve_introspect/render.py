"""evolve_introspect.render — issue 候補 body の生成（#299 / #122-P5）。

候補 dict を GitHub issue body に整形する。末尾に dedup マーカーを埋め込み、
regression（前回 closed の再発）には冒頭にバックリンクを差し込む。

マーカー定数は dedup モジュールが SoT（extract_marker と対をなす）。
"""
from __future__ import annotations

from typing import Any, Dict

from .dedup import MARKER_PREFIX


def render_issue_body(candidate: Dict[str, Any]) -> str:
    """候補本文の末尾に dedup マーカーを埋め込んで返す。"""
    marker = f"<!-- {MARKER_PREFIX}:{candidate['dedup_key']} -->"
    return f"{candidate.get('body', '').rstrip()}\n\n{marker}\n"


def render_regression_body(candidate: Dict[str, Any], prev_number: int) -> str:
    """regression（前回 closed と同一 root cause の再発）候補の body を生成する。

    body 冒頭に「#N の regression（前回 closed）」のバックリンクを差し込み、
    一度直したはずが再発した＝不完全な修正だった文脈をレビュアーに伝える（#33）。
    末尾には通常通り dedup マーカーを残し、再発分も次回以降の dedup 対象に保つ。
    """
    note = (
        f"> ⚠️ #{prev_number} の regression（前回 closed）。"
        f"同一 root cause が再発しており、前回の修正が不完全だった可能性があります。"
    )
    base = render_issue_body(candidate)
    return f"{note}\n\n{base}"
