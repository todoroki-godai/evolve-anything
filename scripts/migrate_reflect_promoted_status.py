#!/usr/bin/env python3
"""#475 §4.6: reflect_confirmed かつ applied の過去記録を promoted へ一括移行する。

1回限りのマイグレーション。§6 で reflect_status の値域を分けたことで、旧来
`promote.py` が直書きしていた "applied"（＝実際には反映先に書かれたことを一度も
確認していない「昇格済み」だけの記録）を、正しい意味の "promoted" に書き換える。

新しいストア・marker ファイルは作らない（`learning_derive_state_from_logs_not_forward_write`
と同型）。移行済みかどうかは reflect_status の分布から read 時に判定できるため、
再実行しても対象が0件なら何もしない（冪等）。

既定 dry-run・`--apply` でのみ実書込（`bin/evolve-revert` と同じ規約）。
"""
import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PLUGIN_ROOT / "scripts" / "lib"))

from rl_common.correction_id import (
    assert_no_unexpected_content_loss,
    atomic_write_text_preserving_mode,
    corrections_write_lock,
    fcntl_unsupported_reason,
    snapshot_identities,
)

CORRECTIONS_FILE = Path.home() / ".claude" / "evolve-anything" / "corrections.jsonl"

# この移行の対象になる旧レコードの条件（#475 §4.6）。
_TARGET_SOURCE = "reflect_confirmed"
_TARGET_STATUS = "applied"
_NEW_STATUS = "promoted"


def _load_jsonl(filepath: Path) -> List[Dict[str, Any]]:
    if not filepath.exists():
        return []
    records = []
    for line in filepath.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


def is_migration_target(record: Dict[str, Any]) -> bool:
    """reflect_confirmed かつ applied のレコードか判定する（§4.6 の移行対象条件）。"""
    return (
        record.get("source") == _TARGET_SOURCE
        and record.get("reflect_status") == _TARGET_STATUS
    )


def migrate(
    corrections_file: Path = CORRECTIONS_FILE,
    dry_run: bool = True,
) -> Dict[str, Any]:
    """reflect_confirmed かつ applied の全件を promoted に書き換える。

    Returns:
        {"total": int, "migrated": int, "dry_run": bool, "already_migrated": bool}
    """
    if dry_run:
        return _migrate_text(corrections_file, write=False)
    reason = fcntl_unsupported_reason()
    if reason is not None:
        return {"total": 0, "migrated": 0, "dry_run": False,
                "already_migrated": False, "error": reason}
    with corrections_write_lock(corrections_file):
        return _migrate_text(corrections_file, write=True)


def _migrate_text(corrections_file: Path, *, write: bool) -> Dict[str, Any]:
    text = corrections_file.read_text(encoding="utf-8") if corrections_file.exists() else ""
    records: List[Dict[str, Any]] = []
    output_lines: List[str] = []
    touched: List[str] = []
    for raw_line in text.splitlines():
        if not raw_line.strip():
            output_lines.append(raw_line)
            continue
        try:
            record = json.loads(raw_line.strip())
        except json.JSONDecodeError:
            output_lines.append(raw_line)
            continue
        if not isinstance(record, dict):
            output_lines.append(raw_line)
            continue
        records.append(record)
        if is_migration_target(record):
            touched.append(raw_line)
            if write:
                updated = dict(record)
                updated["reflect_status"] = _NEW_STATUS
                output_lines.append(json.dumps(updated, ensure_ascii=False))
                continue
        output_lines.append(raw_line)
    targets = [record for record in records if is_migration_target(record)]

    result: Dict[str, Any] = {
        "total": len(records),
        "migrated": len(targets),
        "dry_run": not write,
        "already_migrated": len(targets) == 0,
    }

    if not write or not targets:
        return result

    new_content = "\n".join(output_lines) + "\n" if output_lines else ""
    assert_no_unexpected_content_loss(
        snapshot_identities(text), snapshot_identities(new_content),
        touched_before=snapshot_identities("\n".join(touched)),
    )
    atomic_write_text_preserving_mode(corrections_file, new_content)

    return result


def _raw_is_migration_target(line: str) -> bool:
    try:
        record = json.loads(line.strip())
    except json.JSONDecodeError:
        return False
    return isinstance(record, dict) and is_migration_target(record)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="reflect_confirmed かつ applied の過去記録を promoted へ一括移行する（#475 §4.6）"
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="実際に corrections.jsonl を書き換える（既定は dry-run で書込ゼロ）",
    )
    parser.add_argument(
        "--corrections-file", type=str, default=None,
        help="corrections.jsonl のパス（テスト用）",
    )
    args = parser.parse_args()

    corrections_file = Path(args.corrections_file) if args.corrections_file else CORRECTIONS_FILE
    result = migrate(corrections_file, dry_run=not args.apply)

    print(json.dumps(result, ensure_ascii=False, indent=2))

    if args.apply and result["migrated"] > 0:
        # §4.6: 初回だけの通知（migrated 件数がそのまま「今まで見えなかった件数」）。
        print(
            f"これまでの「はい」{result['migrated']}件は反映されていませんでした。"
            "/reflect で順次確認できます。",
            file=sys.stderr,
        )


if __name__ == "__main__":
    main()
