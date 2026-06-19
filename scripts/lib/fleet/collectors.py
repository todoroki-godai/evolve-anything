"""fleet status の収集 / 永続化ロジック。

PJ 列を並列で audit し `FleetRow` 配列を返す + fleet-runs/*.jsonl への永続化。
fleet/__init__.py から re-export される（後方互換）。
"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import (
    AUDIT_ERROR,
    AUDIT_OK,
    STATUS_ENABLED,
    _DEFAULT_AUTO_MEMORY_ROOT,
    _DEFAULT_PROJECTS_ROOT,
    _DEFAULT_MAX_WORKERS,
    _DEFAULT_SETTINGS_PATH,
    _DEFAULT_TIMEOUT_SEC,
    _current_data_dir,
)
from .audit_runner import IssuesSummary, run_audit_subprocess
from .project_loader import classify_project, enumerate_projects


_UNKNOWN_PROJECT_LABEL = "(unknown)"
_SUBAGENTS_DEFAULT_WINDOW_DAYS = 30


@dataclass
class FleetRow:
    """fleet status 表示 1 行分。"""

    pj_name: str
    status: str  # STATUS_ENABLED / STATUS_STALE / STATUS_NOT_ENABLED
    env_score: float | None = None
    growth_level: int | None = None
    phase: str | None = None
    latest_audit: datetime | None = None
    audit_status: str = AUDIT_OK
    message: str = ""
    issues_summary: IssuesSummary | None = None  # None = 旧 cache (欠落) → 表示 "—"
    subagents_30d: int = 0  # subagents.jsonl 30日窓のカウント、#22
    tokens_30d: int | None = None  # token_usage 30日窓 SUM、None = データ無し → 表示 "--"
    cache_hit_pct: float | None = None  # cache_read / (cache_creation + cache_read)、None = データ無し
    cache_reuse_factor: float | None = None  # cache_read / cache_creation、None = データ無し


def _collect_single(
    pj_path: Path,
    *,
    settings_path: Path,
    auto_memory_root: Path,
    data_dir: Path | None,
    timeout: float,
    subagent_counts: dict[str, int] | None = None,
) -> FleetRow:
    status = classify_project(pj_path, settings_path, auto_memory_root)
    subagents = (subagent_counts or {}).get(pj_path.name, 0)
    if status != STATUS_ENABLED:
        return FleetRow(pj_name=pj_path.name, status=status, subagents_30d=subagents)
    audit = run_audit_subprocess(pj_path, timeout=timeout, data_dir=data_dir)
    return FleetRow(
        pj_name=pj_path.name,
        status=status,
        env_score=audit.env_score,
        growth_level=audit.growth_level,
        phase=audit.phase,
        latest_audit=audit.latest_audit,
        audit_status=audit.status,
        message=audit.message,
        issues_summary=audit.issues_summary,
        subagents_30d=subagents,
    )


def _find_duplicate_basenames(projects: list[Path]) -> set[str]:
    """同一 basename を持つ PJ を検出し、重複 basename 集合を返す。

    evolve-audit の growth-state cache (`growth-state-<basename>.json`) は basename 単位で
    命名されるため、同じ basename の PJ が複数あると cache が衝突し fleet は誤った
    score を表示する。Phase 1 では該当 PJ を AUDIT_ERROR として surface する。
    Phase 3 の per-PJ CLAUDE_PLUGIN_DATA 分離で根本解決予定。
    """
    seen: dict[str, int] = {}
    for pj in projects:
        seen[pj.name] = seen.get(pj.name, 0) + 1
    return {name for name, count in seen.items() if count > 1}


def aggregate_subagents_by_project(
    subagents_path: Path | None = None,
    *,
    window_days: int = _SUBAGENTS_DEFAULT_WINDOW_DAYS,
    now: datetime | None = None,
) -> dict[str, int]:
    """subagents.jsonl を on-the-fly で project 別に group-by 集計する (#22)。

    - timestamp が `window_days` 以内のレコードのみカウント
    - 空 / 欠損 `project` は `(unknown)` に分類
    - 行単位 try/except で破損 1 行が全件落ちないようにする
    - timestamp 不正 / 欠損行も skip（カウント対象外）

    Returns:
        {project_name: count}。キーに `(unknown)` も含まれ得る。
    """
    if subagents_path is None:
        subagents_path = _current_data_dir() / "subagents.jsonl"
    if not subagents_path.is_file():
        return {}

    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=window_days)
    counts: dict[str, int] = {}
    try:
        text = subagents_path.read_text(encoding="utf-8")
    except OSError:
        return {}
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        try:
            rec = json.loads(s)
        except (json.JSONDecodeError, ValueError):
            continue  # 破損 1 行を skip
        if not isinstance(rec, dict):
            continue
        ts_raw = rec.get("timestamp")
        if not isinstance(ts_raw, str):
            continue
        try:
            ts = datetime.fromisoformat(ts_raw)
        except ValueError:
            continue
        # naive な timestamp は UTC とみなす（subagent_observe.py は aware で書き込む）
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if ts < cutoff:
            continue
        project = rec.get("project")
        if not isinstance(project, str) or not project:
            project = _UNKNOWN_PROJECT_LABEL
        counts[project] = counts.get(project, 0) + 1
    return counts


def collect_fleet_status(
    root: Path | None = None,
    settings_path: Path | None = None,
    auto_memory_root: Path | None = None,
    data_dir: Path | None = None,
    timeout: float = _DEFAULT_TIMEOUT_SEC,
    max_workers: int = _DEFAULT_MAX_WORKERS,
    projects: list[Path] | None = None,
) -> list[FleetRow]:
    """全 PJ の fleet ステータスを並列収集して行リストを返す。

    STATUS_ENABLED の PJ のみ subprocess audit を走らせる（STALE/NOT_ENABLED は
    低コストで判定のみ）。並列度は ThreadPoolExecutor(max_workers)。

    同じ basename の PJ が複数ある場合は growth-state cache が衝突するため、
    該当 PJ を AUDIT_ERROR 扱いにして誤ったスコア表示を防ぐ。

    Args:
        projects: 明示的な PJ リスト（fleet-config.json の tracked_projects 用）。
            指定時は root での enumeration をスキップし、このリストを直接使う。
    """
    settings_path = settings_path or _DEFAULT_SETTINGS_PATH
    auto_memory_root = auto_memory_root or _DEFAULT_AUTO_MEMORY_ROOT
    if projects is None:
        root = root or _DEFAULT_PROJECTS_ROOT
        projects = enumerate_projects(root)
    if not projects:
        return []
    dup_basenames = _find_duplicate_basenames(projects)
    # subagents.jsonl を 1 回だけ読んで PJ 別に集計（fleet status 全体の追加コストは O(1)）
    subagent_counts = aggregate_subagents_by_project()

    def _work(pj: Path) -> FleetRow:
        if pj.name in dup_basenames:
            status = classify_project(pj, settings_path, auto_memory_root)
            return FleetRow(
                pj_name=pj.name,
                status=status,
                audit_status=AUDIT_ERROR,
                message="duplicate basename (cache would collide)",
                subagents_30d=subagent_counts.get(pj.name, 0),
            )
        return _collect_single(
            pj,
            settings_path=settings_path,
            auto_memory_root=auto_memory_root,
            data_dir=data_dir,
            timeout=timeout,
            subagent_counts=subagent_counts,
        )

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        return list(pool.map(_work, projects))


def detect_equal_issue_counts(rows: list[FleetRow]) -> list[dict]:
    """複数 PJ の issues_summary total が完全一致したら measurement bug 警報を返す（#419）。

    fleet ISSUES が全 PJ で 599 にぴたり揃ったのは hardcoded_value 検出パイプラインの
    bug が原因で、独立に走査したはずの PJ が偶然同値になるのは測定バグの強シグナル。
    同一の **非ゼロ** total を 2 PJ 以上が共有したら、その total と PJ 群を 1 件の
    警報として返す（0 件一致は「issue なし」で健全なので対象外）。

    Returns:
        [{"total": int, "projects": [pj_name, ...]}]。一致グループごとに 1 件。
        警報なしなら空リスト。
    """
    by_total: dict[int, list[str]] = {}
    for row in rows:
        if row.issues_summary is None:
            continue  # 旧 cache は total 取得不能 → 一致判定に含めない
        total = row.issues_summary.total()
        if total <= 0:
            continue  # 0 件一致は健全
        by_total.setdefault(total, []).append(row.pj_name)
    alarms: list[dict] = []
    for total, projects in by_total.items():
        if len(projects) >= 2:
            alarms.append({"total": total, "projects": projects})
    return alarms


def _serialize_row(row: FleetRow) -> dict:
    d = asdict(row)
    if row.latest_audit is not None:
        d["latest_audit"] = row.latest_audit.isoformat()
    return d


def write_fleet_run(
    rows: list[FleetRow],
    fleet_runs_dir: Path | None = None,
    now: datetime | None = None,
) -> Path:
    """fleet-run を `<dir>/<ts>.jsonl` に追記する。各行は 1 PJ の状態。

    `fleet_runs_dir` 未指定時は呼び出し時点の `CLAUDE_PLUGIN_DATA` を再参照して
    `<data_dir>/fleet-runs/` を使う（import-time capture で stale 化しない）。
    """
    if fleet_runs_dir is None:
        fleet_runs_dir = _current_data_dir() / "fleet-runs"
    now = now or datetime.now(timezone.utc)
    fleet_runs_dir.mkdir(parents=True, exist_ok=True)
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    path = fleet_runs_dir / f"{stamp}.jsonl"
    with path.open("a", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(_serialize_row(row), ensure_ascii=False) + "\n")
    return path
