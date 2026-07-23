"""correction_semantic.representative — representative 品質ヘルパ（#528-3, #253）。

bootstrap/daily の representative（確認 group の代表発話断片）に **assistant の過去レポート
出力が引用ごと混入**して判読困難になる問題（「ℹ️ データ蓄積待ち…」がシグナル本文に混入）を
決定論で解決する:

1. ``user_only_text`` — 元発話テキストから assistant 出力の引用ブロック（``>`` markdown quote /
   code fence / ℹ️・✓・✗ 等のステータス絵文字プレフィックス行）を strip し、user 発話のみ残す。
   全行が assistant 引用なら情報喪失を避けるため元テキストの strip を返す（fallback）。
2. ``prev_action_summary`` — 直前 AI 行動の 1 行要約（「やっぱり、高だけにして」のような
   一行 representative が何に対する修正か不明な問題を、evidence に prev_action を添えて解消する）。
3. ``trim_to_idiom_sentence`` — 1 発言が複数トピック（主要な指摘＋ついでの別要望）を含む場合、
   Haiku が抽出した idiom（修正を端的に表す言い回し）が属するセグメントだけに evidence.text を
   トリムする（#253: 無関係な副次要望が evidence に同居する問題の解消）。トピック分割は
   話題転換語（あと/ついでに等）の直前でのみ行い、句点・疑問符だけの複数文（単一トピック）は
   分割しない（#253 ROUND2）。idiom が見つからない・複数セグメントに跨って曖昧・話題転換語が
   無い、のいずれでも安全側（情報喪失を避け元テキストのまま）にフォールバックする。

決定論・LLM 非依存。bootstrap_backlog / daily_review の representative 生成経路と batch.ingest の
provenance 保存がこのモジュールを通す（user-only 化を二重実装しない）。
"""
from __future__ import annotations

import re
from typing import List, Optional

# assistant 出力の引用と判定する行頭マーカー:
# - markdown blockquote（"> " / "＞"）
# - ステータス/通知の絵文字プレフィックス（ℹ️ ✓ ✗ ⚠️ ✅ ❌ 🔹 ➜ など assistant の構造化出力）
_QUOTE_LINE_RE = re.compile(
    r"^\s*(?:>|＞|ℹ️?|✓|✔|✗|✘|×|⚠️?|✅|❌|🔹|🔸|➜|→|—|・\s*(?:ℹ️|✓|✗))"
)

# code fence（``` … ``` / ~~~ … ~~~）の開始・終了行。
_FENCE_RE = re.compile(r"^\s*(?:```|~~~)")

_TRUNC = 120


def _strip_assistant_lines(text: str) -> str:
    """assistant 引用行（quote / fence ブロック / 絵文字プレフィックス）を除去する。"""
    out_lines = []
    in_fence = False
    for line in text.split("\n"):
        if _FENCE_RE.match(line):
            # fence の開始/終了行はそれ自体も捨て、内部行も in_fence で捨てる
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if _QUOTE_LINE_RE.match(line):
            continue
        out_lines.append(line)
    return "\n".join(out_lines).strip()


def user_only_text(text: Optional[str]) -> str:
    """発話テキストから assistant 引用ブロックを除き user 発話のみを返す（決定論）。

    全行が assistant 引用なら情報喪失を避けるため元テキストの strip を返す（fallback）。
    """
    if not text:
        return ""
    stripped = _strip_assistant_lines(text)
    if stripped:
        return stripped
    # fallback: 全行が引用判定された場合は元テキスト（trim）を返す
    return text.strip()


def prev_action_summary(prev_action: Optional[str]) -> str:
    """直前 AI 行動の 1 行要約（evidence 用・最大 120 文字に丸める）。

    現状 prev_action は既に短い行（"Edit foo.py" 等）なので strip + 改行畳み + 切り詰めのみ。
    """
    if not prev_action:
        return ""
    one_line = " ".join(str(prev_action).split())
    return one_line[:_TRUNC]


# ── トピック分割（#253: 多トピック発言の evidence トリム） ──────────────────
# 話題転換語（あと/ついでに等）の**直前でのみ**分割する。句点・疑問符だけでは分割しない
# （#253 ROUND2: 「字幕がずれています。タイムコードを基準に直してください。」のような
# 単一トピックの2文目＝修正手段まで誤ってトリムして落とすリグレッションが codex レビューで
# 指摘された。句点区切りの複数文というだけでは複数トピックの根拠にならない）。
#
# 境界は「読点直後」だけでなく「文頭」（文末記号＝。！？改行の直後、または話題転換語が
# テキスト先頭にある場合）も対象にする（「〜直して。あと、〜」の「。あと」を境界として
# 扱うため）。読点は境界として消費（除去）し、文末記号は境界として残す（区切り記号は
# 元の文に残したまま前のセグメントを保つ）。
_TOPIC_SHIFT_RE = re.compile(
    r"(?:^|(?<=[。！？\n])|[、,])\s*"
    r"(?=(?:あと|ついでに|ちなみに|それと|あわせて|ところで))"
)


def _split_topics(text: str) -> List[str]:
    """発言を話題転換語の直前でのみトピック単位のセグメントに分割する（決定論）。

    話題転換語が無い発言（句点・疑問符だけの複数文を含む）は分割せず 1 セグメントのまま
    返す（#253 ROUND2: 単一トピックの複数文を誤ってトリムしない）。
    """
    return [seg.strip() for seg in _TOPIC_SHIFT_RE.split(text) if seg.strip()]


def trim_to_idiom_sentence(text: Optional[str], idiom: Optional[str]) -> str:
    """複数トピック発言の evidence.text を idiom が属するセグメントだけにトリムする（#253）。

    1 発言が「主要な指摘＋ついでの別要望」のように複数トピックを含む場合、Haiku が抽出した
    idiom（修正を端的に表す言い回し）を含むセグメントのみを残し、無関係な副次要望を除く。

    安全側フォールバック（いずれも元テキストをそのまま返す）:
    - トピックが 1 個しかない（分割不能）
    - idiom が空/None（どの部分が本題か判断材料が無い）
    - idiom がどのセグメントにも見つからない（Haiku の言い換え等で一致しない）
    - idiom が複数セグメントに跨ってマッチする（曖昧・誤トリムのリスクが高い）
    """
    if not text:
        return text or ""
    idiom = (idiom or "").strip()
    segments = _split_topics(text)
    if len(segments) <= 1 or not idiom:
        return text
    matches = [seg for seg in segments if idiom in seg]
    if len(matches) == 1:
        return matches[0]
    return text
