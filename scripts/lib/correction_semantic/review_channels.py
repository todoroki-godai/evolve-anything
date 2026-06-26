"""correction_semantic.review_channels — y/n 確認に出す weak チャネルの単一ソース（#99）。

evolve の対話昇格 phase（bootstrap_backlog / daily_review）はかつて channel="llm_judge"
固定で、決定論チャネル（#432）は ``evolve-reflect --promote-weak`` でしか昇格できなかった。
その結果「evolve をフル実行しても決定論 weak が queue から減らない」非対称が生じていた
（learning_weak_promotion_channel_asymmetry）。本モジュールは「**どのチャネルを y/n 確認に
出すか**」を 1 箇所に集約し、bootstrap_backlog / daily_review / promote が同じ集合を参照する
（コピー慣習の partial fix を避ける・pitfall_copied_parse_convention_partial_fix）。

チャネルは保存されている中身の濃さで 2 層に分かれる（実データ実測）:

- **content-rich（``REVIEW_CHANNELS``）** = y/n 確認に出して昇格に意味がある:
  - ``llm_judge``: provenance.text=ユーザー発話 + reason=Haiku 判定（#431）
  - ``rephrase``: provenance.text=言い直し後の発話（detector が保存・#432③）
  - ``permission_deny``: provenance.tool_name + tool_input_summary=拒否されたコマンド（#432②）
- **content-poor（``CONTENT_POOR_CHANNELS``）** = 個別昇格に出さない:
  - ``esc_interrupt``: provenance.evidence="[Request interrupted…]" のみ
  - ``manual_edit_after_ai``: provenance.evidence="File has been modified…" のみ
  detector（weak_signals/detectors.py）が「何が起きたか」の周辺文脈（ユーザー発話・直前 AI
  行動）を保存しないため、y/n 確認に出しても判断材料が無く、昇格しても message=channel 名の
  空 correction にしかならない。これらは observability の weak_signals matrix に件数として残り、
  集計シグナル（「N 回中断した」等）として surface する（個別 correction にはしない）。

決定論・LLM 非依存。
"""
from __future__ import annotations

from typing import Any, Dict, FrozenSet

from correction_semantic.representative import user_only_text

# y/n 確認に出す content-rich チャネル（単一ソース）。
REVIEW_CHANNELS: FrozenSet[str] = frozenset(
    {"llm_judge", "rephrase", "permission_deny"}
)

# 個別昇格に出さない content-poor チャネル（observability 集計のみ・#99）。
# 参照用に明示しておき「除外は意図的（detector が文脈未保存）」を機械可読に残す。
CONTENT_POOR_CHANNELS: FrozenSet[str] = frozenset(
    {"esc_interrupt", "manual_edit_after_ai"}
)

# permission_deny の representative に添える拒否コマンドの最大文字数。
_DENY_SUMMARY_TRUNC = 120


def is_review_channel(channel: Any) -> bool:
    """当該 channel が y/n 確認対象（content-rich）かを返す。"""
    return channel in REVIEW_CHANNELS


def signal_text(rec: Dict[str, Any]) -> str:
    """weak_signal レコードから channel 別の actionable 代表テキストを返す（決定論）。

    representative（確認 group の代表発話）と corrections の message 本文の両方がこの
    単一ソースを通す（チャネル別抽出を二重実装しない）:

    - ``llm_judge`` / ``rephrase``: provenance.text を user_only_text で user 発話のみ抽出
      （assistant 引用ブロック混入の除去・#528-3）
    - ``permission_deny``: tool_name + tool_input_summary（拒否されたコマンド）を合成。
      denial_reason は実データで "unknown" が大半なので、意味があるときだけ添える
    - content-poor / 未知 channel: "" を返す（呼び出し側が channel 名へ fallback）
    """
    prov = rec.get("provenance") or {}
    channel = rec.get("channel")

    if channel == "permission_deny":
        tool = str(prov.get("tool_name") or "").strip()
        summary = str(prov.get("tool_input_summary") or "").strip()
        reason = str(prov.get("denial_reason") or "").strip()
        head = f"{tool} の実行を拒否" if tool else "ツール実行を拒否"
        if summary:
            summary = " ".join(summary.split())[:_DENY_SUMMARY_TRUNC]
            head = f"{head}: {summary}"
        if reason and reason.lower() != "unknown":
            head = f"{head}（{reason}）"
        return head

    # llm_judge / rephrase（および text を持つ将来チャネル）は user 発話のみ抽出。
    return user_only_text(prov.get("text") or "")
