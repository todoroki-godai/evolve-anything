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


def seqlock_read(lock_path: Path, read_fn, *, max_retries: int = 5):
    """sidecar 不在でも書込ゼロで一貫した snapshot を読む（seqlock 型 check-after・
    #402 PR-2 §0.2 の一般化）。

    ``emit_decisions`` の dry-run 経路（``evolve_decisions/_emit.py::_dry_run_snapshot``）
    が最初に実装したプロトコルを、2つ目の呼び出し元（revert apply engine の dry-run）が
    現れた時点で共有 primitive として抽出したもの（design-before-fanout）。``_emit.py``
    は committed/tested 済みのため本関数への移行は行わない（後方互換・挙動は不変。
    将来 follow-up での migration は可能）。

    手順（設計 §0.2）:
      1. ``read_only_file_lock`` を試みる
         → 取得できた: そのまま lock 下で ``read_fn()`` を呼ぶ。完了（``acquired=True``）
         → 不在: 2 へ
      2. lock 無しで ``read_fn()`` を呼ぶ（暫定 snapshot）
      3. 読了後に sidecar の不在を再確認する
         → まだ不在: 単調性契約（§0.3）より読区間全体で revert は動いていない。暫定
           snapshot を採用する（``acquired=False``）
         → 出現していた: 暫定 snapshot を破棄し 1 へ戻る（``max_retries`` 上限あり）

    Returns:
        ``(value, acquired)`` — ``value`` は ``read_fn()`` の返り値。``acquired`` は
        locked 経路で読めたか（``True``）／unlocked check-after 経路で読んだか
        （``False``）。呼び出し側はこれを使って §0.3 の「sidecar 不在なのに history に
        signal がある」警告等、ドメイン固有の判定を行える。

    Raises:
        TimeoutError: ``max_retries`` 回試みても snapshot が安定しなかった（sidecar の
            出現/消失を繰り返す）場合。呼び出し側はこれを「新しい状態を一切公開しない」
            契約で扱うこと（§0.2: marker を先に公開してから失敗する順序を作らない）。
    """
    for _ in range(max_retries):
        with read_only_file_lock(lock_path) as acquired:
            if acquired:
                return read_fn(), True
            value = read_fn()
        if not lock_path.exists():
            return value, False
        # 出現していた → 暫定 snapshot を破棄し、次のループで locked 経路を再試行する。
    raise TimeoutError(
        f"seqlock_read: sidecar keeps appearing/disappearing for {lock_path} "
        f"after {max_retries} attempts"
    )


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
