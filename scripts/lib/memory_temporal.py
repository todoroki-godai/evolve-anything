#!/usr/bin/env python3
"""memory ファイルの temporal frontmatter ヘルパー。

APEX-MEM インスパイアの A++ 設計:
- valid_from / superseded_at / decay_days / source_correction_ids の読み取り
- 既存 frontmatter.parse_frontmatter() を流用（後方互換保証済み）
- frontmatter なしの既存ファイルは例外なくデフォルト値を返す

# TODO(APEX-MEM-C): Event-Centric Rewrite への移行時、このモジュールを
# 6ノード型 JSONL グラフ（Rule/Skill/Correction/Session/Pitfall/Memory）の
# Memory ノードパーサーに置き換える。
# 参照: issue #13
"""
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from frontmatter import parse_frontmatter

TEMPORAL_DEFAULTS: dict[str, Any] = {
    "valid_from": None,
    "superseded_at": None,
    "decay_days": None,
    "source_correction_ids": [],
}


def parse_memory_temporal(filepath: Path) -> dict[str, Any]:
    """memory ファイルから temporal フィールドを読み取る。

    frontmatter がないファイル（既存ファイル）は TEMPORAL_DEFAULTS を返す。
    例外は発生しない。
    """
    fm = parse_frontmatter(filepath)
    result = dict(TEMPORAL_DEFAULTS)
    result["valid_from"] = fm.get("valid_from", None)
    result["superseded_at"] = fm.get("superseded_at", None)
    decay = fm.get("decay_days", None)
    # decay_days: 0 以下は null と同じ扱い（負値・ゼロは期限なし）
    result["decay_days"] = decay if (isinstance(decay, int) and decay > 0) else None
    ids = fm.get("source_correction_ids", [])
    result["source_correction_ids"] = ids if isinstance(ids, list) else []
    return result


def is_stale(temporal: dict[str, Any]) -> bool:
    """decay_days を超過しているか判定する。

    - decay_days が None or 0 → 期限なし → False
    - valid_from がない → 判定不能 → False
    """
    decay_days = temporal.get("decay_days")
    if not decay_days:
        return False

    valid_from_str = temporal.get("valid_from")
    if not valid_from_str:
        return False

    try:
        valid_from = datetime.fromisoformat(
            valid_from_str.replace("Z", "+00:00")
        )
        if valid_from.tzinfo is None:
            valid_from = valid_from.replace(tzinfo=timezone.utc)
        age_days = (datetime.now(timezone.utc) - valid_from).days
        return age_days > decay_days
    except (ValueError, TypeError):
        return False


def is_superseded(temporal: dict[str, Any]) -> bool:
    """superseded_at が過去かどうか判定する。"""
    superseded_at_str = temporal.get("superseded_at")
    if not superseded_at_str:
        return False

    try:
        superseded_at = datetime.fromisoformat(
            superseded_at_str.replace("Z", "+00:00")
        )
        if superseded_at.tzinfo is None:
            superseded_at = superseded_at.replace(tzinfo=timezone.utc)
        return superseded_at < datetime.now(timezone.utc)
    except (ValueError, TypeError):
        return False


def make_source_correction_id(session_id: str, timestamp: str) -> str:
    """source_correction_ids の複合キーを生成する。

    形式: "{session_id}#{timestamp}"
    session_id と ms 精度の timestamp の組み合わせで実質一意。
    """
    return f"{session_id}#{timestamp}"
