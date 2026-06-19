"""pitfall-curate 決定論コア（純粋関数群） — 任意PJの pitfalls.md を育てる PJ非依存ツール。

CLI 入口は同ディレクトリの `pitfall_curate.py`。本モジュールは I/O を持たず、
parse / 類似度 / 配布版選定・描画 / drift 検出 / フィールド書き込みの純粋関数のみ。

設計方針:
- LLM はここでは一切呼ばない。普遍性分類の「判断」はスキル本体（agent）が行い、
  本モジュールは決定論的処理だけを担う。これにより単体テストが LLM 非依存になる。
- TF-IDF/Jaccard エンジンは scripts/lib/similarity.py を再利用する（evolve-anything の
  既存 pitfall dedup と一貫）。

pitfalls.md フォーマットは evolve-anything 標準:
    ## Active Pitfalls
    ### <title>
    - **Status**: Active
    - **Root-cause**: action — ...
    - **Transferability**: universal|project|instance   ← 本スキルが付与
    - **Generality**: 1-5                                ← 本スキルが付与
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Set

_here = Path(__file__).resolve().parent
_lib = _here.parent.parent.parent / "scripts" / "lib"
for _p in (_here, _lib):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from similarity import jaccard_coefficient, tokenize  # noqa: E402
# フォーマット I/O 層は parse.py に分離。CLI から core 経由で呼べるよう re-export する。
from parse import (  # noqa: E402,F401
    check_normalized,
    normalize,
    parse_pitfalls,
    render_seed,
)

TRANSFERABILITY = ("universal", "project", "instance")
# 配布版選定スコアで universal/project/instance に与える重み（高いほど普遍的）
_TRANSFER_RANK = {"universal": 2, "project": 1, "instance": 0}
# dedup 入力から除外するメタデータフィールド（判別信号でなくノイズになる）
_META_FIELD_KEYS = frozenset({
    "status", "last-seen", "first-seen", "pre-flight", "pre-flight対応",
    "avoidance-count", "transferability", "generality", "superseded-by",
})
# 日本語/CJK は空白区切りされないため bigram で補助トークン化する範囲
# （ひらがな・カタカナ・CJK統合漢字・半角カナ）。
_CJK_RUN = re.compile(r"[぀-ヿ㐀-鿿豈-﫿ｦ-ﾟ]+")


# --- classification ----------------------------------------------------------

def is_classified(item: Dict[str, Any]) -> bool:
    """Transferability と Generality が妥当な値で設定済みなら True。"""
    f = item["fields"]
    t = f.get("Transferability", "").lower()
    g = f.get("Generality", "")
    return t in TRANSFERABILITY and g.isdigit() and 1 <= int(g) <= 5


def list_unclassified(parsed: Dict[str, List[Dict[str, Any]]]) -> List[Dict[str, str]]:
    """未分類（Transferability/Generality 欠落 or 不正）の pitfall を列挙する。

    agent が分類判断するために必要な最小情報（title / root_cause / status）を返す。
    Graduated は Pre-flight 対象外なので分類不要。
    """
    out: List[Dict[str, str]] = []
    for section in ("active", "candidate"):
        for item in parsed[section]:
            if not is_classified(item):
                out.append({
                    "title": item["title"],
                    "root_cause": item["fields"].get("Root-cause", ""),
                    "status": item["fields"].get("Status", ""),
                    "section": section,
                })
    return out


def set_classification(
    content: str, title: str, transferability: str, generality: int
) -> str:
    """指定 pitfall に Transferability/Generality フィールドを設定する（純粋関数）。

    既存フィールドは保持し、無ければ Root-cause 行の直後（無ければエントリ末尾）に
    追記する。不正値は ValueError。
    """
    if transferability.lower() not in TRANSFERABILITY:
        raise ValueError(
            f"transferability は {TRANSFERABILITY} のいずれか: {transferability!r}"
        )
    if not (1 <= int(generality) <= 5):
        raise ValueError(f"generality は 1-5: {generality!r}")
    return _update_entry_fields(
        content,
        title,
        {"Transferability": transferability.lower(), "Generality": str(int(generality))},
    )


# --- dedup -------------------------------------------------------------------

def _entry_text(item: Dict[str, Any]) -> str:
    """類似度計算用テキスト（title を重み付けして 2 回 + 判別信号）。

    正準フォーマットは Root-cause が判別信号になる。実フォーマット（atlas-browser 等）
    は Root-cause を持たないため、内容フィールド（症状/対策/検出 など。メタデータは除外）を
    fallback として使う。これが無いと dedup が「タイトル2連結」だけになり実ファイルで無力化する。
    """
    rc = item["fields"].get("Root-cause", "")
    if rc:
        signal = rc
    else:
        signal = " ".join(
            v for k, v in item["fields"].items()
            if k.lower() not in _META_FIELD_KEYS
        )
    return f"{item['title']} {item['title']} {signal}"


def _cjk_bigrams(text: str) -> Set[str]:
    """CJK 連続文字を文字 bigram に分割する（日本語の細粒度マッチ用）。"""
    grams: Set[str] = set()
    for run in _CJK_RUN.findall(text):
        if len(run) == 1:
            grams.add(run)
        for k in range(len(run) - 1):
            grams.add(run[k:k + 2])
    return grams


def _sim_tokens(text: str) -> Set[str]:
    """類似度用トークン集合 = 空白区切りトークン ∪ CJK bigram。

    similarity.tokenize は \\W で分割するが Unicode では日本語が分割されない。
    日本語主体の pitfalls でも重複検出できるよう CJK bigram を補助的に加える。
    similarity.py は他モジュール共有のため変更せず、本スキル内でローカルに補強する。
    """
    return tokenize(text) | _cjk_bigrams(text)


def find_similar_pairs(
    parsed: Dict[str, List[Dict[str, Any]]], threshold: float = 0.4
) -> List[Dict[str, Any]]:
    """Active+Candidate 内で Jaccard 類似度が threshold 以上のペアを返す（score降順）。

    Superseded 済み（Status が Superseded で始まる）は除外する。
    """
    items: List[Dict[str, Any]] = []
    for section in ("active", "candidate"):
        for it in parsed[section]:
            if it["fields"].get("Status", "").startswith("Superseded"):
                continue
            items.append(it)

    pairs: List[Dict[str, Any]] = []
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            score = jaccard_coefficient(
                _sim_tokens(_entry_text(items[i])), _sim_tokens(_entry_text(items[j]))
            )
            if score >= threshold:
                pairs.append({
                    "a": items[i]["title"],
                    "b": items[j]["title"],
                    "score": round(score, 4),
                })
    return sorted(pairs, key=lambda p: p["score"], reverse=True)


def mark_superseded(content: str, old_title: str, new_title: str) -> str:
    """old_title を new_title に superseded としてマークする（冪等）。

    old 側に Superseded-by フィールドと Status: Superseded by <new> を設定する。
    既に設定済みなら変更しない。
    """
    parsed = parse_pitfalls(content)
    for section in ("active", "candidate", "graduated"):
        for it in parsed[section]:
            if it["title"] == old_title:
                if it["fields"].get("Superseded-by") == new_title:
                    return content  # 冪等
    return _update_entry_fields(
        content,
        old_title,
        {"Status": f"Superseded by {new_title}", "Superseded-by": new_title},
    )


# --- distill (三段階開示の配布版) --------------------------------------------

def _distill_score(item: Dict[str, Any]) -> tuple:
    f = item["fields"]
    rank = _TRANSFER_RANK.get(f.get("Transferability", "").lower(), 0)
    gen = int(f.get("Generality", "0")) if f.get("Generality", "").isdigit() else 0
    return (rank, gen)


def _is_eligible(item: Dict[str, Any]) -> bool:
    """配布版に載せる資格: Active かつ分類済みかつ Superseded でなく、instance でない。

    instance は特定実装1件にしか当てはまらないため、広く先回り適用する配布版には
    載せない（載っていれば降格漏れ = stale）。
    """
    f = item["fields"]
    return (
        item["section"] == "active"
        and is_classified(item)
        and not f.get("Status", "").startswith("Superseded")
        and f.get("Transferability", "").lower() != "instance"
    )


def _is_mandatory(item: Dict[str, Any], mandatory_generality: int) -> bool:
    """無条件で配布版入り: universal かつ generality>=mandatory_generality。"""
    if not _is_eligible(item):
        return False
    f = item["fields"]
    return (
        f.get("Transferability", "").lower() == "universal"
        and int(f.get("Generality", "0")) >= mandatory_generality
    )


def select_distill(
    parsed: Dict[str, List[Dict[str, Any]]],
    top_n: int,
    mandatory_generality: int = 4,
) -> Dict[str, Any]:
    """配布版に載せる pitfall を選定する。

    universal/generality>=N は必須。残り枠を distill_score 降順で埋める。
    Returns: {"selected": [title...], "mandatory": [title...], "dropped": [title...]}
    """
    eligible = [it for it in parsed["active"] if _is_eligible(it)]
    mandatory = [it for it in eligible if _is_mandatory(it, mandatory_generality)]
    mandatory_titles = [it["title"] for it in mandatory]

    rest = sorted(
        (it for it in eligible if it["title"] not in mandatory_titles),
        key=_distill_score,
        reverse=True,
    )
    selected = list(mandatory_titles)
    for it in rest:
        if len(selected) >= top_n:
            break
        selected.append(it["title"])

    selected_set = set(selected)
    dropped = [it["title"] for it in eligible if it["title"] not in selected_set]
    return {"selected": selected, "mandatory": mandatory_titles, "dropped": dropped}


def render_distribution(
    parsed: Dict[str, List[Dict[str, Any]]], selected: List[str]
) -> str:
    """選定された pitfall の配布版 markdown を生成する。

    reframing 文（「するな」→「しろ。理由〜」）は人手が必要なため、各項目は
    title + root-cause を提示し、`<!-- reframe: ... -->` プレースホルダを残す。
    """
    by_title = {
        it["title"]: it
        for section in parsed.values()
        for it in section
    }
    lines = [
        "# Pitfalls 配布版（Top-N）",
        "",
        "> このファイルは pitfall_curate.py distill で自動生成。"
        "agent にはフル pitfalls.md でなくこの配布版のみを渡す（認知過負荷回避）。",
        "> reframing 文（「するな」→「しろ。理由〜」）は人手で記入する。",
        "",
    ]
    for i, title in enumerate(selected, 1):
        it = by_title.get(title)
        rc = it["fields"].get("Root-cause", "") if it else ""
        gen = it["fields"].get("Generality", "?") if it else "?"
        lines.append(f"## {i}. {title}")
        lines.append(f"- Root-cause: {rc}")
        lines.append(f"- Generality: {gen}")
        lines.append(f"<!-- reframe: {title} を positive reframing で記入 -->")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# --- sync gate ---------------------------------------------------------------

def _distribution_titles(distribution: str) -> set:
    """配布版 markdown が参照している pitfall title 集合（## N. title 行）。"""
    titles = set()
    for line in distribution.splitlines():
        if line.startswith("## "):
            body = line[3:].strip()
            # "N. title" の N. を剥がす
            if "." in body and body.split(".", 1)[0].strip().isdigit():
                body = body.split(".", 1)[1].strip()
            titles.add(body)
    return titles


def check_sync(
    parsed: Dict[str, List[Dict[str, Any]]],
    distribution: str,
    top_n: int,
    mandatory_generality: int = 4,
) -> Dict[str, Any]:
    """記録(pitfalls.md)↔分類↔配布版の3層 drift を検出する。

    Returns: {
      "unclassified": [title...],       # Active/Candidate で未分類
      "missing_mandatory": [title...],  # 必須なのに配布版に無い
      "stale": [title...],              # 配布版にあるが資格なし（降格漏れ）
      "healthy": bool,
    }
    """
    unclassified = [u["title"] for u in list_unclassified(parsed)]

    selection = select_distill(parsed, top_n, mandatory_generality)
    dist_titles = _distribution_titles(distribution)
    missing_mandatory = [t for t in selection["mandatory"] if t not in dist_titles]

    eligible_titles = {it["title"] for it in parsed["active"] if _is_eligible(it)}
    stale = sorted(
        t for t in dist_titles if t and t not in eligible_titles
    )

    healthy = not (unclassified or missing_mandatory or stale)
    return {
        "unclassified": unclassified,
        "missing_mandatory": missing_mandatory,
        "stale": stale,
        "healthy": healthy,
    }


# --- internal ----------------------------------------------------------------

def _update_entry_fields(content: str, title: str, new_fields: Dict[str, str]) -> str:
    """`### title` エントリのフィールドを更新/追記する（行ベース、順序保持）。

    既存フィールド行があれば値を置換、無ければエントリの最後のフィールド行直後に追記。
    """
    lines = content.splitlines()
    # 対象エントリの範囲 [start, end) を特定
    start = None
    for i, line in enumerate(lines):
        if line.startswith("### ") and line[4:].strip() == title:
            start = i
            break
    if start is None:
        return content
    end = len(lines)
    for i in range(start + 1, len(lines)):
        if lines[i].startswith("### ") or lines[i].startswith("## "):
            end = i
            break

    remaining = dict(new_fields)
    last_field_idx = start
    for i in range(start + 1, end):
        stripped = lines[i].lstrip()
        if stripped.startswith("- **"):
            last_field_idx = i
            key = stripped[4:].partition("**:")[0].strip()
            if key in remaining:
                lines[i] = f"- **{key}**: {remaining.pop(key)}"
    # 未挿入フィールドを最後のフィールド行の直後に追加
    if remaining:
        insert_at = last_field_idx + 1
        addition = [f"- **{k}**: {v}" for k, v in remaining.items()]
        lines[insert_at:insert_at] = addition
    return "\n".join(lines) + ("\n" if content.endswith("\n") else "")
