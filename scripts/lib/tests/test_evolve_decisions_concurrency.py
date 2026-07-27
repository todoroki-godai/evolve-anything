"""evolve decision 状態遷移の並行競合まわりのテスト（#287）。

`evolve --drain` の状態は marker / queue / optimize_history の3ファイルにまたがるが、
flock を持っていたのは marker だけだった＝「ファイル破損は避けられるが、判断の消失・
誤帰属・重複は避けられない」。ここでは以下を固定する:

  1. queue の read-modify-write が並行追加を落とさない（#287-1）
  2. optimize_history の「既存確認 → append」が同時実行で重複しない（#287-2）
  3. drain のスナップショット→purge が単一ロックで、かつ自己 deadlock しない（#287-3）
  4. TTL 切れ・破損 marker がディスク残骸として残らない（#287-4）

すべて LLM-free。
"""
import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_LIB = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_LIB))

import evolve_decisions as ed  # noqa: E402


@pytest.fixture
def skill_file(tmp_path):
    p = tmp_path / "skills" / "my-skill" / "SKILL.md"
    p.parent.mkdir(parents=True)
    p.write_text("# my-skill\n\nトリガー: foo\n\n手順。\n", encoding="utf-8")
    return p


@pytest.fixture
def roots(monkeypatch, tmp_path):
    monkeypatch.setattr(ed, "QUEUE_ROOT", tmp_path / "evolve_decisions")
    monkeypatch.setattr(ed, "MARKER_ROOT", tmp_path / "evolve_pending")
    return tmp_path


def _entry(path: str, sha: str) -> dict:
    return {
        "id": ed._proposal_id(path, sha),
        "run_id": "evrun_test",
        "skill_name": "my-skill",
        "skill_path": path,
        "before_sha": sha,
        "fitness_func": "skill_quality",
        "pattern": "p",
    }


# ─── 1. queue の read-modify-write（#287-1）────────────────────────────────


def test_write_queue_is_atomic_and_leaves_no_tmp(roots):
    ed._write_queue("s", [{"id": "a"}, {"id": "b"}])
    path = ed.queue_path_for("s")
    assert [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()] == [
        {"id": "a"},
        {"id": "b"},
    ]
    assert not [p for p in path.parent.iterdir() if p.name.startswith(".")]


def test_emit_holds_queue_lock_for_read_modify_write(roots, skill_file):
    """#287-1: emit の queue RMW がロック下にあることを決定論で固定する。

    `read_queue` → 編集 → `_write_queue` が非ロックだと最後の上書きが勝ち、別 run の
    追加が失われる（ファイルは壊れないので気づけない）。競合窓が短く「同時実行して
    壊れるか」では再現しないため、ロックを外から保持して emit が進めないことを見る。
    """
    result = {
        "phases": {
            "discover": {
                "matched_skills": [
                    {"matched_skill": "my-skill", "skill_path": str(skill_file), "pattern": "p"}
                ]
            }
        }
    }
    done = threading.Event()

    def _emit():
        ed.emit_decisions(result, dry_run=False, slug="s")
        done.set()

    with ed._queue_lock("s"):
        thread = threading.Thread(target=_emit, daemon=True)
        thread.start()
        assert not done.wait(1.0), "queue ロックを持っている間に書き込まれた"
        assert ed.read_queue("s") == []

    assert done.wait(30), "ロック解放後も完走しなかった"
    assert len(ed.read_queue("s")) == 1


def test_ingest_rereads_queue_before_write_so_concurrent_adds_survive(
    roots, skill_file, monkeypatch, tmp_path
):
    """#287-1: ingest の判断中に別 run が queue へ追加した entry を消さない。

    旧実装は drain 開始時に読んだ pending から `remaining` を組み立てて丸ごと上書き
    していたため、判断中に入った追加が最後の上書きで消えた。判断はロック外・書き込み
    直前にロック下で読み直す方式なら生き残る。
    """
    before = ed._sha256(skill_file.read_text(encoding="utf-8"))
    ed._write_queue("s", [_entry(str(skill_file), before)])
    skill_file.write_text("# my-skill\n\n適用済み\n", encoding="utf-8")  # accept 相当

    concurrent = {"id": "evdiff_concurrent", "run_id": "evrun_other", "skill_path": "/x"}

    def _fake_recorder(**kwargs):
        # 判断中に別プロセスが emit したのと同じ状況を作る。
        ed._write_queue("s", ed.read_queue("s") + [concurrent])
        return {"id": kwargs.get("entry_id")}

    monkeypatch.setattr(ed, "_load_recorder", lambda: _fake_recorder)

    summary = ed.ingest_decisions("s", history_file=tmp_path / "hist.jsonl")

    assert len(summary["accepted"]) == 1
    remaining_ids = {e["id"] for e in ed.read_queue("s")}
    assert "evdiff_concurrent" in remaining_ids  # 並行追加が生き残る
    assert summary["accepted"][0] not in remaining_ids  # 消化済みは消える


# ─── 2. optimize_history の同時 append（#287-2）────────────────────────────


_RECORDER = """
import sys
sys.path.insert(0, {lib!r})
sys.path.insert(0, {fitness!r})
from pathlib import Path
from fitness_evolution import record_evolve_diff_decision

record_evolve_diff_decision(
    skill_name="s",
    after_content="# s\\n\\n本文\\n",
    diff_summary="d",
    human_accepted=True,
    history_file=Path({hist!r}),
    entry_id={entry_id!r},
)
"""


def _run_recorders(tmp_path, hist, entry_ids):
    fitness_dir = _LIB.parent.parent / "skills" / "evolve-fitness" / "scripts"
    procs = []
    for i, entry_id in enumerate(entry_ids):
        script = tmp_path / f"rec_{i}.py"
        script.write_text(
            _RECORDER.format(
                lib=str(_LIB), fitness=str(fitness_dir), hist=str(hist), entry_id=entry_id
            ),
            encoding="utf-8",
        )
        procs.append(subprocess.Popen([sys.executable, str(script)]))
    for proc in procs:
        assert proc.wait(timeout=120) == 0
    if not hist.exists():
        return []
    return [json.loads(l) for l in hist.read_text(encoding="utf-8").splitlines() if l.strip()]


def test_record_evolve_diff_decision_holds_history_lock(tmp_path):
    """#287-2: 「既存確認 → append」がロック下にあることを決定論で固定する。

    非ロックだと2プロセスが同時に確認を抜けて2行入り、accept 率・outcome_attribution・
    ADR-046 の昇格判定に非独立証拠が流入する（#279 の N 重記録と同じ汚染）。ただし実際の
    競合窓は数十µs しかなく「同時に走らせて重複するか」では**再現しない**（ロックを外して
    4プロセス並走させても全緑になることを実測で確認）。そこでロックを外から保持し、
    その間 `record_evolve_diff_decision` が進めないことを検査する。
    flock は open file description 単位なので、同一プロセスの別スレッドでも実際にブロックする。
    """
    sys.path.insert(0, str(_LIB.parent.parent / "skills" / "evolve-fitness" / "scripts"))
    from fitness_evolution import record_evolve_diff_decision

    from rl_common.file_lock import file_lock

    hist = tmp_path / "history.jsonl"
    hist.parent.mkdir(parents=True, exist_ok=True)
    done = threading.Event()

    def _record():
        record_evolve_diff_decision(
            skill_name="s",
            after_content="# s\n\n本文\n",
            diff_summary="d",
            human_accepted=True,
            history_file=hist,
            entry_id="locked_id",
        )
        done.set()

    with file_lock(hist.with_name(hist.name + ".lock")):
        thread = threading.Thread(target=_record, daemon=True)
        thread.start()
        assert not done.wait(1.0), "history ロックを持っている間に append された"
        assert not hist.exists() or hist.read_text(encoding="utf-8") == ""

    assert done.wait(30), "ロック解放後も完走しなかった"
    rows = [json.loads(l) for l in hist.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert [r["id"] for r in rows] == ["locked_id"]


def test_concurrent_record_of_same_entry_id_writes_one_row(tmp_path):
    """同一 entry_id を4プロセス同時に記録しても1行（end-to-end の健全性確認）。"""
    hist = tmp_path / "history.jsonl"
    rows = _run_recorders(tmp_path, hist, ["dup_id"] * 4)
    assert [r["id"] for r in rows] == ["dup_id"]


def test_concurrent_record_of_distinct_ids_keeps_all_rows(tmp_path):
    """ロックで直列化しても別 ID の記録は落ちない（over-serialization の回帰防止）。"""
    hist = tmp_path / "history.jsonl"
    ids = [f"id_{i}" for i in range(4)]
    rows = _run_recorders(tmp_path, hist, ids)
    assert sorted(r["id"] for r in rows) == sorted(ids)


# ─── 3. drain の単一ロック（#287-3）────────────────────────────────────────


def test_drain_holds_one_lock_and_does_not_self_deadlock(roots, skill_file, tmp_path):
    """#287-3: drain は marker ロックを保持したまま purge する。

    ロック下から**公開版**の `purge_marker_entries` / `read_pending_marker` を呼ぶと
    flock が open file description 単位ゆえ自分自身と deadlock する。deadlock すると
    テストは失敗でなく**ハング**するので、daemon thread + join(timeout) で検出する。
    """
    before = ed._sha256(skill_file.read_text(encoding="utf-8"))
    ed.write_pending_marker("s", [_entry(str(skill_file), before)], run_id="evrun_test")
    skill_file.write_text("# my-skill\n\n適用済み\n", encoding="utf-8")

    box = {}

    def _run():
        box["summary"] = ed.drain_pending(slug="s", history_file=tmp_path / "hist.jsonl")

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    thread.join(timeout=30)

    assert not thread.is_alive(), "drain が marker ロックで自己 deadlock した"
    assert len(box["summary"]["accepted"]) == 1
    assert ed.read_pending_marker("s") is None  # 消化済みなので marker は消える


def test_purge_locked_variant_does_not_take_the_lock(roots, skill_file):
    """ロック無し版はロック下から呼べる（取得すると入れ子で自己 deadlock する）。"""
    entry = _entry(str(skill_file), "sha")
    ed.write_pending_marker("s", [entry], run_id="evrun_test")

    box = {}

    def _run():
        with ed._marker_lock("s"):
            box["purged"] = ed._purge_marker_entries_locked("s", {entry["id"]})

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    thread.join(timeout=30)

    assert not thread.is_alive()
    assert box["purged"] is True


# ─── 4. TTL 切れ / 破損 marker の GC（#287-4）──────────────────────────────


def _age_file(path: Path, days: int) -> None:
    old = time.time() - days * 86400
    os.utime(path, (old, old))


def test_run_without_emitted_at_expires_via_file_mtime(roots, skill_file):
    """#287-4: `emitted_at` 欠落の run は age 不明でも marker mtime で失効させる。

    そうしないと旧 schema / 壊れた値の run が**永久に失効せず**、marker が残骸になる。
    """
    entry = _entry(str(skill_file), "sha")
    ed.write_pending_marker("s", [entry], run_id="evrun_test")
    path = ed.marker_path("s")
    data = json.loads(path.read_text(encoding="utf-8"))
    for run in data["runs"]:
        run.pop("emitted_at", None)
    path.write_text(json.dumps(data), encoding="utf-8")
    _age_file(path, ed.PENDING_TTL_DAYS + 5)

    assert ed._read_pending_marker_file("s") is None


def test_run_without_emitted_at_survives_within_ttl(roots, skill_file):
    entry = _entry(str(skill_file), "sha")
    ed.write_pending_marker("s", [entry], run_id="evrun_test")
    path = ed.marker_path("s")
    data = json.loads(path.read_text(encoding="utf-8"))
    for run in data["runs"]:
        run.pop("emitted_at", None)
    path.write_text(json.dumps(data), encoding="utf-8")

    marker = ed._read_pending_marker_file("s")
    assert marker is not None and len(marker["pending"]) == 1


def test_invalid_emitted_at_falls_back_to_mtime(roots, skill_file):
    entry = _entry(str(skill_file), "sha")
    ed.write_pending_marker("s", [entry], run_id="evrun_test")
    path = ed.marker_path("s")
    data = json.loads(path.read_text(encoding="utf-8"))
    for run in data["runs"]:
        run["emitted_at"] = "not-a-timestamp"
    path.write_text(json.dumps(data), encoding="utf-8")
    _age_file(path, ed.PENDING_TTL_DAYS + 5)

    assert ed._read_pending_marker_file("s") is None


def test_expired_marker_file_is_physically_removed(roots, skill_file):
    """#287-4: read が None を返すだけでファイルが残ると永久に残骸化する。"""
    entry = _entry(str(skill_file), "sha")
    ed.write_pending_marker("s", [entry], run_id="evrun_test")
    path = ed.marker_path("s")
    data = json.loads(path.read_text(encoding="utf-8"))
    stale = datetime.now(timezone.utc) - timedelta(days=ed.PENDING_TTL_DAYS + 5)
    for run in data["runs"]:
        run["emitted_at"] = stale.isoformat()
    path.write_text(json.dumps(data), encoding="utf-8")

    assert ed.read_pending_marker("s") is None
    assert not path.exists()


def test_corrupt_marker_file_is_physically_removed(roots):
    path = ed.marker_path("s")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ not json", encoding="utf-8")

    assert ed.read_pending_marker("s") is None
    assert not path.exists()


def test_live_marker_is_not_removed(roots, skill_file):
    """有効な marker は GC で消さない（drain 待ちを取り落とさない）。"""
    ed.write_pending_marker("s", [_entry(str(skill_file), "sha")], run_id="evrun_test")
    assert ed.read_pending_marker("s") is not None
    assert ed.marker_path("s").exists()
