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


def _read_reviewed_keys() -> set:
    """daily_review の既読 signal_key 集合を読む（#525-1）。

    correction_semantic（別所有モジュール）からは **read-only** で参照する。
    モジュール/ストア未解決時は空集合にフォールバックし、未読 = 未昇格 として振る舞う
    （既読情報が無いだけで surface 自体は壊さない）。
    """
    try:
        from correction_semantic.daily_review import read_reviewed_keys
    except ImportError:
        return set()
    try:
        return read_reviewed_keys()
    except Exception:
        return set()


# matrix surface で列挙するチャネルの順序（既知チャネル → llm_judge → その他）。
_CHANNEL_ORDER = (
    "manual_edit_after_ai",
    "permission_deny",
    "rephrase",
    "esc_interrupt",
    "llm_judge",
)


def build_weak_signals_section(project_dir: Path) -> Optional[List[str]]:
    """weak_signals レーンの蓄積状況を audit に surface する（#432）。

    観測可能性:
    - weak_signals モジュール / store 未解決 → None（沈黙）
    - store が空（レコード 0）→ None（まだ何も検出していない＝対象外で沈黙）
    - レコードあり → チャネル別×スコープ matrix（全PJ N / 当PJ未昇格 M）を 1 行ずつ
      + 当PJ未昇格と未読の分離を併記（advisory・スコア非関与）

    #490: 当PJ集計は pj_slug フィルタに限定し、total は「（全PJ集計）」で残す。
    #528-2: チャネル別×スコープを matrix 1 行ずつに分解（散文の桁混在を解消）。
    #525-1: 当PJ未昇格を「未昇格 N 件（うち未読 M 件）」に分離し、daily phase
      「新規なし（既読済）」との噛み合わせを取る（既読ストアと突合）。
    daily_review が pj_slug フィルタで当PJのみ昇格する実装（daily_review.py:153）と一致させる。
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

    # #490: 当PJスコープの slug を pj_slug_fast で導出する（daily_review の書込側と同一関数）。
    try:
        from pj_slug import pj_slug_fast
        current_slug: Optional[str] = pj_slug_fast(project_dir)
    except Exception:
        current_slug = None

    reviewed_keys = _read_reviewed_keys()

    total = len(records)

    # チャネル別の (全PJ件数 / 当PJ未昇格 / 当PJ未昇格かつ未読) を集計する。
    # slug が取れない / レコードに pj_slug が無い場合は当PJ扱い（後方互換・#490）。
    all_by_channel: dict = {}            # 全PJ件数（matrix の左）
    cur_unpromoted_by_channel: dict = {}  # 当PJ未昇格（matrix の右）
    unpromoted = 0      # 当PJ未昇格 合計
    unread = 0          # 当PJ未昇格かつ未読 合計（#525-1）
    for r in records:
        ch = r.get("channel", "unknown")
        all_by_channel[ch] = all_by_channel.get(ch, 0) + 1

        rec_slug = r.get("pj_slug")
        is_current = not (
            current_slug is not None and rec_slug is not None and rec_slug != current_slug
        )
        if is_current and not r.get("promoted"):
            cur_unpromoted_by_channel[ch] = cur_unpromoted_by_channel.get(ch, 0) + 1
            unpromoted += 1
            if r.get("signal_key") not in reviewed_keys:
                unread += 1

    # #528-2: チャネル別×スコープ matrix（1 行ずつ）。順序は既知チャネル → その他。
    ordered_channels = [c for c in _CHANNEL_ORDER if c in all_by_channel]
    ordered_channels += [c for c in all_by_channel if c not in _CHANNEL_ORDER]
    matrix_lines: List[str] = []
    for ch in ordered_channels:
        label = _CHANNEL_LABELS.get(ch, ch)
        cur_n = cur_unpromoted_by_channel.get(ch, 0)
        matrix_lines.append(
            f"  - {label}（{ch}）: 全PJ {all_by_channel[ch]} / 当PJ未昇格 {cur_n}"
        )

    header = ["## Weak Signals (暗黙修正シグナル / 昇格前)", ""]
    # #476-2: read_signals() は DATA_DIR 全PJ共通ストアを集計するため (全PJ) 集計である。
    # bootstrap の pj_total は (当PJ) 集計なので、ラベルなしで並ぶと桁の食い違いに見える。
    # スコープを明示して混乱を防ぐ。
    summary_line = (
        f"暗黙修正シグナルが {total} 件（全PJ集計）。"
        " corrections capture が枯渇しているときの語彙非依存な代替報酬源"
        "（advisory・スコア非関与, #432）。チャネル別×スコープは次の matrix で内訳を示す:"
    )
    # #525-1: 昇格導線文は当PJ未昇格と未読を分離する。daily phase「新規なし（既読済）」と
    # 「未昇格 N 件は昇格可能」が噛み合うよう、未読（= 今日の修正確認の対象）を併記する。
    if unpromoted > 0:
        hint_line = (
            f"当PJ未昇格 {unpromoted} 件（うち未読 {unread} 件）。"
            " 未読分は `/rl-anything:evolve` の今日の修正確認 phase で昇格可能"
            "（既読済は再提示されない）。"
        )
        trailer = [hint_line]
    else:
        trailer = []

    # 安全弁②（ADR-047）: idiom_dict 自動昇格を毎回 surface（黙って進まない）。
    return (
        header
        + [summary_line]
        + matrix_lines
        + trailer
        + _autopromote_lines()
        + [""]
    )
