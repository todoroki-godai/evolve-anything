#!/usr/bin/env python3
"""既存 corrections.jsonl に位置非依存 correction_id を一度だけ付与する。"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


PLUGIN_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PLUGIN_ROOT / "scripts" / "lib"))

from rl_common import persistence
from rl_common.correction_id import (
    find_duplicate_ids,
    new_correction_id,
    validate_correction_id,
)


DEFAULT_CORRECTIONS = Path.home() / ".claude" / "evolve-anything" / "corrections.jsonl"


@dataclass
class MigrationResult:
    status: str
    total: int = 0
    newly_assigned: int = 0
    malformed_lines: int = 0
    duplicates: list[str] = field(default_factory=list)
    reason: Optional[str] = None
    initial_identity: Optional[dict] = None
    final_identity: Optional[dict] = None
    backup_path: Optional[str] = None


def _identity_of(stat_result, content: str) -> dict:
    return {
        "inode": stat_result.st_ino,
        "size": stat_result.st_size,
        "mtime_ns": stat_result.st_mtime_ns,
        "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
    }


def _backup(filepath: Path) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    backup_path = filepath.with_name(f"{filepath.name}.bak-{timestamp}")
    shutil.copy2(filepath, backup_path)
    if not backup_path.is_file():
        raise OSError(f"backup was not created: {backup_path}")
    return backup_path


def migrate(filepath: Path, *, dry_run: bool = True) -> MigrationResult:
    filepath = Path(filepath)
    if not persistence._HAVE_FCNTL:
        return MigrationResult(
            status="retry_required",
            reason="fcntl unavailable: migration is not supported",
        )
    if not filepath.exists():
        return MigrationResult(status="completed", total=0, newly_assigned=0)
    if filepath.is_symlink():
        return MigrationResult(status="conflict", reason="symlink_not_supported")

    backup_path: Optional[Path] = None
    try:
        if not dry_run:
            backup_path = _backup(filepath)
        orig_stat = filepath.stat()
        raw_content = filepath.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        return MigrationResult(
            status="retry_required",
            reason=str(error),
            backup_path=str(backup_path) if backup_path else None,
        )

    initial_identity = _identity_of(orig_stat, raw_content)
    raw_lines = raw_content.splitlines()
    new_lines: list[str] = []
    final_records: list[dict] = []
    newly_assigned = 0
    malformed = 0

    for line in raw_lines:
        stripped = line.strip()
        if not stripped:
            new_lines.append(line)
            continue
        try:
            record = json.loads(stripped)
        except json.JSONDecodeError:
            new_lines.append(line)
            malformed += 1
            continue
        if not isinstance(record, dict):
            new_lines.append(line)
            malformed += 1
            continue

        correction_id = record.get("correction_id")
        if validate_correction_id(correction_id):
            new_lines.append(line)
        else:
            record = dict(record)
            record["correction_id"] = new_correction_id()
            newly_assigned += 1
            new_lines.append(json.dumps(record, ensure_ascii=False))
        final_records.append(record)

    duplicate_counts = find_duplicate_ids(final_records)
    common = {
        "total": len(raw_lines),
        "newly_assigned": newly_assigned,
        "malformed_lines": malformed,
        "initial_identity": initial_identity,
        "backup_path": str(backup_path) if backup_path else None,
    }
    if duplicate_counts:
        return MigrationResult(
            status="conflict",
            duplicates=sorted(duplicate_counts),
            **common,
        )
    if dry_run:
        return MigrationResult(status="dry_run", **common)

    new_content = "\n".join(new_lines) + "\n" if new_lines else ""
    try:
        tmp_fd, tmp_path = tempfile.mkstemp(
            dir=str(filepath.parent), suffix=".correction_id_migrate.tmp"
        )
    except OSError as error:
        return MigrationResult(status="retry_required", reason=str(error), **common)

    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as output:
            output.write(new_content)
        os.chmod(tmp_path, stat.S_IMODE(orig_stat.st_mode))

        current_stat = filepath.stat()
        current_content = filepath.read_text(encoding="utf-8")
        current_identity = _identity_of(current_stat, current_content)
        identity_keys = ("inode", "size", "mtime_ns", "sha256")
        if tuple(current_identity[key] for key in identity_keys) != tuple(
            initial_identity[key] for key in identity_keys
        ):
            os.unlink(tmp_path)
            return MigrationResult(
                status="conflict",
                reason="file changed between read and replace (identity/hash mismatch)",
                final_identity=current_identity,
                **common,
            )
        os.replace(tmp_path, filepath)
    except (OSError, UnicodeDecodeError) as error:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        return MigrationResult(status="retry_required", reason=str(error), **common)

    try:
        verify_content = filepath.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        return MigrationResult(
            status="incomplete",
            reason=f"post-write read failed: {error}",
            **common,
        )
    if verify_content != new_content:
        return MigrationResult(
            status="incomplete",
            reason="post-write content mismatch",
            **common,
        )

    try:
        final_identity = _identity_of(filepath.stat(), verify_content)
    except OSError as error:
        return MigrationResult(
            status="incomplete",
            reason=f"post-write stat failed: {error}",
            **common,
        )
    return MigrationResult(status="completed", final_identity=final_identity, **common)


_EXIT_CODES = {
    "dry_run": 0,
    "completed": 0,
    "incomplete": 1,
    "conflict": 2,
    "retry_required": 3,
}


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="バックアップを作成して tempfile + os.replace で移行を適用する",
    )
    parser.add_argument(
        "filepath",
        nargs="?",
        type=Path,
        default=DEFAULT_CORRECTIONS,
        help="対象 corrections.jsonl",
    )
    args = parser.parse_args(argv)
    result = migrate(args.filepath, dry_run=not args.apply)
    print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    return _EXIT_CODES[result.status]


if __name__ == "__main__":
    raise SystemExit(main())
