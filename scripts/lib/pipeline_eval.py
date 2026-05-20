"""スキル生成3型比較評価フレームワーク。

型1（パターン抽出型）と型2（プロンプト最適化型）を横断比較する。
LLM 呼び出しは一切行わない（pure 計算のみ）。

公開クラス:
  EvalResult       -- 1パイプラインの評価結果
  ComparisonReport -- 複数パイプラインの比較レポート
  PipelineEvalRunner -- 評価・比較を実行するランナー
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

try:
    from evolution_memory import load_patterns
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from evolution_memory import load_patterns


# ── データクラス ──────────────────────────────────────────

@dataclass
class EvalResult:
    """1パイプラインの評価結果。

    Attributes:
        pipeline_type:      "pattern_extraction" | "prompt_optimization"
        skill_name:         評価対象のスキル名。
        trigger_precision:  TP / (TP + FP)。FP+TP=0 のとき 0.0。
        trigger_recall:     TP / (TP + FN)。TP+FN=0 のとき 0.0。
        convergence_cycles: 型2のみ有効（パターン件数）。型1は 0。
        eval_count:         評価に使ったクエリ数。
        details:            追加情報（tp/fp/fn, pattern_count 等）。
    """
    pipeline_type: Literal["pattern_extraction", "prompt_optimization"]
    skill_name: str
    trigger_precision: float
    trigger_recall: float
    convergence_cycles: int
    eval_count: int
    details: dict = field(default_factory=dict)


@dataclass
class ComparisonReport:
    """複数パイプラインの比較レポート。

    Attributes:
        skill_name: 対象スキル名。
        results:    比較対象の EvalResult リスト。
        winner:     precision + recall の合計が最も高い pipeline_type。
        summary:    人間可読な1行サマリ。
    """
    skill_name: str
    results: list[EvalResult]
    winner: str
    summary: str


# ── ランナー ──────────────────────────────────────────────

class PipelineEvalRunner:
    """型1・型2パイプラインを評価し比較するランナー。

    LLM を呼ばない。すべての計算は入力データと既存ファイルから行う。
    """

    # ── 型1: パターン抽出型 ──────────────────────────────

    def run_pattern_extraction(
        self,
        skill_name: str,
        eval_set: list[dict[str, Any]],
    ) -> EvalResult:
        """型1: eval_set の should_trigger ラベルを正解とした precision/recall を計算。

        eval_set の各エントリは以下の形式を期待する:
          {"query": "...", "should_trigger": True, "predicted_trigger": True}

        ``predicted_trigger`` がない場合は ``should_trigger`` と同じとみなす（後方互換）。

        計算式:
          - TP = should_trigger=True  かつ predicted_trigger=True  の件数
          - FP = should_trigger=False かつ predicted_trigger=True  の件数（誤検知）
          - FN = should_trigger=True  かつ predicted_trigger=False の件数（見逃し）
          - precision = TP / (TP + FP)、TP+FP=0 のとき 0.0
          - recall    = TP / (TP + FN)、TP+FN=0 のとき 0.0

        全 should_trigger=False かつ predicted_trigger=False のときは
        TP=FP=FN=0 → precision=recall=0.0。
        """
        tp = sum(
            1 for e in eval_set
            if e.get("should_trigger") and e.get("predicted_trigger", e.get("should_trigger"))
        )
        fp = sum(
            1 for e in eval_set
            if not e.get("should_trigger") and e.get("predicted_trigger", False)
        )
        fn = sum(
            1 for e in eval_set
            if e.get("should_trigger") and not e.get("predicted_trigger", e.get("should_trigger"))
        )

        precision = _safe_divide(tp, tp + fp)
        recall = _safe_divide(tp, tp + fn)

        return EvalResult(
            pipeline_type="pattern_extraction",
            skill_name=skill_name,
            trigger_precision=precision,
            trigger_recall=recall,
            convergence_cycles=0,
            eval_count=len(eval_set),
            details={"tp": tp, "fp": fp, "fn": fn},
        )

    # ── 型2: プロンプト最適化型 ──────────────────────────

    def run_prompt_optimization(
        self,
        skill_name: str,
        eval_set: list[dict[str, Any]],
    ) -> EvalResult:
        """型2: evolution_memory のパターン数から収束サイクルを推定し評価する。

        precision/recall は型1と同じ計算（eval_set のラベルを正解とする）。
        convergence_cycles = load_patterns で取得できたパターン件数。
        パターンが多いほど最適化が進んでいると解釈する。
        """
        patterns = load_patterns(skill_name, limit=1000)
        convergence_cycles = len(patterns)

        tp = sum(
            1 for e in eval_set
            if e.get("should_trigger") and e.get("predicted_trigger", e.get("should_trigger"))
        )
        fp = sum(
            1 for e in eval_set
            if not e.get("should_trigger") and e.get("predicted_trigger", False)
        )
        fn = sum(
            1 for e in eval_set
            if e.get("should_trigger") and not e.get("predicted_trigger", e.get("should_trigger"))
        )

        precision = _safe_divide(tp, tp + fp)
        recall = _safe_divide(tp, tp + fn)

        return EvalResult(
            pipeline_type="prompt_optimization",
            skill_name=skill_name,
            trigger_precision=precision,
            trigger_recall=recall,
            convergence_cycles=convergence_cycles,
            eval_count=len(eval_set),
            details={"pattern_count": convergence_cycles},
        )

    # ── 比較 ─────────────────────────────────────────────

    def compare(self, results: list[EvalResult]) -> ComparisonReport:
        """複数パイプラインの結果を比較し ComparisonReport を生成する。

        winner 判定: precision + recall の合計が最大の pipeline_type。
        同点の場合は results リストの先頭を優先する。
        """
        if not results:
            return ComparisonReport(
                skill_name="",
                results=[],
                winner="",
                summary="比較対象の結果がありません。",
            )

        skill_name = results[0].skill_name

        # 異スキル名混在チェック
        mixed = {r.skill_name for r in results}
        if len(mixed) > 1:
            raise ValueError(
                f"compare() に複数の skill_name が混在しています: {sorted(mixed)}"
            )

        # winner: precision + recall の合計が最大
        best = max(results, key=lambda r: r.trigger_precision + r.trigger_recall)
        winner = best.pipeline_type

        summary = _build_summary(best, results)

        return ComparisonReport(
            skill_name=skill_name,
            results=results,
            winner=winner,
            summary=summary,
        )


# ── ユーティリティ ────────────────────────────────────────

def _safe_divide(numerator: float, denominator: float) -> float:
    """ゼロ除算を 0.0 として返す安全な除算。"""
    if denominator == 0:
        return 0.0
    return numerator / denominator


def _build_summary(best: EvalResult, all_results: list[EvalResult]) -> str:
    """人間可読な1行サマリを生成する。"""
    score = best.trigger_precision + best.trigger_recall
    lines = [
        f"winner={best.pipeline_type} "
        f"(precision={best.trigger_precision:.2f}, recall={best.trigger_recall:.2f}, "
        f"score={score:.2f})"
    ]
    for r in all_results:
        if r.pipeline_type != best.pipeline_type:
            s = r.trigger_precision + r.trigger_recall
            lines.append(
                f"  vs {r.pipeline_type}: "
                f"precision={r.trigger_precision:.2f}, recall={r.trigger_recall:.2f}, "
                f"score={s:.2f}"
            )
    return " | ".join(lines)
