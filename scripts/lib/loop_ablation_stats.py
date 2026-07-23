"""設計文脈あり/なし生成の比較較正実験 — 統計判定コア（#234 PR3）。

issue #234: harness 自動進化の改善が「探索予算増加（単純サンプリング）」由来か
「設計改善（corrections/context を使った誘導）」由来かを切り分けるための opt-in
較正実験。本モジュールは比較可能性判定・スコア比較・コスト見積もりの純粋関数のみを
持つ（LLM 非依存・mock 不要）。CLI・LLM 呼び出しのオーケストレーションは
skills/evolve-loop-orchestrator/scripts/loop_ablation.py 側。
"""
from typing import Any, Dict, List, Optional

from score_noise import compute_stats

# 空白差分だけを「差分あり」と誤判定しないための床。
MIN_PROMPT_DIFF_CHARS = 20

# サンプル数がこれ未満なら low_sample_size_caveat を立てる。
LOW_SAMPLE_SIZE_THRESHOLD = 5

AXES = ("technical", "domain", "structure", "integrated")

# LLM 呼び出し回数見積もり定数（生成 2n + 採点 6n = 計 8n 回、#234 PR3）。
_CHARS_PER_TOKEN = 4  # 粗い概算（日本語混在を考慮した保守値）
_GENERATION_OVERHEAD_TOKENS = 800  # 生成プロンプト定型部（corrections/context 込み）
_SCORING_OVERHEAD_TOKENS = 300  # 軸別採点プロンプト定型部


def assess_comparability(
    designed_prompt: str,
    naive_prompt: str,
    corrections: List[Dict[str, Any]],
    context: Dict[str, Any],
) -> Dict[str, Any]:
    """designed/naive プロンプトが実質同一かを判定する。

    corrections 件数などの間接指標でなく、実際に組み立てた2プロンプト文字列の
    差分文字数で判定する（`determine_strategy("auto", [])` が corrections 空でも
    llm_improve に自動フォールバックし non-trivial なプロンプトを組み立てるため、
    corrections 件数だけでは正確に判定できない）。
    """
    diff_chars = abs(len(designed_prompt) - len(naive_prompt))
    comparable = diff_chars >= MIN_PROMPT_DIFF_CHARS
    return {
        "comparable": comparable,
        "prompt_diff_chars": diff_chars,
        "corrections_count": len(corrections),
        "context_signals": [
            k for k in ("workflow_hint", "audit_issues", "pitfalls") if context.get(k)
        ],
        "reason": (
            None
            if comparable
            else "designed prompt と naive prompt が実質同一（corrections/context シグナルなし）"
        ),
    }


def _axis_verdict(d_stats: Dict[str, float], n_stats: Dict[str, float], epsilon: float):
    """designed/naive 1軸分の統計から verdict/delta/overlap を導出する。"""
    delta = round(d_stats["mean"] - n_stats["mean"], 4)
    d_lower, d_upper = d_stats["mean"] - d_stats["std"], d_stats["mean"] + d_stats["std"]
    n_lower, n_upper = n_stats["mean"] - n_stats["std"], n_stats["mean"] + n_stats["std"]
    overlap = not (d_lower > n_upper or n_lower > d_upper)

    if abs(delta) <= epsilon:
        verdict = "inconclusive"
    elif overlap:
        # 平均差はあるが分散が大きく信頼帯が重なる → 判定を保留する
        verdict = "inconclusive"
    elif delta > 0:
        verdict = "designed_wins"
    else:
        verdict = "naive_wins"

    return verdict, delta, overlap


def compare_ablation_scores(
    designed_scores: Dict[str, List[float]],
    naive_scores: Dict[str, List[float]],
    epsilon: float,
) -> Dict[str, Any]:
    """designed/naive 両条件のスコア群を軸別に比較する。

    Args:
        designed_scores: {"technical": [...], "domain": [...], "structure": [...], "integrated": [...]}
        naive_scores: designed_scores と同形。
        epsilon: 改善/劣化の判定閾値（呼び出し側が run_loop.SCORE_EPSILON を渡す想定。
            新しい閾値をここで発明しない）。

    Returns:
        {
            "verdict": "designed_wins" | "inconclusive" | "naive_wins"（integrated 軸基準）,
            "naive_wins_warning": bool,
            "low_sample_size_caveat": bool,
            "n": int,
            "axes": {axis: {"verdict", "delta", "overlap", "designed_stats", "naive_stats"}},
        }
    """
    axes_result: Dict[str, Any] = {}
    for axis in AXES:
        d_list = designed_scores.get(axis) or []
        n_list = naive_scores.get(axis) or []
        if not d_list or not n_list:
            axes_result[axis] = {
                "verdict": "inconclusive",
                "reason": "insufficient_data",
                "delta": None,
                "overlap": None,
                "designed_stats": None,
                "naive_stats": None,
            }
            continue
        d_stats = compute_stats(d_list)
        n_stats = compute_stats(n_list)
        verdict, delta, overlap = _axis_verdict(d_stats, n_stats, epsilon)
        axes_result[axis] = {
            "verdict": verdict,
            "delta": delta,
            "overlap": overlap,
            "designed_stats": d_stats,
            "naive_stats": n_stats,
        }

    overall_verdict = axes_result.get("integrated", {}).get("verdict", "inconclusive")

    all_lens = [len(v) for v in designed_scores.values() if v]
    n = max(all_lens) if all_lens else 0

    return {
        "verdict": overall_verdict,
        "naive_wins_warning": overall_verdict == "naive_wins",
        "low_sample_size_caveat": n < LOW_SAMPLE_SIZE_THRESHOLD,
        "n": n,
        "axes": axes_result,
    }


def estimate_ablation_cost(content_length: int, n: int) -> Dict[str, Any]:
    """コスト見積もり。生成2n回・採点6n回=計8n回の LLM 呼び出しを対象ファイル文字数駆動で見積もる。"""
    generation_calls = 2 * n
    scoring_calls = 6 * n
    total_calls = generation_calls + scoring_calls

    content_tokens = content_length // _CHARS_PER_TOKEN
    est_generation_tokens = generation_calls * (content_tokens + _GENERATION_OVERHEAD_TOKENS)
    est_scoring_tokens = scoring_calls * (content_tokens + _SCORING_OVERHEAD_TOKENS)
    est_total_tokens = est_generation_tokens + est_scoring_tokens

    return {
        "n": n,
        "content_length": content_length,
        "generation_calls": generation_calls,
        "scoring_calls": scoring_calls,
        "total_calls": total_calls,
        "est_total_tokens": est_total_tokens,
    }
