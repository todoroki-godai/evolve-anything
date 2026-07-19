#!/usr/bin/env python3
"""バリエーション生成（evolve-loop-orchestrator 専用）

#234 PR1: 配線drift修理。

旧 `generate_variants()` は genetic-prompt-optimizer/scripts/optimize.py を
`--generations 1 --population <N>` で subprocess 呼び出ししていたが、この2
オプションは optimize.py 側で廃止済み（`_DEPRECATED_OPTIONS`）で、呼ぶと
`_check_deprecated_options()` が検知して `sys.exit(1)` する。バリエーション
生成は dry-run 含め常時失敗していた。

本モジュールは optimize.py へのCLI呼び出しをやめ、genetic-prompt-optimizer の
低レベル関数（collect_corrections / collect_context / determine_strategy /
build_patch_prompt / generate_candidate）を直接 import して使う。
`optimize.py` の `PopulationBroadcastOptimizer.run()` は対象ファイルへの直接
書き込みという副作用を持ち、戻り値も単一 winner のみで、
run_loop.py が期待する「複数candidatesを評価用に返す（ファイル書き込みなし）」
という前提と衝突するため使わない。
"""
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional

# --- sys.path 設定（自己完結。run_loop.py 側の sys.path 設定に依存しない） ---
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

# corrections パス（optimize.py の値と1行複製。定数importはしない設計方針）
_CORRECTIONS_PATH = Path.home() / ".claude" / "evolve-anything" / "corrections.jsonl"
_MAX_CORRECTIONS_PER_PATCH = 10


def _target_skill_name(target_path: Path) -> str:
    """対象スキルのスキル名を推定する（SKILL.md は親ディレクトリ名にフォールバック）。"""
    name = target_path.stem
    if name == "SKILL":
        name = target_path.parent.name
    return name


def generate_variants(
    target_path: str,
    population: int = 3,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """直接パッチ最適化で複数バリエーションを並行生成する（ファイル書き込みなし）。

    dry_run=True の場合、LLM 呼び出し・ファイル書き込みゼロで population 件の
    合成candidateを返す（構造テストとして実配線を通す価値があるため、
    corrections収集/strategy決定はdry_runでも実行する）。
    """
    target = Path(target_path)
    try:
        original_content = target.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError) as exc:
        return {"error": f"対象ファイルが見つかりません: {exc}"}

    skill_name = _target_skill_name(target)
    corrections = collect_corrections(skill_name, _CORRECTIONS_PATH, _MAX_CORRECTIONS_PER_PATCH)
    context = collect_context(target, _plugin_root, skill_name)
    strategy = determine_strategy("auto", corrections)

    if dry_run:
        candidates = [
            {
                "id": f"candidate_{i}",
                "content": original_content + f"\n<!-- evolve-loop dry-run candidate {i} -->\n",
            }
            for i in range(population)
        ]
        return {
            "target": target_path,
            "strategy": strategy,
            "corrections_used": len(corrections),
            "dry_run": True,
            "n_candidates": population,
            "passed_count": len(candidates),
            "candidates": candidates,
        }

    is_rule_file = ".claude/rules/" in str(target)
    max_lines = MAX_RULE_LINES if is_rule_file else MAX_SKILL_LINES
    max_chars = max_chars_for(max_lines)
    pitfall_path_obj = target.parent / "references" / "pitfalls.md"
    pitfall_path = str(pitfall_path_obj) if pitfall_path_obj.exists() else None
    claude_cwd: Optional[str] = str(Path.home()) if detect_scope(target) == "global" else None

    prompt = build_patch_prompt(
        original_content, corrections, context, strategy, is_rule_file, max_lines
    )

    raw_results: List[Optional[Dict[str, Any]]] = [None] * population
    with ThreadPoolExecutor(max_workers=max(1, population)) as executor:
        future_to_index = {
            executor.submit(
                generate_candidate,
                prompt,
                original_content,
                claude_cwd,
                max_lines,
                pitfall_path,
                max_chars,
            ): i
            for i in range(population)
        }
        for future in as_completed(future_to_index):
            i = future_to_index[future]
            try:
                raw_results[i] = future.result()
            except Exception as exc:  # noqa: BLE001 — 1候補の例外で全体をクラッシュさせない
                raw_results[i] = {
                    "id": f"candidate_{i}",
                    "content": None,
                    "passed": False,
                    "error": str(exc),
                }

    candidates = [
        {"id": f"candidate_{i}", "content": r["content"]}
        for i, r in enumerate(raw_results)
        if r and r.get("passed") and r.get("content")
    ]
    passed_count = len(candidates)

    if passed_count == 0:
        return {
            "error": "全候補がゲート不合格",
            "n_candidates": population,
            "passed_count": 0,
        }

    return {
        "target": target_path,
        "strategy": strategy,
        "corrections_used": len(corrections),
        "dry_run": False,
        "n_candidates": population,
        "passed_count": passed_count,
        "candidates": candidates,
    }
