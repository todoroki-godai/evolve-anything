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
    compute_importance_score,
    reinforce_memory,
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


class TestUpdateCount:
    """update_count — LLM 自己更新メモリの劣化警告 (Issue #97, arXiv:2605.12978)。

    詳細: docs/research/faulty-updated-memories.md
    """

    def test_default_is_zero(self):
        """TEMPORAL_DEFAULTS は update_count: 0 を含む。"""
        assert TEMPORAL_DEFAULTS["update_count"] == 0

    def test_no_frontmatter_returns_zero(self, tmp_path):
        """frontmatter なしファイル → update_count: 0。"""
        f = tmp_path / "old.md"
        f.write_text("# Old memory\n", encoding="utf-8")
        result = parse_memory_temporal(f)
        assert result["update_count"] == 0

    def test_explicit_update_count_parsed(self, tmp_path):
        """frontmatter に update_count: 3 → 正しく読み取る。"""
        f = tmp_path / "updated.md"
        f.write_text(
            "---\nname: x\nupdate_count: 3\n---\n# Body\n",
            encoding="utf-8",
        )
        result = parse_memory_temporal(f)
        assert result["update_count"] == 3

    def test_negative_normalized_to_zero(self, tmp_path):
        """負値は 0 に正規化（不正値サイレントフォールバック）。"""
        f = tmp_path / "bad.md"
        f.write_text(
            "---\nname: x\nupdate_count: -5\n---\n",
            encoding="utf-8",
        )
        result = parse_memory_temporal(f)
        assert result["update_count"] == 0

    def test_non_int_normalized_to_zero(self, tmp_path):
        """型が int でない値も 0 に正規化。"""
        f = tmp_path / "bad2.md"
        f.write_text(
            "---\nname: x\nupdate_count: 'three'\n---\n",
            encoding="utf-8",
        )
        result = parse_memory_temporal(f)
        assert result["update_count"] == 0

    def test_bool_normalized_to_zero(self, tmp_path):
        """bool は int のサブクラスだが 0 に正規化する（true/false は不正値）。"""
        f = tmp_path / "bool_true.md"
        f.write_text(
            "---\nname: x\nupdate_count: true\n---\n",
            encoding="utf-8",
        )
        result = parse_memory_temporal(f)
        assert result["update_count"] == 0

        f2 = tmp_path / "bool_false.md"
        f2.write_text(
            "---\nname: x\nupdate_count: false\n---\n",
            encoding="utf-8",
        )
        result2 = parse_memory_temporal(f2)
        assert result2["update_count"] == 0


class TestComputeImportanceScore:
    """compute_importance_score — rule-based スコア計算。"""

    def test_high_with_corrections_and_updates(self):
        """high + 2 corrections + 3 updates → 0.8 + 0.06 + 0.06 = 0.92"""
        fm = {
            "importance": "high",
            "source_correction_ids": ["a#1", "b#2"],
            "update_count": 3,
        }
        result = compute_importance_score(fm)
        assert abs(result - 0.92) < 1e-9

    def test_defaults_empty_fm(self):
        """fm={} → デフォルト medium base = 0.5"""
        result = compute_importance_score({})
        assert result == 0.5

    def test_low_importance(self):
        """low → base 0.2"""
        result = compute_importance_score({"importance": "low"})
        assert result == 0.2

    def test_correction_bonus_capped(self):
        """correction_bonus は 0.15 上限"""
        fm = {"importance": "medium", "source_correction_ids": list(range(10))}
        result = compute_importance_score(fm)
        # 0.5 + 0.15 (cap) + 0 = 0.65
        assert abs(result - 0.65) < 1e-9

    def test_update_bonus_capped(self):
        """update_bonus は 0.10 上限"""
        fm = {"importance": "medium", "update_count": 100}
        result = compute_importance_score(fm)
        # 0.5 + 0 + 0.10 (cap) = 0.60
        assert abs(result - 0.60) < 1e-9

    def test_result_capped_at_1(self):
        """合計が 1.0 を超える場合は 1.0 に切り捨て"""
        fm = {
            "importance": "high",
            "source_correction_ids": list(range(10)),
            "update_count": 100,
        }
        result = compute_importance_score(fm)
        assert result == 1.0


class TestReinforceMemory:
    """reinforce_memory — frontmatter の更新テスト。"""

    def test_reinforce_updates_fields(self, tmp_path):
        """reinforce_memory を呼ぶと importance_score/last_reinforced_at/update_count が更新される。"""
        f = tmp_path / "test_entry.md"
        f.write_text(
            "---\n"
            "name: test-entry\n"
            "importance: high\n"
            "source_correction_ids:\n"
            "  - 'sess#2026-01-01T00:00:00.000Z'\n"
            "  - 'sess#2026-01-02T00:00:00.000Z'\n"
            "update_count: 3\n"
            "---\n"
            "# Body\n",
            encoding="utf-8",
        )

        reinforce_memory(f, reason="test reinforcement")

        from lib.frontmatter import parse_frontmatter
        fm = parse_frontmatter(f)

        assert fm["update_count"] == 4
        assert isinstance(fm["importance_score"], float)
        assert fm["importance_score"] > 0.0
        # last_reinforced_at は ISO8601 形式
        assert fm["last_reinforced_at"] is not None
        from datetime import datetime
        # パース可能であることを確認
        dt = datetime.fromisoformat(fm["last_reinforced_at"])
        assert dt is not None

    def test_reinforce_no_frontmatter_is_noop(self, tmp_path):
        """frontmatter なし → no-op（ファイル内容が変わらない）。"""
        f = tmp_path / "no_fm.md"
        original = "# No frontmatter\nSome content.\n"
        f.write_text(original, encoding="utf-8")

        reinforce_memory(f, reason="should be no-op")

        assert f.read_text(encoding="utf-8") == original

    def test_reinforce_nonexistent_file_is_noop(self, tmp_path):
        """存在しないファイル → 例外なく no-op。"""
        f = tmp_path / "nonexistent.md"
        reinforce_memory(f, reason="no-op")  # should not raise


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
