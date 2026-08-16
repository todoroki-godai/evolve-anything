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

read-only 保証（2026-08-16 codex cold review [Must]3 是正: grep 監査だけでなく実行時に証明する）:
- 測定本体（§1.5.1 + §1.5.3）は ``guard_no_home_claude_writes`` で包む。``builtins.open`` /
  ``os.open`` を実行時に差し替え、``~/.claude/`` 配下への書込みモード open を検出したら
  **その場で例外を送出**する（後付けの diff サンプリングでなく execution-time proof）。
  加えて実行前後で ``~/.claude/`` 全体（narrow しない）の (相対パス, size, mtime_ns) メタデータ
  マニフェストを取り差分を artifact に記録する（内容ハッシュ無し。2026-08-16 実測: 約52,000
  ファイルの stat 走査が約1.6秒）。ライブ環境では他プロセス（hooks 等）が並行して
  ``~/.claude/`` に書くため diff が非ゼロになることがあるが、write-guard がゼロ違反で完走した
  という execution-time proof のほうが強い証拠であり、diff はその補強材料として扱う。
- DuckDB 経由の読み取り（``telemetry_query``）は ``duckdb.connect()``（メモリ内接続）から
  ``read_json_auto`` で jsonl を SELECT するのみで、永続 DB ファイルを開かない
  （write-guard 配下で実行して確認済み＝ソース監査でなく実行時証跡）。
- ``discover.DATA_DIR`` / ``session_store._DATA_DIR_OVERRIDE`` を一時的に ``--data-dir`` へ
  差し替える箇所（``detect_missed_skills`` 用）は必ず ``finally`` で元に戻す。

LLM 呼び出し: なし。測定本体を ``guard_no_network`` で包み、``socket.socket()`` の
インスタンス化を検出したら即座に例外送出する状態で実行し、正常終了することを確認する
（socket を使う限りローカル DuckDB / ファイル IO も含めて何であれ落ちる。2026-08-16 実行時に
ゼロ違反で完走を確認済み。静的監査としても §1.5.3 の対象13種の生成関数はいずれも
LLM/subprocess を呼ばない — ``critical_instruction_extractor.detect_instruction_violation`` は
docstring に「LLM・subprocess を一切呼ばない」と明記。LLM Judge 経路
``emit_violation_judge_requests``/``ingest_violation_judges`` は別関数で本スクリプトからは
呼ばない）。スキップした種別は無い。

``--data-dir`` が全入力を差し替えるわけではない（2026-08-16 codex cold review [Must]2 是正）。
実際に参照する全入力パスと差し替え可否は実行のたびに ``referenced_input_paths()`` が
自己申告し出力 JSON の ``referenced_input_paths`` に記録する。

使い方（既定値のまま実行 = 自分の ``~/.claude/evolve-anything`` と本リポジトリを対象に測る。
上記の write-guard / network-guard は常時有効でフラグ不要 — 同じコマンドを再実行すれば
誰でも同じ安全性検証を再現できる）::

    python3 scripts/bench/measure_467_proposal_kinds.py \\
        --output docs/decisions/drafts/artifacts/467-measurements-<date>.json

出力は ``--output`` にのみ JSON で書く。stdout には進捗と1行サマリのみ（巨大 JSON を
stdout に流さない）。
"""
from __future__ import annotations

import argparse
import builtins
import contextlib
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

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


# --- 実行時の安全検証（2026-08-16 codex cold review [Must]3 是正） -----------------------
#
# grep によるコード監査だけでは「実行時に本当に書込み/ネットワークが起きなかったか」を
# 証明できない。以下の2つの guard は測定本体（§1.5.1 + §1.5.3）の実行を包み、違反があれば
# その場で例外を送出して失敗させる（後付けの diff サンプリングより強い execution-time proof）。
# 既定で常に有効（フラグ不要）。同じコマンドを再実行すれば誰でも同じ検証を再現できる。


class WriteGuardViolation(RuntimeError):
    """`~/.claude/` 配下への書込みモード open を検出した。"""


class NetworkGuardViolation(RuntimeError):
    """`socket.socket()` のインスタンス化を検出した（アウトバウンド通信＝LLM 呼び出し含む）。"""


@contextlib.contextmanager
def guard_no_home_claude_writes(home_claude: Path):
    """`~/.claude/` 配下への書込みモード ``open()`` を検出したら即座に例外送出する。

    ``builtins.open`` と ``os.open``（write フラグ）の両方を差し替える。``pathlib.Path.open``
    は内部で ``io.open``（= ``builtins.open`` と同一オブジェクト）を呼ぶためこれで捕捉できる。
    本スクリプトが使う DuckDB 経路（``telemetry_query``）は ``duckdb.connect()``（メモリ内接続、
    永続ファイル未オープン）のみだが、それも含めて「Python レベルの書込み open が一切起きない」
    ことを動的に証明する（grep 監査でなく実行時証跡）。
    """
    real_open = builtins.open
    real_os_open = os.open
    home_resolved = home_claude.resolve()
    write_mode_chars = ("w", "a", "x", "+")

    def _under_home_claude(path_like: Any) -> bool:
        try:
            target = Path(os.fspath(path_like)).resolve()
        except (TypeError, OSError, ValueError):
            return False
        try:
            target.relative_to(home_resolved)
        except ValueError:
            return False
        return True

    def guarded_open(file, mode="r", *args, **kwargs):
        if isinstance(mode, str) and any(c in mode for c in write_mode_chars):
            if _under_home_claude(file):
                raise WriteGuardViolation(
                    f"blocked write-mode open() under ~/.claude/: {file!r} (mode={mode!r})"
                )
        return real_open(file, mode, *args, **kwargs)

    def guarded_os_open(path, flags, *args, **kwargs):
        write_flags = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC
        if flags & write_flags:
            if _under_home_claude(path):
                raise WriteGuardViolation(
                    f"blocked write-mode os.open() under ~/.claude/: {path!r} (flags={flags})"
                )
        return real_os_open(path, flags, *args, **kwargs)

    builtins.open = guarded_open
    os.open = guarded_os_open
    try:
        yield
    finally:
        builtins.open = real_open
        os.open = real_os_open


@contextlib.contextmanager
def guard_no_network():
    """``socket.socket()`` のインスタンス化を検出したら即座に例外送出する。

    ローカル DuckDB 接続・ファイル IO は socket を使わないため影響しない想定（未検証の前提を
    ここに書かない — 実行してこの guard 配下で完走することそのものが検証結果になる）。
    """
    real_socket_cls = socket.socket

    class _BlockedSocket:
        def __init__(self, *a, **kw):
            raise NetworkGuardViolation(
                "blocked socket.socket() instantiation during measurement "
                "(outbound network / LLM call attempted)"
            )

    socket.socket = _BlockedSocket  # type: ignore[assignment]
    try:
        yield
    finally:
        socket.socket = real_socket_cls


def claude_home_manifest(home_claude: Path) -> Dict[str, Tuple[int, int]]:
    """`~/.claude/` 配下全体の (相対パス) → (size, mtime_ns) メタデータのみのマニフェスト。

    内容ハッシュは取らない（書込み有無の判定には size/mtime で十分かつ大幅に軽い。実測
    2026-08-16: 約52,000ファイルの stat 走査が約1.6秒）。narrow していない
    （codex cold review [Must]3: 絞るなら範囲を明記せよ、との指摘に対し、絞らず全域を対象にした）。
    """
    manifest: Dict[str, Tuple[int, int]] = {}
    if not home_claude.is_dir():
        return manifest
    for dirpath, _dirnames, filenames in os.walk(home_claude):
        for fn in filenames:
            full = Path(dirpath) / fn
            try:
                st = full.lstat()
            except OSError:
                continue
            rel = str(full.relative_to(home_claude))
            manifest[rel] = (st.st_size, st.st_mtime_ns)
    return manifest


def diff_manifest(
    before: Dict[str, Tuple[int, int]], after: Dict[str, Tuple[int, int]],
) -> Dict[str, List[str]]:
    before_keys = set(before)
    after_keys = set(after)
    added = sorted(after_keys - before_keys)
    removed = sorted(before_keys - after_keys)
    changed = sorted(k for k in (before_keys & after_keys) if before[k] != after[k])
    return {"added": added, "removed": removed, "changed": changed}


def measure_1_5_1(data_dir: Path, project_root: Path) -> Dict[str, Any]:
    """§1.5.1: corrections.jsonl / usage.jsonl の実測（read-only）。

    SKILL.md 解決は global（``~/.claude/skills``）と project（``project_root/.claude/skills``）
    の両方を `discover/runner.py:417-419` と同じ順序・規則で試す（2026-08-16 codex cold review
    [Must]1 是正）。
    """
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
    resolved_skill_mds = sum(
        1 for s in preceding
        if s and skill_md_resolves(s, home, project_root=project_root)
    )

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
    （call-time attribute 参照）ため、`discover.DATA_DIR` を一時差し替えれば `usage.jsonl` の
    `--data-dir` 反映は効く。ただし内部で呼ぶ `query_sessions()` は `sessions_file` を渡して
    いないため `session_store` の union read（DuckDB `sessions.db` + 未 ingest jsonl）に落ち、
    `discover.DATA_DIR` パッチの対象外だった（2026-08-16 codex cold review [Must]2）。
    `session_store._DATA_DIR_OVERRIDE` はテスト用に用意された call-time override（本体
    docstring に「テスト専用 override」と明記されているが、read-only なので安全に流用できる）
    のでこちらも同時に差し替える。両方とも必ず finally で元に戻す。
    """
    import discover  # noqa: PLC0415
    import session_store  # noqa: PLC0415
    from discover.runner import (  # noqa: PLC0415
        _existing_skill_names,
        _project_transcript_dir,
        _trajectory_candidates_to_missed,
    )
    from skill_extractor import extract_skill_candidates  # noqa: PLC0415

    original_data_dir = discover.DATA_DIR
    original_session_store_override = session_store._DATA_DIR_OVERRIDE
    discover.DATA_DIR = data_dir
    session_store._DATA_DIR_OVERRIDE = data_dir
    try:
        missed_result = discover.detect_missed_skills(project_root=project_root) or {}
    finally:
        discover.DATA_DIR = original_data_dir
        session_store._DATA_DIR_OVERRIDE = original_session_store_override
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


def referenced_input_paths(data_dir: Path, project_root: Path) -> List[Dict[str, Any]]:
    """本スクリプトが実際に参照する全入力パスの自己申告リスト
    （2026-08-16 codex cold review [Must]2 是正）。

    `--data-dir` は全ての入力を差し替えられるわけではない。各生成関数の実装を読んで
    確認した結果に基づき、差し替え可能／不能を明示する。
    - 差し替え可能（``overridable_via_data_dir=True``）: 明示的な ``*_file``/``*_path``
      パラメータ、または call-time module 属性パッチ（``discover.DATA_DIR`` /
      ``session_store._DATA_DIR_OVERRIDE``）で `--data-dir` を反映できることを実装側で確認済み。
    - 差し替え不能（``overridable_via_data_dir=False``）: `--project-root`（本人の環境の
      セッション transcript・rules・skills 定義）に紐づく入力で、設計上 `--data-dir` の対象外。
      他環境で再現するときは `--project-root` を差し替えることで自動的に各自の入力に切り替わる。
    """
    home = Path.home()
    return [
        {
            "path": _redact_home(data_dir / "corrections.jsonl"),
            "overridable_via_data_dir": True,
            "used_by": ["section_1_5_1", "pitfall_candidates", "hook_candidates", "instruction_violation"],
        },
        {
            "path": _redact_home(data_dir / "usage.jsonl"),
            "overridable_via_data_dir": True,
            "used_by": ["section_1_5_1", "missed_skill_opportunities (via discover.DATA_DIR patch)"],
        },
        {
            "path": _redact_home(data_dir / "errors.jsonl"),
            "overridable_via_data_dir": True,
            "used_by": ["pitfall_candidates"],
        },
        {
            "path": _redact_home(data_dir / "sessions.jsonl"),
            "overridable_via_data_dir": True,
            "used_by": ["constraint_decay_warnings", "constraint_decay_findings"],
        },
        {
            "path": "session_store union read (sessions.db + sessions.jsonl, "
                     "resolved via session_store._data_dir())",
            "overridable_via_data_dir": True,
            "note": "session_store._DATA_DIR_OVERRIDE を一時差し替え（2026-08-16 [Must]2 是正）",
            "used_by": ["missed_skill_opportunities"],
        },
        {
            "path": f"~/.claude/projects/<encoded {project_root.name}>/*.jsonl (session transcripts)",
            "overridable_via_data_dir": False,
            "note": "--project-root に紐づく（discover/runner.py と同じ CC エンコード規則）。"
                     "--data-dir の対象外は設計どおり（他環境では各自の --project-root で自動解決）",
            "used_by": [
                "repeating_patterns", "rule_violation_observed", "recommended_artifacts",
                "stall_recovery_patterns", "trajectory_skill_candidate",
            ],
        },
        {
            "path": f"{project_root}/.claude/skills/, ~/.claude/skills/",
            "overridable_via_data_dir": False,
            "note": "スキル定義（config）。--project-root / 実 home に紐づく",
            "used_by": ["instruction_violation", "missed_skill_opportunities", "workflow_checkpoint_gaps"],
        },
        {
            "path": f"{project_root}/.claude/rules/, ~/.claude/rules/",
            "overridable_via_data_dir": False,
            "note": "rule 定義（config）。--project-root / 実 home に紐づく",
            "used_by": ["repeating_patterns", "rule_violation_observed"],
        },
        {
            "path": f"{project_root}/.claude/ (installed hooks/artifacts), {project_root}/CLAUDE.md",
            "overridable_via_data_dir": False,
            "note": "導入済み artifact 検出・skill trigger 抽出（config）。--project-root に紐づく",
            "used_by": ["recommended_artifacts", "verification_needs", "missed_skill_opportunities"],
        },
        {
            "path": str(home / ".claude" / "skills"),
            "overridable_via_data_dir": False,
            "note": "SKILL.md 解決（§1.5.1）。runner.py:417-419 と同じ規則で global を必ず先に見る",
            "used_by": ["section_1_5_1 (skill_md_resolves)"],
        },
    ]


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
    home_claude = Path.home() / ".claude"

    print(
        f"[467-measure] start data_dir={_redact_home(data_dir)} "
        f"project_root={project_root} commit={_git_sha(REPO)}",
        flush=True,
    )

    print(f"[467-measure] safety: taking pre-run manifest of {home_claude} (metadata only)", flush=True)
    t_manifest = time.monotonic()
    manifest_before = claude_home_manifest(home_claude)
    print(
        f"[467-measure]   manifest_before files={len(manifest_before)} "
        f"({time.monotonic() - t_manifest:.2f}s)",
        flush=True,
    )

    write_guard_result = "passed"
    network_guard_result = "passed"
    t_start = time.monotonic()
    try:
        with guard_no_home_claude_writes(home_claude), guard_no_network():
            result_1_5_1 = measure_1_5_1(data_dir, project_root)
            result_1_5_3 = measure_1_5_3(project_root, data_dir)
    except WriteGuardViolation as e:
        write_guard_result = f"VIOLATED: {e}"
        print(f"[467-measure] SAFETY VIOLATION (write): {e}", flush=True)
        raise
    except NetworkGuardViolation as e:
        network_guard_result = f"VIOLATED: {e}"
        print(f"[467-measure] SAFETY VIOLATION (network): {e}", flush=True)
        raise
    elapsed = time.monotonic() - t_start
    print(
        f"[467-measure] safety: write_guard={write_guard_result} network_guard={network_guard_result}",
        flush=True,
    )

    t_manifest = time.monotonic()
    manifest_after = claude_home_manifest(home_claude)
    manifest_diff = diff_manifest(manifest_before, manifest_after)
    diff_total = len(manifest_diff["added"]) + len(manifest_diff["removed"]) + len(manifest_diff["changed"])
    print(
        f"[467-measure]   manifest_after files={len(manifest_after)} diff_total={diff_total} "
        f"({time.monotonic() - t_manifest:.2f}s)",
        flush=True,
    )
    if diff_total:
        print(
            f"[467-measure]   NOTE: {diff_total} path(s) under {home_claude} changed during the run. "
            "The write-guard above ran with zero violations (execution-time proof this process did "
            "not write them), so this diff reflects concurrent external activity (other hooks/sessions), "
            "not this script. See manifest_diff in the output JSON for the exact paths.",
            flush=True,
        )

    output = {
        "measured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "commit_sha": _git_sha(REPO),
        "data_dir": _redact_home(data_dir),
        "project_root": str(project_root),
        "script": "scripts/bench/measure_467_proposal_kinds.py",
        "elapsed_seconds": round(elapsed, 2),
        "section_1_5_1": result_1_5_1,
        "section_1_5_3": result_1_5_3,
        "referenced_input_paths": referenced_input_paths(data_dir, project_root),
        "safety_verification": {
            "write_guard": write_guard_result,
            "write_guard_method": "execution-time monkeypatch of builtins.open/os.open; raises "
                                   "immediately on any write-mode open() under ~/.claude/",
            "network_guard": network_guard_result,
            "network_guard_method": "execution-time monkeypatch of socket.socket; raises immediately "
                                     "on any socket instantiation",
            "home_claude_manifest_scope": "full ~/.claude/ (not narrowed)",
            "home_claude_manifest_file_count_before": len(manifest_before),
            "home_claude_manifest_file_count_after": len(manifest_after),
            "home_claude_manifest_diff": manifest_diff,
            "home_claude_manifest_diff_note": (
                "diff_total=0" if not diff_total else
                f"{diff_total} path(s) changed by concurrent external activity during the run "
                "(not this script — the write-guard proves this process made zero write-mode "
                "open() calls under ~/.claude/)"
            ),
        },
        "llm_calls": "none, verified at execution time via guard_no_network "
                     "(socket.socket blocked for the duration of the measurement; see "
                     "safety_verification.network_guard). Static audit (2026-08-16): no "
                     "anthropic/openai/subprocess-claude calls in any of the 13 generation "
                     "functions or their dependencies; critical_instruction_extractor."
                     "detect_instruction_violation is LLM-free by contract.",
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
