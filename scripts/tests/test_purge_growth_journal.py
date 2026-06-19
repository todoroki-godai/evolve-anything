#!/usr/bin/env python3
"""purge_growth_journal_test_pollution のテスト（#420）。

test 汚染エントリ（project=test_*/tmp*）の検出・dry-run・--apply + backup を
tmp fixture で検証する（実環境には一切触れない）。
"""
import json
import sys
from pathlib import Path

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from purge_growth_journal_test_pollution import (  # noqa: E402
    BUCKET_KEEP,
    BUCKET_PURGE,
    BUCKET_UNKNOWN,
    classify_project,
    partition_records,
    purge_journal,
)


# ── classify_project ────────────────────────────────────────────


class TestClassifyProject:
    @pytest.mark.parametrize(
        "project,expected",
        [
            ("test_foo", BUCKET_PURGE),
            ("test-bar", BUCKET_PURGE),
            ("tmpabc123", BUCKET_PURGE),
            ("tmp", BUCKET_PURGE),
            ("", BUCKET_UNKNOWN),
            ("unknown", BUCKET_UNKNOWN),
            (None, BUCKET_UNKNOWN),
            ("evolve-anything", BUCKET_KEEP),
            ("docs-platform", BUCKET_KEEP),
            ("my-real-project", BUCKET_KEEP),
        ],
    )
    def test_classify(self, project, expected):
        assert classify_project(project) == expected

    def test_whitespace_stripped(self):
        assert classify_project("  test_foo  ") == BUCKET_PURGE
        assert classify_project("   ") == BUCKET_UNKNOWN


# ── partition_records ───────────────────────────────────────────


class TestPartitionRecords:
    def test_partition_keeps_unknown_and_real(self):
        records = [
            {"project": "test_x", "targets": ["a"]},
            {"project": "tmpY", "targets": ["b"]},
            {"project": "unknown", "targets": ["c"]},
            {"project": "", "targets": ["d"]},
            {"project": "evolve-anything", "targets": ["e"]},
        ]
        kept, buckets = partition_records(records)
        assert len(buckets[BUCKET_PURGE]) == 2
        assert len(buckets[BUCKET_UNKNOWN]) == 2
        assert len(buckets[BUCKET_KEEP]) == 1
        # kept = unknown + keep（purge は除外）
        assert len(kept) == 3
        assert all(classify_project(r.get("project")) != BUCKET_PURGE for r in kept)

    def test_non_dict_or_missing_project_kept(self):
        records = [{"type": "other"}, {"project": "real"}]
        kept, buckets = partition_records(records)
        # project が無い行は安全側で keep
        assert len(kept) == 2
        assert len(buckets[BUCKET_PURGE]) == 0


# ── purge_journal ───────────────────────────────────────────────


def _write_journal(path: Path, records):
    with open(path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


class TestPurgeJournal:
    def _sample(self):
        return [
            {"type": "crystallization", "project": "test_a", "targets": ["x"]},
            {"type": "crystallization", "project": "tmp_b", "targets": ["y"]},
            {"type": "crystallization", "project": "unknown", "targets": ["z"]},
            {"type": "crystallization", "project": "evolve-anything", "targets": ["w"]},
        ]

    def test_missing_file(self, tmp_path):
        report = purge_journal(tmp_path / "nope.jsonl", apply=False)
        assert report["exists"] is False
        assert report["applied"] is False
        assert report["total"] == 0

    def test_dry_run_does_not_modify(self, tmp_path):
        journal = tmp_path / "growth-journal.jsonl"
        _write_journal(journal, self._sample())
        before = journal.read_text()

        report = purge_journal(journal, apply=False)

        assert report["applied"] is False
        assert report["total"] == 4
        assert report["purge"] == 2
        assert report["unknown"] == 1
        assert report["keep"] == 1
        assert report["backup"] is None
        # ファイルは一切変更されない
        assert journal.read_text() == before
        # backup も作られない
        assert list(tmp_path.glob("*.bak*")) == []

    def test_apply_removes_and_backups(self, tmp_path):
        journal = tmp_path / "growth-journal.jsonl"
        _write_journal(journal, self._sample())

        report = purge_journal(journal, apply=True)

        assert report["applied"] is True
        assert report["purge"] == 2
        assert report["backup"] is not None
        backup = Path(report["backup"])
        assert backup.exists()
        # backup は元の 4 件を保持
        assert len(backup.read_text().strip().split("\n")) == 4

        # purge 後のファイルは unknown + keep の 2 件のみ
        remaining = [
            json.loads(line)
            for line in journal.read_text().strip().split("\n")
            if line.strip()
        ]
        assert len(remaining) == 2
        projects = {r["project"] for r in remaining}
        assert projects == {"unknown", "evolve-anything"}

    def test_apply_no_test_entries_no_backup(self, tmp_path):
        journal = tmp_path / "growth-journal.jsonl"
        _write_journal(
            journal,
            [
                {"project": "evolve-anything", "targets": ["x"]},
                {"project": "unknown", "targets": ["y"]},
            ],
        )
        before = journal.read_text()

        report = purge_journal(journal, apply=True)

        assert report["purge"] == 0
        assert report["applied"] is False
        assert report["backup"] is None
        assert journal.read_text() == before
        assert list(tmp_path.glob("*.bak*")) == []
