"""Weak signals（暗黙修正シグナル）の observability セクション生成（#432）。

weak_signals レーンに溜まった暗黙修正シグナル（4 チャネル）の件数を audit に surface する。
RL ループの報酬入力（corrections）が枯渇しているとき（capture_rate #421）、行動シグナルは
語彙非依存の代替報酬源になりうる。reflect 確認後に昇格する未確認候補がどれだけ溜まっているかを
人が把握できるよう、チャネル別件数と未昇格数を併記する。

**advisory のみ**: スコア重みには入れない（corrections 本流に直接入れない設計、#432）。
observability contract の `build_*_section` 契約（`(project_dir) -> Optional[List[str]]`）は
他 builder と同一。決定論・LLM 非依存。store を読むだけで検出（batch 走査）はしない。
"""
from pathlib import Path
from typing import List, Optional

# チャネル名 → 日本語ラベル（surface 表示用）。
_CHANNEL_LABELS = {
    "manual_edit_after_ai": "直後手編集",
    "permission_deny": "permission deny",
    "rephrase": "言い直し",
    "esc_interrupt": "Esc 中断",
}


def build_weak_signals_section(project_dir: Path) -> Optional[List[str]]:
    """weak_signals レーンの蓄積状況を audit に surface する（#432）。

    観測可能性:
    - weak_signals モジュール / store 未解決 → None（沈黙）
    - store が空（レコード 0）→ None（まだ何も検出していない＝対象外で沈黙）
    - レコードあり → チャネル別件数 + 未昇格数を併記（advisory・スコア非関与）
    """
    try:
        from weak_signals.store import read_signals
    except ImportError:
        return None

    try:
        records = read_signals()
    except Exception:
        return None

    if not records:
        return None  # まだ何も溜まっていない → 沈黙

    by_channel: dict = {}
    unpromoted = 0
    for r in records:
        ch = r.get("channel", "unknown")
        by_channel[ch] = by_channel.get(ch, 0) + 1
        if not r.get("promoted"):
            unpromoted += 1

    total = len(records)
    parts = []
    for ch in ("manual_edit_after_ai", "permission_deny", "rephrase", "esc_interrupt"):
        if ch in by_channel:
            label = _CHANNEL_LABELS.get(ch, ch)
            parts.append(f"{label} {by_channel[ch]}")
    # 既知チャネル外（将来の llm_judge 等）も拾う
    for ch, n in by_channel.items():
        if ch not in _CHANNEL_LABELS:
            parts.append(f"{ch} {n}")

    header = ["## Weak Signals (暗黙修正シグナル / 昇格前)", ""]
    return header + [
        f"暗黙修正シグナルが {total} 件（{' / '.join(parts)}）。"
        f"うち未昇格 {unpromoted} 件は reflect 確認後に corrections 本流へ昇格候補。"
        "corrections capture が枯渇しているときの語彙非依存な代替報酬源（advisory・スコア非関与, #432）。",
        "",
    ]
