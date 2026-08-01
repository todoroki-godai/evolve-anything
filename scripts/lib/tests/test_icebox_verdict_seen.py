"""icebox_verdict_seen の既読ストアテスト（#352）。

一度提示した (issue番号, 評価値ハッシュ) は再提示しない。評価値が変わればハッシュが変わり
再提示対象に戻る。既読ストアは daily_review.py の物理キー集合パターンを踏襲する。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_lib_dir = Path(__file__).resolve().parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

import icebox_verdict_seen as seen  # noqa: E402


def _verdict(number=1, lane="met", value=10, reason="x satisfied"):
    return {"number": number, "lane": lane, "value": value, "reason": reason}


class TestFingerprint:
    def test_same_verdict_same_fingerprint(self):
        v1 = _verdict()
        v2 = _verdict()
        assert seen.verdict_fingerprint(v1) == seen.verdict_fingerprint(v2)

    def test_different_value_different_fingerprint(self):
        v1 = _verdict(value=10)
        v2 = _verdict(value=20)
        assert seen.verdict_fingerprint(v1) != seen.verdict_fingerprint(v2)

    def test_different_lane_different_fingerprint(self):
        v1 = _verdict(lane="met")
        v2 = _verdict(lane="archive_candidate")
        assert seen.verdict_fingerprint(v1) != seen.verdict_fingerprint(v2)


class TestReadEmpty:
    def test_missing_file_returns_empty_set(self, tmp_path):
        assert seen.read_seen_keys(tmp_path / "no-such.jsonl") == set()


class TestRecordAndFilter:
    def test_record_then_filter_excludes_seen(self, tmp_path):
        path = tmp_path / "icebox_verdict_seen.jsonl"
        v = _verdict(number=42)
        result = seen.record_seen([v], path=path)
        assert result["written"] == 1
        assert result["dry_run"] is False

        keys = seen.read_seen_keys(path)
        assert seen.verdict_key(v) in keys

        unseen = seen.filter_unseen([v], keys)
        assert unseen == []

    def test_changed_value_reappears(self, tmp_path):
        path = tmp_path / "icebox_verdict_seen.jsonl"
        v1 = _verdict(number=42, value=10)
        seen.record_seen([v1], path=path)
        keys = seen.read_seen_keys(path)

        v2 = _verdict(number=42, value=20)  # 値が変わった
        unseen = seen.filter_unseen([v2], keys)
        assert unseen == [v2]

    def test_dry_run_writes_nothing(self, tmp_path):
        path = tmp_path / "icebox_verdict_seen.jsonl"
        result = seen.record_seen([_verdict()], path=path, dry_run=True)
        assert result["written"] == 1
        assert result["dry_run"] is True
        assert not path.exists()

    def test_duplicate_record_is_idempotent(self, tmp_path):
        path = tmp_path / "icebox_verdict_seen.jsonl"
        v = _verdict()
        seen.record_seen([v], path=path)
        result = seen.record_seen([v], path=path)
        assert result["written"] == 0  # 既に既読なので追記なし
        lines = path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1

    def test_filter_unseen_keeps_multiple_new_and_drops_seen(self, tmp_path):
        path = tmp_path / "icebox_verdict_seen.jsonl"
        v1 = _verdict(number=1)
        v2 = _verdict(number=2)
        seen.record_seen([v1], path=path)
        keys = seen.read_seen_keys(path)
        unseen = seen.filter_unseen([v1, v2], keys)
        assert unseen == [v2]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
