#!/usr/bin/env python3
"""memory ファイルの temporal frontmatter ヘルパーのテスト。

TDD: Task 1 — memory frontmatter schema 定義 + 後方互換パーサー
"""
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from memory_temporal import (
    TEMPORAL_DEFAULTS,
    parse_memory_temporal,
    is_stale,
    is_superseded,
    make_source_correction_id,
)


class TestParseMemoryTemporal:
    """parse_memory_temporal — frontmatter なしファイルは安全にデフォルトを返す。"""

    def test_no_frontmatter_returns_defaults(self, tmp_path):
        """frontmatter なし（既存ファイル）→ デフォルト値で安全に動作する。"""
        f = tmp_path / "old_memory.md"
        f.write_text("# Some memory\nContent here.\n", encoding="utf-8")

        result = parse_memory_temporal(f)

        assert result["valid_from"] is None
        assert result["superseded_at"] is None
        assert result["decay_days"] is None
        assert result["source_correction_ids"] == []

    def test_full_frontmatter_parsed(self, tmp_path):
        """全フィールドあり → 正しく読み取る。"""
        f = tmp_path / "new_memory.md"
        f.write_text(
            "---\n"
            "name: test\n"
            "type: feedback\n"
            "valid_from: '2026-01-15T00:00:00Z'\n"
            "superseded_at: null\n"
            "decay_days: 180\n"
            "source_correction_ids:\n"
            "  - 'abc123#2026-01-15T10:23:00.456Z'\n"
            "---\n"
            "# Content\n",
            encoding="utf-8",
        )

        result = parse_memory_temporal(f)

        assert result["valid_from"] == "2026-01-15T00:00:00Z"
        assert result["superseded_at"] is None
        assert result["decay_days"] == 180
        assert result["source_correction_ids"] == ["abc123#2026-01-15T10:23:00.456Z"]

    def test_nonexistent_file_returns_defaults(self, tmp_path):
        """存在しないファイル → 例外なくデフォルトを返す。"""
        f = tmp_path / "missing.md"
        result = parse_memory_temporal(f)
        assert result == TEMPORAL_DEFAULTS

    def test_partial_frontmatter_fills_defaults(self, tmp_path):
        """一部フィールドだけある → 残りはデフォルトで補完。"""
        f = tmp_path / "partial.md"
        f.write_text(
            "---\nname: partial\ndecay_days: 90\n---\n# Content\n",
            encoding="utf-8",
        )
        result = parse_memory_temporal(f)
        assert result["decay_days"] == 90
        assert result["valid_from"] is None
        assert result["source_correction_ids"] == []


class TestIsStale:
    """is_stale — decay_days の境界条件。"""

    def test_null_decay_days_never_stale(self):
        """decay_days: null → 期限なし、stale にならない。"""
        fm = {**TEMPORAL_DEFAULTS, "valid_from": "2020-01-01T00:00:00Z", "decay_days": None}
        assert is_stale(fm) is False

    def test_zero_decay_days_not_stale(self):
        """decay_days: 0 → 即時 stale ではなく「期限なし」と同じ扱い（null 相当）。"""
        fm = {**TEMPORAL_DEFAULTS, "valid_from": "2020-01-01T00:00:00Z", "decay_days": 0}
        assert is_stale(fm) is False

    def test_exceeded_decay_days_is_stale(self):
        """decay_days を超過 → stale。"""
        fm = {
            **TEMPORAL_DEFAULTS,
            "valid_from": "2024-01-01T00:00:00Z",
            "decay_days": 30,
        }
        assert is_stale(fm) is True

    def test_within_decay_days_not_stale(self):
        """decay_days 以内 → stale でない。"""
        now = datetime.now(timezone.utc).isoformat()
        fm = {**TEMPORAL_DEFAULTS, "valid_from": now, "decay_days": 365}
        assert is_stale(fm) is False

    def test_no_valid_from_never_stale(self):
        """valid_from なし → stale 判定不能 → False。"""
        fm = {**TEMPORAL_DEFAULTS, "decay_days": 1}
        assert is_stale(fm) is False


class TestIsSuperseded:
    """is_superseded — superseded_at の判定。"""

    def test_null_not_superseded(self):
        """superseded_at: null → 現在有効。"""
        fm = {**TEMPORAL_DEFAULTS}
        assert is_superseded(fm) is False

    def test_past_date_is_superseded(self):
        """過去の superseded_at → superseded。"""
        fm = {**TEMPORAL_DEFAULTS, "superseded_at": "2020-01-01T00:00:00Z"}
        assert is_superseded(fm) is True

    def test_future_date_not_superseded(self):
        """未来の superseded_at → まだ有効。"""
        fm = {**TEMPORAL_DEFAULTS, "superseded_at": "2099-01-01T00:00:00Z"}
        assert is_superseded(fm) is False


class TestMakeSourceCorrectionId:
    """make_source_correction_id — 複合キー生成。"""

    def test_format(self):
        """session_id#timestamp 形式で生成される。"""
        result = make_source_correction_id("sess-abc123", "2026-01-15T10:23:00.456Z")
        assert result == "sess-abc123#2026-01-15T10:23:00.456Z"

    def test_unique_per_ms(self):
        """同じ session_id でも ms が違えば異なるキー。"""
        a = make_source_correction_id("sess", "2026-01-15T10:23:00.000Z")
        b = make_source_correction_id("sess", "2026-01-15T10:23:00.001Z")
        assert a != b
