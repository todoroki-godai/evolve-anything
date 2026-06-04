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

from llm_broker import (  # noqa: F401  (parse_score は後方互換のため re-export)
    FALLBACK_SCORE,
    build_requests,
    parse_responses,
    parse_score,
)
from scorer_prompts import DEFAULT_AXIS_WEIGHTS as AXIS_WEIGHTS, get_axis_prompts
from scorer_schema import ConfidenceInterval


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


def to_confidence_interval(stats: Dict) -> ConfidenceInterval:
    """compute_stats() の返り値から ConfidenceInterval を組み立てる。

    スコアが 1 件のみの場合は std=0.0、lower==upper==mean、n=1 となる。

    Args:
        stats: compute_stats() が返した dict（mean/std/n を含む）

    Returns:
        ConfidenceInterval インスタンス
    """
    mean = stats["mean"]
    std = stats["std"]
    return ConfidenceInterval(
        mean=mean,
        std=std,
        lower=round(mean - std, 4),
        upper=round(mean + std, 4),
        n=stats["n"],
    )


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


def build_scoring_requests(content: str, runs: int = 5) -> List[Dict]:
    """採点リクエスト一覧を決定論で生成する（LLM ゼロ＝Phase A: 前処理）。

    claude -p を呼ばず、「何を採点すべきか」だけを JSON 化可能な形で返す。
    Phase B（assistant が Task/インラインで採点）が各 prompt を 0.0〜1.0 で採点し、
    id をキーにした responses を作る。Phase C（aggregate_from_responses）が集約する。

    共通基盤 ``llm_broker.build_requests`` を使い、run/axis は meta 経由で持たせた上で
    後方互換のフラット形 ``{"id", "run", "axis", "prompt"}`` に展開して返す。

    Returns:
        List[{"id": "r{run}:{axis}", "run": int, "axis": str, "prompt": str}]
    """
    prompts = get_axis_prompts()
    items = [
        {"id": f"r{run_idx}:{axis}", "run": run_idx, "axis": axis}
        for run_idx in range(runs)
        for axis in AXIS_WEIGHTS
    ]
    broker_reqs = build_requests(
        items, lambda it: prompts[it["axis"]].format(content=content)
    )
    return [
        {
            "id": r["id"],
            "run": r["meta"]["run"],
            "axis": r["meta"]["axis"],
            "prompt": r["prompt"],
        }
        for r in broker_reqs
    ]


def aggregate_from_responses(
    requests: List[Dict], responses: Dict[str, object], runs: int
) -> Dict:
    """Phase B の採点結果を集約して measure_noise と同形の結果を返す（LLM ゼロ＝Phase C: ゲート）。

    Args:
        requests: build_scoring_requests の出力（id→run/axis の対応）
        responses: {request_id: score}。値は float でも生テキストでも可（parse_score で吸収）。
            欠損 id は FALLBACK_SCORE で穴埋めする（assistant の採点漏れで壊さない）。
        runs: 計測回数

    Returns:
        {"runs", "raw", "stats", "recommended_epsilon"}（measure_noise と同形、target は持たない）
    """
    # Phase C: 応答を broker で正規化（欠損 id は parse_score(None)=FALLBACK で穴埋め）
    parsed = parse_responses(requests, responses, parser=parse_score)
    # id→(run,axis) の対応は requests を単一ソースとして使う（id 形式の重複定義を避ける）
    raw: List[Dict[str, float]] = [{} for _ in range(runs)]
    for req in requests:
        run_idx = req["run"]
        if not (0 <= run_idx < runs):
            continue
        raw[run_idx][req["axis"]] = parsed[req["id"]]

    for axis_scores in raw:
        # assistant の採点漏れ（欠損軸）は FALLBACK_SCORE で穴埋め
        for axis in AXIS_WEIGHTS:
            axis_scores.setdefault(axis, FALLBACK_SCORE)
        integrated = sum(
            axis_scores[axis] * weight for axis, weight in AXIS_WEIGHTS.items()
        )
        axis_scores["integrated"] = round(integrated, 4)

    stats = aggregate_runs(raw)
    epsilon = recommend_epsilon(stats["integrated"]) if stats else 0.02
    return {
        "runs": runs,
        "raw": raw,
        "stats": stats,
        "recommended_epsilon": epsilon,
    }


def _run_claude_prompt(prompt_str: str, max_retries: int = 2) -> float:
    """フォーマット済みプロンプトを claude -p に渡しスコアを返す。失敗時はリトライ。

    DEPRECATED（[ADR-037]）: claude -p 全廃に向け、新規経路は
    build_scoring_requests + aggregate_from_responses（ファイルベース2相）を使う。
    既存 CLI（bin/rl-prompt-compare）との後方互換のため当面は残置。
    """
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
                # マッチがあるときだけ確定。無ければ retry に回す（従来挙動を維持）
                if re.search(r"(0\.\d+|1\.0|0|1)", result.stdout.strip()):
                    return parse_score(result.stdout)
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


def main(argv=None) -> int:
    """claude -p 全廃のファイルベース2相 CLI（[ADR-037] PoC）。

    Phase A（前処理・LLM ゼロ）:
        score_noise.py --emit-requests <target> [--runs N]
        → 採点リクエスト JSON を stdout に出力。assistant がこれを読み、
          各 prompt を Task/インラインで 0.0〜1.0 採点し responses.json を作る。

    Phase C（ゲート・LLM ゼロ）:
        score_noise.py --aggregate <responses.json> --requests <requests.json> [--runs N]
        → responses を集約してノイズ統計 JSON を stdout に出力。

    claude -p は一切呼ばない。LLM 作業（Phase B）は assistant 側に分離される。
    """
    import argparse
    import json

    parser = argparse.ArgumentParser(description="score_noise file-based 2-phase (ADR-037)")
    parser.add_argument("--emit-requests", metavar="TARGET", help="Phase A: 採点リクエスト生成")
    parser.add_argument("--aggregate", metavar="RESPONSES_JSON", help="Phase C: 採点結果を集約")
    parser.add_argument("--requests", metavar="REQUESTS_JSON", help="Phase C 用 requests JSON")
    parser.add_argument("--runs", type=int, default=5)
    args = parser.parse_args(argv)

    if args.emit_requests:
        content = Path(args.emit_requests).read_text(encoding="utf-8")
        requests = build_scoring_requests(content, runs=args.runs)
        print(
            json.dumps(
                {"target": args.emit_requests, "runs": args.runs, "requests": requests},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    if args.aggregate:
        if not args.requests:
            parser.error("--aggregate には --requests が必要です")
        responses = json.loads(Path(args.aggregate).read_text(encoding="utf-8"))
        req_doc = json.loads(Path(args.requests).read_text(encoding="utf-8"))
        requests = req_doc["requests"] if isinstance(req_doc, dict) else req_doc
        runs = req_doc.get("runs", args.runs) if isinstance(req_doc, dict) else args.runs
        result = aggregate_from_responses(requests, responses, runs=runs)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    parser.error("--emit-requests または --aggregate を指定してください")
    return 2


if __name__ == "__main__":
    import sys

    sys.exit(main())
