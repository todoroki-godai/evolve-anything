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
        + "（重み 1.0・ADR-047 安全弁②）。誤昇格は `evolve-reflect --revoke-idiom <idiom_key>` で巻き戻せる。"
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


def _backlog_lane_lines(current_slug: Optional[str]) -> List[str]:
    """過去 backlog（daily phase 圏外）の昇格導線を別レーンで返す（#583）。

    「今日の修正確認 phase で昇格可能」の案内は daily_review が拾える分にしか当てはまらない。
    daily_review は ``max_groups``（既定 5）で上位 group しか提示せず、bootstrap も marker 済み
    （``is_done``）なら過去 backlog を拾わない。両者の隙間に落ちる過去未読分（marker 済み かつ
    ``build_review`` の ``remaining > 0``）が実在するときだけ、``reflect --show-weak-signals``
    → ``--promote-weak`` という全件入口を案内する。

    判定は read-only（marker は読むだけ・build_review は読み取りのみで既読集合を書かない）。
    slug 未解決 / モジュール未解決 / 読込失敗は空リスト（従来挙動＝誘導なしへフォールバック）。
    """
    if not current_slug:
        return []
    try:
        from correction_semantic import bootstrap_backlog as _bb
        from correction_semantic import daily_review as _dr
    except ImportError:
        return []
    try:
        # bootstrap が未消化（marker 未設定）なら過去 backlog は bootstrap がまとめて拾う。
        # その場合は別レーンを出さない（誤誘導・重複案内の回避）。
        if not _bb.is_done(current_slug):
            return []
        # marker 済み: daily が上位 group しか拾えないので remaining 分が構造的に外れる。
        review = _dr.build_review(current_slug, dry_run=True)
        remaining = int(review.get("remaining", 0) or 0)
    except Exception:
        return []
    if remaining <= 0:
        return []
    return [
        f"  ⚠ うち過去 backlog {remaining} 件は今日の修正確認 phase の上位提示から外れている"
        "（bootstrap 消化済み・daily は上位のみ提示）。"
        " 全件は `/evolve-anything:reflect --show-weak-signals --weak-channel llm_judge` で確認し"
        " `--promote-weak <signal_key,...>` で昇格する。",
    ]


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
    # #117: 未読を content-rich（REVIEW_CHANNELS）と content-poor に分けて集計する。
    # 昇格入口は #99 で daily_review（evolve）が content-rich = llm_judge / rephrase /
    # permission_deny を拾うよう拡張された。旧 #562 は「llm_judge のみ evolve・残り決定論は
    # reflect」で二分していたが、それだと content-rich な rephrase / permission_deny を
    # reflect へ誤誘導し、逆に昇格すると空 correction にしかならない content-poor（esc /
    # 手編集）を「reflect で昇格可能」と誤って案内していた（learning_weak_promotion_channel_asymmetry）。
    # バケットを REVIEW_CHANNELS 単一ソースに揃えることで導線を daily_review の実カバレッジに追随させる。
    from correction_semantic.review_channels import (
        CONTENT_POOR_CHANNELS,
        REVIEW_CHANNELS,
    )

    all_by_channel: dict = {}            # 全PJ件数（matrix の左）
    cur_unpromoted_by_channel: dict = {}  # 当PJ未昇格（matrix の右）
    unpromoted = 0      # 当PJ未昇格 合計
    unread = 0          # 当PJ未昇格かつ未読 合計（#525-1）
    unread_review = 0       # #117: content-rich チャネルの未読（daily_review/evolve が昇格）
    unread_content_poor = 0  # #117: content-poor チャネルの未読（detector 文脈未保存・観測のみ）
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
                if ch in REVIEW_CHANNELS:
                    unread_review += 1
                elif ch in CONTENT_POOR_CHANNELS:
                    unread_content_poor += 1

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
    # #525-1 / #117: 昇格導線文は当PJ未昇格と未読を分離し、未読をチャネル層別に案内する。
    #   - content-rich 未読（llm_judge / rephrase / permission_deny）→ 今日の修正確認 phase（evolve）で昇格
    #   - content-poor 未読（esc / 手編集）→ detector 文脈未保存ゆえ個別昇格の対象外（観測のみ）
    # content-rich 未読 0 のときは「今日の修正確認 phase」行を出さない（誤誘導防止）。
    trailer: List[str] = []
    if unpromoted > 0:
        trailer.append(
            f"当PJ未昇格 {unpromoted} 件（うち未読 {unread} 件）。"
        )
        if unread_review > 0:
            trailer.append(
                f"  うち content-rich チャネル {unread_review} 件は"
                " `/evolve-anything:evolve` の今日の修正確認 phase で昇格可能"
                "（llm_judge / 言い直し / permission deny・既読済は再提示されない）。"
            )
            # #583: 「今日の修正確認 phase で昇格可能」の案内と実導線の食い違いを解消する。
            # daily_review は max_groups（既定 5）で上位 group しか提示せず、bootstrap も
            # marker 済み（is_done）なら過去 backlog を一切拾わない。両者の隙間に落ちる
            # 「marker 済み × daily 上位を超える過去未読分」は両 phase から構造的に外れる
            # （案内文どおりに進めても入口がない）。その隙間が実在する（marker 済み かつ
            # daily.build_review の remaining > 0）ときだけ、過去 backlog 全件の真の入口
            # （reflect --show-weak-signals → --promote-weak は read_unpromoted ベースで
            # marker/既読を見ず全件拾える）を別レーンで surface する。判定は read-only で、
            # 取得失敗時は従来挙動（誘導なし）にフォールバックする。
            for backlog_line in _backlog_lane_lines(current_slug):
                trailer.append(backlog_line)
        if unread_content_poor > 0:
            trailer.append(
                f"  うち content-poor チャネル {unread_content_poor} 件"
                "（Esc 中断 / 直後手編集）は detector が周辺文脈を保存しないため個別昇格の対象外"
                "（昇格しても channel 名だけの空 correction になる・件数のみ観測・#99）。"
            )

    # 安全弁②（ADR-047）: idiom_dict 自動昇格を毎回 surface（黙って進まない）。
    return (
        header
        + [summary_line]
        + matrix_lines
        + trailer
        + _autopromote_lines()
        + [""]
    )
