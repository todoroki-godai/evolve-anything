#!/usr/bin/env python3
"""ファイル単位の排他ロックと atomic write の単一ソース（#287）。

evolve の decision 状態は marker / queue / optimize_history の3ファイルにまたがるが、
`flock` を持っていたのは marker JSON だけだった＝「壊れた JSON は避けられるが、判断の
消失・重複は避けられない」。read-modify-write を持つストアがこの2関数を共有する。

⚠️ `flock` は **open file description 単位**なので、同一プロセスで同じロックを入れ子に
取ると自分自身と deadlock する。ロック下から呼ぶ内部処理はロックを取らない `_locked`
版に分けること（`evolve_decisions` の marker purge が実例）。
"""
from __future__ import annotations

import fcntl
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


@contextmanager
def file_lock(lock_path: Path) -> Iterator[None]:
    """`lock_path` を排他ロックして read-modify-write を process 間で直列化する。

    ロックは対象ファイルそのものでなく sidecar に取る（atomic replace は inode を
    差し替えるため、対象ファイルに取ったロックは replace 後の新 inode を守らない）。
    """
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "a", encoding="utf-8") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def atomic_write_text(path: Path, text: str) -> None:
    """reader が部分内容を見ないよう sibling tmp から atomic replace する。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(path)
    finally:
        if tmp.exists():
            tmp.unlink()
