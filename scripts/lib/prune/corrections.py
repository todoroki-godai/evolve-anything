"""corrections.jsonl の読み込みと decay-based クリーンアップ（旧 prune.py 由来）。

- load_corrections: skill 別グループ化 + 後方互換フィールド補完
- cleanup_corrections: decay_days 超過の applied/skipped レコード削除

prune/__init__.py から re-export される（後方互換）。
DATA_DIR は package 経由で遅延参照する
（テスト mock.patch("prune.DATA_DIR", ...) 追従）。
"""
import json
from datetime import datetime, timezone
from typing import Dict, List

from .config import DEFAULT_DECAY_DAYS


def load_corrections() -> Dict[str, List[Dict]]:
    """corrections.jsonl を読み込み、skill_name 別にグループ化して返す。

    corrections.jsonl が存在しない場合は空辞書を返す。
    新旧フィールド両対応: matched_patterns, sentiment, decay_days, routing_hint,
    guardrail, reflect_status, extracted_learning, project_path がなくても読める。
    """
    from . import DATA_DIR  # noqa: PLC0415

    corrections_file = DATA_DIR / "corrections.jsonl"
    if not corrections_file.exists():
        return {}

    by_skill: Dict[str, List[Dict]] = {}
    for line in corrections_file.read_text(encoding="utf-8").splitlines():
        try:
            record = json.loads(line)
            skill = record.get("last_skill")
            if skill:
                # 新フィールドにデフォルト値を設定（後方互換）
                record.setdefault("matched_patterns", [record.get("correction_type", "unknown")])
                record.setdefault("sentiment", "negative")
                record.setdefault("decay_days", DEFAULT_DECAY_DAYS)
                record.setdefault("routing_hint", "correction")
                record.setdefault("guardrail", False)
                record.setdefault("reflect_status", "pending")
                record.setdefault("extracted_learning", "")
                record.setdefault("project_path", "")
                by_skill.setdefault(skill, []).append(record)
        except json.JSONDecodeError:
            continue
    return by_skill


def cleanup_corrections(dry_run: bool = False) -> Dict[str, int]:
    """corrections.jsonl から decay_days 超過の applied/skipped レコードを削除する。

    - applied/skipped で decay_days 超過 → 削除
    - pending レコード → 保持（削除しない）
    - decay_days 未設定のレコードは DEFAULT_DECAY_DAYS を使用

    ``dry_run`` が真の場合、removed/kept の算出・返り値は従来どおり行うが
    ファイルへの書き戻しは行わない（#154: dry-run 実行で corrections.jsonl が
    書き換わっていた不具合の修正）。

    Returns:
        {"removed": int, "kept": int} の統計情報。
    """
    from . import DATA_DIR  # noqa: PLC0415

    corrections_file = DATA_DIR / "corrections.jsonl"
    if not corrections_file.exists():
        return {"removed": 0, "kept": 0}

    now = datetime.now(timezone.utc)
    kept_lines: List[str] = []
    removed = 0

    for line in corrections_file.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            kept_lines.append(line)
            continue

        status = record.get("reflect_status", "pending")
        # pending レコードは常に保持
        if status not in ("applied", "skipped"):
            kept_lines.append(line)
            continue

        # decay_days 超過チェック
        decay_days = record.get("decay_days", DEFAULT_DECAY_DAYS)
        timestamp = record.get("timestamp", "")
        if not timestamp:
            kept_lines.append(line)
            continue

        try:
            ts_clean = timestamp.replace("Z", "+00:00")
            dt = datetime.fromisoformat(ts_clean)
            age_days = (now - dt).total_seconds() / 86400
        except (ValueError, TypeError):
            kept_lines.append(line)
            continue

        if age_days > decay_days:
            removed += 1
        else:
            kept_lines.append(line)

    # ファイルを書き戻し（dry_run 時はスキップ）
    if not dry_run:
        corrections_file.write_text(
            "\n".join(kept_lines) + "\n" if kept_lines else "",
            encoding="utf-8",
        )

    return {"removed": removed, "kept": len(kept_lines)}
