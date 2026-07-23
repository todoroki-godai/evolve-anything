#!/usr/bin/env python3
"""採用前再評価（selection re-eval）— winner's curse 補正 (#234 PR2)

`run_loop.py` は複数variantを3軸LLM judgeで評価し、`best = max(variants,
key=lambda v: v["score"])` で最大スコアの variant を選ぶ。これは統計的に
winner's curse（複数候補から argmax で選ばれた値は選択バイアスにより真の値を
系統的に過大評価する）であり、1回評価の max をそのまま H_best に採用し続ける
とノイズによる見かけの改善が積み重なる「H_best 膨張ラチェット」が起きる。

本モジュールは、採用が決まった best variant を追加で n 回再評価し、その平均
（新規 n 回のみ。選択時に argmax で選ばれた単発値は混ぜない）を H_best 候補
として採用することでこの過大評価を補正する。

`run_loop.py` の `_score_variant_axes` / `_compute_verdict` / `_dominates` は
run_loop.py 内の private 関数（共有モジュール化されていない）のため、
run_loop.py への逆 import による循環 import を避けるべく呼び出し側から
関数として注入する（variant_generation.py が optimize_core という独立した
共有モジュールから import できたのとは事情が異なる）。
"""
import sys
from pathlib import Path
from typing import Any, Callable, Dict, Optional

# --- sys.path 設定（自己完結。run_loop.py 側の sys.path 設定に依存しない） ---
_plugin_root = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(_plugin_root / "scripts" / "lib"))

from score_noise import compute_stats  # noqa: E402


def selection_reeval(
    content: str,
    target_path: str,
    global_best_score: float,
    global_best_axes: Dict[str, float],
    n: int,
    epsilon: float,
    dry_run: bool,
    score_fn: Callable[[str, str, bool], Dict[str, float]],
    verdict_fn: Callable[[float, float], str],
    dominates_fn: Callable[..., bool],
) -> Dict[str, Any]:
    """選ばれた best variant を追加で n 回再評価し、winner's curse を補正する。

    平均は「新規 n 回のみ」の平均（選択時に argmax で選ばれた単発値は混ぜない）。
    格下げ後の verdict は verdict_fn で再判定する（STABLE 固定にしない。大外れ
    なら REGRESSED も自然に発火しうる）。mean_axes を使った Pareto 再チェックも
    行う（integrated 平均だけ補正して軸別スコアが単発のままだと一貫性がない
    ため。追加コストはゼロ、再評価で既に4軸の値が手に入っている）。
    """
    raw_axes = [score_fn(content, target_path, dry_run) for _ in range(n)]
    raw_scores = [a["integrated"] for a in raw_axes]
    stats = compute_stats(raw_scores)
    mean_axes = {
        axis: sum(a.get(axis, 0.0) for a in raw_axes) / n
        for axis in ("technical", "domain", "structure", "integrated")
    }
    improvement_after = stats["mean"] - global_best_score
    post_verdict = verdict_fn(improvement_after, epsilon)
    if post_verdict == "IMPROVED" and global_best_axes:
        if not dominates_fn(mean_axes, global_best_axes, tolerance=epsilon):
            post_verdict = "STABLE"
    return {
        "n": n,
        "raw_scores": raw_scores,
        "mean_score": stats["mean"],
        "std": stats["std"],
        "mean_axes": mean_axes,
        "improvement_after": improvement_after,
        "post_verdict": post_verdict,
        "downgraded": post_verdict != "IMPROVED",
    }


def run_selection_reeval_step(
    best: Dict[str, Any],
    target_path: str,
    global_best_score: float,
    global_best_axes: Dict[str, float],
    improvement: float,
    verdict: str,
    enabled: bool,
    n: int,
    epsilon: float,
    dry_run: bool,
    score_fn: Callable[[str, str, bool], Dict[str, float]],
    verdict_fn: Callable[[float, float], str],
    dominates_fn: Callable[..., bool],
):
    """Step 3.6 の呼び出し・print・best/improvement/verdict 上書きをまとめて行う。

    呼び出し側（run_loop.py）が「Pareto チェックの後・人間確認 Step4 の前」に
    置く前提。(a) 決定論・ゼロコストの Pareto 判定で先に STABLE 格下げが決まった
    候補は無駄な追加 LLM 呼び出しをしない (b) 人間確認の判断材料自体を補正済み
    の値にする、という2つの設計判断はこの呼び出し順に依存する。

    Returns:
        (best, improvement, verdict, pre_reeval_score, reeval_result)
    """
    pre_reeval_score = best["score"]
    reeval_result: Optional[Dict[str, Any]] = None
    if verdict == "IMPROVED" and enabled and n > 0:
        print(
            f"Step 3.6: 選定後再評価 — claude -p を追加 {n * 3} 回呼び出します。"
            f"無効化: --no-selection-reeval"
        )
        reeval_result = selection_reeval(
            best["content"], target_path, global_best_score, global_best_axes,
            n=n, epsilon=epsilon, dry_run=dry_run,
            score_fn=score_fn, verdict_fn=verdict_fn, dominates_fn=dominates_fn,
        )
        best["score"] = reeval_result["mean_score"]
        best["axes"] = reeval_result["mean_axes"]
        improvement = reeval_result["improvement_after"]
        verdict = reeval_result["post_verdict"]
        if reeval_result["downgraded"]:
            print(
                f"  再評価: 平均{reeval_result['mean_score']:.2f}"
                f"(単発{pre_reeval_score:.2f}) → {verdict}に格下げ"
            )
            # REGRESSED 時の pitfall 記録は下流の既存コード（verdict=="REGRESSED" を
            # 見て記録する Step 4 分岐）に委ねる。ここで重複記録しない。
        else:
            print(f"  再評価: 平均{reeval_result['mean_score']:.2f}(単発{pre_reeval_score:.2f}) → 改善維持")
    return best, improvement, verdict, pre_reeval_score, reeval_result


def build_reeval_fields(
    reeval_result: Optional[Dict[str, Any]],
    pre_reeval_score: float,
    enabled: bool,
    n: int,
) -> Dict[str, Any]:
    """loop_result へマージする selection_reeval 関連フィールドを組み立てる。"""
    return {
        "selection_reeval_enabled": enabled,
        "selection_reeval_ran": reeval_result is not None,
        "selection_reeval_n": n,
        "pre_reeval_score": pre_reeval_score,
        "selection_reeval_raw_scores": reeval_result["raw_scores"] if reeval_result else [],
        "selection_reeval_mean_score": reeval_result["mean_score"] if reeval_result else None,
        "selection_reeval_std": reeval_result["std"] if reeval_result else None,
        "selection_reeval_mean_axes": reeval_result["mean_axes"] if reeval_result else None,
        "selection_reeval_downgraded": reeval_result["downgraded"] if reeval_result else False,
    }


def format_reeval_tag(loop_result: Dict[str, Any]) -> str:
    """サマリー行に付与する再評価タグ文字列を返す（未発火なら空文字）。"""
    if not loop_result.get("selection_reeval_ran"):
        return ""
    return (
        f" (reeval: {loop_result['selection_reeval_mean_score']:.2f}"
        f"±{loop_result['selection_reeval_std']:.2f}, "
        f"単発{loop_result['pre_reeval_score']:.2f}から補正)"
    )
