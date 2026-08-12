"""ADR-054 Phase 0（B1）: NotificationItem 群の digest 化・merge・additionalContext 連結。

収集関数（``collectors.py``）が返した ``NotificationItem`` の list から、SessionStart
systemMessage の最終文字列を組み立てる（§4.2/§4.4）。
"""
from .model import NotificationItem  # noqa: F401  (型ヒント・re-export 用)

# ADR-054 Phase 0 §4.4: systemMessage の Tier2 予算（頭裁定・実効上限契約は §4.4 参照）。
TIER2_BUDGET_CHARS = 400


def _merge_notification_text(items: "list[NotificationItem]") -> "str | None":
    """§4.2/§4.4: 発火1件ならフル文、2件以上なら digest 化して結合する。

    - 発火0件 → None
    - 発火1件 → その系統の ``text``（フル文）をそのまま使う
    - 発火2件以上 → 全 item の ``digest`` を使う。Tier1 は無条件・全量で先に結合し
      （絶対に truncate しない）、Tier2 は残り予算（``TIER2_BUDGET_CHARS`` − Tier1合計）に
      入る分だけ発火順に追加する。あふれた分は「（ほか: 系統名）」で畳む（件数のみは禁止）。
      切り詰めは digest 単位（文字列途中では切らない）。
    """
    if not items:
        return None
    if len(items) == 1:
        return items[0].text

    tier1 = [it for it in items if it.tier == 1]
    tier2 = [it for it in items if it.tier == 2]

    segments = [it.digest for it in tier1]
    included_tier2: "list[str]" = []
    overflow_labels: "list[str]" = []
    for it in tier2:
        candidate = segments + included_tier2 + [it.digest]
        if len(" / ".join(candidate)) <= TIER2_BUDGET_CHARS:
            included_tier2.append(it.digest)
        else:
            overflow_labels.append(it.label)

    body = " / ".join(segments + included_tier2)
    if overflow_labels:
        body = f"{body}（ほか: {'/'.join(overflow_labels)}）"

    text = f"[evolve-anything] {body}"

    if any(it.tail_link for it in items):
        text = f"{text} → /evolve-anything:queue で開始"

    return text


def _build_additional_context(
    work_context_summary: "str | None", proposal_context: "str | None"
) -> "str | None":
    """work_context summary（あれば）と session_proposal の指示（あれば）を連結する（§4.1/§4.5）。"""
    parts = [p for p in (work_context_summary, proposal_context) if p]
    if not parts:
        return None
    return "\n\n".join(parts)
