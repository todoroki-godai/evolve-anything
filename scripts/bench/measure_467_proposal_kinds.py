#!/usr/bin/env python3
"""#467 rev5 §1.5 の実測を再現する（codex cold review [Must]C の解消）。

対象ドラフト: ``docs/decisions/drafts/467-all-proposal-types-to-morning-yn.md`` §1.5.0
「[実測] の再現手順」。本スクリプトはその自然言語の説明をコードとして固定し、他者の環境でも
（当人のデータに対しては）再実行できるようにする。

2フェーズ:

- §1.5.1: ``corrections.jsonl`` / ``usage.jsonl`` を読み、corrections の集計・
  「同一 session_id で correction の直前にある Skill 呼び出し」の復元・
  ``discover/runner.py:417`` と同じ規則での SKILL.md 解決可否を計算する。
  join ロジックの純関数は ``scripts/lib/measure_467_join.py``（単体テスト
  ``scripts/lib/tests/test_measure_467_join.py`` あり）。
- §1.5.3: ``proposal_lane_coverage.PROPOSAL_KINDS`` の ``lane_connected=False`` 全13種を、
  各生成関数を個別 import して直接呼び出し産出件数を数える（``run_discover()`` 全体は実行しない）。

read-only 保証:
- ``~/.claude/`` 配下には一切書き込まない。DuckDB 経由の読み取り（``telemetry_query``）は
  ``duckdb.connect()``（メモリ内接続）から ``read_json_auto`` で jsonl を SELECT するのみで、
  永続 DB ファイルを開かない（2026-08-16 実装時にソース確認済み）。
- ``pitfall_manager.extract_pitfall_candidates`` / ``discover.detect_repeated_correction_patterns`` /
  他の生成関数はいずれも純計算で、pitfalls.md や suppression ログへの書き込み関数
  （``pitfall_manager.recording`` / ``discover.suppression``）は呼ばない（2026-08-16 実装時に
  ソース grep で確認済み・呼び出しコメントに根拠を残す）。
- ``discover.DATA_DIR`` を一時的に ``--data-dir`` へ差し替える箇所（``detect_missed_skills`` 用）は
  必ず ``finally`` で元に戻す。

LLM 呼び出し: なし。§1.5.3 の対象13種の生成関数はいずれも LLM/subprocess を呼ばない
（``critical_instruction_extractor.detect_instruction_violation`` は docstring に
「LLM・subprocess を一切呼ばない」と明記。LLM Judge 経路
``emit_violation_judge_requests``/``ingest_violation_judges`` は別関数で本スクリプトからは
呼ばない）。他 12種の生成関数・依存モジュールも 2026-08-16 実装時に
``anthropic``/``openai``/``subprocess`` の grep で不在を確認済み。スキップした種別は無い。

使い方（既定値のまま実行 = 自分の ``~/.claude/evolve-anything`` と本リポジトリを対象に測る）::

    python3 scripts/bench/measure_467_proposal_kinds.py \\
        --output docs/decisions/drafts/artifacts/467-measurements-<date>.json

出力は ``--output`` にのみ JSON で書く。stdout には進捗と1行サマリのみ（巨大 JSON を
stdout に流さない）。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional

REPO = Path(__file__).resolve().parent.parent.parent
LIB = REPO / "scripts" / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from measure_467_join import (  # noqa: E402
    count_skill_usage,
    load_jsonl,
    resolve_preceding_skills,
    skill_md_resolves,
    summarize_corrections,
)


def _default_data_dir() -> Path:
    return Path.home() / ".claude" / "evolve-anything"


def measure_1_5_1(data_dir: Path) -> Dict[str, Any]:
    """§1.5.1: corrections.jsonl / usage.jsonl の実測（read-only）。"""
    print(f"[467-measure] phase §1.5.1: reading {data_dir}", flush=True)
    t0 = time.monotonic()

    corrections = load_jsonl(data_dir / "corrections.jsonl")
    usage = load_jsonl(data_dir / "usage.jsonl")
    print(
        f"[467-measure]   loaded corrections={len(corrections)} usage_records={len(usage)}",
        flush=True,
    )

    corr_summary = summarize_corrections(corrections)
    skill_call_total = count_skill_usage(usage)

    preceding = resolve_preceding_skills(corrections, usage)
    preceded_count = sum(1 for s in preceding if s)

    home = Path.home()
    resolved_skill_mds = sum(1 for s in preceding if s and skill_md_resolves(s, home))

    elapsed = time.monotonic() - t0
    print(
        f"[467-measure]   preceded={preceded_count}/{corr_summary['total']} "
        f"skill_md_resolved={resolved_skill_mds}/{max(preceded_count, 1) if preceded_count else 0} "
        f"({elapsed:.2f}s)",
        flush=True,
    )

    return {
        "corrections_total": corr_summary["total"],
        "corrections_last_skill_truthy": corr_summary["last_skill_truthy"],
        "corrections_source_counts": corr_summary["source_counts"],
        "corrections_correction_type_counts": corr_summary["correction_type_counts"],
        "usage_skill_call_total": skill_call_total,
        "corrections_with_preceding_skill_call": preceded_count,
        "preceding_skill_calls_with_resolvable_skill_md": resolved_skill_mds,
    }


# --- §1.5.3: PROPOSAL_KINDS の lane_connected=False 全13種を個別に測る ------------------


def _measure_repeating_and_rule_violation(project_root: Path) -> Dict[str, int]:
    """`repeating_patterns` / `rule_violation_observed`（discover/runner.py:311-340 と同じ経路）。"""
    from tool_usage_analyzer import analyze_tool_usage  # noqa: PLC0415
    from rule_violation_lane import (  # noqa: PLC0415
        default_rule_dirs,
        extract_prohibited_command_heads,
        partition_rule_violations,
    )

    tool_result = analyze_tool_usage(project_root=project_root) or {}
    prohibited = extract_prohibited_command_heads(default_rule_dirs(project_root))
    partitioned = partition_rule_violations(
        tool_result.get("repeating_patterns", []),
        prohibited,
        project_root=project_root,
    )
    return {
        "repeating_patterns": len(partitioned["skill_candidates"]),
        "rule_violation_observed": len(partitioned["rule_violation_observed"]),
    }


def _measure_pitfall_and_hook(project_root: Path, data_dir: Path) -> Dict[str, int]:
    """`pitfall_candidates` / `hook_candidates`（discover/runner.py:357-374 と同じ経路）。"""
    from pitfall_manager import extract_pitfall_candidates  # noqa: PLC0415
    from discover import detect_repeated_correction_patterns  # noqa: PLC0415
    from telemetry_query import query_corrections, query_errors  # noqa: PLC0415

    proj_name = project_root.name
    corrections_data = query_corrections(
        project=proj_name, corrections_file=data_dir / "corrections.jsonl",
    )
    errors_data = query_errors(
        project=proj_name, errors_file=data_dir / "errors.jsonl",
    )
    pitfall_result = extract_pitfall_candidates(corrections_data, errors=errors_data)
    hook_candidates = detect_repeated_correction_patterns(corrections_data)
    return {
        "pitfall_candidates": len(pitfall_result["candidates"]),
        "hook_candidates": len(hook_candidates),
    }


def _measure_instruction_violation(project_root: Path, data_dir: Path) -> Dict[str, int]:
    """`instruction_violation`（discover/runner.py:378-445 と同じ経路。LLM-free。）"""
    from critical_instruction_extractor import (  # noqa: PLC0415
        detect_instruction_violation,
        extract_critical_lines,
    )
    from issue_schema import make_instruction_violation_issue  # noqa: PLC0415
    from telemetry_query import query_corrections  # noqa: PLC0415

    proj_name = project_root.name
    corrections_data = query_corrections(
        project=proj_name, corrections_file=data_dir / "corrections.jsonl",
    )

    skill_corrections = [c for c in corrections_data if c.get("last_skill")]
    _MAX_CORRECTION_CHECKS = 20
    skill_corrections = sorted(
        skill_corrections, key=lambda c: c.get("timestamp", ""), reverse=True,
    )[:_MAX_CORRECTION_CHECKS]

    violations = []
    for corr in skill_corrections:
        skill_name = corr["last_skill"]
        skill_dirs = list(Path.home().glob(f".claude/skills/{skill_name}/SKILL.md"))
        pj_skill_dir = project_root / ".claude" / "skills" / skill_name
        pj_skill_dirs = (
            list((pj_skill_dir / "SKILL.md").parent.glob("SKILL.md"))
            if pj_skill_dir.exists() else []
        )
        all_skill_mds = skill_dirs + [d for d in pj_skill_dirs if d not in skill_dirs]

        for skill_md in all_skill_mds:
            content = skill_md.read_text(encoding="utf-8")
            instructions = extract_critical_lines(content)
            if not instructions:
                continue
            violation = detect_instruction_violation(corr, instructions)
            if violation:
                violations.append(
                    make_instruction_violation_issue(
                        skill_name=skill_name,
                        skill_path=str(skill_md),
                        instruction_text=violation.instruction.original,
                        correction_message=violation.correction_message,
                        match_type=violation.match_type,
                        confidence=violation.confidence,
                        reason=violation.reason,
                        needs_review=violation.needs_review,
                    )
                )
            break  # 最初にマッチしたスキルのみ（runner.py と同一）

    return {"instruction_violation": len(violations)}


def _measure_missed_and_trajectory(project_root: Path, data_dir: Path) -> Dict[str, int]:
    """`missed_skill_opportunities` / `trajectory_skill_candidate`
    （discover/runner.py:228-276 と同じ経路。LLM-free）。

    `detect_missed_skills` は `from . import DATA_DIR` を呼び出し時に評価する
    （call-time attribute 参照）ため、`discover.DATA_DIR` を一時差し替えれば
    `--data-dir` を反映できる。必ず finally で元に戻す。
    """
    import discover  # noqa: PLC0415
    from discover.runner import (  # noqa: PLC0415
        _existing_skill_names,
        _project_transcript_dir,
        _trajectory_candidates_to_missed,
    )
    from skill_extractor import extract_skill_candidates  # noqa: PLC0415

    original_data_dir = discover.DATA_DIR
    discover.DATA_DIR = data_dir
    try:
        missed_result = discover.detect_missed_skills(project_root=project_root) or {}
    finally:
        discover.DATA_DIR = original_data_dir
    missed = missed_result.get("missed") or []

    traj_root = _project_transcript_dir(project_root)
    traj_candidates = extract_skill_candidates(projects_root=traj_root)
    existing_missed = {m.get("skill") for m in missed}
    known_skills = _existing_skill_names(project_root)
    surfaced, merged = _trajectory_candidates_to_missed(
        traj_candidates,
        threshold=discover.TRAJECTORY_SKILL_SCORE_THRESHOLD,
        existing_skills=existing_missed,
        known_skills=known_skills,
    )

    return {
        "missed_skill_opportunities": len(missed) + len(merged),
        "trajectory_skill_candidate": len(surfaced),
    }


def _measure_verification_needs(project_root: Path) -> Dict[str, int]:
    """`verification_needs`（discover/runner.py:302-309 と同じ経路）。"""
    from verification_catalog import detect_verification_needs  # noqa: PLC0415

    needs = detect_verification_needs(project_root)
    return {"verification_needs": len(needs)}


def _measure_recommended_artifacts(project_root: Path) -> Dict[str, int]:
    """`recommended_artifacts`（discover/runner.py:312-347 と同じ経路）。"""
    from discover import detect_recommended_artifacts  # noqa: PLC0415
    from tool_usage_analyzer import analyze_tool_usage  # noqa: PLC0415

    tool_result = analyze_tool_usage(project_root=project_root) or {}
    recommended = detect_recommended_artifacts(tool_usage_patterns=tool_result)
    return {"recommended_artifacts": len(recommended)}


def _measure_stall_recovery(project_root: Path) -> Dict[str, int]:
    """`stall_recovery_patterns`（discover/runner.py:463-478 と同じ経路）。"""
    from tool_usage_analyzer import (  # noqa: PLC0415
        STALL_RECOVERY_RECENCY_DAYS,
        detect_stall_recovery_patterns,
        extract_tool_calls_by_session,
    )

    session_commands = extract_tool_calls_by_session(
        project_root, max_age_days=STALL_RECOVERY_RECENCY_DAYS,
    )
    patterns = detect_stall_recovery_patterns(session_commands)
    return {"stall_recovery_patterns": len(patterns)}


def _measure_workflow_checkpoint_gaps(project_root: Path) -> Dict[str, int]:
    """`workflow_checkpoint_gaps`（discover/runner.py:480-505 と同じ経路）。"""
    from workflow_checkpoint import detect_checkpoint_gaps, is_workflow_skill  # noqa: PLC0415

    skills_dir = project_root / ".claude" / "skills"
    workflow_gaps = []
    if skills_dir.is_dir():
        for skill_dir in sorted(skills_dir.iterdir()):
            if not skill_dir.is_dir():
                continue
            if not is_workflow_skill(skill_dir):
                continue
            gaps = detect_checkpoint_gaps(skill_dir.name, skill_dir, project_root)
            if gaps:
                workflow_gaps.append({"skill_name": skill_dir.name, "gaps": gaps})
    return {"workflow_checkpoint_gaps": len(workflow_gaps)}


def _measure_constraint_decay(data_dir: Path) -> Dict[str, int]:
    """`constraint_decay_warnings` / `constraint_decay_findings`（discover/runner.py:447-461）。"""
    from discover.patterns import detect_constraint_decay  # noqa: PLC0415

    decay_findings = detect_constraint_decay(
        sessions_path=data_dir / "sessions.jsonl",
        corrections_path=data_dir / "corrections.jsonl",
    )
    warnings = [f for f in decay_findings if f.get("severity") == "WARNING"]
    return {
        "constraint_decay_warnings": len(warnings),
        "constraint_decay_findings": len(decay_findings),
    }


# kind 名 → (呼び出し用サブ関数, 説明)。1関数が複数 kind を返すことがある
# （runner.py の生成経路が共有されているため。§1.5.3 の対象と同一の分割）。
_KIND_MEASURERS: Dict[str, Callable[..., Dict[str, int]]] = {
    "repeating_patterns": _measure_repeating_and_rule_violation,
    "rule_violation_observed": _measure_repeating_and_rule_violation,
    "pitfall_candidates": _measure_pitfall_and_hook,
    "hook_candidates": _measure_pitfall_and_hook,
    "instruction_violation": _measure_instruction_violation,
    "missed_skill_opportunities": _measure_missed_and_trajectory,
    "trajectory_skill_candidate": _measure_missed_and_trajectory,
    "verification_needs": _measure_verification_needs,
    "recommended_artifacts": _measure_recommended_artifacts,
    "stall_recovery_patterns": _measure_stall_recovery,
    "workflow_checkpoint_gaps": _measure_workflow_checkpoint_gaps,
    "constraint_decay_warnings": _measure_constraint_decay,
    "constraint_decay_findings": _measure_constraint_decay,
}


def measure_1_5_3(project_root: Path, data_dir: Path) -> Dict[str, Any]:
    """§1.5.3: PROPOSAL_KINDS の lane_connected=False 全13種を個別 import で直接測る。

    ``run_discover()`` 全体は実行しない。1関数呼び出しで複数 kind の値が出る場合は
    その呼び出しを1回だけ行い結果を分配する（同じ計算を2回走らせない）。
    """
    print(f"[467-measure] phase §1.5.3: project_root={project_root}", flush=True)
    sys.path.insert(0, str(LIB))  # 生成関数のモジュール解決用

    from proposal_lane_coverage import PROPOSAL_KINDS  # noqa: PLC0415

    target_kinds = [pk.kind for pk in PROPOSAL_KINDS if not pk.lane_connected]

    counts: Dict[str, Optional[int]] = {}
    errors: Dict[str, str] = {}
    skipped: Dict[str, str] = {}
    done_calls: set = set()

    call_args: Dict[Callable[..., Dict[str, int]], tuple] = {
        _measure_repeating_and_rule_violation: (project_root,),
        _measure_pitfall_and_hook: (project_root, data_dir),
        _measure_instruction_violation: (project_root, data_dir),
        _measure_missed_and_trajectory: (project_root, data_dir),
        _measure_verification_needs: (project_root,),
        _measure_recommended_artifacts: (project_root,),
        _measure_stall_recovery: (project_root,),
        _measure_workflow_checkpoint_gaps: (project_root,),
        _measure_constraint_decay: (data_dir,),
    }

    for kind in target_kinds:
        fn = _KIND_MEASURERS.get(kind)
        if fn is None:
            errors[kind] = "no measurer registered (spec/impl drift)"
            continue
        if fn in done_calls:
            continue
        t0 = time.monotonic()
        try:
            result = fn(*call_args[fn])
            for k, v in result.items():
                counts[k] = v
            done_calls.add(fn)
            print(
                f"[467-measure]   {fn.__name__}: {result} ({time.monotonic() - t0:.2f}s)",
                flush=True,
            )
        except Exception as e:  # noqa: BLE001 individual-kind isolation（他 kind を道連れにしない）
            errors[kind] = f"{type(e).__name__}: {e}"
            print(f"[467-measure]   {fn.__name__} FAILED: {errors[kind]}", flush=True)

    missing = [k for k in target_kinds if k not in counts and k not in errors]
    for k in missing:
        errors[k] = "not populated by its measurer (bug)"

    return {
        "target_kinds": target_kinds,
        "counts": counts,
        "errors": errors,
        "skipped": skipped,
    }


def _git_sha(repo: Path) -> str:
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True, timeout=10,
        )
        return out.stdout.strip()
    except Exception:
        return "(unknown)"


def _redact_home(path: Path) -> str:
    """個人特定可能なローカル絶対パスを `~` 表記にする。"""
    try:
        return "~/" + str(path.resolve().relative_to(Path.home().resolve()))
    except ValueError:
        return str(path)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--data-dir", type=str, default=None,
        help="corrections.jsonl / usage.jsonl / errors.jsonl / sessions.jsonl のあるディレクトリ"
             "（既定: ~/.claude/evolve-anything/）",
    )
    ap.add_argument(
        "--project-root", type=str, default=None,
        help="§1.5.3 の対象プロジェクト（既定: 本リポジトリ）",
    )
    ap.add_argument(
        "--output", type=str, required=True,
        help="結果 JSON の出力先（stdout には1行サマリのみ）",
    )
    args = ap.parse_args()

    data_dir = Path(args.data_dir).expanduser() if args.data_dir else _default_data_dir()
    project_root = Path(args.project_root).expanduser().resolve() if args.project_root else REPO

    print(
        f"[467-measure] start data_dir={_redact_home(data_dir)} "
        f"project_root={project_root} commit={_git_sha(REPO)}",
        flush=True,
    )

    t_start = time.monotonic()
    result_1_5_1 = measure_1_5_1(data_dir)
    result_1_5_3 = measure_1_5_3(project_root, data_dir)
    elapsed = time.monotonic() - t_start

    output = {
        "measured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "commit_sha": _git_sha(REPO),
        "data_dir": _redact_home(data_dir),
        "project_root": str(project_root),
        "script": "scripts/bench/measure_467_proposal_kinds.py",
        "elapsed_seconds": round(elapsed, 2),
        "section_1_5_1": result_1_5_1,
        "section_1_5_3": result_1_5_3,
        "llm_calls": "none (audited 2026-08-16: no anthropic/openai/subprocess-claude calls "
                     "in any of the 13 generation functions or their dependencies; "
                     "critical_instruction_extractor.detect_instruction_violation is LLM-free "
                     "by contract)",
    }

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    n_ok = len(result_1_5_3["counts"])
    n_err = len(result_1_5_3["errors"])
    print(
        f"[467-measure] done in {elapsed:.2f}s — corrections={result_1_5_1['corrections_total']} "
        f"1.5.3_kinds_ok={n_ok} 1.5.3_kinds_error={n_err} → wrote {out_path}",
        flush=True,
    )


if __name__ == "__main__":
    main()
