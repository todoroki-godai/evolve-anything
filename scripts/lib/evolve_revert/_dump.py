"""evolve_revert._dump — ``--dump-before``（#402 段階3 §2 手順3 / C14, C15）。

revert を実行せず before 本文を指定パスへ取り出すだけの操作。``--apply`` との排他は
CLI 側（段階4）が強制する契約——本関数自体は revert（対象ファイル・history への
書込）を一切行わない。dry-run の対象外（明示的な書込操作。決定2の CHANGELOG decode
ワンライナーの CLI 版）。
"""
from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Union

from evolve_decision_ids import _decompress_before_content

from ._entry import find_entry
from ._target import resolve_target

REASON_ENTRY_NOT_FOUND = "entry_not_found"
REASON_BEFORE_UNAVAILABLE = "before_unavailable"
REASON_DEST_EXISTS = "dest_exists"
REASON_DEST_IS_TARGET = "dest_is_target"


@dataclass(frozen=True)
class DumpResult:
    ok: bool
    reason: Optional[str] = None
    path: Optional[str] = None


def dump_before(
    entry_id: str, dest: Union[str, Path], *, slug: Optional[str] = None
) -> DumpResult:
    """entry_id の before 本文全文を ``dest`` へ書き出す（revert は実行しない）。

    - 出力先が既存なら**既定で拒否**する（上書きしない）
    - 出力先が対象ファイル自身と同一パスなら拒否する（skills ディレクトリ内へ dump
      しようとして対象を壊す事故を防ぐ・tacchi）
    - **publish は ``os.link`` による atomic no-clobber**（C15）。「不在確認 →
      ``os.replace``」は確認後に作られたファイルを上書きするため禁止。固定する
      不変条件: ①既存を上書きしない ②完成前の内容を出力先名で公開しない
      ③失敗時に部分ファイルを残さない
    """
    lookup = find_entry(entry_id, slug)
    if lookup.entry is None:
        return DumpResult(ok=False, reason=REASON_ENTRY_NOT_FOUND)
    entry = lookup.entry

    before_b64 = entry.get("revert_before_b64")
    if not before_b64:
        return DumpResult(ok=False, reason=REASON_BEFORE_UNAVAILABLE)

    dest_path = Path(dest).expanduser()
    resolution = resolve_target(entry)
    if resolution.path is not None:
        try:
            if dest_path.resolve() == resolution.path.resolve():
                return DumpResult(ok=False, reason=REASON_DEST_IS_TARGET)
        except OSError:
            pass  # 解決不能なら同一性判定不能——existing チェックへフォールバック

    if dest_path.exists():
        return DumpResult(ok=False, reason=REASON_DEST_EXISTS)

    before_content = _decompress_before_content(before_b64)

    dest_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest_path.with_name(f".{dest_path.name}.{uuid.uuid4().hex}.tmp")
    try:
        tmp.write_text(before_content, encoding="utf-8")
        # ②完成前の内容を出力先名で公開しない: tmp が完成してから初めて dest 名を作る。
        # ①③: os.link は dest が既存なら FileExistsError（不変条件①）。tmp は finally
        # で必ず unlink する（不変条件③・失敗時に部分ファイルを残さない）。
        os.link(tmp, dest_path)
    except FileExistsError:
        return DumpResult(ok=False, reason=REASON_DEST_EXISTS)
    finally:
        if tmp.exists():
            tmp.unlink()

    return DumpResult(ok=True, path=str(dest_path))
