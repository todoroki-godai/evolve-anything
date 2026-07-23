#!/usr/bin/env python3
"""fleet purge-corrections CLI（#206 運用対応）のテスト。

`auto_memory_purge.purge_mismatched_pending()` 自体のロジックテストは
`test_auto_memory_purge.py` に既にあるため、ここでは CLI 経由の argparse dispatch
配線が正しいことのみを確認する（二重テストにしない）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_test_dir = Path(__file__).resolve().parent
_lib_dir = _test_dir.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

import auto_memory_broker as amb  # noqa: E402
from fleet import cli as fcli  # noqa: E402


def _write_queue_record(data_dir: Path, slug: str, dedup_key: str, corrections: list) -> None:
    """他 PJ 混入済みキューを再現するテスト専用ヘルパー（test_auto_memory_purge.py と同型）。"""
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


def _mixed_corrections() -> list:
    return [
        {"session_id": "s1", "timestamp": "t1", "project_path": "myproject"},
        {"session_id": "s2", "timestamp": "t2", "project_path": "otherproject"},
    ]


class TestPurgeCorrectionsCliDryRun:
    def test_dry_run_default_prints_affected_and_writes_nothing(self, tmp_path, capsys):
        _write_queue_record(tmp_path, "myproject", "k1", _mixed_corrections())
        queue_path = amb.queue_path_for("myproject", tmp_path)
        before_mtime = queue_path.stat().st_mtime_ns
        before_content = queue_path.read_text()

        rc = fcli.main(["purge-corrections", "--data-dir", str(tmp_path)])

        assert rc == 0
        out = capsys.readouterr().out
        assert "myproject" in out
        assert "rejected_count: 1" in out
        assert "--apply" in out
        # 書込ゼロ（1バイトも書かない契約）
        assert queue_path.stat().st_mtime_ns == before_mtime
        assert queue_path.read_text() == before_content

    def test_dry_run_clean_queue_reports_no_affected(self, tmp_path, capsys):
        clean = [{"session_id": "s1", "timestamp": "t1", "project_path": "myproject"}]
        _write_queue_record(tmp_path, "myproject", "k1", clean)

        rc = fcli.main(["purge-corrections", "--data-dir", str(tmp_path)])

        assert rc == 0
        out = capsys.readouterr().out
        assert "affected_slugs: なし" in out


class TestPurgeCorrectionsCliApply:
    def test_apply_flag_writes_cleaned_queue(self, tmp_path, capsys):
        _write_queue_record(tmp_path, "myproject", "k1", _mixed_corrections())
        queue_path = amb.queue_path_for("myproject", tmp_path)
        before_content = queue_path.read_text()

        rc = fcli.main(["purge-corrections", "--data-dir", str(tmp_path), "--apply"])

        assert rc == 0
        assert queue_path.read_text() != before_content
        records = amb.read_queue("myproject", tmp_path)
        assert len(records) == 1
        kept = records[0]["corrections"]
        assert len(kept) == 1
        assert kept[0]["project_path"] == "myproject"
        out = capsys.readouterr().out
        assert "キューファイルを書き換えました" in out


class TestPurgeCorrectionsCliJson:
    def test_json_flag_outputs_valid_json(self, tmp_path, capsys):
        _write_queue_record(tmp_path, "myproject", "k1", _mixed_corrections())

        rc = fcli.main(["purge-corrections", "--data-dir", str(tmp_path), "--json"])

        assert rc == 0
        out = capsys.readouterr().out
        result = json.loads(out)
        assert result["affected_slugs"] == ["myproject"]
        assert result["rejected_count"] == 1
        assert result["dry_run"] is True

    def test_json_apply_reflects_dry_run_false(self, tmp_path, capsys):
        _write_queue_record(tmp_path, "myproject", "k1", _mixed_corrections())

        rc = fcli.main(
            ["purge-corrections", "--data-dir", str(tmp_path), "--apply", "--json"]
        )

        assert rc == 0
        result = json.loads(capsys.readouterr().out)
        assert result["dry_run"] is False
        assert result["rejected_count"] == 1


class TestPurgeCorrectionsCliDataDirDefault:
    def test_no_data_dir_falls_back_to_default_canonical(self, tmp_path, monkeypatch, capsys):
        """--data-dir 省略時は data_dir_migration.default_canonical() を使う配線を確認。"""
        import data_dir_migration

        monkeypatch.setattr(data_dir_migration, "default_canonical", lambda: tmp_path)

        rc = fcli.main(["purge-corrections", "--json"])

        assert rc == 0
        result = json.loads(capsys.readouterr().out)
        # queue_dir が存在しない tmp_path のため空結果（例外を投げない）
        assert result["scanned_slugs"] == []
