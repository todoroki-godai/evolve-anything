"""Fixed-corpus correction capture metrics shared by audit and the A0 harness."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
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


_REMEASURE = "python3 scripts/bench/judge_eval.py --run --approve-harness --variant <新しい名前>"


def evaluate_capture_union(
    eval_rows: Iterable[dict[str, Any]], result_rows: Iterable[dict[str, Any]],
    harness_sha: str, model: str, batch_size: int,
) -> dict[str, Any]:
    """Advisory only: detect known ID/content/provenance mismatches, then count both lanes."""
    def unavailable(reason: str) -> dict[str, Any]:
        return {"measured": False, "reason": f"{reason}。再測: {_REMEASURE}"}

    examples, results = list(eval_rows), list(result_rows)
    eval_by_id: dict[str, dict[str, Any]] = {}
    for row in examples:
        ident, body = row.get("eval_id"), row.get("text")
        if not isinstance(ident, str) or not ident or ident in eval_by_id:
            return unavailable("評価 ID の欠落・重複")
        if not isinstance(body, str) or row.get("label") not in ("TP", "not_TP"):
            return unavailable("評価行の本文・ラベル不正")
        eval_by_id[ident] = row
    result_by_id: dict[str, dict[str, Any]] = {}
    timestamps: list[str] = []
    for result in results:
        ident = result.get("prompt_id")
        if not isinstance(ident, str) or not ident or ident in result_by_id:
            return unavailable("判定 ID の欠落・重複")
        result_by_id[ident] = result
        meta = result.get("meta")
        if not isinstance(meta, dict) or result.get("status") != "ok" or type(meta.get("rep")) is not int or meta["rep"] != 0:
            return unavailable("判定行の状態・rep 不正")
        if not isinstance(meta.get("expected"), bool) or not isinstance(meta.get("predicted"), bool):
            return unavailable("判定値は bool 必須")
        if (meta.get("harness_sha"), meta.get("model"), meta.get("batch_size_config")) != (harness_sha, model, batch_size):
            return unavailable("AI 判定の来歴が現行条件と不一致")
        timestamp = meta.get("generated_at")
        try:
            parsed = datetime.fromisoformat(timestamp)
            if parsed.utcoffset() != timedelta(0):
                raise ValueError("UTC required")
        except (TypeError, ValueError, AttributeError):
            return unavailable("AI 判定の生成日時が不明")
        timestamps.append(timestamp)
    if not examples or set(eval_by_id) != set(result_by_id):
        return unavailable("評価 ID と判定 ID が一対一でない")

    from rl_common import detection
    positives = caught = regex_caught = judge_caught = hits = 0
    for ident, row in eval_by_id.items():
        meta = result_by_id[ident]["meta"]
        if (meta.get("prompt_sha256") != hashlib.sha256(row["text"].encode("utf-8")).hexdigest()
                or meta["expected"] != (row["label"] == "TP")):
            return unavailable("同一 ID の本文・ラベル不一致")
        positive = row["label"] == "TP"
        regex = detection.should_include_message(row["text"]) and detection._detect_correction(
            row["text"], false_positive_hashes=()
        ) is not None
        judge = meta["predicted"]
        positives += positive
        caught += bool(positive and (regex or judge))
        regex_caught += bool(positive and regex)
        judge_caught += bool(positive and judge)
        hits += bool(regex or judge)
    if not positives or not hits:
        return unavailable("合計の分母・検出数が不足")
    return {
        "measured": True, "caught": caught, "positives": positives,
        "regex_caught": regex_caught, "judge_caught": judge_caught,
        "hits": hits, "recall": caught / positives, "precision": caught / hits,
        "recall_ci": wilson_interval(caught, positives),
        "generated_at": min(timestamps) if min(timestamps) == max(timestamps)
                        else f"{min(timestamps)}〜{max(timestamps)}",
        "harness_sha": harness_sha, "model": model,
    }


def load_capture_union(eval_candidates: Iterable[Path], results_path: Path) -> dict[str, Any]:
    """Reuse the frozen corpus loader and candidate order; never write either artifact."""
    import sys
    bench_dir = Path(__file__).resolve().parents[1] / "bench"
    if str(bench_dir) not in sys.path:
        sys.path.insert(0, str(bench_dir))
    import judge_eval

    rows = None
    for candidate in eval_candidates:
        if candidate.exists():
            try:
                rows = load_capture_eval_set(candidate)
                break
            except (CaptureEvalIntegrityError, OSError, ValueError):
                continue
    if rows is None:
        return {"measured": False, "reason": "評価セットなし・不一致", "display": results_path.exists()}
    if not results_path.exists():
        return {"measured": False, "reason": f"AI 判定結果なし。再測: {_REMEASURE}"}
    try:
        results = [json.loads(line) for line in results_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except (OSError, ValueError):
        return {"measured": False, "reason": "AI 判定結果の読込失敗"}
    return evaluate_capture_union(rows, results, judge_eval.compute_harness_sha(),
                                  judge_eval.RunConfig().model, judge_eval.DEFAULT_BATCH_SIZE)
