"""Early Stopping: セクション単位の早期停止ルール"""
from __future__ import annotations
import logging
import math
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# 定数
PLATEAU_COUNT: int = 3
MARGINAL_GAIN_THRESHOLD: float = 0.01
QUALITY_THRESHOLD: float = 0.95


@dataclass
class EarlyStopRule:
    """停止条件パラメータ。"""
    quality_threshold: float = QUALITY_THRESHOLD
    plateau_count: int = PLATEAU_COUNT
    budget_limit: int | None = None
    marginal_gain_threshold: float = MARGINAL_GAIN_THRESHOLD

    def __post_init__(self):
        """不正パラメータをデフォルト値に修正。"""
        if self.quality_threshold < 0 or self.quality_threshold > 1:
            logger.warning(f"Invalid quality_threshold {self.quality_threshold}, using default {QUALITY_THRESHOLD}")
            self.quality_threshold = QUALITY_THRESHOLD
        if self.plateau_count < 1:
            logger.warning(f"Invalid plateau_count {self.plateau_count}, using default {PLATEAU_COUNT}")
            self.plateau_count = PLATEAU_COUNT
        if self.marginal_gain_threshold < 0:
            logger.warning(f"Invalid marginal_gain_threshold {self.marginal_gain_threshold}, using default {MARGINAL_GAIN_THRESHOLD}")
            self.marginal_gain_threshold = MARGINAL_GAIN_THRESHOLD


def should_stop(
    section_id: str,
    history: list[float],
    rule: EarlyStopRule,
    cumulative_cost: int | None = None,
) -> tuple[bool, str]:
    """停止判定。(停止するか, 停止理由) を返す。

    4条件:
    1. quality_reached: history[-1] >= quality_threshold
    2. plateau: 直近 plateau_count 回の改善なし
    3. budget_reached: cumulative_cost >= budget_limit
    4. diminishing_returns: history[-1] - history[-2] < marginal_gain_threshold

    history が空または1件以下 -> 停止しない。
    例外発生時 -> 停止せず続行（安全側）。
    """
    try:
        # NaN/Inf を除去
        clean_history = [h for h in history if isinstance(h, (int, float)) and math.isfinite(h)]

        if len(clean_history) <= 1:
            return False, ""

        # 1. Budget check (最優先)
        if rule.budget_limit is not None and cumulative_cost is not None:
            if cumulative_cost >= rule.budget_limit:
                reason = "budget_reached"
                logger.info(f"{section_id}: stopped ({reason}), final_score={clean_history[-1]:.3f}")
                return True, reason

        # 2. Quality reached
        if clean_history[-1] >= rule.quality_threshold:
            reason = "quality_reached"
            logger.info(f"{section_id}: stopped ({reason}), final_score={clean_history[-1]:.3f}")
            return True, reason

        # 3. Plateau detection
        if len(clean_history) >= rule.plateau_count + 1:
            recent = clean_history[-(rule.plateau_count + 1):]
            no_improvement = all(recent[i + 1] <= recent[i] for i in range(len(recent) - 1))
            if no_improvement:
                reason = "plateau"
                logger.info(f"{section_id}: stopped ({reason}), final_score={clean_history[-1]:.3f}")
                return True, reason

        # 4. Diminishing returns
        gain = clean_history[-1] - clean_history[-2]
        if gain < rule.marginal_gain_threshold:
            reason = "diminishing_returns"
            logger.info(f"{section_id}: stopped ({reason}), final_score={clean_history[-1]:.3f}")
            return True, reason

        return False, ""

    except Exception as e:
        logger.error(f"Early stopping check failed for {section_id}: {e}")
        return False, ""
