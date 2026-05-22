"""corrections.jsonl のコーパスレベル診断。

繰り返し失敗パターン TOP-N を集計する独立モジュール。
pipeline_reflector/ (remediation-outcomes.jsonl 対象) とは別の関心事。
"""
from __future__ import annotations

import json
import sys as _sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# rl_common が sys.path にある前提（hooks/ パターン準拠）
_LIB = Path(__file__).resolve().parent
if str(_LIB) not in _sys.path:
    _sys.path.insert(0, str(_LIB))
try:
    from rl_common.detection import CORRECTION_PATTERNS as _CP
    _POSITIVE_TYPES: frozenset[str] = frozenset(
        k for k, v in _CP.items() if v.get("type") == "positive"
    )
except ImportError:
    _POSITIVE_TYPES = frozenset({"perfect", "great-approach", "keep-doing"})

CORRECTIONS_FILE = Path.home() / ".claude" / "rl-anything" / "corrections.jsonl"
MIN_DISPLAY_RECORDS = 10  # この件数以上ないと意味のある集計にならない（D9）


def load_corrections_for_insights(
    lookback_days: int = 90,
    corrections_file: Path | None = None,
) -> list[dict[str, Any]]:
    """corrections.jsonl を読み込む。lookback_days フィルタ付き。

    outcomes.py の load_outcomes() パターン準拠。
    全フィールドアクセスは .get() で fallback（D8）。
    """
    path = corrections_file if corrections_file is not None else CORRECTIONS_FILE
    if not path.exists():
        return []

    cutoff: str | None = None
    if lookback_days is not None:
        cutoff_dt = datetime.now(timezone.utc) - timedelta(days=lookback_days)
        cutoff = cutoff_dt.isoformat()

    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
            if cutoff and rec.get("timestamp", "").replace("Z", "+00:00") < cutoff:
                continue
            records.append(rec)
        except json.JSONDecodeError:
            continue
    return records


def count_repeated_patterns(
    lookback_days: int = 90,
    top_n: int = 5,
    min_count: int = 3,
    corrections_file: Path | None = None,
) -> list[dict[str, Any]]:
    """繰り返し失敗パターン TOP-N を返す。

    Returns:
        [
            {
                "correction_type": str,
                "count": int,
                "example_messages": list[str],  # 最大3件のメッセージ例
                "error_category": str | None,   # 最頻 error_category（.get() fallback）
            },
            ...
        ]
        件数不足 (< MIN_DISPLAY_RECORDS) の場合は []
    """
    records = load_corrections_for_insights(
        lookback_days=lookback_days,
        corrections_file=corrections_file,
    )

    # D9: 集計に意味がある件数に満たない場合はスキップ
    if len(records) < MIN_DISPLAY_RECORDS:
        return []

    # ポジティブ型を除外して集計
    type_messages: dict[str, list[str]] = defaultdict(list)
    type_error_cats: dict[str, list[str]] = defaultdict(list)

    for rec in records:
        ct = rec.get("correction_type", "unknown")
        # ポジティブ型は除外
        if ct in _POSITIVE_TYPES:
            continue
        msg = rec.get("message", "")
        # 件数カウントのため常にエントリを作成（メッセージなしでも発生回数に含める）
        if msg:
            type_messages[ct].append(msg)
        else:
            type_messages[ct].append("")  # メッセージなし分もカウント対象
        ec = rec.get("error_category", None)
        if ec is not None:
            type_error_cats[ct].append(ec)

    # min_count 未満のものを除外して件数降順に並べ top_n 件取得
    counts: list[tuple[str, int]] = [
        (ct, len(msgs))
        for ct, msgs in type_messages.items()
        if len(msgs) >= min_count
    ]
    counts.sort(key=lambda x: x[1], reverse=True)
    counts = counts[:top_n]

    result: list[dict[str, Any]] = []
    for ct, count in counts:
        # 最大 3 件のメッセージ例（空文字は除く）
        all_msgs = [m for m in type_messages[ct] if m]
        examples = all_msgs[:3]

        # 最頻 error_category（フィールドが存在しない場合は None）
        ec_list = type_error_cats.get(ct, [])
        most_common_ec: str | None = None
        if ec_list:
            most_common_ec = Counter(ec_list).most_common(1)[0][0]

        result.append(
            {
                "correction_type": ct,
                "count": count,
                "example_messages": examples,
                "error_category": most_common_ec,
            }
        )

    return result
