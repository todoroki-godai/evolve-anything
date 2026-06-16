"""correction_semantic.representative — representative 品質ヘルパ（#528-3）。

bootstrap/daily の representative（確認 group の代表発話断片）に **assistant の過去レポート
出力が引用ごと混入**して判読困難になる問題（「ℹ️ データ蓄積待ち…」がシグナル本文に混入）を
決定論で解決する:

1. ``user_only_text`` — 元発話テキストから assistant 出力の引用ブロック（``>`` markdown quote /
   code fence / ℹ️・✓・✗ 等のステータス絵文字プレフィックス行）を strip し、user 発話のみ残す。
   全行が assistant 引用なら情報喪失を避けるため元テキストの strip を返す（fallback）。
2. ``prev_action_summary`` — 直前 AI 行動の 1 行要約（「やっぱり、高だけにして」のような
   一行 representative が何に対する修正か不明な問題を、evidence に prev_action を添えて解消する）。

決定論・LLM 非依存。bootstrap_backlog / daily_review の representative 生成経路と batch.ingest の
provenance 保存がこのモジュールを通す（user-only 化を二重実装しない）。
"""
from __future__ import annotations

import re
from typing import Optional

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
