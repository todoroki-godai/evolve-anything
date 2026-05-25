"""backfill_turn_indices — sessions.jsonl と corrections.jsonl に turn 系フィールドを追記。

constraint_decay が必要とする 2 フィールドを backfill する一度きり操作:
  - sessions.jsonl  : max_turn_index = human_message_count - 1
  - corrections.jsonl: turn_index (raw session JSONL の timestamp マッチングで算出)

安全設計:
  - 実行前に両ファイルを .bak-<timestamp> として自動バックアップ
  - --dry-run デフォルト True (明示的に False にしないと書き込まない)
  - tmpfile + atomic rename で書き込み途中のファイル破壊を防ぐ
"""
import json
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional


def _parse_ts(ts_str: str) -> Optional[datetime]:
    """ISO 8601 文字列を UTC aware datetime に変換する。失敗時は None。"""
    if not ts_str:
        return None
    try:
        return datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
    except ValueError:
        return None


def compute_turn_index(correction_ts: str, raw_session_path: Path) -> Optional[int]:
    """correction_ts 以前の最後の human ターンのインデックスを返す（0-indexed）。

    correction は UserPromptSubmit hook で発火するため、
    correction_ts ≤ その human ターンの送信時刻 に対応する。
    raw_session_path が存在しない、または該当ターンがない場合は None。
    """
    corr_dt = _parse_ts(correction_ts)
    if corr_dt is None or not raw_session_path.exists():
        return None

    last_idx: Optional[int] = None
    human_count = 0
    for line in raw_session_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if rec.get("type") not in ("human", "user"):
            continue
        turn_dt = _parse_ts(rec.get("timestamp", ""))
        if turn_dt is not None and turn_dt <= corr_dt:
            last_idx = human_count
        human_count += 1

    return last_idx


def find_session_raw_jsonl(session_id: str, projects_dir: Path) -> Optional[Path]:
    """session_id に対応する raw JSONL ファイルを projects_dir 以下から探す。"""
    for p in projects_dir.glob(f"*/{session_id}.jsonl"):
        return p
    return None


def _atomic_write(target: Path, content: str) -> None:
    """tmpfile に書き込んでから atomic rename する。"""
    tmp = target.with_suffix(".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(target)


def backfill_sessions(sessions_path: Path, dry_run: bool = True) -> int:
    """sessions.jsonl に max_turn_index = human_message_count - 1 を追記する。

    Returns:
        追記したレコード数
    """
    if not sessions_path.exists():
        return 0

    records = []
    added = 0
    for line in sessions_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            records.append(line)
            continue

        hmc = rec.get("human_message_count")
        if hmc and isinstance(hmc, int) and hmc > 0 and "max_turn_index" not in rec:
            rec["max_turn_index"] = hmc - 1
            added += 1

        records.append(json.dumps(rec, ensure_ascii=False))

    if not dry_run and added > 0:
        _atomic_write(sessions_path, "\n".join(records) + "\n")

    return added


def backfill_missing_sessions(
    sessions_path: Path,
    corrections_path: Path,
    projects_dir: Path,
    dry_run: bool = True,
) -> int:
    """corrections.jsonl にあって sessions.jsonl にない session を追加する。

    raw JSONL から human_message_count と max_turn_index を計算して追記する。

    Returns:
        追加したレコード数
    """
    if not corrections_path.exists():
        return 0

    # 既存 session_id を収集
    existing_ids: set = set()
    sessions_lines: List[str] = []
    if sessions_path.exists():
        for line in sessions_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            sessions_lines.append(line)
            try:
                rec = json.loads(line)
                existing_ids.add(rec.get("session_id", ""))
            except json.JSONDecodeError:
                pass

    # corrections から missing session_id を収集
    missing_ids: Dict[str, dict] = {}  # session_id → correction record (for context)
    for line in corrections_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        sid = rec.get("session_id", "")
        if sid and sid not in existing_ids and sid not in missing_ids:
            missing_ids[sid] = rec

    if not missing_ids:
        return 0

    added = 0
    new_lines = list(sessions_lines)
    for sid in missing_ids:
        raw = find_session_raw_jsonl(sid, projects_dir)
        if raw is None:
            continue

        hmc = 0
        project = ""
        first_ts = ""
        for line in raw.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not project:
                project = str(raw.parent.name)
            if not first_ts:
                first_ts = rec.get("timestamp", "")
            if rec.get("type") in ("human", "user"):
                hmc += 1

        if hmc == 0:
            continue

        new_rec = {
            "session_id": sid,
            "human_message_count": hmc,
            "max_turn_index": hmc - 1,
            "project": project,
            "timestamp": first_ts,
            "source": "backfill_turn_indices",
        }
        new_lines.append(json.dumps(new_rec, ensure_ascii=False))
        added += 1

    if not dry_run and added > 0:
        _atomic_write(sessions_path, "\n".join(new_lines) + "\n")

    return added


def backfill_corrections(
    corrections_path: Path,
    sessions_path: Path,
    projects_dir: Path,
    dry_run: bool = True,
) -> int:
    """corrections.jsonl に turn_index を追記する。

    各 correction の timestamp と raw session JSONL の human ターンを照合し、
    correction_ts 以前の最後の human ターンのインデックス（0-indexed）を記録する。

    Returns:
        追記したレコード数
    """
    if not corrections_path.exists():
        return 0

    records = []
    added = 0
    for line in corrections_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            records.append(line)
            continue

        if "turn_index" in rec:
            records.append(json.dumps(rec, ensure_ascii=False))
            continue

        sid = rec.get("session_id", "")
        correction_ts = rec.get("timestamp", "")
        if not sid or not correction_ts:
            records.append(json.dumps(rec, ensure_ascii=False))
            continue

        raw = find_session_raw_jsonl(sid, projects_dir)
        if raw is None:
            records.append(json.dumps(rec, ensure_ascii=False))
            continue

        turn_idx = compute_turn_index(correction_ts, raw)
        if turn_idx is not None:
            rec["turn_index"] = turn_idx
            added += 1

        records.append(json.dumps(rec, ensure_ascii=False))

    if not dry_run and added > 0:
        _atomic_write(corrections_path, "\n".join(records) + "\n")

    return added


def run_backfill(
    data_dir: Path,
    projects_dir: Path,
    dry_run: bool = True,
) -> dict:
    """バックアップ → 3 ステップの backfill を実行する。

    Returns:
        {"sessions_updated": int, "sessions_added": int, "corrections_updated": int}
    """
    from datetime import datetime as _dt

    sessions_path = data_dir / "sessions.jsonl"
    corrections_path = data_dir / "corrections.jsonl"

    if not dry_run:
        ts = _dt.now().strftime("%Y%m%d%H%M%S")
        if sessions_path.exists():
            shutil.copy2(sessions_path, sessions_path.with_suffix(f".jsonl.bak-{ts}"))
        if corrections_path.exists():
            shutil.copy2(corrections_path, corrections_path.with_suffix(f".jsonl.bak-{ts}"))

    sessions_updated = backfill_sessions(sessions_path, dry_run=dry_run)
    sessions_added = backfill_missing_sessions(
        sessions_path, corrections_path, projects_dir, dry_run=dry_run
    )
    corrections_updated = backfill_corrections(
        corrections_path, sessions_path, projects_dir, dry_run=dry_run
    )

    return {
        "sessions_updated": sessions_updated,
        "sessions_added": sessions_added,
        "corrections_updated": corrections_updated,
    }
