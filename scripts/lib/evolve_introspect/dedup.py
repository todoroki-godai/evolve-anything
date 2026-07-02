"""evolve_introspect.dedup — issue 候補の重複仕分けと dedup マーカー（#299 / #122-P5）。

candidate と既存 issue を照合し open dup / closed regression / unique に仕分ける。
body に埋め込む隠しマーカー（dedup_key）を root cause 単位の最強シグナルとし、
マーカーなしの手動起票にはタイトル類似度でフォールバックする。

leaf モジュール（パッケージ内の他モジュールに依存しない）。
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional

# body に埋め込む隠しマーカー。これがあれば既存 issue と root cause 単位で
# 確実に dedup できる（タイトル類似度より強いシグナル）。
MARKER_PREFIX = "evolve-introspect"
_MARKER_RE = re.compile(r"<!--\s*" + re.escape(MARKER_PREFIX) + r":([^\s>]+)\s*-->")

# タイトル類似度で dup と見なす閾値（marker なしで手動起票された既存 issue 向け）。
_TITLE_SIMILARITY_THRESHOLD = 0.80


def extract_marker(text: str) -> Optional[str]:
    """body から dedup_key を取り出す。無ければ None。"""
    m = _MARKER_RE.search(text or "")
    return m.group(1) if m else None


def filter_duplicates(
    candidates: List[Dict[str, Any]],
    existing_issues: List[Dict[str, Any]],
    title_threshold: float = _TITLE_SIMILARITY_THRESHOLD,
) -> Dict[str, List[Dict[str, Any]]]:
    """既存 issue と重複する候補を仕分ける（open/closed を区別）。

    1) body の隠しマーカー（dedup_key）が **open** issue に一致 → dup（最強シグナル）。
    2) マーカーが無い手動起票でも、**open** issue とタイトル類似度が閾値以上なら dup。
    3) マーカーが **closed** issue にのみ一致 → regression（再発）。dup にはせず unique に
       残しつつ、前歴 #N を呼び出し側へ surface する（#33）。一度直したはずが再発した
       ＝不完全な修正だった文脈をレビュアーに伝えるため、新規起票時に backlink を添える。

    closed issue へのタイトル類似一致は「確実に前歴へ紐づけられない」ため regression
    扱いしない（誤バックリンク防止）。マーカー一致のみを regression シグナルとする。

    Args:
        candidates: analyze_evolve_result が出した候補リスト。
        existing_issues: [{"number": int, "title": str, "body": str, "state": str}, ...]
            （gh issue list 由来）。state 欠落は後方互換で open 扱い。

    Returns:
        {
          "unique": [...],
          "duplicates": [{**candidate, "existing_number": int, "reason": str}],
          "regressions": [{**candidate, "existing_number": int, "reason": "closed_marker"}],
        }
    """
    open_marker_index: Dict[str, int] = {}
    closed_marker_index: Dict[str, int] = {}
    open_issues: List[Dict[str, Any]] = []
    for issue in existing_issues or []:
        if _is_closed(issue):
            key = extract_marker(issue.get("body", ""))
            # open が後で勝てるよう、closed は open に無いときだけ採用（open 優先）
            if key and key not in closed_marker_index:
                closed_marker_index[key] = issue.get("number")
        else:
            open_issues.append(issue)
            key = extract_marker(issue.get("body", ""))
            if key:
                open_marker_index[key] = issue.get("number")

    unique: List[Dict[str, Any]] = []
    duplicates: List[Dict[str, Any]] = []
    regressions: List[Dict[str, Any]] = []
    for cand in candidates:
        dup_number, reason = _match_existing(cand, open_issues, open_marker_index, title_threshold)
        if dup_number is not None:
            duplicates.append({**cand, "existing_number": dup_number, "reason": reason})
            continue
        # open に dup が無い場合のみ closed マーカー（regression）を判定
        prev_closed = closed_marker_index.get(cand["dedup_key"])
        if prev_closed is not None:
            regressions.append({**cand, "existing_number": prev_closed, "reason": "closed_marker"})
        unique.append(cand)
    return {"unique": unique, "duplicates": duplicates, "regressions": regressions}


def _is_closed(issue: Dict[str, Any]) -> bool:
    """issue が closed かを判定する。state 欠落（旧 gh 出力）は open 扱い（後方互換）。"""
    state = issue.get("state")
    return isinstance(state, str) and state.strip().lower() == "closed"


def _match_existing(
    cand: Dict[str, Any],
    existing_issues: List[Dict[str, Any]],
    marker_index: Dict[str, int],
    title_threshold: float,
) -> tuple:
    if cand["dedup_key"] in marker_index:
        return marker_index[cand["dedup_key"]], "marker"
    cand_title = _normalize_title(cand.get("title", ""))
    best_num, best_ratio = None, 0.0
    for issue in existing_issues:
        ratio = SequenceMatcher(None, cand_title, _normalize_title(issue.get("title", ""))).ratio()
        if ratio > best_ratio:
            best_num, best_ratio = issue.get("number"), ratio
    if best_ratio >= title_threshold:
        return best_num, f"title_similarity={best_ratio:.2f}"
    return None, ""


def _normalize_title(title: str) -> str:
    s = (title or "").lower()
    s = s.replace("`", "")
    s = re.sub(r"\s+", " ", s).strip()
    return s
