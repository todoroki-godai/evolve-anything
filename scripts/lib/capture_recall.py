"""Fixed-corpus correction capture metrics shared by audit and the A0 harness."""
from __future__ import annotations

import hashlib
import json
from math import sqrt
from pathlib import Path
from typing import Any, Callable, Iterable


EXPECTED_EVAL_ROWS = 416
EXPECTED_EVAL_SHA256 = "6a65520ba6ede89842fa4bdedb38a89ec70346dda55fdae2718fffbbf575d01e"


class CaptureEvalIntegrityError(ValueError):
    """The frozen capture-evaluation corpus is not the approved artifact."""


def load_capture_eval_set(path: Path) -> list[dict[str, Any]]:
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != EXPECTED_EVAL_SHA256:
        raise CaptureEvalIntegrityError("capture evaluation corpus hash mismatch")
    rows = [json.loads(line) for line in raw.decode("utf-8").splitlines() if line.strip()]
    if len(rows) != EXPECTED_EVAL_ROWS:
        raise CaptureEvalIntegrityError("capture evaluation corpus row count mismatch")
    return rows


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
    positives = caught = hits = 0
    for row in rows:
        text = row.get("text")
        label = row.get("label")
        if not isinstance(text, str):
            raise ValueError("capture evaluation text must be a string")
        if label not in {"TP", "not_TP"}:
            raise ValueError("capture evaluation label must be TP or not_TP")
        is_positive = label == "TP"
        if is_positive:
            positives += 1
        hit = bool((include_message is None or include_message(text)) and detector(text) is not None)
        caught += int(is_positive and hit)
        hits += int(hit)
    recall_ci = wilson_interval(caught, positives)
    precision_ci = wilson_interval(caught, hits)
    return {
        "examples": len(rows), "positives": positives, "caught": caught, "hits": hits,
        "recall": caught / positives if positives else None,
        "precision": caught / hits if hits else None,
        "recall_ci": recall_ci, "precision_ci": precision_ci,
    }
