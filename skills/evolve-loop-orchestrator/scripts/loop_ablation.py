#!/usr/bin/env python3
"""設計文脈 vs naive 生成比較の較正実験（#234 PR3・opt-in CLI）。

issue #234: harness 自動進化の改善が「探索予算増加（単純サンプリング, test-time
scaling）」由来か「設計改善（corrections/context を使った誘導）」由来かを切り分ける
opt-in 較正実験（arXiv 2607.12227）。社内シニアエンジニアレビューの結論により、
毎ループ実行の evolve-loop-orchestrator（run_loop.py）に統計的対照実験を混ぜ込むのは
筋が悪いため、別コマンドの opt-in CLI として切り出した。

judge_audit.harness（#188）と同型の dry-run 既定パターン（llm-batch-guard 準拠）:
- dry-run（既定）: 比較可能性チェック + コスト見積もりを print して終わる。
  **LLM 呼び出しゼロ**。対話確認（input()）は一切なし。実行は `--run` を付けて
  ユーザーが別途再実行する構造的ゲート。
- `--run` で実際に designed/naive 各 n 件を生成・採点する。生成 2n 回・採点
  最大 6n 回（3軸 × 通過候補）で計 8n 回。LLM 呼び出しは既存の
  `generate_candidate`（optimize_core.py）/ `_score_variant_axes`（run_loop.py）に
  限定し、本モジュール自体は subprocess を直接呼ばない（単体テストはこの2箇所を
  mock する。no-llm-in-tests 完全整合）。
- 対象ファイルへの書き込みは一切行わない（read-only ツール。
  PopulationBroadcastOptimizer や run_loop.py の apply 経路と違い、この較正実験は
  比較のためだけに存在し、対象を一切変更しない）。

低レベル関数（collect_corrections/collect_context/determine_strategy/
build_patch_prompt/generate_candidate）は variant_generation.py（#234 PR1）と同じ
optimize_core.py を直接 import して再利用する。designed/naive の違いは
build_patch_prompt へ渡す corrections/context 引数のみで表現し（単一変数分離）、
generate_candidate へ渡すその他の引数（original_content/claude_cwd/max_lines/
pitfall_path/max_chars）は両条件で完全同一にする。
"""
from __future__ import annotations

import argparse
import io
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional

# --- sys.path 設定（自己完結。variant_generation.py と同型・他モジュールの sys.path
# 設定に依存しない） ---
_optimizer_scripts = Path(__file__).parent.parent.parent / "genetic-prompt-optimizer" / "scripts"
sys.path.insert(0, str(_optimizer_scripts))
_plugin_root = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(_plugin_root / "scripts" / "lib"))

from optimize_core import (  # noqa: E402
    build_patch_prompt,
    collect_context,
    collect_corrections,
    detect_scope,
    determine_strategy,
    generate_candidate,
)
from line_limit import MAX_RULE_LINES, MAX_SKILL_LINES, max_chars_for  # noqa: E402
import loop_ablation_stats as _stats  # noqa: E402

# run_loop.py の _score_variant_axes / SCORE_EPSILON を再利用する
# （自己完結 sys.path。variant_generation.py と同型）。
_own_scripts_dir = Path(__file__).parent
if str(_own_scripts_dir) not in sys.path:
    sys.path.insert(0, str(_own_scripts_dir))
import run_loop as _run_loop  # noqa: E402

# corrections パス（variant_generation.py の値と1行複製。定数importはしない設計方針）
_CORRECTIONS_PATH = Path.home() / ".claude" / "evolve-anything" / "corrections.jsonl"
_MAX_CORRECTIONS_PER_PATCH = 10


def _target_skill_name(target_path: Path) -> str:
    """対象スキルのスキル名を推定する（SKILL.md は親ディレクトリ名にフォールバック）。"""
    name = target_path.stem
    if name == "SKILL":
        name = target_path.parent.name
    return name


def _build_prompts(target: Path, original_content: str):
    """designed/naive 両プロンプトを実際に組み立てる。

    corrections/context の収集は1回のみ行い（designed/naive で重複しない）、naive 条件は
    build_patch_prompt に corrections=[] / context={} を渡すことで表現する。
    original_content/claude_cwd/max_lines/pitfall_path/max_chars は両条件で完全同一に
    し、違いは prompt 文字列だけになるようにする（単一変数分離）。
    """
    skill_name = _target_skill_name(target)
    corrections = collect_corrections(skill_name, _CORRECTIONS_PATH, _MAX_CORRECTIONS_PER_PATCH)
    context = collect_context(target, _plugin_root, skill_name)

    is_rule_file = ".claude/rules/" in str(target)
    max_lines = MAX_RULE_LINES if is_rule_file else MAX_SKILL_LINES
    max_chars = max_chars_for(max_lines)
    pitfall_path_obj = target.parent / "references" / "pitfalls.md"
    pitfall_path = str(pitfall_path_obj) if pitfall_path_obj.exists() else None
    claude_cwd: Optional[str] = str(Path.home()) if detect_scope(target) == "global" else None

    designed_strategy = determine_strategy("auto", corrections)
    naive_strategy = determine_strategy("auto", [])

    designed_prompt = build_patch_prompt(
        original_content, corrections, context, designed_strategy, is_rule_file, max_lines
    )
    naive_prompt = build_patch_prompt(
        original_content, [], {}, naive_strategy, is_rule_file, max_lines
    )

    gen_kwargs: Dict[str, Any] = {
        "original_content": original_content,
        "claude_cwd": claude_cwd,
        "max_lines": max_lines,
        "pitfall_path": pitfall_path,
        "max_chars": max_chars,
    }
    return designed_prompt, naive_prompt, corrections, context, gen_kwargs


def _generate_condition(
    prompt: str, n: int, gen_kwargs: Dict[str, Any], label: str
) -> List[Dict[str, Any]]:
    """label 条件（designed/naive）で n 件を並行生成し、ゲート通過分のみ返す。

    ThreadPoolExecutor パターンは variant_generation.py の generate_variants と同型。
    """
    if n <= 0:
        return []

    raw_results: List[Optional[Dict[str, Any]]] = [None] * n
    with ThreadPoolExecutor(max_workers=max(1, n)) as executor:
        future_to_index = {
            executor.submit(
                generate_candidate,
                prompt,
                gen_kwargs["original_content"],
                gen_kwargs["claude_cwd"],
                gen_kwargs["max_lines"],
                gen_kwargs["pitfall_path"],
                gen_kwargs["max_chars"],
            ): i
            for i in range(n)
        }
        for future in as_completed(future_to_index):
            i = future_to_index[future]
            try:
                raw_results[i] = future.result()
            except Exception as exc:  # noqa: BLE001 — 1候補の例外で全体をクラッシュさせない
                raw_results[i] = {"content": None, "passed": False, "error": str(exc)}

    return [
        {"id": f"{label}_{i}", "content": r["content"]}
        for i, r in enumerate(raw_results)
        if r and r.get("passed") and r.get("content")
    ]


def _score_condition(candidates: List[Dict[str, Any]], target_path: str) -> Dict[str, List[float]]:
    """ゲート通過候補を軸別スコアリストへ集約する（run_loop._score_variant_axes 再利用）。"""
    scores: Dict[str, List[float]] = {axis: [] for axis in _stats.AXES}
    for cand in candidates:
        axes = _run_loop._score_variant_axes(cand["content"], target_path, dry_run=False)
        for axis in _stats.AXES:
            if axis in axes:
                scores[axis].append(axes[axis])
    return scores


def _print_comparability(comparability: Dict[str, Any], out) -> None:
    print("=== 比較可能性チェック ===", file=out)
    print(
        f"comparable={comparability['comparable']} "
        f"(prompt差分={comparability['prompt_diff_chars']}文字, "
        f"corrections={comparability['corrections_count']}件, "
        f"context_signals={comparability['context_signals']})",
        file=out,
    )
    if comparability["reason"]:
        print(f"  理由: {comparability['reason']}", file=out)


def _print_cost(cost: Dict[str, Any], out) -> None:
    print(
        f"コスト見積もり: n={cost['n']} → "
        f"生成{cost['generation_calls']}回 + 採点{cost['scoring_calls']}回 "
        f"= 計{cost['total_calls']}回（推定 ~{cost['est_total_tokens']} トークン）",
        file=out,
    )


def _print_generation_summary(designed_n: int, naive_n: int, n: int, out) -> None:
    print(f"\n生成中... designed x{n} / naive x{n}", file=out)
    print(f"  ゲート通過: designed {designed_n}/{n}  naive {naive_n}/{n}", file=out)


def _print_comparison(comparison: Dict[str, Any], epsilon: float, out) -> None:
    print(f"\n=== スコア比較（epsilon={epsilon}）===", file=out)
    for axis in _stats.AXES:
        ax = comparison["axes"][axis]
        print(f"  {axis}: verdict={ax['verdict']} delta={ax.get('delta')}", file=out)
    print(f"\n総合判定: {comparison['verdict']}", file=out)
    if comparison["naive_wins_warning"]:
        print("⚠️  naive が designed を上回りました。設計文脈の有効性に疑問符が付きます。", file=out)
    if comparison["low_sample_size_caveat"]:
        print(
            f"注意: n={comparison['n']} はサンプルサイズが小さく統計的信頼性が限定的です。",
            file=out,
        )


def run_ablation(
    target_path: str,
    *,
    n: int = 3,
    run: bool = False,
    force: bool = False,
    out=None,
) -> Dict[str, Any]:
    """設計文脈あり/なし生成の比較較正実験（dry-run既定）。"""
    out = out if out is not None else sys.stdout
    target = Path(target_path)

    try:
        original_content = target.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError) as exc:
        print(f"対象ファイルが見つかりません: {exc}", file=out)
        return {"error": f"対象ファイルが見つかりません: {exc}"}

    print(f"=== 較正実験: 設計文脈 vs naive 生成（対象: {target_path}）===\n", file=out)

    designed_prompt, naive_prompt, corrections, context, gen_kwargs = _build_prompts(
        target, original_content
    )

    comparability = _stats.assess_comparability(designed_prompt, naive_prompt, corrections, context)
    cost = _stats.estimate_ablation_cost(len(original_content), n)

    _print_comparability(comparability, out)
    print(file=out)
    _print_cost(cost, out)

    result: Dict[str, Any] = {
        "target": target_path,
        "n": n,
        "dry_run": not run,
        "forced": False,
        "comparability": comparability,
        "cost": cost,
    }

    if not run:
        print("\n[dry-run] LLM 呼び出しゼロ。実行は --run を付けてください。", file=out)
        return result

    if not comparability["comparable"] and not force:
        reason = comparability["reason"]
        print(f"\nエラー: 較正実験になりません（{reason}）。", file=out)
        print("designed/naive プロンプトが実質同一のため、比較しても差分は生まれません。", file=out)
        print("強制実行するには --force を付けてください。", file=out)
        result["aborted"] = True
        result["error"] = reason
        return result

    result["forced"] = bool(force and not comparability["comparable"])

    designed_candidates = _generate_condition(designed_prompt, n, gen_kwargs, "designed")
    naive_candidates = _generate_condition(naive_prompt, n, gen_kwargs, "naive")
    _print_generation_summary(len(designed_candidates), len(naive_candidates), n, out)

    designed_scores = _score_condition(designed_candidates, target_path)
    naive_scores = _score_condition(naive_candidates, target_path)

    comparison = _stats.compare_ablation_scores(
        designed_scores, naive_scores, epsilon=_run_loop.SCORE_EPSILON
    )

    result["designed_passed"] = len(designed_candidates)
    result["naive_passed"] = len(naive_candidates)
    result["designed_scores"] = designed_scores
    result["naive_scores"] = naive_scores
    result["comparison"] = comparison

    _print_comparison(comparison, _run_loop.SCORE_EPSILON, out)

    return result


def main(argv: Optional[List[str]] = None) -> None:
    ap = argparse.ArgumentParser(
        description="設計文脈 vs naive 生成比較の較正実験（#234 PR3・opt-in）"
    )
    ap.add_argument("--target", required=True, help="対象ファイルパス")
    ap.add_argument("--n", type=int, default=3, help="designed/naive 各条件の生成件数（既定 3）")
    ap.add_argument("--run", action="store_true", help="実際に LLM を呼ぶ（既定は dry-run）")
    ap.add_argument("--force", action="store_true", help="比較不能でも強制実行する")
    ap.add_argument("--json", action="store_true", help="JSON 出力")
    args = ap.parse_args(argv)

    out = io.StringIO() if args.json else sys.stdout
    result = run_ablation(args.target, n=args.n, run=args.run, force=args.force, out=out)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))

    if result.get("error") or result.get("aborted"):
        sys.exit(1)


if __name__ == "__main__":
    main()
