"""Bandit Section Selector: Thompson Sampling ベースのセクション優先度付け"""
from __future__ import annotations

import json
import logging
import random
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)

LOO_ALPHA_SCALE: float = 5.0


class BanditSectionSelector:
    """Thompson Sampling で改善余地の大きいセクションを動的選択する。"""

    def __init__(self, section_ids: list[str]):
        """各セクションに Beta(1, 1)（一様事前分布）を初期化。"""
        self.alpha: dict[str, float] = {sid: 1.0 for sid in section_ids}
        self.beta: dict[str, float] = {sid: 1.0 for sid in section_ids}

    def initialize_from_importance(
        self, scores: dict[str, float], scale: float = LOO_ALPHA_SCALE
    ):
        """LOO 重要度スコアを alpha の初期値に反映。

        負のスコアは 0 にクランプ。正規化後 alpha = 1.0 + normalized * scale。
        """
        if not scores:
            return
        max_score = max(scores.values())
        if max_score <= 0:
            return
        for sid, score in scores.items():
            if sid in self.alpha:
                normalized = max(0.0, score / max_score)
                self.alpha[sid] = 1.0 + normalized * scale

    def select_top_k(self, k: int) -> list[str]:
        """Thompson Sampling で上位k件のセクションIDを返す。

        k がセクション数以上の場合は全セクションを返す。
        sampling 例外時は一様ランダム選択にフォールバック。
        """
        n = len(self.alpha)
        if k >= n:
            return list(self.alpha.keys())

        try:
            samples = {}
            for sid in self.alpha:
                samples[sid] = random.betavariate(self.alpha[sid], self.beta[sid])
            ranked = sorted(samples, key=lambda x: samples[x], reverse=True)
            return ranked[:k]
        except Exception:
            logger.warning(
                "Thompson Sampling failed, falling back to random selection"
            )
            all_ids = list(self.alpha.keys())
            random.shuffle(all_ids)
            return all_ids[:k]

    def update(self, section_id: str, improved: bool):
        """最適化結果に基づいて Beta 分布を更新。"""
        if section_id not in self.alpha:
            logger.warning(f"Unknown section_id: {section_id}")
            return
        if improved:
            self.alpha[section_id] += 1.0
        else:
            self.beta[section_id] += 1.0

    def get_state(self) -> dict[str, tuple[float, float]]:
        """全セクションの (alpha, beta) を返す（永続化用）。"""
        return {sid: (self.alpha[sid], self.beta[sid]) for sid in self.alpha}

    def save_state(self, output_dir: str | Path):
        """alpha/beta を bandit_state.json に永続化。"""
        path = Path(output_dir) / "bandit_state.json"
        state = {sid: [a, b] for sid, (a, b) in self.get_state().items()}
        path.write_text(json.dumps(state, indent=2))

    @classmethod
    def load_state(
        cls, output_dir: str | Path, section_ids: list[str]
    ) -> "BanditSectionSelector":
        """bandit_state.json から状態を復元。失敗時は Beta(1,1) で新規開始。"""
        path = Path(output_dir) / "bandit_state.json"
        selector = cls(section_ids)
        try:
            if path.exists():
                data = json.loads(path.read_text())
                for sid in section_ids:
                    if sid in data:
                        selector.alpha[sid] = float(data[sid][0])
                        selector.beta[sid] = float(data[sid][1])
        except Exception:
            logger.warning(f"Failed to load {path}, starting fresh with Beta(1,1)")
        return selector


def estimate_importance(
    sections: list[dict],  # list of {"id": str, "content": str}
    evaluator: Callable[[str], float],
    full_content: str,
) -> dict[str, float]:
    """Leave-One-Out ablation で各セクションの重要度を推定。

    N+1 コール: ベースライン1回 + 各セクション除外N回。
    evaluator 失敗時は空辞書を返す（呼び出し元で Beta(1,1) 続行）。
    """
    try:
        baseline = evaluator(full_content)
    except Exception:
        logger.warning("LOO baseline evaluation failed, skipping importance estimation")
        return {}

    importance: dict[str, float] = {}
    for section in sections:
        sid = section["id"]
        remaining = [s for s in sections if s["id"] != sid]
        ablated_content = "\n".join(s["content"] for s in remaining)
        try:
            score_without = evaluator(ablated_content)
            importance[sid] = baseline - score_without
        except Exception:
            logger.warning(
                f"LOO evaluation failed for section {sid}, assigning importance 0.0"
            )
            importance[sid] = 0.0

    return importance
