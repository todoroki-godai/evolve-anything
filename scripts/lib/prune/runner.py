"""prune オーケストレータ + CLI main（旧 prune.py 由来）。

prune/__init__.py から re-export される（後方互換）。
各検出関数は package 経由で遅延参照する
（テスト mock.patch("prune.detect_X", ...) 追従）。
"""
import json
from pathlib import Path
from typing import Any, Dict, Optional


def run_prune(
    project_dir: Optional[str] = None,
    reorganize_merge_groups: Optional[list] = None,
) -> Dict[str, Any]:
    """Prune を実行して候補を返す。"""
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
        merge_duplicates,
        safe_global_check,
    )

    proj = Path(project_dir) if project_dir else Path.cwd()
    artifacts = find_artifacts(proj)

    zero_invocations, plugin_unused = detect_zero_invocations(artifacts)

    # 貢献スコアを取得（Retirement 候補検出に使用）
    # project_root を指定せずクロスプロジェクト全体で集計する。
    # detect_decay_candidates と同じスコープに合わせることでグローバルスキルの
    # 誤 Retirement を防ぐ（project-scoped では同名のグローバルスキルが
    # 低スコアで誤フラグされる可能性がある）。
    from audit import load_usage_data, aggregate_contribution_scores  # noqa: PLC0415
    _usage_records = load_usage_data()
    _contribution_scores = aggregate_contribution_scores(_usage_records)

    candidates = {
        "dead_globs": detect_dead_globs(proj),
        "zero_invocations": zero_invocations,
        "plugin_unused": plugin_unused,
        "global_candidates": safe_global_check(artifacts),
        "duplicate_candidates": detect_duplicates(artifacts),
        "decay_candidates": detect_decay_candidates(artifacts),
        "reference_drift_candidates": detect_reference_drift(artifacts, proj),
        "retirement_candidates": detect_retirement_candidates(artifacts, _contribution_scores),
    }

    total = sum(len(v) for v in candidates.values() if isinstance(v, list))
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
