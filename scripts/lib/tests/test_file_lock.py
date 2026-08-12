"""rl_common/file_lock.py のユニットテスト（#287）。

decision 状態（marker / queue / optimize_history）の read-modify-write を直列化する
共有プリミティブ。LLM 非依存・決定論。
"""
import fcntl
import json
import subprocess
import sys
import threading
from pathlib import Path

import pytest

_LIB = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_LIB))

from rl_common.file_lock import (  # noqa: E402
    atomic_write_text,
    file_lock,
    read_only_file_lock,
    try_file_lock,
)


def test_atomic_write_creates_parents_and_leaves_no_tmp(tmp_path):
    target = tmp_path / "nested" / "state.json"
    atomic_write_text(target, json.dumps({"a": 1}))
    assert json.loads(target.read_text(encoding="utf-8")) == {"a": 1}
    # sibling tmp が残らない（reader が中途半端なファイルを拾わない）。
    assert [p.name for p in target.parent.iterdir()] == ["state.json"]


def test_atomic_write_overwrites_in_place(tmp_path):
    target = tmp_path / "state.json"
    atomic_write_text(target, "old")
    atomic_write_text(target, "new")
    assert target.read_text(encoding="utf-8") == "new"


def test_file_lock_creates_lock_dir(tmp_path):
    lock = tmp_path / "locks" / "slug.lock"
    with file_lock(lock):
        assert lock.exists()


def test_file_lock_is_reentrant_safe_across_sequential_uses(tmp_path):
    """連続取得（入れ子でない）は素通りする — 解放漏れの回帰防止。"""
    lock = tmp_path / "slug.lock"
    for _ in range(3):
        with file_lock(lock):
            pass


_WORKER = """
import sys, time
sys.path.insert(0, {lib!r})
from pathlib import Path
from rl_common.file_lock import atomic_write_text, file_lock

counter, lock = Path({counter!r}), Path({lock!r})
for _ in range(20):
    with file_lock(lock):
        value = int(counter.read_text(encoding="utf-8"))
        time.sleep(0.001)  # read と write の間を広げ、ロック無しなら必ず落ちるようにする
        atomic_write_text(counter, str(value + 1))
"""


# ── try_file_lock（#410 round2 [Should]②: non-blocking 取得）────────────────


def test_try_file_lock_acquires_when_free(tmp_path):
    lock = tmp_path / "slug.lock"
    with try_file_lock(lock) as acquired:
        assert acquired is True
        assert lock.exists()


def test_try_file_lock_yields_false_when_already_locked(tmp_path):
    """既存 file_lock（blocking）が保持中は、try_file_lock は待たずに False を返す。"""
    lock = tmp_path / "slug.lock"
    with file_lock(lock):
        with try_file_lock(lock) as acquired:
            assert acquired is False


def test_try_file_lock_releases_and_allows_next_acquire(tmp_path):
    lock = tmp_path / "slug.lock"
    with try_file_lock(lock) as acquired1:
        assert acquired1 is True
    with try_file_lock(lock) as acquired2:
        assert acquired2 is True


def test_try_file_lock_does_not_change_existing_file_lock_behavior(tmp_path):
    """既存の file_lock（blocking）は try_file_lock 追加後も従来どおり動く（回帰防止）。"""
    lock = tmp_path / "slug.lock"
    with file_lock(lock):
        assert lock.exists()


def test_concurrent_read_modify_write_does_not_lose_updates(tmp_path):
    """ロック無しなら lost update になる更新が、4 プロセス × 20 回でも全部残る。

    subprocess を使う（multiprocessing の spawn は pytest 配下で親を再 import する）。
    """
    counter = tmp_path / "counter"
    counter.write_text("0", encoding="utf-8")
    script = tmp_path / "worker.py"
    script.write_text(
        _WORKER.format(lib=str(_LIB), counter=str(counter), lock=str(tmp_path / "counter.lock")),
        encoding="utf-8",
    )

    procs = [subprocess.Popen([sys.executable, str(script)]) for _ in range(4)]
    for proc in procs:
        assert proc.wait(timeout=60) == 0

    assert int(counter.read_text(encoding="utf-8")) == 80


# ── read_only_file_lock（#402 PR-2 §0.1: 書込ゼロの read-only lock）──────────
#
# `file_lock` と違い parent の mkdir も append open もしない。sidecar が既存なら
# 読み取り open + `flock(LOCK_EX)` で排他取得し True を yield、不在なら False を
# yield して取得しない（dry-run 純度契約「1バイトも書かない」を破らない）。


def test_read_only_file_lock_leaves_existing_sidecar_byte_identical(tmp_path):
    """契約テスト1: 既存 sidecar の inode / size / mtime / ctime / 内容 hash を変えない。"""
    lock = tmp_path / "history.jsonl.lock"
    lock.write_text("existing sidecar content", encoding="utf-8")
    before_stat = lock.stat()
    before_bytes = lock.read_bytes()

    with read_only_file_lock(lock) as acquired:
        assert acquired is True

    after_stat = lock.stat()
    assert lock.read_bytes() == before_bytes
    assert after_stat.st_ino == before_stat.st_ino
    assert after_stat.st_size == before_stat.st_size
    assert after_stat.st_mtime == before_stat.st_mtime
    assert after_stat.st_ctime == before_stat.st_ctime


def test_read_only_file_lock_missing_sidecar_yields_false_and_creates_nothing(tmp_path):
    """契約テスト2: sidecar 不在時に False を yield し、ファイルもディレクトリも作らない。"""
    lock = tmp_path / "nested" / "does-not-exist" / "history.jsonl.lock"

    with read_only_file_lock(lock) as acquired:
        assert acquired is False

    assert not lock.exists()
    assert not lock.parent.exists()


def test_read_only_file_lock_raises_on_flock_unsupported(tmp_path, monkeypatch):
    """契約テスト8: `flock` が ENOTSUP/ENOLCK 等で失敗したら unlocked read へフォールバック
    せず例外を送出する。"""
    import errno

    lock = tmp_path / "history.jsonl.lock"
    lock.write_text("", encoding="utf-8")

    def _boom(*_a, **_kw):
        raise OSError(errno.ENOTSUP, "Operation not supported")

    monkeypatch.setattr(fcntl, "flock", _boom)

    entered = False
    with pytest.raises(OSError):
        with read_only_file_lock(lock):
            entered = True  # ここに来たら暗黙フォールバックしてしまっている
    assert entered is False


def test_read_only_file_lock_releases_fd_and_lock_on_exception(tmp_path):
    """契約テスト9: with 内で例外が起きても fd/lock が確実に解放される（デッドロックしない）。"""
    lock = tmp_path / "history.jsonl.lock"
    lock.write_text("", encoding="utf-8")

    class Boom(Exception):
        pass

    with pytest.raises(Boom):
        with read_only_file_lock(lock) as acquired:
            assert acquired is True
            raise Boom("simulated failure inside locked region")

    # 解放されていなければここで永久にブロックする（daemon thread + timeout で hang→fail 変換）。
    box: dict = {}

    def _reacquire():
        with read_only_file_lock(lock) as acquired2:
            box["acquired2"] = acquired2

    thread = threading.Thread(target=_reacquire, daemon=True)
    thread.start()
    thread.join(timeout=10)
    assert not thread.is_alive(), "例外後も lock が解放されず再取得がハングした"
    assert box.get("acquired2") is True


def test_read_only_file_lock_releases_fd_when_flock_wait_is_interrupted(tmp_path, monkeypatch):
    """契約テスト9続: `flock` 待機中の割り込み（EINTR 相当）でも fd が確実に閉じられる。

    リークしていれば以降の取得試行が「開きっぱなしの fd」に阻まれずそのまま成功する
    ことで確認する（fd 個別の解放漏れはプロセス全体のハングという形では顕在化しない
    ため、繰り返し取得できることを証拠にする）。
    """
    lock = tmp_path / "history.jsonl.lock"
    lock.write_text("", encoding="utf-8")

    with monkeypatch.context() as m:
        def _interrupted(*_a, **_kw):
            raise InterruptedError("simulated EINTR")

        m.setattr(fcntl, "flock", _interrupted)
        with pytest.raises(OSError):
            with read_only_file_lock(lock):
                pass

    # flock を実装に戻した後、繰り返し取得してもリークの影響が無いことを固定する。
    for _ in range(5):
        with read_only_file_lock(lock) as acquired:
            assert acquired is True
