"""採点ノイズ計測ユーティリティ。

同一スキルを N 回採点し、軸別スコアの標準偏差を算出する。
H_best 比較の epsilon 推奨値を出力する。
"""

import re
import statistics
import subprocess
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, List

from scorer_prompts import DEFAULT_AXIS_WEIGHTS as AXIS_WEIGHTS, get_axis_prompts

FALLBACK_SCORE = 0.5


def compute_stats(scores: List[float]) -> Dict:
    """スコアリストから統計量を算出する。"""
    n = len(scores)
    mean = statistics.mean(scores)
    std = statistics.stdev(scores) if n > 1 else 0.0
    return {
        "mean": round(mean, 4),
        "std": round(std, 4),
        "min": round(min(scores), 4),
        "max": round(max(scores), 4),
        "n": n,
    }


def recommend_epsilon(stats: Dict) -> float:
    """2σ を epsilon として推奨する（下限 0.02、上限 0.15）。"""
    epsilon = stats["std"] * 2
    return round(max(0.02, min(0.15, epsilon)), 3)


def aggregate_runs(runs: List[Dict[str, float]]) -> Dict[str, Dict]:
    """N 回分のスコア結果を軸別に集約して統計を返す。

    runs が空リストの場合は空 dict を返す。
    """
    if not runs:
        return {}
    axes = list(runs[0].keys())
    result = {}
    for axis in axes:
        scores = [r[axis] for r in runs]
        result[axis] = compute_stats(scores)
    return result


def _run_claude_prompt(prompt_str: str, max_retries: int = 2) -> float:
    """フォーマット済みプロンプトを claude -p に渡しスコアを返す。失敗時はリトライ。"""
    for _ in range(max_retries + 1):
        try:
            result = subprocess.run(
                ["claude", "-p", "--output-format", "text"],
                input=prompt_str,
                capture_output=True,
                text=True,
                timeout=60,
            )
            if result.returncode == 0:
                match = re.search(r"(0\.\d+|1\.0|0|1)", result.stdout.strip())
                if match:
                    return float(match.group(1))
        except subprocess.TimeoutExpired:
            continue
        except FileNotFoundError:
            break
    return FALLBACK_SCORE


def _score_single_axis(axis: str, content: str, max_retries: int = 2) -> float:
    """単一軸のスコアを claude -p で取得する。失敗時は max_retries 回リトライ。"""
    prompt = get_axis_prompts()[axis].format(content=content)
    return _run_claude_prompt(prompt, max_retries)


def _run_once(content: str) -> Dict[str, float]:
    """3軸を並列スコアリングして1回分の結果を返す。"""
    axis_scores: Dict[str, float] = {}
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {
            executor.submit(_score_single_axis, axis, content): axis
            for axis in AXIS_WEIGHTS
        }
        for future in as_completed(futures):
            axis = futures[future]
            try:
                axis_scores[axis] = future.result()
            except Exception as exc:
                warnings.warn(f"Axis {axis} scoring failed: {exc}")
                axis_scores[axis] = FALLBACK_SCORE

    integrated = sum(
        axis_scores.get(axis, FALLBACK_SCORE) * weight
        for axis, weight in AXIS_WEIGHTS.items()
    )
    axis_scores["integrated"] = round(integrated, 4)
    return axis_scores


def compare_prompt_versions(
    a_runs: List[Dict[str, float]],
    b_runs: List[Dict[str, float]],
    drift_threshold: float = 0.05,
    sigma_improvement_threshold: float = 0.005,
) -> Dict:
    """A/B 2 つのプロンプトバージョンの計測結果を比較する。

    Args:
        a_runs: バージョン A の N 回計測結果
        b_runs: バージョン B の N 回計測結果
        drift_threshold: 平均ドリフトの許容閾値（これを超えると警告）
        sigma_improvement_threshold: σ 改善幅の最小値（これ未満なら "tie"）

    Returns:
        {
            "a": {"stats": ...}, "b": {"stats": ...},
            "mean_drift": float,         # |mean_b - mean_a|
            "sigma_delta": float,        # std_b - std_a（負なら B のノイズが小さい）
            "mean_drift_warning": bool,  # drift_threshold 超過か
            "recommended": "a" | "b" | "tie",
        }
    """
    a_stats = aggregate_runs(a_runs)
    b_stats = aggregate_runs(b_runs)

    mean_a = a_stats["integrated"]["mean"]
    mean_b = b_stats["integrated"]["mean"]
    std_a = a_stats["integrated"]["std"]
    std_b = b_stats["integrated"]["std"]

    mean_drift = abs(mean_b - mean_a)
    sigma_delta = std_b - std_a
    drift_warning = mean_drift > drift_threshold

    if drift_warning:
        # 平均が大きくドリフトしている場合、採点基準が変わってしまっているので
        # σ 改善があっても B 採用は危険
        recommended = "a"
    elif sigma_delta < -sigma_improvement_threshold:
        recommended = "b"  # B のノイズが有意に小さい
    elif sigma_delta > sigma_improvement_threshold:
        recommended = "a"  # A のノイズが有意に小さい
    else:
        recommended = "tie"

    return {
        "a": {"runs": a_runs, "stats": a_stats},
        "b": {"runs": b_runs, "stats": b_stats},
        "mean_drift": round(mean_drift, 4),
        "sigma_delta": round(sigma_delta, 4),
        "mean_drift_warning": drift_warning,
        "recommended": recommended,
    }


def measure_noise(target_path: str, runs: int = 5) -> Dict:
    """同一ファイルを runs 回採点してノイズ統計を返す。

    Returns:
        {
            "target": str,
            "runs": int,
            "raw": List[Dict],          # 各回のスコア
            "stats": Dict[str, Dict],   # 軸別統計
            "recommended_epsilon": float,
        }
    """
    content = Path(target_path).read_text(encoding="utf-8")
    raw: List[Dict[str, float]] = []

    for i in range(runs):
        print(f"  採点 {i + 1}/{runs}...", flush=True)
        raw.append(_run_once(content))

    stats = aggregate_runs(raw)
    epsilon = recommend_epsilon(stats["integrated"])

    return {
        "target": target_path,
        "runs": runs,
        "raw": raw,
        "stats": stats,
        "recommended_epsilon": epsilon,
    }
