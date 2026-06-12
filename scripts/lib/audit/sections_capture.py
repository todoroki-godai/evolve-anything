"""Correction capture 率の observability セクション生成（#421）。

RL ループの報酬入力（corrections）が枯渇していないかを surface する。capture 率が低いとき、
それが「検出器の仕様通りの少なさ」なのか「capture 漏れ」なのかを人が判別できるよう、
分母（active session）と分子（correction を持つ session）を併記する。

**advisory のみ**: スコア重みには入れない（壊れた入力の上に重みを作らない、#421）。
observability contract から参照される `build_*_section` 契約
（`(project_dir) -> Optional[List[str]]`）は他 builder と同一。決定論・LLM 非依存。
"""
from pathlib import Path
from typing import List, Optional, Tuple

# capture 率が「枯渇」とみなされる閾値（advisory のしきい値で、スコアには影響しない）。
_STARVATION_THRESHOLD = 0.10


def _llm_judge_count() -> int:
    """**当 PJ slug の** weak_signals llm_judge channel 件数を返す（#476-1 / #476 fixup）。

    capture_rate は hook が書く corrections.jsonl のみを分子にするが、correction の
    意味判定は weak_signals レーンの llm_judge channel に隔離される（#431）。hook capture
    が 0% でも llm_judge が大量捕捉していれば「報酬入力枯渇」は誤警告。channel 別表示で
    実態（hook N / llm_judge M）を併記し、llm_judge があれば枯渇判定を抑制する。

    **slug スコープ必須（全PJ共通 DATA_DIR pitfall）**: weak_signals.jsonl は全 PJ 共通
    ストアなので、PJ フィルタなしで数えると hook N（当PJ window 集計）と桁が混在し、さらに
    他 PJ の llm_judge シグナルが当 PJ の枯渇警告を誤って抑制する。bootstrap_backlog と同じ
    `r.get("pj_slug")` 突合で当 PJ slug に限定する。slug 導出は optimize_history_store.resolve_slug
    （worktree 安全・git-common-dir 親で本体 slug に正規化）に合わせる。

    store / slug 未解決 / 読込失敗は 0（防御的・沈黙でなく従来挙動へフォールバック）。
    """
    try:
        from weak_signals.store import read_signals
    except ImportError:
        return 0
    try:
        from optimize_history_store import resolve_slug
        slug = resolve_slug()
    except Exception:
        return 0
    try:
        return sum(
            1
            for r in read_signals()
            if r.get("channel") == "llm_judge" and r.get("pj_slug") == slug
        )
    except Exception:
        return 0


def _resolve_store_files() -> Tuple[Path, Path]:
    """usage.jsonl / corrections.jsonl の正準パスを解決する（#358 hook-writer 系）。

    audit.DATA_DIR を base に hook_store_path で解決し、tool 文脈でも hook が書いた
    plugin-data dir を回収する。テストはこの関数を patch して tmp store に向ける。
    """
    from rl_common import hook_store_path

    from . import DATA_DIR as _DATA_DIR

    return (
        hook_store_path("usage.jsonl", base=_DATA_DIR),
        hook_store_path("corrections.jsonl", base=_DATA_DIR),
    )


def build_capture_rate_section(project_dir: Path) -> Optional[List[str]]:
    """correction capture 率を audit に surface する（#421）。

    capture 率 = 「min_turns 以上のターンを持つセッション（usage 行数 proxy）」のうち
    「correction を 1 件以上検出したセッション」の割合。report の他 builder と異なり
    project_dir には依存せず環境グローバルなテレメトリ（usage/corrections）を読む。

    観測可能性:
    - active session が 0（テレメトリ未蓄積 / 長セッション無し）→ None（対象外で沈黙）
    - active session があり capture 率が閾値以上 → 「評価したが枯渇兆候なし ✓」
      （silence != evaluated。値は低い少なさが仕様か漏れか判別できるよう常に併記）
    - 閾値未満 → ⚠ で starvation を surface。分母/分子を併記し、検出器仕様か漏れかの
      判別材料を残す（hook 有用性評価 #318 の follow-through）。
    """
    try:
        import capture_rate
    except ImportError:
        return None

    try:
        usage_file, corrections_file = _resolve_store_files()
        result = capture_rate.compute_capture_rate(
            usage_file=usage_file, corrections_file=corrections_file
        )
    except Exception:
        return None

    if not result.get("applicable"):
        return None  # active session なし → テレメトリ未蓄積で対象外

    active = result["active_sessions"]
    captured = result["captured_sessions"]
    rate = result["capture_rate"]
    min_turns = result["min_turns"]
    days = result["days"]

    # #476-1: capture を channel 別に表示する。hook 系（corrections.jsonl）は capture_rate の
    # 分子、意味判定系（weak_signals の llm_judge channel）は別レーン。両方を併記して
    # 「hook 0% だが llm_judge が大量捕捉」の実態を可視化する。
    llm_judge = _llm_judge_count()

    header = ["## Correction Capture (報酬入力の捕捉率)", ""]
    detail = (
        f"（直近 {days} 日 / {min_turns}+ ターンのセッション {active} 件中 "
        f"{captured} 件で correction を検出）"
    )
    channel_line = (
        f"channel 別: hook {captured} 件（capture 率 {rate:.0%}）/ "
        f"llm_judge {llm_judge} 件（当PJ・weak_signals レーン・昇格前）"
    )

    if rate >= _STARVATION_THRESHOLD:
        return header + [
            f"✓ 評価したが枯渇兆候なし: capture 率 {rate:.0%} {detail}",
            channel_line,
            "",
        ]

    # #476-1: hook capture が低くても llm_judge が捕捉していれば「枯渇」ではない。
    # 誤警告を避け、weak_signals → 昇格フローへ誘導する。
    if llm_judge > 0:
        return header + [
            f"hook 経由の capture 率は低い（{rate:.0%}）が、意味判定レーン（llm_judge）で "
            f"{llm_judge} 件捕捉済み。{channel_line}",
            "未昇格の llm_judge シグナルは `/rl-anything:evolve` の今日の修正確認 phase で昇格可能"
            "（報酬入力は枯渇していない・advisory・スコア非関与, #421/#476）。",
            "",
        ]

    return header + [
        f"⚠ correction capture 率が低い: {rate:.0%} {detail}。"
        "RL ループの報酬入力（corrections）が枯渇している可能性。"
        "検出器の仕様通りの少なさか capture 漏れかを `corrections.jsonl` の中身で確認し、"
        "漏れなら `correction_detect` hook の発火条件を見直す（advisory・スコア非関与, #421）。",
        channel_line,
        "",
    ]
