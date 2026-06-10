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

    header = ["## Correction Capture (報酬入力の捕捉率)", ""]
    detail = (
        f"（直近 {days} 日 / {min_turns}+ ターンのセッション {active} 件中 "
        f"{captured} 件で correction を検出）"
    )

    if rate >= _STARVATION_THRESHOLD:
        return header + [
            f"✓ 評価したが枯渇兆候なし: capture 率 {rate:.0%} {detail}",
            "",
        ]

    return header + [
        f"⚠ correction capture 率が低い: {rate:.0%} {detail}。"
        "RL ループの報酬入力（corrections）が枯渇している可能性。"
        "検出器の仕様通りの少なさか capture 漏れかを `corrections.jsonl` の中身で確認し、"
        "漏れなら `correction_detect` hook の発火条件を見直す（advisory・スコア非関与, #421）。",
        "",
    ]
