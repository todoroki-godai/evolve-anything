#!/usr/bin/env python3
"""Slop Detector — 決定論的 regex/ヒューリスティックで AI slop パターンを検出する。

LLM を使わない・コストゼロ。

スコア定義:
  slop_score: float [0.0, 1.0]
    - 1.0 = slop なし（最良）
    - 0.0 = 重大な slop が多数（最悪）

使い方:
    from slop_detector import detect_slop
    result = detect_slop(text)
    print(result.slop_score, result.hits)
"""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List

_PATTERNS_PATH = Path(__file__).resolve().parent / "slop_patterns.json"


# ---------------------------------------------------------------------------
# データ構造
# ---------------------------------------------------------------------------


@dataclass
class SlopResult:
    """detect_slop() の戻り値。

    Attributes:
        slop_score: float [0.0, 1.0]。高いほど良い（slop が少ない）。
        hits: 検出されたパターンのリスト。各要素は
            {"pattern_id": str, "span": [start, end], "snippet": str}。
    """
    slop_score: float
    hits: List[Dict[str, Any]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# パターン読み込み（モジュールレベルで一度だけ）
# ---------------------------------------------------------------------------


def _load_patterns() -> List[Dict[str, Any]]:
    try:
        data = json.loads(_PATTERNS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    compiled = []
    for pat in data:
        try:
            compiled.append({
                **pat,
                "_re": re.compile(pat["regex"]),
            })
        except re.error:
            continue
    return compiled


_PATTERNS: List[Dict[str, Any]] = _load_patterns()

# 最大ペナルティ上限（1.0 を超えると score が負になる）
_MAX_PENALTY_CAP = 1.0


# ---------------------------------------------------------------------------
# 公開 API
# ---------------------------------------------------------------------------


def detect_slop(text: str) -> SlopResult:
    """text 中の slop パターンを検出し SlopResult を返す。

    Args:
        text: 検査対象のテキスト文字列。

    Returns:
        SlopResult(slop_score, hits)
          slop_score は 0.0〜1.0。高いほど slop が少なく良い。
          hits は各マッチの詳細リスト。
    """
    if not text or not text.strip():
        return SlopResult(slop_score=1.0, hits=[])

    hits: List[Dict[str, Any]] = []

    for pat in _PATTERNS:
        compiled_re: re.Pattern = pat["_re"]
        weight: float = pat.get("weight", 0.1)
        pid: str = pat["id"]

        for m in compiled_re.finditer(text):
            start, end = m.start(), m.end()
            snippet = text[start:end]
            hits.append({
                "pattern_id": pid,
                "span": [start, end],
                "snippet": snippet,
            })

    # ペナルティ計算: 各 hit の weight を累積し、score = 1 - cumulative_penalty
    # 同一パターンの重複 hit は 1 回のみカウント（per-pattern ユニーク化）
    seen_patterns: Dict[str, int] = {}
    for h in hits:
        pid = h["pattern_id"]
        seen_patterns[pid] = seen_patterns.get(pid, 0) + 1

    total_penalty = 0.0
    for pat in _PATTERNS:
        pid = pat["id"]
        count = seen_patterns.get(pid, 0)
        if count == 0:
            continue
        weight = pat.get("weight", 0.1)
        # 複数 hit は逓減: 1回目 * weight, 2回目以降は 0.5 逓減
        penalty = weight + (count - 1) * weight * 0.5
        total_penalty += penalty

    total_penalty = min(total_penalty, _MAX_PENALTY_CAP)
    slop_score = round(max(0.0, 1.0 - total_penalty), 4)

    return SlopResult(slop_score=slop_score, hits=hits)


def get_patterns() -> List[Dict[str, Any]]:
    """登録済みパターン一覧を返す（_re フィールドを除外）。"""
    return [
        {k: v for k, v in pat.items() if k != "_re"}
        for pat in _PATTERNS
    ]
