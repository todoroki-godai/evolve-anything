"""remediation suppression ledger — 却下された提案を永続化し重複提案を防ぐ（#477-2）。

背景: remediation の個別承認フローで、ユーザーがスキップ/却下した提案を記録する仕組みが
なかった（merge の `add_merge_suppression`（discover/suppression.py）や `triage_ledger`
（#308）に相当するものが remediation に不在）。その結果、べき等性原則
（重複した提案を行ってはならない MUST NOT）に反し、次回 evolve で同じ提案が再出していた。

本モジュールは却下を `LEDGER_ROOT/<slug>.jsonl` に dedup_key 単位で永続化し、TTL（既定45日）
内であれば同じ提案を抑制する。TTL を過ぎたら 1 回だけ再 surface する（環境が変わって
妥当になった可能性があるため再評価の機会を与える）。

設計は triage_ledger.py（#308）を範に踏襲する:
  - per-slug 分離 + worktree 安全 slug（`git rev-parse --git-common-dir` の親 basename。
    show-toplevel は worktree 内で worktree 名になり食い違うため不可。ADR-031 /
    pitfall_worktree_slug_show_toplevel）。
  - append-only + load 時 dedup_key で last-write-wins collapse。
  - **dry-run 非書込**: `record_rejection(persist=False)` は一切書き込まない
    （triage_ledger が --dry-run でも upsert していた #308 の前科と同じ轍を踏まない。
    pitfall_dryrun_stateful_store_write）。

決定論・LLM 非依存。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

_PLUGIN_DATA_ENV = os.environ.get("CLAUDE_PLUGIN_DATA", "")
DATA_DIR = Path(_PLUGIN_DATA_ENV) if _PLUGIN_DATA_ENV else Path.home() / ".claude" / "rl-anything"
LEDGER_ROOT = DATA_DIR / "remediation_suppression"
# 連続提示回数を追跡する surfaced マーカーの root（#494 発見1: record_rejection の安全網）。
SURFACED_ROOT = DATA_DIR / "remediation_surfaced"

# 解決されないまま連続でこの回数 surface された提案を自動却下する（決定論 fallback）。
DEFAULT_AUTO_REJECT_AFTER_RUNS = 2

# git repo 外（slug 解決不能）の保全先。
UNATTRIBUTED_SLUG = "_unattributed"

_SLUG_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")

DAY_SECONDS = 86400.0
# TTL: この日数を過ぎたら却下を1回だけ再 surface（再評価の機会）。triage_ledger と同値。
DEFAULT_TTL_DAYS = 45


# ─────────────────────────────────────────────────────────────────
# slug 解決（triage_ledger / optimize_history_store と同パターン）
# ─────────────────────────────────────────────────────────────────
def _sanitize_slug(slug: str) -> str:
    cleaned = _SLUG_UNSAFE.sub("_", slug)
    return cleaned or UNATTRIBUTED_SLUG


def resolve_slug(cwd: Optional[Path] = None) -> str:
    """current（または指定 cwd の）project slug を返す。

    worktree 安全: `git rev-parse --git-common-dir` で本体 repo の .git を取り、
    その親ディレクトリ名を slug とする。git repo 外なら UNATTRIBUTED_SLUG。
    """
    cwd_path = Path(cwd) if cwd is not None else Path.cwd()
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--git-common-dir"],
            cwd=str(cwd_path),
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return UNATTRIBUTED_SLUG

    if not out:
        return UNATTRIBUTED_SLUG

    common_dir = Path(out)
    if not common_dir.is_absolute():
        common_dir = (cwd_path / common_dir).resolve()
    repo_root = common_dir.parent
    slug = repo_root.name
    return slug or UNATTRIBUTED_SLUG


# ─────────────────────────────────────────────────────────────────
# dedup_key
# ─────────────────────────────────────────────────────────────────
def dedup_key(issue: Dict[str, Any]) -> str:
    """issue を一意に識別する安定キーを返す（type + file + 主要 detail）。

    同じ提案かどうかは「同じ問題タイプ・同じファイル・同じ核となる対象」で決まる。
    detail のうち提案の同一性を決める固定キー（path/matched/name/section/skill_name）
    のみを使い、行数のような可変値は含めない（lines が 11→12 になっても同じ提案として
    抑制する）。sha256 先頭16hex に畳む。
    """
    issue_type = str(issue.get("type", ""))
    file_path = str(issue.get("file", ""))
    detail = issue.get("detail", {}) or {}
    parts: List[str] = []
    for k in ("path", "matched", "name", "section", "skill_name", "ref", "target"):
        v = detail.get(k)
        if isinstance(v, (str, int, float)) and str(v):
            parts.append(f"{k}={v}")
    subject = "|".join(parts)
    raw = f"{issue_type}\x1f{file_path}\x1f{subject}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


# ─────────────────────────────────────────────────────────────────
# ストア
# ─────────────────────────────────────────────────────────────────
def ledger_path(slug: str) -> Path:
    return LEDGER_ROOT / f"{_sanitize_slug(slug)}.jsonl"


def load_ledger(slug: str) -> Dict[str, Dict[str, Any]]:
    """slug の台帳を dedup_key→record の dict で読む（last-write-wins collapse）。

    空行・壊れた JSON 行・dedup_key 欠落レコードはスキップ。
    """
    path = ledger_path(slug)
    if not path.exists():
        return {}
    records: Dict[str, Dict[str, Any]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        key = rec.get("dedup_key")
        if not key:
            continue
        records[key] = rec  # last-write-wins
    return records


def _upsert(record: Dict[str, Any], slug: str) -> None:
    path = ledger_path(slug)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def compact(slug: str) -> None:
    """append 累積を dedup_key ごと1行に物理圧縮する（肥大化対策）。"""
    records = load_ledger(slug)
    path = ledger_path(slug)
    if not records:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(records[k], ensure_ascii=False) for k in sorted(records)]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ─────────────────────────────────────────────────────────────────
# 記録 / 抑制判定
# ─────────────────────────────────────────────────────────────────
def _now() -> float:
    return time.time()


def record_rejection(
    issue: Dict[str, Any],
    *,
    slug: str,
    now: Optional[float] = None,
    ttl_days: int = DEFAULT_TTL_DAYS,
    persist: bool = True,
) -> Dict[str, Any]:
    """提案の却下を記録し、書き込んだ（または書き込むはずだった）レコードを返す。

    Args:
        persist: False の場合、台帳への書き込みを一切行わない（dry-run 経路）。
            evolve --dry-run の「変更なし」契約を守るためのゲート（#308 の轍を踏まない）。
    """
    now = _now() if now is None else now
    key = dedup_key(issue)
    record = {
        "dedup_key": key,
        "type": issue.get("type", ""),
        "file": issue.get("file", ""),
        "decided_at": now,
        "ttl_days": ttl_days,
    }
    if persist:
        _upsert(record, slug)
    return record


def is_suppressed(
    issue: Dict[str, Any],
    *,
    slug: str,
    now: Optional[float] = None,
) -> bool:
    """issue が現在抑制対象か（過去に却下され TTL 内）を返す。副作用なし。

    TTL を過ぎたレコードは抑制解除（False）。再 surface して再評価の機会を与える。
    """
    now = _now() if now is None else now
    record = load_ledger(slug).get(dedup_key(issue))
    if record is None:
        return False
    decided_at = float(record.get("decided_at", 0.0))
    rec_ttl_days = int(record.get("ttl_days", DEFAULT_TTL_DAYS))
    return now <= decided_at + rec_ttl_days * DAY_SECONDS


def filter_suppressed(
    issues: List[Dict[str, Any]],
    *,
    slug: str,
    now: Optional[float] = None,
) -> Dict[str, List[Dict[str, Any]]]:
    """issue リストを抑制対象 / surface 対象に2分割する。副作用なし。

    Returns:
        {"surface": [...], "suppressed": [...]}。入力順を保つ。
    """
    now = _now() if now is None else now
    ledger = load_ledger(slug)

    def _suppressed(it: Dict[str, Any]) -> bool:
        record = ledger.get(dedup_key(it))
        if record is None:
            return False
        decided_at = float(record.get("decided_at", 0.0))
        rec_ttl_days = int(record.get("ttl_days", DEFAULT_TTL_DAYS))
        return now <= decided_at + rec_ttl_days * DAY_SECONDS

    surface: List[Dict[str, Any]] = []
    suppressed: List[Dict[str, Any]] = []
    for it in issues:
        (suppressed if _suppressed(it) else surface).append(it)
    return {"surface": surface, "suppressed": suppressed}


# ─────────────────────────────────────────────────────────────────
# emit→reconcile 決定論 fallback（#494 発見1: record_rejection の安全網）
# ─────────────────────────────────────────────────────────────────
# 背景: 却下の記録は SKILL.md Step 5.5 の散文 MUST（assistant が inline python で record_rejection
# を叩く）だけが入口で、取りこぼすと却下が永久消失し #477 が解いた「同じ提案が毎回再出」が再発する
# （learning_skill_md_must_not_enforcement）。Step 7.8 の drain（restore_state の undrained 検出）
# のような安全網が remediation だけ無かった。
#
# 対処: surfaced マーカー（per-slug）で各提案 dedup_key の「連続提示回数」を決定論で追跡し、
# 解決されないまま閾値回数 surface され続けた提案を自動却下（record_rejection）する。
#   - 提案が今回検出されなくなった = 解決された（修正された / 既に suppressed）→ marker から落とす。
#   - 連続提示回数が auto_reject_after_runs に達した = ユーザーが対応も明示却下もしなかった
#     → 自動却下して再提示を止める（毎回再出の症状を断つ）。
# dry-run（persist=False）は marker / ledger に一切書かない（pitfall_dryrun_stateful_store_write）。
def surfaced_path(slug: str) -> Path:
    return SURFACED_ROOT / f"{_sanitize_slug(slug)}.json"


def _load_surfaced(slug: str) -> Dict[str, Dict[str, Any]]:
    """surfaced マーカーを dedup_key→{count, first_seen, last_seen} の dict で読む。"""
    path = surfaced_path(slug)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    entries = data.get("entries") if isinstance(data, dict) else None
    return entries if isinstance(entries, dict) else {}


def _write_surfaced(slug: str, entries: Dict[str, Dict[str, Any]]) -> None:
    path = surfaced_path(slug)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"slug": slug, "entries": entries}, ensure_ascii=False),
        encoding="utf-8",
    )


def reconcile_surfaced(
    current_issues: List[Dict[str, Any]],
    *,
    slug: str,
    now: Optional[float] = None,
    auto_reject_after_runs: int = DEFAULT_AUTO_REJECT_AFTER_RUNS,
    ttl_days: int = DEFAULT_TTL_DAYS,
    persist: bool = True,
) -> Dict[str, Any]:
    """個別承認に出す提案の連続提示を追跡し、未解決のまま閾値回数 surface されたものを自動却下する。

    各 evolve run の remediation phase（個別承認候補確定後）で1回呼ぶ。SKILL.md の inline
    record_rejection が走らなくても、毎回再出する提案をこの安全網が決定論で抑制する。

    Args:
        current_issues: 今回 surface する個別承認候補（proposable_custom_individual）。
        auto_reject_after_runs: 連続提示がこの回数に達したら自動却下する（既定2）。
        persist: False（dry-run）なら marker / ledger に一切書かない。返り値の件数は計算する。

    Returns:
        {"auto_rejected": int, "tracked": int, "resolved": int, "auto_rejected_keys": [str, ...]}
    """
    now = _now() if now is None else now
    prior = _load_surfaced(slug)

    # 今回 surface する提案を dedup_key で索引化（既に suppressed のものは追跡しない）。
    current_by_key: Dict[str, Dict[str, Any]] = {}
    for it in current_issues:
        if is_suppressed(it, slug=slug, now=now):
            continue
        current_by_key[dedup_key(it)] = it

    new_entries: Dict[str, Dict[str, Any]] = {}
    auto_rejected_keys: List[str] = []
    resolved = 0

    # 1) 今回も出ている提案: 連続提示回数を増やす（新規は 1）。閾値到達で自動却下。
    for key, issue in current_by_key.items():
        prev = prior.get(key) or {}
        count = int(prev.get("count", 0)) + 1
        first_seen = prev.get("first_seen", now)
        if count >= auto_reject_after_runs:
            # 自動却下: ledger に記録し marker からは落とす（以降 is_suppressed が True）。
            if persist:
                record_rejection(issue, slug=slug, now=now, ttl_days=ttl_days, persist=True)
            auto_rejected_keys.append(key)
        else:
            new_entries[key] = {"count": count, "first_seen": first_seen, "last_seen": now}

    # 2) 前回 marker にあったが今回出ていない提案 = 解決された → marker から落とす（却下しない）。
    for key in prior:
        if key not in current_by_key:
            resolved += 1

    if persist:
        _write_surfaced(slug, new_entries)

    return {
        "auto_rejected": len(auto_rejected_keys),
        "auto_rejected_keys": auto_rejected_keys,
        "tracked": len(new_entries),
        "resolved": resolved,
    }
