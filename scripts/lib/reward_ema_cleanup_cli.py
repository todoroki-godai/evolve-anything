"""`reward_ema.jsonl` から旧 Agent 帰属行を除去する CLI 本体（#480）。

既定は read-only dry-run。apply は原本を丸ごとバックアップしてから、
``rl_common.file_lock`` の sidecar lock と atomic replace を使って正本を置換する。

ADR-049 の ``store_write`` / ``store_write_raw`` は JSONL の append 専用であり、既存行の
削除（read-modify-write）には使えない。ここで append barrier を迂回して直接 open/write を
再発明せず、read-modify-write の単一ソース ``file_lock`` / ``atomic_write_text`` を使う。
書込み先は既定で registry 登録済み canonical ``reward_ema.jsonl`` に固定し、``--path`` は
隔離コピーに対する検証・復元用の明示パス例外口である。
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import sys
import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from rl_common import DATA_DIR
from rl_common.file_lock import atomic_write_text, file_lock
from rl_common.path_display import home_relative_display
from rl_common.usage_schema import is_agent_skill_label

STORE_NAME = "reward_ema.jsonl"


class CleanupError(Exception):
    """安全に検査・是正できない入力を表す。"""


@dataclass(frozen=True)
class Inspection:
    path: Path
    original_text: str
    cleaned_text: str
    total_records: int
    removed_records: int
    skills: dict[str, int]
    backup_path: Optional[Path] = None


def inspect_file(path: Path) -> Inspection:
    """全行を検証し、Agent 行を除いた内容をメモリ上だけで組み立てる。"""
    try:
        original = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise CleanupError(f"対象ファイルがありません: {_display(path)}") from exc
    except (OSError, UnicodeError) as exc:
        raise CleanupError(f"対象ファイルを読めません: {exc}") from exc

    kept: list[str] = []
    skills: Counter[str] = Counter()
    total = 0
    for line_no, line in enumerate(original.splitlines(keepends=True), start=1):
        if not line.strip():
            kept.append(line)
            continue
        try:
            record = json.loads(line)
        except (json.JSONDecodeError, UnicodeError) as exc:
            raise CleanupError(f"{line_no} 行目が有効な JSON ではありません: {exc}") from exc
        if not isinstance(record, dict):
            raise CleanupError(f"{line_no} 行目が JSON object ではありません")
        total += 1
        skill = record.get("skill")
        if is_agent_skill_label(skill):
            skills[skill] += 1
        else:
            kept.append(line)
    return Inspection(
        path=path,
        original_text=original,
        cleaned_text="".join(kept),
        total_records=total,
        removed_records=sum(skills.values()),
        skills=dict(sorted(skills.items())),
    )


def _backup_name(path: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    return path.with_name(f"{path.name}.backup-{stamp}-{uuid.uuid4().hex[:8]}")


def _create_backup_locked(result: Inspection, mode: int) -> Path:
    backup = _backup_name(result.path)
    atomic_write_text(backup, result.original_text)
    os.chmod(backup, mode)
    return backup


def apply_cleanup(path: Path) -> Inspection:
    """sidecar lock 下で再読込し、バックアップ成功後だけ正本を atomic replace する。"""
    lock_path = path.with_name(f".{path.name}.lock")
    with file_lock(lock_path):
        result = inspect_file(path)
        if result.removed_records == 0:
            return result
        try:
            mode = path.stat().st_mode & 0o777
            backup = _create_backup_locked(result, mode)
            atomic_write_text(path, result.cleaned_text)
            os.chmod(path, mode)
        except OSError as exc:
            raise CleanupError(f"書込みに失敗しました: {exc}") from exc
        return Inspection(**{**result.__dict__, "backup_path": backup})


def _display(path: Path) -> str:
    return home_relative_display(path.expanduser().resolve())


def _render(result: Inspection, *, applied: bool) -> None:
    mode = "apply 完了" if applied and result.removed_records else "dry-run"
    if applied and result.removed_records == 0:
        mode = "apply（変更なし）"
    print(f"[evolve-reward-ema-cleanup] {mode}")
    print(f"対象: {_display(result.path)}")
    print(f"除去対象: {result.removed_records} / {result.total_records} 件")
    print("理由: rl_common.usage_schema.is_agent_skill_label が Agent 帰属ラベルと判定")
    print("skill 別:")
    if result.skills:
        for skill, count in result.skills.items():
            print(f"  - {skill}: {count} 件")
    else:
        print("  - なし")
    if not applied:
        print("書込み: なし（適用する場合は --apply を明示）")
    if result.backup_path is not None:
        backup = _display(result.backup_path)
        target = _display(result.path)
        print(f"バックアップ: {backup}")
        print(f"復元: cp -- {shlex.quote(backup)} {shlex.quote(target)}")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="evolve-reward-ema-cleanup",
        description=(
            "reward_ema.jsonl の旧 Agent 帰属行を除去する。既定は dry-run。"
            "--apply 時は書込み前に原本をバックアップする"
        ),
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="バックアップ作成後に対象行を実際に除去する（既定は dry-run・書込ゼロ）",
    )
    parser.add_argument(
        "--path", type=Path, default=None, metavar="PATH",
        help="隔離コピーを検査するための明示パス（省略時は canonical reward_ema.jsonl）",
    )
    args = parser.parse_args(argv)
    target = args.path.expanduser() if args.path else DATA_DIR / STORE_NAME
    try:
        result = apply_cleanup(target) if args.apply else inspect_file(target)
    except CleanupError as exc:
        print(f"[evolve-reward-ema-cleanup] エラー: {exc}", file=sys.stderr)
        return 1
    _render(result, applied=args.apply)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
