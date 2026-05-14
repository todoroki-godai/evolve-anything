"""rl-anything fleet — 全 PJ 横断のメンテナンス拠点（Phase 1: status のみ）。

設計: `todoroki-main-design-20260422-140954.md` Phase 1 節。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

STATUS_ENABLED = "ENABLED"
STATUS_STALE = "STALE"
STATUS_NOT_ENABLED = "NOT_ENABLED"

AUDIT_OK = "OK"
AUDIT_TIMEOUT = "TIMEOUT"
AUDIT_ERROR = "ERROR"

from rl_common import DATA_DIR as _DEFAULT_DATA_DIR  # honors CLAUDE_PLUGIN_DATA

_DEFAULT_SETTINGS_PATH = Path.home() / ".claude" / "settings.json"
_DEFAULT_AUTO_MEMORY_ROOT = Path.home() / ".claude" / "projects"
_DEFAULT_PROJECTS_ROOT = Path.home() / "tools"
_DEFAULT_RL_AUDIT_BIN = Path(__file__).resolve().parent.parent.parent / "bin" / "rl-audit"
_PLUGIN_KEY_PREFIX = "rl-anything@"
_SETTINGS_RETRY_SLEEP_SEC = 0.1
_DEFAULT_TIMEOUT_SEC = 10.0
_DEFAULT_MAX_WORKERS = 2
_KILL_GRACE_SEC = 2.0


def _current_data_dir() -> Path:
    """CLAUDE_PLUGIN_DATA を呼び出し時に再参照して DATA_DIR を返す。

    `rl_common._DEFAULT_DATA_DIR` は import-time capture のため env 後追い変更を
    反映できない。fleet-runs 書き出しなど呼び出しタイミングが重要な処理では
    こちらを使う。
    """
    env_val = os.environ.get("CLAUDE_PLUGIN_DATA", "")
    return Path(env_val) if env_val else Path.home() / ".claude" / "rl-anything"


@dataclass
class AuditResult:
    """PJ audit の結果（TIMEOUT/ERROR 区別付き）。"""

    status: str  # AUDIT_OK | AUDIT_TIMEOUT | AUDIT_ERROR
    env_score: float | None = None
    phase: str | None = None
    growth_level: int | None = None
    latest_audit: datetime | None = None
    message: str = ""
    issues_summary: "IssuesSummary | None" = None


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


def _pj_safe_name(pj_path: Path) -> str:
    """growth-state cache 命名に使う safe_name（growth_engine._cache_path と同じルール）。"""
    name = pj_path.resolve().name or "unknown"
    return re.sub(r"[^a-zA-Z0-9_\-]", "_", name)


def resolve_auto_memory_dir(pj_path: Path) -> Path:
    """PJ パスから Claude Code auto-memory ディレクトリを逆引きする。

    命名規則: `~/.claude/projects/-<絶対パスを `/` → `-` に置換>`

    例: `/Users/foo/bar` → `~/.claude/projects/-Users-foo-bar`

    相対パスや trailing slash は `Path.resolve()` で正規化してから変換する。
    特殊文字 (`-` を含むディレクトリ名等) は Phase 3 で扱う (本実装は非対応)。
    """
    absolute = pj_path.resolve()
    slug = str(absolute).replace("/", "-")
    return Path.home() / ".claude" / "projects" / slug


def enumerate_projects(root: Path) -> list[Path]:
    """PJ 候補を列挙する。

    `root` 直下の子ディレクトリで、以下いずれかを持つものを PJ とみなす:
    - `.claude/` ディレクトリ
    - `CLAUDE.md` ファイル

    除外ルール:
    - ドットで始まるディレクトリ (`.worktrees/` 等) は開発メタデータのため
    - シンボリックリンクは任意パスへの audit trampoline を防ぐため

    `root` 自体が存在しない場合は空リストを返す。
    返り値はディレクトリ名でソート。
    """
    if not root.is_dir():
        return []
    projects: list[Path] = []
    for child in sorted(root.iterdir(), key=lambda p: p.name):
        if not child.is_dir() or child.name.startswith(".") or child.is_symlink():
            continue
        if (child / ".claude").is_dir() or (child / "CLAUDE.md").is_file():
            projects.append(child)
    return projects


def _load_settings_with_retry(settings_path: Path) -> dict | None:
    """settings.json を読んで dict を返す。parse 失敗時は 100ms 後に 1 回 retry。"""
    for attempt in range(2):
        if not settings_path.is_file():
            return None
        try:
            return json.loads(settings_path.read_text())
        except (json.JSONDecodeError, OSError):
            if attempt == 0:
                time.sleep(_SETTINGS_RETRY_SLEEP_SEC)
                continue
            return None


def _is_plugin_enabled(settings: dict) -> bool:
    """settings.enabledPlugins に rl-anything@* が truthy で含まれるか。"""
    enabled = settings.get("enabledPlugins") or {}
    if not isinstance(enabled, dict):
        return False
    for key, value in enabled.items():
        if key.startswith(_PLUGIN_KEY_PREFIX) and bool(value):
            return True
    return False


def _latest_activity(auto_memory_dir: Path) -> float | None:
    """auto-memory ディレクトリ内の `.jsonl` の最新 mtime を返す。無ければ None。"""
    if not auto_memory_dir.is_dir():
        return None
    latest: float | None = None
    for f in auto_memory_dir.glob("*.jsonl"):
        try:
            mtime = f.stat().st_mtime
        except OSError:
            continue
        if latest is None or mtime > latest:
            latest = mtime
    return latest


def classify_project(
    pj_path: Path,
    settings_path: Path | None = None,
    auto_memory_root: Path | None = None,
    stale_days: int = 30,
    now: datetime | None = None,
) -> str:
    """PJ の rl-anything 導入状況を 3 値で判定する。

    判定表 (設計 Phase 1 ハイブリッド):
    - `rl-anything@*` 有効 + auto-memory の直近 `.jsonl` が `stale_days` 以内 → ENABLED
    - `rl-anything@*` 有効 + auto-memory 古い or 欠損 → STALE
    - `rl-anything@*` 無効 or settings 欠損 / 破損（retry も失敗） → NOT_ENABLED

    `settings_path` が破損していた場合は 100ms sleep 後に 1 回だけ retry する。
    """
    settings_path = settings_path or _DEFAULT_SETTINGS_PATH
    auto_memory_root = auto_memory_root or _DEFAULT_AUTO_MEMORY_ROOT
    now = now or datetime.now(timezone.utc)

    settings = _load_settings_with_retry(settings_path)
    if settings is None or not _is_plugin_enabled(settings):
        return STATUS_NOT_ENABLED

    slug = str(pj_path.resolve()).replace("/", "-")
    auto_memory_dir = auto_memory_root / slug

    latest = _latest_activity(auto_memory_dir)
    if latest is None:
        return STATUS_STALE
    age_sec = now.timestamp() - latest
    if age_sec > stale_days * 86400:
        return STATUS_STALE
    return STATUS_ENABLED


def run_audit_subprocess(
    pj_path: Path,
    timeout: float = 10.0,
    data_dir: Path | None = None,
    rl_audit_bin: Path | None = None,
) -> AuditResult:
    """PJ の audit を subprocess で実行し growth-state から結果を読み取る。

    - `bin/rl-audit --growth --skip-rescore -- <pj_path>` を実行（副作用: growth-state 更新）
    - `--` 区切りで PJ パスに leading `-` があっても argparse を誤動作させない
    - `data_dir` 指定時は `CLAUDE_PLUGIN_DATA=<data_dir>` を env に設定
    - subprocess は `start_new_session=True` で別プロセスグループに隔離し、timeout 時は
      `os.killpg` で子孫まで確実に終了させる（孤児化した rl-audit 子孫が growth-state を
      半書き状態で残すことを防ぐ）
    - subprocess timeout / returncode 非ゼロ / growth-state 破損は `AuditResult.status` で区別

    Phase 1 では rl-audit stdout は parse せず growth-state JSON を唯一の真実とする。
    """
    rl_audit_bin = rl_audit_bin or _DEFAULT_RL_AUDIT_BIN
    effective_data_dir = data_dir or _DEFAULT_DATA_DIR
    # flags を positional より前に置き `--` で区切る → PJ 名が `-` で始まっても安全
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
            start_new_session=True,  # 別プロセスグループ → killpg 可能
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

    # env_score は「環境スコア」(`compute_environment_fitness` 結果)、
    # progress は「phase 内の進捗」— 別物。fleet は env_score を表示する (#86)。
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


def _safe_compute_level(env_score: object) -> int | None:
    if not isinstance(env_score, (int, float)):
        return None
    try:
        from growth_level import compute_level
    except ImportError:
        return None
    return compute_level(float(env_score)).level


# Format helpers / status table は fleet/formatters.py に集約済み（後方互換のため再エクスポート）
from .formatters import (  # noqa: E402, F401
    _TABLE_HEADERS,
    _format_short_int,
    _format_cell_tokens,
    _format_cell_cache_hit,
    _format_relative,
    _format_cell_score,
    _format_cell_level,
    _format_cell_phase,
    _format_cell_last_audit,
    _format_cell_audit,
    _format_cell_issues,
    _format_cell_subagents,
    format_status_table,
)


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

    rl-audit の growth-state cache (`growth-state-<basename>.json`) は basename 単位で
    命名されるため、同じ basename の PJ が複数あると cache が衝突し fleet は誤った
    score を表示する。Phase 1 では該当 PJ を AUDIT_ERROR として surface する。
    Phase 3 の per-PJ CLAUDE_PLUGIN_DATA 分離で根本解決予定。
    """
    seen: dict[str, int] = {}
    for pj in projects:
        seen[pj.name] = seen.get(pj.name, 0) + 1
    return {name for name, count in seen.items() if count > 1}


_UNKNOWN_PROJECT_LABEL = "(unknown)"
_SUBAGENTS_DEFAULT_WINDOW_DAYS = 30


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


def main(argv: list[str] | None = None) -> int:
    """`bin/rl-fleet` エントリポイント。"""
    parser = argparse.ArgumentParser(
        prog="rl-fleet",
        description="全 PJ 横断で rl-anything の健康状態を一覧表示する",
    )
    sub = parser.add_subparsers(dest="command")
    status_p = sub.add_parser("status", help="各 PJ のステータスを表形式で表示（default）")
    for p in (parser, status_p):
        p.add_argument("--root", type=Path, default=None, help="PJ 列挙のルート（config 未設定時の fallback、default: ~/tools）")
        p.add_argument("--timeout", type=float, default=_DEFAULT_TIMEOUT_SEC, help="PJ 毎の audit タイムアウト秒 (default: 10)")
        p.add_argument("--max-workers", type=int, default=_DEFAULT_MAX_WORKERS, help="並列数 (default: 2)")
        p.add_argument("--no-write", action="store_true", help="fleet-runs/*.jsonl への追記をスキップ")
        p.add_argument("--all", dest="show_all", action="store_true", help="STALE/NOT_ENABLED PJ も含めて全表示（デフォルトは STALE 除外）")

    discover_p = sub.add_parser(
        "discover",
        help="Claude Code が認識している PJ を検出し、track/ignore を対話的に設定",
    )
    discover_p.add_argument("--non-interactive", action="store_true", help="候補を表示するのみ（承認なし）")

    tg_p = sub.add_parser(
        "test-guard",
        help="各 PJ で no-llm-in-tests / pytest-no-llm の導入状況を一覧",
    )
    tg_sub = tg_p.add_subparsers(dest="tg_command")
    tg_status = tg_sub.add_parser("status", help="導入状況を表形式で表示（default）")
    tg_status.add_argument("--root", type=Path, default=None,
                           help="PJ 列挙のルート (fleet-config 未設定時の fallback)")
    tg_status.add_argument("--json", action="store_true", help="JSON 出力")

    tokens_p = sub.add_parser(
        "tokens",
        help="PJ別 LLM トークン消費 (TOP-N / anomaly / drill-down / backfill)",
    )
    tokens_p.add_argument("--days", type=int, default=30, help="集計期間（日）。default: 30")
    tokens_p.add_argument("--pj", type=str, default=None, help="特定 PJ にドリルダウン (canonical pj_id)")
    tokens_p.add_argument("--by", type=str, default="session", choices=["session", "model", "week"], help="--pj 指定時の分解軸")
    tokens_p.add_argument("--anomaly", action="store_true", help="WoW + cache hit 異常のみ表示")
    tokens_p.add_argument("--backfill", action="store_true", help="transcript JSONL を ingest")
    tokens_p.add_argument("--all", action="store_true", help="--backfill 時に全期間 ingest（default 90 日）")
    tokens_p.add_argument("--json", action="store_true", help="JSON 出力")

    args = parser.parse_args(argv)

    if args.command == "discover":
        return _run_discover(args)
    if args.command == "tokens":
        return _run_tokens(args)
    if args.command == "test-guard":
        return _run_test_guard(args)

    # default: status
    return _run_status(args)


def _run_status(args) -> int:
    """fleet-config.json の tracked_projects を優先、未設定時は --root で fallback。"""
    import fleet_config

    config = fleet_config.load_config()
    tracked = config.get("tracked_projects", [])

    projects: list[Path] | None = None
    new_candidates: list[Path] = []
    if tracked:
        projects = [Path(p) for p in tracked]
        # ついでに新候補を検出して hint 表示
        discovered = fleet_config.filter_valid_projects(
            fleet_config.discover_cc_projects()
        )
        new_candidates = fleet_config.diff_candidates(config, discovered)

    rows = collect_fleet_status(
        root=args.root,
        timeout=args.timeout,
        max_workers=args.max_workers,
        projects=projects,
    )
    _inject_token_metrics(rows, days=30)
    show_all = getattr(args, "show_all", False)
    if not show_all:
        stale_count = sum(1 for r in rows if r.status == STATUS_STALE)
        rows = [r for r in rows if r.status != STATUS_STALE]
    else:
        stale_count = 0
    print(format_status_table(rows), end="")
    if not show_all and stale_count:
        print(f"[fleet] STALE {stale_count} PJ を非表示にしています（--all で全表示）")
    if new_candidates:
        print(
            f"\n[fleet] 新しい PJ 候補を {len(new_candidates)} 件検出しました。"
            f" `rl-fleet discover` で track/ignore を設定してください。",
        )
    if not args.no_write:
        write_fleet_run(rows)
    return 0


def _run_test_guard(args) -> int:
    """各 PJ の test-guard 導入状況を表示。tracked_projects 優先、未設定なら --root。"""
    import json
    import test_guard
    import fleet_config

    config = fleet_config.load_config()
    tracked = config.get("tracked_projects", [])
    if tracked:
        projects = [Path(p) for p in tracked]
    else:
        root = args.root or Path.home() / "tools"
        projects = enumerate_projects(root)

    rows = test_guard.collect_test_guard_rows(projects)
    if getattr(args, "json", False):
        print(json.dumps([{
            "pj_name": r.pj_name,
            "pj_path": str(r.pj_path),
            "languages": sorted(r.languages),
            "uses_llm": r.uses_llm,
            "has_precommit_hook": r.has_precommit_hook,
            "has_pytest_no_llm": r.has_pytest_no_llm,
            "needs_attention": r.needs_attention,
        } for r in rows], indent=2, ensure_ascii=False))
    else:
        print(test_guard.format_test_guard_table(rows), end="")
    return 0


def _run_discover(args) -> int:
    """CC の `~/.claude/projects/` から PJ を検出し、対話的に track/ignore を決定。"""
    import fleet_config

    config = fleet_config.load_config()
    discovered = fleet_config.filter_valid_projects(
        fleet_config.discover_cc_projects()
    )
    candidates = fleet_config.diff_candidates(config, discovered)

    tracked_count = len(config.get("tracked_projects", []))
    ignored_count = len(config.get("ignored_projects", []))
    print(f"[fleet] 既存設定: tracked={tracked_count}, ignored={ignored_count}")

    if not candidates:
        print("[fleet] 新しい PJ 候補はありません。")
        return 0

    print(f"[fleet] 新候補 {len(candidates)} 件:")
    for i, pj in enumerate(candidates, 1):
        markers = []
        if (pj / "CLAUDE.md").is_file():
            markers.append("CLAUDE.md")
        if (pj / ".claude").is_dir():
            markers.append(".claude/")
        marker_str = ", ".join(markers) if markers else "(none)"
        print(f"  [{i:>2}] {pj}  ({marker_str})")

    if args.non_interactive:
        print("[fleet] --non-interactive 指定のため承認せず終了。")
        return 0

    print()
    print("各 PJ について 'a' (track), 'i' (ignore), 's' (skip=次回再提案) で回答。")
    print("'q' で中断・保存。空入力は skip 扱い。")
    print()

    changed = False
    for i, pj in enumerate(candidates, 1):
        while True:
            ans = input(f"  [{i}/{len(candidates)}] {pj}: [a/i/s/q] ").strip().lower()
            if ans in ("a", "i", "s", "q", ""):
                break
            print("  → 'a' (track), 'i' (ignore), 's' (skip), 'q' (quit) のいずれかを入力")
        if ans == "q":
            print("[fleet] 中断しました。ここまでの変更を保存します。")
            break
        if ans == "a":
            fleet_config.track_project(config, pj)
            changed = True
            print(f"    → tracked")
        elif ans == "i":
            fleet_config.ignore_project(config, pj)
            changed = True
            print(f"    → ignored")
        # s or empty: skip (do nothing)

    if changed:
        from datetime import datetime, timezone
        config["last_discovery"] = datetime.now(timezone.utc).isoformat()
        fleet_config.save_config(config)
        final_tracked = len(config.get("tracked_projects", []))
        final_ignored = len(config.get("ignored_projects", []))
        print(f"\n[fleet] 保存しました: tracked={final_tracked}, ignored={final_ignored}")
    else:
        print("\n[fleet] 変更なし（skip のみ）。")
    return 0


def _inject_token_metrics(rows: list[FleetRow], days: int = 30) -> None:
    """token_usage SoR から TOP-N 全体を引いて FleetRow に注入する。

    Match key: FleetRow.pj_name (basename) と pj_slug (encoded path 末尾セグメント) を
    末尾一致で照合する。データ無し PJ は None のまま。
    """
    try:
        import token_usage_query as tuq  # type: ignore
    except ImportError:
        return
    try:
        # 1 回で十分大きな N を取得して全 PJ をカバー
        consumers = tuq.top_n_consumers(days=days, n=10_000)
    except Exception:
        return
    if not consumers:
        return
    # pj_slug → metric。同名衝突時は最初を採用
    by_slug: dict[str, dict] = {}
    for c in consumers:
        slug = (c.get("pj_slug") or "").lower()
        if slug and slug not in by_slug:
            by_slug[slug] = c
    for row in rows:
        # row.pj_name 末尾セグメントは "-" で区切られた最後 (例: "rl-anything" → "anything")
        # token_usage_ingest._pj_slug_from_id と同ロジック
        last = row.pj_name.rstrip("-").split("-")[-1].lower() if row.pj_name else ""
        c = by_slug.get(last)
        if c is None:
            continue
        row.tokens_30d = c.get("tokens")
        hit = c.get("cache_hit_pct")
        row.cache_hit_pct = float(hit) if hit is not None else None


def _resolve_pj_id(query: str) -> str | list[str] | None:
    """`--pj` 引数を DB 上の pj_id に解決する。

    - 完全一致 (pj_id or pj_slug) があればそれを返す
    - 部分一致 (pj_id endswith / contains) を試す
    - 候補 1 件 → str、複数 → list[str] (ambiguous)、ゼロ → None
    """
    try:
        import token_usage_store as tus  # type: ignore
    except ImportError:
        return None
    try:
        rows = tus.query(
            "SELECT pj_id, ANY_VALUE(pj_slug) FROM token_usage GROUP BY pj_id"
        )
    except Exception as e:
        print(f"[fleet tokens] _resolve_pj_id query failed: {e}", file=sys.stderr)
        return None
    pairs = [(r[0], r[1] or "") for r in rows]
    # 1. exact match (pj_id 優先 → 一意なら即返す)
    for pj_id, _slug in pairs:
        if query == pj_id:
            return pj_id
    # 1b. slug exact match (複数あり得るので集める)
    slug_hits = [pj_id for pj_id, slug in pairs if query == slug]
    if len(slug_hits) == 1:
        return slug_hits[0]
    if len(slug_hits) > 1:
        return sorted(slug_hits)
    # 2. endswith match (slug 形式の suffix)
    suffix = f"-{query}" if not query.startswith("-") else query
    endswith_hits = [pj_id for pj_id, _ in pairs if pj_id.endswith(suffix)]
    if len(endswith_hits) == 1:
        return endswith_hits[0]
    if len(endswith_hits) > 1:
        return sorted(endswith_hits)
    # 3. contains match (fallback)
    contains_hits = [pj_id for pj_id, _ in pairs if query in pj_id]
    if len(contains_hits) == 1:
        return contains_hits[0]
    if len(contains_hits) > 1:
        return sorted(contains_hits)
    return None


def _run_tokens(args) -> int:
    """`rl-fleet tokens` サブコマンド。"""
    try:
        import token_usage_query as tuq  # type: ignore
        import token_usage_store as tus  # type: ignore
    except ImportError:
        print("token_usage modules not available", file=sys.stderr)
        return 1

    # backfill モード
    if getattr(args, "backfill", False):
        try:
            import token_usage_ingest as tui  # type: ignore
        except ImportError:
            print("token_usage_ingest not available", file=sys.stderr)
            return 1
        days = None if getattr(args, "all", False) else getattr(args, "days", 90)
        agg = tui.ingest_all_projects(days=days, progress=True)
        if getattr(args, "json", False):
            print(json.dumps(agg, ensure_ascii=False))
        else:
            print(
                f"[fleet tokens] backfill done: inserted={agg['inserted']} "
                f"skipped={agg['skipped']} files={agg['files_processed']} "
                f"projects={agg.get('projects', 0)}"
            )
        return 0

    days = getattr(args, "days", 30)

    # 空 DB チェック
    db_empty = (not tus.HAS_DUCKDB) or (not tus.USAGE_DB.exists())
    if not db_empty:
        try:
            row = tus.query("SELECT COUNT(*) FROM token_usage")
            db_empty = (not row) or (row[0][0] == 0)
        except Exception:
            db_empty = True

    if db_empty:
        msg = "[fleet tokens] No data. Run `rl-fleet tokens --backfill` to ingest transcripts."
        print(msg, file=sys.stderr)
        if getattr(args, "json", False):
            print(json.dumps({"empty": True}, ensure_ascii=False))
        return 0

    # PJ 別ドリルダウン
    pj = getattr(args, "pj", None)
    if pj:
        by = getattr(args, "by", "session") or "session"
        resolved = _resolve_pj_id(pj)
        if resolved is None:
            print(f"[fleet tokens] pj not found: {pj!r}", file=sys.stderr)
            return 1
        if isinstance(resolved, list):
            print(
                f"[fleet tokens] ambiguous --pj {pj!r}, multiple matches:",
                file=sys.stderr,
            )
            for cand in resolved:
                print(f"  {cand}", file=sys.stderr)
            return 1
        rows = tuq.pj_breakdown(resolved, by=by, limit=10)
        if getattr(args, "json", False):
            print(json.dumps({"pj_id": resolved, "by": by, "rows": rows}, ensure_ascii=False, default=str))
        else:
            print(f"## {resolved} — breakdown by {by}")
            for r in rows:
                print(f"  {r['key']}\t{_format_short_int(r.get('tokens', 0))}")
        return 0

    # anomaly モード
    if getattr(args, "anomaly", False):
        wow = tuq.wow_anomalies()
        cache = tuq.cache_hit_anomalies()
        if getattr(args, "json", False):
            print(json.dumps({"wow": wow, "cache_hit": cache}, ensure_ascii=False, default=str))
        else:
            print("## Anomalies")
            for a in wow:
                print(f"  WoW: {a['pj_id']} +{a['wow_pct']:.0f}% ({_format_short_int(a['last_week'])} → {_format_short_int(a['this_week'])})")
            for a in cache:
                print(f"  cache: {a['pj_id']} {a['last_hit_pct']:.0f}% → {a['this_hit_pct']:.0f}% (drop {a['drop_pt']:.0f}pt)")
        return 0

    # デフォルトサマリ: TOP 3 + anomaly
    top = tuq.top_n_consumers(days=days, n=3)
    wow = tuq.wow_anomalies()
    cache = tuq.cache_hit_anomalies()
    if getattr(args, "json", False):
        print(json.dumps({
            "top": top, "wow": wow, "cache_hit": cache, "days": days,
        }, ensure_ascii=False, default=str))
        return 0
    print(f"## Token Consumption (last {days} days)\n")
    print("TOP 3 consumers:")
    for i, c in enumerate(top, 1):
        hit = f" (cache hit {c['cache_hit_pct']:.0f}%)" if c.get("cache_hit_pct") is not None else ""
        print(f"  {i}. {c.get('pj_slug') or c['pj_id']}\t{_format_short_int(c['tokens'])}{hit}")
    if wow or cache:
        print("\nAnomalies detected:")
        for a in wow:
            print(f"  • {a['pj_id']}: WoW +{a['wow_pct']:.0f}% ({_format_short_int(a['last_week'])} → {_format_short_int(a['this_week'])})")
        for a in cache:
            print(f"  • {a['pj_id']}: cache hit {a['last_hit_pct']:.0f}% → {a['this_hit_pct']:.0f}% (drop {a['drop_pt']:.0f}pt)")
    return 0


def _parse_iso(ts: object) -> datetime | None:
    if not isinstance(ts, str):
        return None
    try:
        return datetime.fromisoformat(ts)
    except ValueError:
        return None


if __name__ == "__main__":
    raise SystemExit(main())
