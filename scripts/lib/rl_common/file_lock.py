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

    ブロッキング取得（他プロセスが保持中なら解放まで無期限に待つ）。無期限待機を避けたい
    呼び出し元は ``try_file_lock``（non-blocking 版）を使うこと。
    """
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "a", encoding="utf-8") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


@contextmanager
def try_file_lock(lock_path: Path) -> Iterator[bool]:
    """`lock_path` の非 blocking 排他取得を試みる（#410 round2 [Should]②）。

    取得できれば ``True`` を yield しロックを保持する（with を抜けると解放）。
    既に他プロセス/スレッドが保持中なら**待たずに** ``False`` を yield する（ロックは
    取得しない）。daily runner のような「1日1回・取れなければ翌日回ればよい」用途で、
    無期限 blocking の ``file_lock`` が後続プロセスを長時間止めるのを避けるために使う。

    既存の ``file_lock``（blocking）とは別名の関数として追加し、挙動・呼び出し元は
    一切変更しない（後方互換）。
    """
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "a", encoding="utf-8") as fh:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


@contextmanager
def read_only_file_lock(lock_path: Path) -> Iterator[bool]:
    """既存 sidecar を**書込ゼロ**で排他取得する（#402 PR-2 §0.1）。

    取得できたら ``True``、sidecar 不在なら ``False`` を yield する（取得しない）。
    ``file_lock`` と違い ``lock_path.parent.mkdir(...)`` も ``open(path, "a")``（不在
    なら作成する追記 open）もしない — 読み取り専用 open（``"r"``）は不在ファイルを
    作らないため、dry-run 純度契約（1バイトも書かない）を破らない。

    ``flock`` が ``ENOTSUP`` / ``ENOLCK`` 等で失敗した場合は**例外を送出する**（unlocked
    read へ暗黙フォールバックしない）。呼び出し元が黙って古い/不整合な状態を採用しない
    ための安全側の失敗。

    ⚠️ ``flock`` は **advisory lock** であり、この関数を経由しない非協調 writer（直接
    ``open(..., "w")`` する等）を排除しない。対応環境は macOS のローカル filesystem /
    通常の Linux filesystem。NFS / SMB 等のネットワーク filesystem は非対応（exclusive
    lock に書込 open を要する実装があり、書込ゼロが成立しない）。

    既存の ``file_lock`` / ``try_file_lock`` は一切変更しない（後方互換）。
    """
    try:
        fh = open(lock_path, "r", encoding="utf-8")
    except FileNotFoundError:
        yield False
        return
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            yield True
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    finally:
        fh.close()


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
