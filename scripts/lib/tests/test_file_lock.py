"""rl_common/file_lock.py のユニットテスト（#287）。

decision 状態（marker / queue / optimize_history）の read-modify-write を直列化する
共有プリミティブ。LLM 非依存・決定論。
"""
import json
import subprocess
import sys
from pathlib import Path

_LIB = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_LIB))

from rl_common.file_lock import atomic_write_text, file_lock, try_file_lock  # noqa: E402


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
