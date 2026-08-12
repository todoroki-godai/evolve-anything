"""提案候補抽出 helper（`evolve_decisions` パッケージ分割・#383）。

`_extract_candidates`（discover / skill_evolve の skill diff）、`_advisory_pending`
（advisory detector、#284）、`_load_recorder`（fitness_evolution の遅延 import）を束ねる。
振る舞いはゼロ変更で、`evolve_decisions/__init__.py` が全名前を re-export し後方互換と
`setattr(evolve_decisions, ...)` 束縛を保つ。

⚠️ 束縛フェンス（`evolve_decisions/__init__.py` docstring 参照）: `_collect_advisory_proposals`
は test の `monkeypatch.setattr(evolve_decisions, "_collect_advisory_proposals", ...)` 対象
なので、同 module 内の `_advisory_pending` からの呼び出しも `import evolve_decisions as _ed`
経由にする（同一ファイル内のベア名呼び出しだと package 属性の差し替えをすり抜ける）。
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from evolve_decision_ids import _repo_identity, _sha256


def _collect_advisory_proposals(project_dir: Path) -> List[Any]:
    """advisory detector の提案を集める（遅延 import・失敗は呼び出し側で握る）。"""
    from advisory_proposals import collect_advisory_proposals

    return collect_advisory_proposals(project_dir)


def _advisory_pending(project_dir: Optional[str], run_id: str) -> List[Dict[str, Any]]:
    """advisory 提案を pending entry へ変換する（#284）。

    accept 判定は skill 提案と同じ「対象ファイルの sha が変わったか」。現行 adapter
    （invalid_frontmatter / testpaths_coverage）はいずれも修正がファイル変更を伴うので
    この判定で足りる。ファイル変更を伴わない advisory を足すときは判定方式から設計する
    （#267 Sprint 1 の未決事項）。

    ``fitness_func`` は付けない — advisory の判断は optimize_history でなく
    advisory_decisions.jsonl に入るため（母集団の均質性を保つ）。
    """
    import evolve_decisions as _ed

    base = Path(project_dir) if project_dir else Path.cwd()
    out: List[Dict[str, Any]] = []
    for proposal in _ed._collect_advisory_proposals(base):
        target = proposal.target_paths[0] if proposal.target_paths else None
        if not target:
            continue
        path = Path(target)
        if not path.is_absolute():
            path = base / path
        try:
            before = path.read_text(encoding="utf-8")
        except OSError:
            continue  # 読めない対象は accept 判定できないので載せない
        out.append(
            {
                "id": proposal.id,
                "run_id": run_id,
                "detector_id": proposal.detector_id,
                "title": proposal.title,
                "action": proposal.action,
                "target_path": str(path),
                # advisory の id は既に detector+相対targets ベースで worktree 非依存
                # （advisory_proposals._proposal_id）。worktree_root は orphan 判定
                # （#376 AC5）専用に別途付与する。
                "worktree_root": _repo_identity(str(path)).get("worktree_root"),
                "before_sha": _sha256(before),
                "pattern": f"advisory:{proposal.detector_id}",
                "proposal_type": "advisory",
            }
        )
    return out


# 提案対象とみなす suitability（high/medium のみ issue 化される — evolve.py Phase 3.5）。
_SKILL_EVOLVE_PROPOSED = ("high", "medium")


def _extract_candidates(result: Dict[str, Any]) -> List[Dict[str, str]]:
    """accept/reject 記録対象のスキル内容提案を result から抽出する。

    対象（いずれも適用されれば SKILL.md content が変わる＝fitness_func=skill_quality で
    均質に採点でき、母集団が「混合でなく増量」になる）:
      - discover の matched_skills（skill diff, #223 と同クラス）
      - skill_evolve の high/medium 適性 assessment（自己進化パターン組み込み提案）

    remediation の fix は target が rules/hooks/構造と異種で skill_quality 母集団の均質性を
    壊すため対象外（ADR-041 follow-up の意図的スコープ）。

    同一 skill_path は1件に畳む（discover 優先）。
    """
    phases = result.get("phases") or {}
    seen: set = set()
    out: List[Dict[str, str]] = []

    # 1) discover matched_skills（skill diff）
    for m in (phases.get("discover") or {}).get("matched_skills") or []:
        sp = m.get("skill_path")
        name = m.get("matched_skill")
        if not sp or not name or sp in seen:
            continue
        seen.add(sp)
        out.append({
            "skill_name": name, "skill_path": sp,
            "pattern": m.get("pattern", ""), "proposal_type": "skill_diff",
        })

    # 2) skill_evolve 適性 high/medium（自己進化パターン組み込み提案）
    for a in (phases.get("skill_evolve") or {}).get("assessments") or []:
        if a.get("suitability") not in _SKILL_EVOLVE_PROPOSED:
            continue
        skill_dir = a.get("skill_dir")
        name = a.get("skill_name")
        if not skill_dir or not name:
            continue
        sp = str(Path(skill_dir) / "SKILL.md")
        if sp in seen:
            continue
        seen.add(sp)
        out.append({
            "skill_name": name, "skill_path": sp,
            "pattern": f"skill_evolve:{a.get('suitability')}", "proposal_type": "skill_evolve",
        })

    return out


def _record_advisory_event(
    slug: str, entry: Dict[str, Any], tracked: Optional[str], decision: str, *, reason: Optional[str] = None,
) -> None:
    """advisory pending 1件の terminal/fact を記録する（#267）。呼び側で not dry_run を確認済み前提。"""
    from advisory_decision_log import record_advisory_decision

    record_advisory_decision(
        slug=slug,
        proposal_id=entry["id"],
        detector_id=str(entry.get("detector_id") or "unknown"),
        target_path=str(tracked or ""),
        decision=decision,
        run_id=entry.get("run_id"),
        reason=reason,
    )


def _load_recorder():
    """fitness_evolution.record_evolve_diff_decision を遅延 import（lib 外モジュール）。"""
    import evolve_decisions as _ed

    fe_dir = _ed._LIB.parent.parent / "skills" / "evolve-fitness" / "scripts"
    if str(fe_dir) not in sys.path:
        sys.path.insert(0, str(fe_dir))
    from fitness_evolution import record_evolve_diff_decision  # noqa: E402

    return record_evolve_diff_decision
