"""Weak signals（暗黙修正シグナル）の observability セクション生成（#432）。

weak_signals レーンに溜まった暗黙修正シグナル（4 チャネル）の件数を audit に surface する。
RL ループの報酬入力（corrections）が枯渇しているとき（capture_rate #421）、行動シグナルは
語彙非依存の代替報酬源になりうる。reflect 確認後に昇格する未確認候補がどれだけ溜まっているかを
人が把握できるよう、チャネル別件数と未昇格数を併記する。

**advisory のみ**: スコア重みには入れない（corrections 本流に直接入れない設計、#432）。
observability contract の `build_*_section` 契約（`(project_dir) -> Optional[List[str]]`）は
他 builder と同一。決定論・LLM 非依存。store を読むだけで検出（batch 走査）はしない。
"""
import json
from pathlib import Path
from typing import List, Optional

# チャネル名 → 日本語ラベル（surface 表示用）。
_CHANNEL_LABELS = {
    "manual_edit_after_ai": "直後手編集",
    "permission_deny": "permission deny",
    "rephrase": "言い直し",
    "esc_interrupt": "Esc 中断",
}

# 自動昇格 surface で列挙する idiom の最大数（行が伸びすぎないよう抑える）。
_MAX_IDIOM_LIST = 5


def _corrections_path() -> Path:
    """corrections.jsonl の正準パスを ADR-042 resolver 経由で解決する（テストで差し替え可）。"""
    import os

    import rl_common  # 遅延 import（hook/tool 文脈の patch 追従）

    env = os.environ.get("CLAUDE_PLUGIN_DATA", "")
    return Path(rl_common.resolve_data_dir(env)) / "corrections.jsonl"


def _autopromote_lines() -> List[str]:
    """corrections.jsonl の idiom_dict 自動昇格（非 invalidated）を surface する行を返す（安全弁②・ADR-047）。

    idiom_autopromote は confirmed idiom に一致した新規 weak_signal を人間再確認なしで昇格する。
    黙って進まないよう、累計の自動昇格件数 + idiom 一覧を必ず可視化する（ADR-028 observability）。
    0 件ならノイズを足さないため空リストを返す。
    """
    path = _corrections_path()
    if not path.exists():
        return []
    promoted_msgs: List[str] = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                if r.get("promoted_by") != "idiom_dict":
                    continue
                if r.get("invalidated"):
                    continue
                promoted_msgs.append(r.get("message") or "")
    except OSError:
        return []

    n = len(promoted_msgs)
    if n == 0:
        return []
    shown = [m for m in promoted_msgs if m][:_MAX_IDIOM_LIST]
    listing = " / ".join(shown) if shown else ""
    more = f" ほか {n - len(shown)} 件" if n > len(shown) else ""
    line = (
        f"うち idiom_dict 自動昇格は累計 {n} 件"
        + (f"（{listing}{more}）" if listing else "")
        + "。human-confirmed idiom に一致した新規シグナルを再確認なしで昇格"
        + "（重み 1.0・ADR-047 安全弁②）。誤昇格は `rl-reflect --revoke-idiom <idiom_key>` で巻き戻せる。"
    )
    return [line]


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

    hint = (
        f"うち未昇格 {unpromoted} 件は `/rl-anything:evolve` の今日の修正確認 phase で昇格可能。"
        if unpromoted > 0
        else ""
    )
    header = ["## Weak Signals (暗黙修正シグナル / 昇格前)", ""]
    # #476-2: read_signals() は DATA_DIR 全PJ共通ストアを集計するため (全PJ) 集計である。
    # bootstrap の pj_total は (当PJ) 集計なので、ラベルなしで並ぶと桁の食い違いに見える。
    # スコープを明示して混乱を防ぐ。
    body_line = (
        f"暗黙修正シグナルが {total} 件（全PJ集計）（{' / '.join(parts)}）。"
        + (f" {hint}" if hint else "")
        + " corrections capture が枯渇しているときの語彙非依存な代替報酬源（advisory・スコア非関与, #432）。"
    )
    # 安全弁②（ADR-047）: idiom_dict 自動昇格を毎回 surface（黙って進まない）。
    return header + [body_line] + _autopromote_lines() + [""]
