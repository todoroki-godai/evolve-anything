"""Strategy Router: ファイルサイズに基づく最適化手法の自動選択"""
from __future__ import annotations
from typing import Literal

STRATEGY_THRESHOLD: int = 200  # 行数閾値


def select_strategy(file_lines: int) -> Literal["self_refine", "budget_mpo"]:
    """ファイル行数に基づき最適化手法を選択。

    Args:
        file_lines: ファイルの行数

    Returns:
        "self_refine" (< 200行) or "budget_mpo" (>= 200行)

    Raises:
        ValueError: file_lines が負の値
    """
    if file_lines < 0:
        raise ValueError(f"file_lines must be non-negative, got {file_lines}")
    if file_lines < STRATEGY_THRESHOLD:
        return "self_refine"
    return "budget_mpo"
