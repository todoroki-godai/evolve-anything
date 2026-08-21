"""Fixed-corpus correction capture metrics shared by audit and the A0 harness."""
from __future__ import annotations

from math import sqrt
from typing import Any, Callable, Iterable


def wilson_interval(successes: int, total: int, z: float = 1.96) -> tuple[float, float]:
    if total == 0:
        return (0.0, 0.0)
    rate = successes / total
    denom = 1 + z**2 / total
    center = (rate + z**2 / (2 * total)) / denom
    half = z * sqrt(rate * (1 - rate) / total + z**2 / (4 * total**2)) / denom
    return (max(0.0, center - half), min(1.0, center + half))


def evaluate_capture_recall(
    examples: Iterable[dict[str, Any]],
    detector: Callable[[str], Any],
    include_message: Callable[[str], bool] | None = None,
) -> dict[str, Any]:
    rows = list(examples)
    positives = caught = hits = true_hits = 0
    for row in rows:
        text = row["text"]
        is_positive = row["label"] == "TP"
        if is_positive:
            positives += 1
        hit = bool((include_message is None or include_message(text)) and detector(text) is not None)
        caught += int(is_positive and hit)
        hits += int(hit)
        true_hits += int(is_positive and hit)
    recall_ci = wilson_interval(caught, positives)
    precision_ci = wilson_interval(true_hits, hits)
    return {
        "examples": len(rows), "positives": positives, "caught": caught, "hits": hits,
        "recall": caught / positives if positives else None,
        "precision": true_hits / hits if hits else None,
        "recall_ci": recall_ci, "precision_ci": precision_ci,
    }
