"""correction_semantic.idiom_filter — 過汎用 idiom の FP guard（#527）。

過汎用な短文字 idiom が confirmed 化されると idiom_autopromote（#463: confirmed idiom と
同テキストの再発を機械昇格）の **FP 製造機**になる。実測（correction_idioms.jsonl の
confirmed 中の極短 idiom）では「いやいや」「じゃなくて」「気がする」「比率だけ」
「いや、2/24の」のような相槌・推量・断片が昇格していた。

このモジュールは idiom 化対象かどうかを **決定論で**判定する 3 ゲートを 1 関数に集約する
（idiom 抽出時 = batch.ingest と、昇格時 = idiom_autopromote の両方がこれを通す。
ロジックを二重実装しない・normalize_idiom_text と同じ「単一ソース」方針）:

1. 最小長 floor: 正規化後 ``MIN_IDIOM_CHARS`` 文字未満を弾く（極短 idiom は過汎用）。
2. 日常語 stopword: 相槌・推量・否定のみで具体修正内容を持たない言い回しを弾く。
3. 文脈固有トークン: 日付（2/24・2026/06/15）・割合（80%）・序数（3番目）など、その発話
   固有で再発しても別文脈になる断片を弾く。

これらは**過汎用な idiom が confirmed の母集団に入るのを構造的に止める**ための guard で、
誤って弾いても安全側（昇格しないだけ・人間が daily/bootstrap で個別承認すればよい）に倒す。
閾値・stopword は issue #527 の実測リストを根拠に固定する（合成 fixture でなく実コーパス由来）。

決定論・LLM 非依存。``idiom_eligible`` が True を返したものだけが idiom 化 / 昇格対象になる。
"""
from __future__ import annotations

import re
from typing import List, Optional, Tuple

# ── ゲート①: 最小長 floor ──────────────────────────────────────────
# 実測の極短 idiom（「いやいや」=4 / 「じゃなくて」=5 / 「気がする」=4 / 「比率だけ」=4）は
# すべて過汎用だった。median 10 文字の生発話断片（bootstrap_backlog 知見）から極短を弾く
# floor として 8 文字。8 文字未満は具体的な修正内容を持ちにくく FP 率が高い。
MIN_IDIOM_CHARS = 8

# ── ゲート②: 日常語 stopword（相槌・推量・否定のみで具体内容を持たない言い回し）──────────
# 8 文字以上でも「やっぱり気がするんだよなぁ」のように推量・相槌・否定だけで構成された
# 言い回しは具体的な修正対象を持たないため idiom 化しない。部分文字列マッチで判定する
# （これらの語が**主成分**である発話を弾く）。issue #527 の実測リスト + 設計例（prompt.py）
# の相槌・推量表現を根拠に固定。
STOPWORD_SUBSTRINGS = (
    "いやいや",
    "気がする",
    "ないよ",
    "じゃなくて",
    "そうじゃない",
    "やっぱり",
    "なんとなく",
    "だと思う",
    "かもしれない",
    "んだよなぁ",
    "んだよな",
)

# 「具体的な修正内容」を示す名詞・指示が含まれていれば stopword と共起しても idiom として残す。
# 相槌+具体名（「四国めたんじゃなくてつむぎにして」）を誤って弾かないためのレスキュー。
# stopword 語を除いた残りに content keyword（漢字/カタカナ 2 字以上）があれば「具体内容あり」。
_CONTENT_KEYWORD_RE = re.compile(r"[一-龥々]{2,}|[ァ-ヴー]{2,}")

# ── ゲート③: 文脈固有トークン（日付・割合・序数など再発しても別文脈になる断片）──────────
# 「いや、2/24の」「比率だけ 80%」のような断片は、その発話固有の数値を含むため confirmed 化
# しても再発時に別の数値・別文脈になり機械昇格が FP になる。日付・割合・序数を検出して弾く。
_DATE_RE = re.compile(r"\d{1,4}\s*[/／-]\s*\d{1,2}")  # 2/24, 2026/06/15, 06-15
_PERCENT_RE = re.compile(r"\d+\s*[%％]")               # 80%, 80％
_ORDINAL_RE = re.compile(r"\d+\s*(?:番目|番|個目|つ目|件目|行目|ページ|ページ目|位)")  # 3番目, 5件目


def _normalize(text: Optional[str]) -> str:
    """周囲空白を strip する（store.normalize_idiom_text と同じ正準化方針）。"""
    if not text:
        return ""
    return text.strip()


def _has_context_token(text: str) -> bool:
    """日付・割合・序数など文脈固有の数値断片を含むか（決定論）。"""
    return bool(
        _DATE_RE.search(text)
        or _PERCENT_RE.search(text)
        or _ORDINAL_RE.search(text)
    )


def _is_stopword_only(text: str) -> bool:
    """相槌・推量・否定の stopword が主成分で具体的な修正内容を持たないか。

    stopword を含み、かつ stopword 語を除いた残りに content keyword（漢字/カタカナ 2 字以上）が
    無ければ「stopword のみ」と判定する。具体名（「四国めたん」「つむぎ」等）が残っていれば
    具体内容ありとして残す。
    """
    matched = [w for w in STOPWORD_SUBSTRINGS if w in text]
    if not matched:
        return False
    residue = text
    for w in matched:
        residue = residue.replace(w, "")
    return not _CONTENT_KEYWORD_RE.search(residue)


def eligible_reason(text: Optional[str]) -> Optional[str]:
    """idiom 化対象でない場合の拒否理由を返す（surface 用）。対象なら None。

    順序: too_short → context_token → stopword（弾く理由の代表値を 1 つ返す）。
    """
    norm = _normalize(text)
    if len(norm) < MIN_IDIOM_CHARS:
        return "too_short"
    if _has_context_token(norm):
        return "context_token"
    if _is_stopword_only(norm):
        return "stopword"
    return None


def idiom_eligible(text: Optional[str]) -> bool:
    """この idiom テキストを個人辞書 / confirmed / 自動昇格の対象にしてよいか（決定論）。

    3 ゲート（floor / stopword / context token）を全通過したら True。
    """
    return eligible_reason(text) is None


def filter_idioms(texts: List[str]) -> Tuple[List[str], List[Tuple[str, str]]]:
    """idiom テキスト群を (採用, [(拒否テキスト, 理由), ...]) に分割する（surface 用）。

    入力順を保つ決定論パーティション。
    """
    kept: List[str] = []
    rejected: List[Tuple[str, str]] = []
    for t in texts:
        reason = eligible_reason(t)
        if reason is None:
            kept.append(t)
        else:
            rejected.append((t, reason))
    return kept, rejected
