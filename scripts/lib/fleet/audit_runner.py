"""fleet PJ audit subprocess 実行ロジック。

`run_audit_subprocess` で `bin/evolve-audit` を起動し growth-state JSON から結果を読む。
fleet/__init__.py から re-export される（後方互換）。
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from . import (
    AUDIT_ERROR,
    AUDIT_OK,
    AUDIT_TIMEOUT,
    _DEFAULT_DATA_DIR,
    _DEFAULT_RL_AUDIT_BIN,
    _KILL_GRACE_SEC,
)
from .project_loader import _pj_safe_name, _safe_compute_level


@dataclass
class IssuesSummary:
    """fleet status 表示用の issues count（growth-state cache 由来、#22）。

    cache に issues_summary キーが無い (旧 cache) 場合は None で扱い、表示は "—"。

    Note: `scripts/lib/issues_summary.IssuesSummary` (audit 側、書込み用) とは
    意図的に別クラス。両者は growth-state cache JSON のフィールド名を契約として
    繋がる（同名 5 フィールドを共有）。display 側は read-only で `total()` のみ
    持ち、compute ロジックは audit 側に寄せている。
    """

    line_violations: int = 0
    hardcoded_values: int = 0
    potential_duplicates: int = 0
    corrections_unprocessed: int = 0
    skill_quality_degraded_count: int = 0

    def total(self) -> int:
        return (
            self.line_violations
            + self.hardcoded_values
            + self.potential_duplicates
            + self.corrections_unprocessed
            + self.skill_quality_degraded_count
        )


@dataclass
class AuditResult:
    """PJ audit の結果（TIMEOUT/ERROR 区別付き）。"""

    status: str  # AUDIT_OK | AUDIT_TIMEOUT | AUDIT_ERROR
    env_score: float | None = None
    phase: str | None = None
    growth_level: int | None = None
    latest_audit: datetime | None = None
    message: str = ""
    issues_summary: IssuesSummary | None = None


def _parse_iso(ts: object) -> datetime | None:
    if not isinstance(ts, str):
        return None
    try:
        return datetime.fromisoformat(ts)
    except ValueError:
        return None


def _parse_issues_summary(raw: object) -> IssuesSummary | None:
    """growth-state cache の issues_summary dict を IssuesSummary に変換。

    None / 欠落 / 非 dict はすべて None を返す（旧 cache 互換、UI 側で "—" 表示）。
    未知キーは無視、欠損キーは 0、非数値も 0 で耐える。
    """
    if not isinstance(raw, dict):
        return None
    def _i(k: str) -> int:
        v = raw.get(k)
        return int(v) if isinstance(v, (int, float)) else 0
    return IssuesSummary(
        line_violations=_i("line_violations"),
        hardcoded_values=_i("hardcoded_values"),
        potential_duplicates=_i("potential_duplicates"),
        corrections_unprocessed=_i("corrections_unprocessed"),
        skill_quality_degraded_count=_i("skill_quality_degraded_count"),
    )


def _terminate_process_group(proc: subprocess.Popen) -> None:
    """subprocess のプロセスグループを SIGTERM→SIGKILL で順次停止させる。

    `start_new_session=True` で起動した子プロセスは別セッション/PGID を持つので、
    `os.killpg` で子孫まとめて落とせる。
    """
    try:
        pgid = os.getpgid(proc.pid)
    except OSError:
        pgid = proc.pid
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(pgid, sig)
        except OSError:
            break
        try:
            proc.wait(timeout=_KILL_GRACE_SEC)
            return
        except subprocess.TimeoutExpired:
            continue


def run_audit_subprocess(
    pj_path: Path,
    timeout: float = 10.0,
    data_dir: Path | None = None,
    rl_audit_bin: Path | None = None,
) -> AuditResult:
    """PJ の audit を subprocess で実行し growth-state から結果を読み取る。

    - `bin/evolve-audit --growth --skip-rescore -- <pj_path>` を実行（副作用: growth-state 更新）
    - `--` 区切りで PJ パスに leading `-` があっても argparse を誤動作させない
    - `data_dir` 指定時は `CLAUDE_PLUGIN_DATA=<data_dir>` を env に設定
    - subprocess は `start_new_session=True` で別プロセスグループに隔離し、timeout 時は
      `os.killpg` で子孫まで確実に終了させる（孤児化した evolve-audit 子孫が growth-state を
      半書き状態で残すことを防ぐ）
    - subprocess timeout / returncode 非ゼロ / growth-state 破損は `AuditResult.status` で区別

    Phase 1 では evolve-audit stdout は parse せず growth-state JSON を唯一の真実とする。
    """
    rl_audit_bin = rl_audit_bin or _DEFAULT_RL_AUDIT_BIN
    effective_data_dir = data_dir or _DEFAULT_DATA_DIR
    cmd = [
        sys.executable, str(rl_audit_bin),
        "--growth", "--skip-rescore",
        "--", str(pj_path),
    ]
    env = os.environ.copy()
    if data_dir is not None:
        env["CLAUDE_PLUGIN_DATA"] = str(data_dir)

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
            start_new_session=True,
        )
    except OSError as e:
        return AuditResult(AUDIT_ERROR, message=f"spawn failed: {e}")

    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _terminate_process_group(proc)
        return AuditResult(AUDIT_TIMEOUT, message=f"timeout after {timeout}s")

    if proc.returncode != 0:
        stderr_tail = (stderr or "").strip().splitlines()
        tail = stderr_tail[-1] if stderr_tail else f"returncode {proc.returncode}"
        return AuditResult(AUDIT_ERROR, message=tail[:200])

    state_path = effective_data_dir / f"growth-state-{_pj_safe_name(pj_path)}.json"
    if not state_path.is_file():
        return AuditResult(AUDIT_OK, message="no growth-state cache")
    try:
        state = json.loads(state_path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        return AuditResult(AUDIT_ERROR, message=f"state parse: {e}")

    env_score = state.get("env_score")
    phase = state.get("phase")
    growth_level = _safe_compute_level(env_score)
    latest_audit = _parse_iso(state.get("updated_at"))
    issues = _parse_issues_summary(state.get("issues_summary"))
    return AuditResult(
        status=AUDIT_OK,
        env_score=env_score if isinstance(env_score, (int, float)) else None,
        phase=phase if isinstance(phase, str) else None,
        growth_level=growth_level,
        latest_audit=latest_audit,
        issues_summary=issues,
    )
