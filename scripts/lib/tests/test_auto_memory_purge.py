"""auto_memory_purge.purge_mismatched_pending のユニットテスト（#206）。

auto_memory Stop hook が project_path フィルタ無しで全 PJ 共有ストア（corrections.jsonl）を
読み、他 PJ の correction を enqueue していた事故の修復ツール。すべて LLM-free。
"""
import json
from pathlib import Path

import pytest

import auto_memory_broker as amb
import auto_memory_purge as amp


@pytest.fixture
def tmp_data_dir(tmp_path):
    d = tmp_path / "evolve-anything"
    d.mkdir()
    return d


def _write_queue_record(data_dir: Path, slug: str, dedup_key: str, corrections: list) -> None:
    """enqueue() の reject ゲートをバイパスして queue に生レコードを直接書く。

    既存バグ（#206 修正前の Stop hook）が実際に書いた「他 PJ 混入済み」キューを
    再現するためのテスト専用ヘルパー。修正後の enqueue() は既にこの混入を防ぐため、
    purge_mismatched_pending の対象は「修正前に書かれたレガシー汚染データ」。
    """
    path = amb.queue_path_for(slug, data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    rec = {
        "dedup_key": dedup_key,
        "slug": slug,
        "corrections": corrections,
        "enqueued_at": "2026-01-01T00:00:00Z",
    }
    with path.open("a") as f:
        f.write(json.dumps(rec) + "\n")


def test_purge_dry_run_detects_but_writes_nothing(tmp_data_dir):
    """dry-run: 不一致 correction を検出するが一切書き込まない。"""
    mixed = [
        {"session_id": "s1", "timestamp": "t1", "project_path": "myproject"},
        {"session_id": "s2", "timestamp": "t2", "project_path": "otherproject"},
    ]
    _write_queue_record(tmp_data_dir, "myproject", "k1", mixed)
    queue_path = amb.queue_path_for("myproject", tmp_data_dir)
    before_mtime = queue_path.stat().st_mtime_ns
    before_content = queue_path.read_text()

    result = amp.purge_mismatched_pending(tmp_data_dir, dry_run=True)

    assert result["dry_run"] is True
    assert result["rejected_count"] == 1
    assert result["affected_slugs"] == ["myproject"]
    # 書込ゼロ: mtime/内容とも不変
    assert queue_path.stat().st_mtime_ns == before_mtime
    assert queue_path.read_text() == before_content


def test_purge_apply_removes_only_mismatched(tmp_data_dir):
    """apply: 不一致 correction のみ除去し、一致分は残す。"""
    mixed = [
        {"session_id": "s1", "timestamp": "t1", "project_path": "myproject"},
        {"session_id": "s2", "timestamp": "t2", "project_path": "otherproject"},
    ]
    _write_queue_record(tmp_data_dir, "myproject", "k1", mixed)

    result = amp.purge_mismatched_pending(tmp_data_dir, dry_run=False)

    assert result["dry_run"] is False
    assert result["rejected_count"] == 1
    records = amb.read_queue("myproject", tmp_data_dir)
    assert len(records) == 1
    kept = records[0]["corrections"]
    assert len(kept) == 1
    assert kept[0]["project_path"] == "myproject"


def test_purge_removes_record_when_all_corrections_mismatched(tmp_data_dir):
    """record 内の全 correction が不一致なら record ごと消化する。"""
    mismatched_only = [{"session_id": "s1", "timestamp": "t1", "project_path": "otherproject"}]
    _write_queue_record(tmp_data_dir, "myproject", "k1", mismatched_only)

    result = amp.purge_mismatched_pending(tmp_data_dir, dry_run=False)

    assert result["removed_records"] == 1
    assert amb.read_queue("myproject", tmp_data_dir) == []


def test_purge_leaves_clean_queue_untouched(tmp_data_dir):
    """混入の無いキューは affected 扱いにならない。"""
    clean = [{"session_id": "s1", "timestamp": "t1", "project_path": "myproject"}]
    _write_queue_record(tmp_data_dir, "myproject", "k1", clean)

    result = amp.purge_mismatched_pending(tmp_data_dir, dry_run=False)

    assert result["rejected_count"] == 0
    assert result["affected_slugs"] == []
    assert len(amb.read_queue("myproject", tmp_data_dir)) == 1


def test_purge_no_queue_dir_returns_empty_result(tmp_data_dir):
    """auto_memory_queue ディレクトリが無い場合は空結果を返す（例外を投げない）。"""
    result = amp.purge_mismatched_pending(tmp_data_dir, dry_run=True)
    assert result["scanned_slugs"] == []
    assert result["rejected_count"] == 0
    assert result["removed_records"] == 0


def test_purge_scans_multiple_pj_queues(tmp_data_dir):
    """全 PJ の queue ファイルを走査し、affected_slugs は混入があったものだけ返す。"""
    _write_queue_record(
        tmp_data_dir, "pj-a", "ka",
        [
            {"session_id": "a1", "timestamp": "t1", "project_path": "pj-a"},
            {"session_id": "a2", "timestamp": "t2", "project_path": "pj-b"},
        ],
    )
    _write_queue_record(
        tmp_data_dir, "pj-b", "kb",
        [{"session_id": "b1", "timestamp": "t1", "project_path": "pj-b"}],
    )

    result = amp.purge_mismatched_pending(tmp_data_dir, dry_run=True)

    assert set(result["scanned_slugs"]) == {"pj-a", "pj-b"}
    assert result["affected_slugs"] == ["pj-a"]
    assert result["rejected_count"] == 1


def test_purge_unattributed_corrections_not_flagged(tmp_data_dir):
    """project_path 欠落の correction は不一致扱いされない（寛容に許容）。"""
    generic = [{"session_id": "s1", "timestamp": "t1"}]
    _write_queue_record(tmp_data_dir, "myproject", "k1", generic)

    result = amp.purge_mismatched_pending(tmp_data_dir, dry_run=True)

    assert result["rejected_count"] == 0
    assert result["affected_slugs"] == []
