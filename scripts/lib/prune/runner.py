"""prune オーケストレータ + CLI main（旧 prune.py 由来）。

prune/__init__.py から re-export される（後方互換）。
各検出関数は package 経由で遅延参照する
（テスト mock.patch("prune.detect_X", ...) 追従）。
"""
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional


def run_prune(
    project_dir: Optional[str] = None,
    reorganize_merge_groups: Optional[list] = None,
    now: Optional[datetime] = None,
    pj_scoped: bool = True,
) -> Dict[str, Any]:
    """Prune を実行して候補を返す。

    ``now`` は zero_invocation 観測窓 suppress 判定（#522-2/#529-1）のために注入可能。
    未指定時は現在時刻を使う。

    ``pj_scoped`` が真（既定）のとき、global 淘汰候補は PJ 単独では判断不能なため
    フル配列を result に積まず件数サマリ（``{"count", "pointer"}``）に畳む（#586）。
    cross-PJ で全件評価したい場合（CLI 全 PJ 走査）は ``pj_scoped=False`` でフル配列を返す。
    """
    # mock.patch("prune.detect_X", ...) / mock.patch("prune.find_artifacts", ...) 追従のため package 経由で参照
    from . import (  # noqa: PLC0415
        cleanup_corrections,
        detect_dead_globs,
        detect_decay_candidates,
        detect_duplicates,
        detect_reference_drift,
        detect_retirement_candidates,
        detect_zero_invocations,
        find_artifacts,
        make_global_candidates_summary,
        make_zero_invocation_suppression_summary,
        merge_duplicates,
        safe_global_check,
        zero_invocation_window_suppressed,
    )

    proj = Path(project_dir) if project_dir else Path.cwd()
    artifacts = find_artifacts(proj)

    zero_invocations, plugin_unused = detect_zero_invocations(artifacts, project_dir=proj)

    # 観測窓が usage 記録修正日 (#478) をまたぐ間は zero_invocation を suppress し、
    # 「計測待ち N 件」サマリに置換する。data 自身が欠損で信頼不可なのに per-item 調査
    # MUST を課す矛盾（#522-2 / advisory↔MUST 矛盾 #529-1）を構造的に解消する。
    zero_invocations_suppressed = None
    if zero_invocation_window_suppressed(now=now):
        zero_invocations_suppressed = make_zero_invocation_suppression_summary(
            suppressed_count=len(zero_invocations)
        )
        zero_invocations = []

    # 貢献スコアを取得（Retirement 候補検出に使用）
    # project_root を指定せずクロスプロジェクト全体で集計する。
    # detect_decay_candidates と同じスコープに合わせることでグローバルスキルの
    # 誤 Retirement を防ぐ（project-scoped では同名のグローバルスキルが
    # 低スコアで誤フラグされる可能性がある）。
    from audit import load_usage_data, aggregate_contribution_scores  # noqa: PLC0415
    _usage_records = load_usage_data()
    _contribution_scores = aggregate_contribution_scores(_usage_records)

    # global 淘汰候補: PJスコープ evolve では判断材料が不足する（cross-PJ 使用状況が必要）ため、
    # フル配列を result に積まず件数サマリに畳む（#586）。全件評価したい CLI 走査では
    # pj_scoped=False でフル配列を維持する。
    global_full = safe_global_check(artifacts)
    if pj_scoped:
        global_value: Any = make_global_candidates_summary(len(global_full))
        global_count = len(global_full)
    else:
        global_value = global_full
        global_count = len(global_full)

    candidates = {
        "dead_globs": detect_dead_globs(proj),
        "zero_invocations": zero_invocations,
        "zero_invocations_suppressed": zero_invocations_suppressed,
        "plugin_unused": plugin_unused,
        "global_candidates": global_value,
        "duplicate_candidates": detect_duplicates(artifacts),
        "decay_candidates": detect_decay_candidates(artifacts),
        "reference_drift_candidates": detect_reference_drift(artifacts, proj),
        "retirement_candidates": detect_retirement_candidates(artifacts, _contribution_scores),
    }

    # list の要素数を合算する。PJスコープでは global_candidates が dict サマリになり
    # 上の合算から漏れるため、件数を別途加算して total を維持する（#586）。
    total = sum(len(v) for v in candidates.values() if isinstance(v, list))
    if pj_scoped:
        total += global_count
    candidates["total_candidates"] = total

    rules = artifacts.get("rules", [])
    candidates["rules_info"] = [
        {"name": p.stem, "scope": "global" if ".claude/rules" in str(p) and "projects" not in str(p) else "project"}
        for p in rules
    ]

    cleanup_result = cleanup_corrections()
    candidates["corrections_cleanup"] = cleanup_result

    merge_result = merge_duplicates(
        candidates["duplicate_candidates"],
        reorganize_merge_groups=reorganize_merge_groups,
        project_dir=project_dir,
    )
    candidates["merge_result"] = merge_result

    return candidates


def main() -> None:
    import sys

    project = sys.argv[1] if len(sys.argv) > 1 else None
    result = run_prune(project)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
