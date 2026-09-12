#!/usr/bin/env python3
"""#467 A案の判断材料: `instruction_violation` の産出 0 を本番経路で段階分解する。

`docs/decisions/drafts/artifacts/467-measurements-2026-09-12.md` が rev6 の前提として
要求した分解（join → `resolve_plugin_skill_path`/`bare_skill_name` →
`extract_critical_lines` → `detect_instruction_violation`）を、**本番と同じ関数**で
段階ごとに数える。既存の `measure_467_proposal_kinds.py` は SKILL.md 解決に生名 glob を
使っており本番経路と乖離していた（同 artifact の「限界」節）。本スクリプトはその乖離を
埋めることだけを目的とする。

LLM 呼び出しなし（`detect_instruction_violation` は契約上 LLM-free、
`extract_critical_lines` は正規表現のみ）。書込みなし（read-only）。
安全性検証は `measure_467_proposal_kinds` の write_guard / network_guard を再利用する
（同型の guard を2箇所で独立実装しない = design-before-fanout）。ただし `cross_check` は
`run_discover()` 全体を通すため本番経路として `git` 等の子プロセスが走る。そこだけは
socket のみ塞ぎ、起動した子プロセスの argv を verbatim で記録する guard に差し替える
（名前の列挙で許可・拒否を判定しない。記録は JSON の `cross_check.subprocesses_spawned`）。

使い方（`--project-root` は必須。省略すると worktree 起点になり実ストアを読めない）:

    python3 scripts/bench/measure_467_instruction_violation_pipeline.py \
      --project-root /Users/matsukaze-takashi/matsukaze-utils/evolve-anything \
      --output docs/decisions/drafts/artifacts/467-iv-pipeline-2026-09-12.json
"""
from __future__ import annotations

import argparse
import contextlib
import json
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

_BENCH_DIR = Path(__file__).resolve().parent
if str(_BENCH_DIR) not in sys.path:
    sys.path.insert(0, str(_BENCH_DIR))

from measure_467_proposal_kinds import (  # noqa: E402
    WriteGuardViolation,
    NetworkGuardViolation,
    claude_home_manifest,
    diff_manifest,
    guard_no_home_claude_writes,
    guard_no_network,
    _default_data_dir,
    _redact_home,
)

# 本番 lib の import 元。`--project-root` はストア・skills のスコープ決定にのみ使い、
# コードの出所はここ（= 本スクリプトが置かれた checkout）である
_PLUGIN_ROOT = _BENCH_DIR.parent.parent
_LIB = _PLUGIN_ROOT / "scripts" / "lib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))


@contextlib.contextmanager
def guard_socket_record_subprocess(spawned: List[List[str]]):
    """socket は塞ぎ、子プロセス起動は**塞がず記録する**（cross_check 用）。

    `run_discover()` は本番経路として `git` 等の子プロセスを起動するため、
    `measure_467_proposal_kinds.guard_no_network` の Popen 一律ブロックでは通らない。
    ここでは「LLM を呼んでいないこと」を**名前の列挙で判定せず**、起動した全コマンドを
    verbatim で記録して JSON に載せ、人間が読めるようにする（許可・拒否の判定はしない）。
    アウトバウンド通信そのものは socket で塞いだままなので、ネットワーク経由の LLM 呼び出しは
    起きない。子プロセス内部からの通信はこの guard の対象外（記録された argv で判断する）。
    """
    import os
    import socket
    import subprocess as _sp

    real_socket = socket.socket
    real_popen_init = _sp.Popen.__init__
    real_system = os.system

    class _BlockedSocket:
        def __init__(self, *a, **kw):
            raise NetworkGuardViolation(
                "blocked socket.socket() during cross_check (outbound network attempted)"
            )

    def _recording_popen_init(self, *a, **kw):
        args = kw.get("args", a[0] if a else None)
        try:
            spawned.append(
                [str(x) for x in args] if isinstance(args, (list, tuple)) else [str(args)]
            )
        except Exception:  # noqa: BLE001
            spawned.append(["<unprintable>"])
        return real_popen_init(self, *a, **kw)

    def _recording_system(command):
        spawned.append(["os.system", str(command)])
        return real_system(command)

    socket.socket = _BlockedSocket  # type: ignore[assignment]
    _sp.Popen.__init__ = _recording_popen_init  # type: ignore[method-assign]
    os.system = _recording_system  # type: ignore[assignment]
    try:
        yield
    finally:
        socket.socket = real_socket  # type: ignore[assignment]
        _sp.Popen.__init__ = real_popen_init  # type: ignore[method-assign]
        os.system = real_system  # type: ignore[assignment]


def _home_rel(p: Path) -> str:
    try:
        return "~/" + str(p.relative_to(Path.home()))
    except ValueError:
        return str(p)


def decompose(project_root: Path) -> Dict[str, Any]:
    """`discover/runner.py:422-516` の instruction_violation 経路を段階ごとに数える。

    本番ループを転記して計測点を挟む方式のため、転記忠実性は `cross_check`
    （`run_discover` の実結果との一致）と `positive_control`（違反が在る入力で両経路が 1 を
    返すこと）で機械的に検証する。**`cross_check` 単独では実測 S5=0 のため `0 == 0` で
    恒真になり転記ズレを検出できない**ので、陽性対照が必須。

    定数の出所: `_MAX_INSTRUCTIONS` は本番から import する（値がずれたら追随する）。
    `_MAX_CORRECTION_CHECKS` は本番 `runner.py:444` の**写し**（try ブロック内ローカルで
    import できない。本番が変わっても本スクリプトは追随しない）。
    """
    from discover.runner import _fetch_corrections_with_last_skill  # noqa: PLC0415
    from critical_instruction_extractor import (  # noqa: PLC0415
        extract_critical_lines,
        detect_instruction_violation,
        _MAX_INSTRUCTIONS,
    )
    from skill_origin import resolve_plugin_skill_path  # noqa: PLC0415
    from rl_common.usage_schema import bare_skill_name  # noqa: PLC0415

    proj_name = project_root.name

    # --- S0: join 済み corrections（runner.py:435） ---
    corrections_data = _fetch_corrections_with_last_skill(proj_name)
    s0 = len(corrections_data)

    # --- S1: last_skill truthy のみ（runner.py:438-440） ---
    skill_corrections = [c for c in corrections_data if c.get("last_skill")]
    s1 = len(skill_corrections)

    # --- S2: 新しい順に 20 件へ切り詰め（runner.py:444-458） ---
    _MAX_CORRECTION_CHECKS = 20  # 写し: runner.py:444（import 不能）
    skill_corrections = sorted(
        skill_corrections, key=lambda c: c.get("timestamp", ""), reverse=True
    )
    truncated = len(skill_corrections) > _MAX_CORRECTION_CHECKS
    if truncated:
        skill_corrections = skill_corrections[:_MAX_CORRECTION_CHECKS]
    s2 = len(skill_corrections)

    # --- S3〜S5: 解決 → critical 行抽出 → 違反判定 ---
    per_correction: List[Dict[str, Any]] = []
    unresolved_count = 0
    resolved_count = 0
    unresolved_reasons: Counter = Counter()
    resolved_sources: Counter = Counter()
    zero_instruction_skill_mds = 0
    instructions_total = 0
    violations: List[Dict[str, Any]] = []

    for corr in skill_corrections:
        skill_name = corr["last_skill"]
        plugin_skill_md, unresolved_reason = resolve_plugin_skill_path(skill_name)
        bare = bare_skill_name(skill_name) or skill_name
        skill_dirs = list(Path.home().glob(f".claude/skills/{bare}/SKILL.md"))
        pj_skill_dir = project_root / ".claude" / "skills" / bare
        pj_skill_dirs = list(pj_skill_dir.glob("SKILL.md")) if pj_skill_dir.exists() else []

        all_skill_mds: List[Path] = []
        if plugin_skill_md is not None:
            all_skill_mds.append(plugin_skill_md)
        all_skill_mds += [d for d in skill_dirs if d not in all_skill_mds]
        all_skill_mds += [d for d in pj_skill_dirs if d not in all_skill_mds]

        entry: Dict[str, Any] = {
            "last_skill": skill_name,
            "bare": bare,
            "plugin_resolved": plugin_skill_md is not None,
            "plugin_unresolved_reason": unresolved_reason,
            "home_skill_md_hits": len(skill_dirs),
            "project_skill_md_hits": len(pj_skill_dirs),
            "skill_md_candidates": [_home_rel(p) for p in all_skill_mds],
        }

        if not all_skill_mds:
            # runner.py:484-486（silence != evaluated）
            unresolved_count += 1
            unresolved_reasons[unresolved_reason or "not_found"] += 1
            entry["stage_reached"] = "S3_unresolved"
            per_correction.append(entry)
            continue

        resolved_count += 1
        if plugin_skill_md is not None:
            resolved_sources["plugin"] += 1
        elif skill_dirs:
            resolved_sources["home"] += 1
        else:
            resolved_sources["project"] += 1

        entry["stage_reached"] = "S4_instructions"
        checked: List[Dict[str, Any]] = []
        for skill_md in all_skill_mds:
            content = skill_md.read_text(encoding="utf-8")
            instructions = extract_critical_lines(content)
            if not instructions:
                # runner.py:491-492: 次の候補へ（break しない）
                zero_instruction_skill_mds += 1
                checked.append({"skill_md": _home_rel(skill_md), "critical_lines": 0})
                continue
            instructions_total += len(instructions)
            violation = detect_instruction_violation(corr, instructions)
            checked.append(
                {
                    "skill_md": _home_rel(skill_md),
                    "critical_lines": len(instructions),
                    "critical_lines_examined": min(len(instructions), _MAX_INSTRUCTIONS),
                    "violation": None if violation is None else violation.match_type,
                }
            )
            if violation is not None:
                entry["stage_reached"] = "S5_violation"
                violations.append(
                    {
                        "skill_name": skill_name,
                        "skill_md": _home_rel(skill_md),
                        "match_type": violation.match_type,
                        "confidence": violation.confidence,
                        "needs_review": violation.needs_review,
                    }
                )
            else:
                entry["stage_reached"] = "S5_no_violation"
            break  # runner.py:510（最初にマッチしたスキルのみ）
        entry["skill_mds_checked"] = checked
        per_correction.append(entry)

    by_match_type = Counter(v["match_type"] for v in violations)

    return {
        "stages": {
            "S0_corrections_after_join": s0,
            "S1_last_skill_truthy": s1,
            "S2_after_max_checks_slice": s2,
            "S2_truncated": truncated,
            "S2_max_correction_checks": _MAX_CORRECTION_CHECKS,
            "S3_resolved": resolved_count,
            "S3_unresolved": unresolved_count,
            "S3_unresolved_by_reason": dict(unresolved_reasons),
            "S3_resolved_by_source": dict(resolved_sources),
            "S4_skill_mds_with_zero_critical_lines": zero_instruction_skill_mds,
            "S4_critical_lines_total": instructions_total,
            "S4_max_instructions_examined_per_skill": _MAX_INSTRUCTIONS,
            "S5_violations": len(violations),
            "S5_by_match_type": dict(by_match_type),
        },
        "violations": violations,
        # last_skill 名と件数のみ。correction 本文は artifact に載せない
        "per_correction": per_correction,
        "last_skill_histogram": dict(
            Counter(c["last_skill"] for c in skill_corrections)
        ),
    }


def cross_check(project_root: Path, decomposed: Dict[str, Any]) -> Dict[str, Any]:
    """`run_discover` の実結果と突き合わせ、転記が本番と一致することを機械で確認する。"""
    from discover.runner import run_discover  # noqa: PLC0415

    started = time.time()
    result = run_discover(project_root=project_root)
    elapsed = round(time.time() - started, 1)

    prod_violations = len(result.get("instruction_violations", []) or [])
    prod_unresolved = int(result.get("instruction_violations_unresolved", 0) or 0)
    err = result.get("instruction_violations_error")

    stages = decomposed["stages"]
    matches = (
        prod_violations == stages["S5_violations"]
        and prod_unresolved == stages["S3_unresolved"]
        and err is None
    )
    return {
        "run_discover_instruction_violations": prod_violations,
        "run_discover_instruction_violations_unresolved": prod_unresolved,
        "run_discover_instruction_violations_error": err,
        "decomposed_S5_violations": stages["S5_violations"],
        "decomposed_S3_unresolved": stages["S3_unresolved"],
        "matches_production": matches,
        "run_discover_seconds": elapsed,
    }


def positive_control(project_root: Path, decomposed: Dict[str, Any]) -> Dict[str, Any]:
    """陽性対照: 違反が**在る**入力を注入し、両経路が 1 を返すことを確かめる。

    `cross_check` の一致判定は実測が S5=0 のため `0 == 0` で恒真になり、転記が壊れていても
    通ってしまう（0件ゲートは永久に緑）。そこで、実測で S5 まで到達したスキルの SKILL.md から
    **実際に抽出された critical 行**を1本取り、その本文をそのまま correction の message に
    した合成 correction 1 件だけを `_fetch_corrections_with_last_skill` の戻り値として注入する。
    注入は `discover.runner` のモジュール属性差し替えで、decompose 側（call-time import）と
    `run_discover()` 側（`runner.py:435`）の両方に効く。実ストア・実ファイルは書き換えない。

    期待: decompose の S5 = 1 かつ `run_discover()` の `instruction_violations` = 1。
    どちらかが 0 なら転記か本番経路のどちらかが壊れている。
    """
    import discover.runner as _runner  # noqa: PLC0415
    from critical_instruction_extractor import extract_critical_lines  # noqa: PLC0415

    # 実測で S5 まで到達した（=解決でき critical 行もある）スキルを選ぶ
    target = next(
        (
            e
            for e in decomposed["per_correction"]
            if e["stage_reached"].startswith("S5") and e.get("skill_mds_checked")
        ),
        None,
    )
    if target is None:
        return {"skipped": True, "reason": "S5 まで到達した correction が実測に無い"}

    skill_md = Path(target["skill_mds_checked"][0]["skill_md"].replace("~/", str(Path.home()) + "/"))
    instructions = extract_critical_lines(skill_md.read_text(encoding="utf-8"))
    if not instructions:
        return {"skipped": True, "reason": f"critical 行が 0: {target['skill_mds_checked'][0]['skill_md']}"}

    injected = [
        {
            "message": instructions[0].original,
            "timestamp": "2026-01-01T00:00:00Z",
            "session_id": "positive-control",
            "last_skill": target["last_skill"],
        }
    ]

    real_fetch = _runner._fetch_corrections_with_last_skill
    _runner._fetch_corrections_with_last_skill = lambda _proj: [dict(c) for c in injected]
    try:
        injected_decomposed = decompose(project_root)
        result = _runner.run_discover(project_root=project_root)
    finally:
        _runner._fetch_corrections_with_last_skill = real_fetch

    dec_v = injected_decomposed["stages"]["S5_violations"]
    prod_v = len(result.get("instruction_violations", []) or [])
    return {
        "injected_last_skill": target["last_skill"],
        "injected_skill_md": target["skill_mds_checked"][0]["skill_md"],
        "injected_instruction_source_line": instructions[0].source_line,
        "decomposed_S5_violations": dec_v,
        "run_discover_instruction_violations": prod_v,
        "match_types": injected_decomposed["stages"]["S5_by_match_type"],
        "both_detect_one": dec_v == 1 and prod_v == 1,
    }


def _git_sha(repo: Path) -> str:
    try:
        return subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
    except Exception:  # noqa: BLE001
        return "unknown"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", required=True, type=Path)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument(
        "--skip-cross-check", action="store_true",
        help="run_discover 全体の実行を省く（転記忠実性の機械確認が付かない）",
    )
    args = parser.parse_args()

    project_root = args.project_root.expanduser().resolve()
    data_dir = _default_data_dir()
    home_claude = Path.home() / ".claude"

    before = claude_home_manifest(home_claude)

    write_guard_result = "passed"
    network_guard_result = "passed"
    decomposed: Optional[Dict[str, Any]] = None
    try:
        with guard_no_home_claude_writes(home_claude), guard_no_network():
            decomposed = decompose(project_root)
    except WriteGuardViolation as e:
        write_guard_result = f"VIOLATED: {e}"
    except NetworkGuardViolation as e:
        network_guard_result = f"VIOLATED: {e}"

    # cross_check は `run_discover()` 全体を通すため、本番経路として git 等の子プロセスが
    # 起動する。Popen 一律ブロックの guard では通らないので、socket だけ塞ぎ子プロセスは
    # 記録する guard に切り替える（記録は JSON に verbatim で載せる）。
    checked: Dict[str, Any] = {"skipped": True, "reason": "--skip-cross-check"}
    cc_write_guard = "not_run"
    cc_network_guard = "not_run"
    spawned: List[List[str]] = []
    if not args.skip_cross_check and decomposed is not None:
        cc_write_guard = "passed"
        cc_network_guard = "passed"
        try:
            with guard_no_home_claude_writes(home_claude), guard_socket_record_subprocess(spawned):
                checked = cross_check(project_root, decomposed)
        except WriteGuardViolation as e:
            cc_write_guard = f"VIOLATED: {e}"
            checked = {"skipped": True, "reason": f"write_guard: {e}"}
        except NetworkGuardViolation as e:
            cc_network_guard = f"VIOLATED: {e}"
            checked = {"skipped": True, "reason": f"network_guard: {e}"}
    positive: Dict[str, Any] = {"skipped": True, "reason": "--skip-cross-check"}
    if not args.skip_cross_check and decomposed is not None:
        try:
            with guard_no_home_claude_writes(home_claude), guard_socket_record_subprocess(spawned):
                positive = positive_control(project_root, decomposed)
        except (WriteGuardViolation, NetworkGuardViolation) as e:
            positive = {"skipped": True, "reason": str(e)}

    checked["write_guard"] = cc_write_guard
    checked["network_guard"] = cc_network_guard
    checked["subprocesses_spawned"] = spawned

    after = claude_home_manifest(home_claude)
    manifest_diff = diff_manifest(before, after)

    payload = {
        "measured_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "purpose": (
            "#467 rev6 の A 案（instruction_violation の復活）判断のための本番経路分解。"
            "467-measurements-2026-09-12.md が要求した分解を埋める。"
        ),
        "project_root": _redact_home(project_root),
        "data_dir": _redact_home(data_dir),
        "plugin_root_sha": _git_sha(_PLUGIN_ROOT),
        "measured_code_sha": _git_sha(project_root),
        "result": decomposed,
        "cross_check": checked,
        "positive_control": positive,
        "safety_verification": {
            "write_guard": write_guard_result,
            "network_guard": network_guard_result,
            "home_claude_manifest_diff": manifest_diff,
            "home_claude_files_before": len(before),
            "llm_calls": 0,
            "llm_free_basis": (
                "detect_instruction_violation / extract_critical_lines はいずれも "
                "LLM・subprocess を呼ばない（critical_instruction_extractor.py の契約）。"
                "network_guard が実行時に socket を塞いで検証する。"
            ),
        },
    }

    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.output:
        out = args.output if args.output.is_absolute() else Path.cwd() / args.output
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text + "\n", encoding="utf-8")
        print(f"[467-iv] wrote {out}")
    print(text)


if __name__ == "__main__":
    main()
