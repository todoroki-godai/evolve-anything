"""evolve の初回 backfill 提案テスト。

usage.jsonl が不在 / 0件（テレメトリ未取得）のとき、check_data_sufficiency が
backfill 提案フラグを返すことを検証する。単なるデータ不足（少量だが観測あり）
とは区別すること。
"""
import sys
from pathlib import Path
from unittest import mock

_plugin_root = Path(__file__).resolve().parent.parent.parent.parent.parent
sys.path.insert(0, str(_plugin_root / "skills" / "evolve" / "scripts"))
sys.path.insert(0, str(_plugin_root / "scripts" / "lib"))
sys.path.insert(0, str(_plugin_root / "scripts"))


class TestBackfillSuggestion:
    def test_telemetry_empty_recommends_backfill(self, tmp_path):
        """usage.jsonl 不在 + セッション0 → backfill_recommended: True。"""
        import evolve
        usage_file = tmp_path / "usage.jsonl"  # 不在
        with mock.patch.object(evolve, "DATA_DIR", tmp_path), \
             mock.patch.object(evolve, "count_new_sessions", return_value=0), \
             mock.patch.object(evolve, "count_new_observations", return_value=0):
            result = evolve.check_data_sufficiency()
        assert result["sufficient"] is False
        assert result["telemetry_empty"] is True
        assert result["backfill_recommended"] is True
        assert "backfill" in result["message"].lower()

    def test_partial_data_not_backfill(self, tmp_path):
        """少量だが観測あり → backfill_recommended: False（単なるデータ不足）。"""
        import evolve
        usage_file = tmp_path / "usage.jsonl"
        usage_file.write_text(
            '{"timestamp": "2026-01-01T00:00:00Z", "session_id": "s1"}\n',
            encoding="utf-8",
        )
        with mock.patch.object(evolve, "DATA_DIR", tmp_path), \
             mock.patch.object(evolve, "count_new_sessions", return_value=1), \
             mock.patch.object(evolve, "count_new_observations", return_value=1):
            result = evolve.check_data_sufficiency()
        assert result["sufficient"] is False
        assert result["telemetry_empty"] is False
        assert result["backfill_recommended"] is False

    def test_sufficient_data_not_backfill(self, tmp_path):
        """データ十分 → backfill 提案しない。"""
        import evolve
        usage_file = tmp_path / "usage.jsonl"
        lines = [
            f'{{"timestamp": "2026-01-01T00:00:0{i}Z", "session_id": "s{i}"}}'
            for i in range(25)
        ]
        usage_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
        with mock.patch.object(evolve, "DATA_DIR", tmp_path), \
             mock.patch.object(evolve, "count_new_sessions", return_value=5), \
             mock.patch.object(evolve, "count_new_observations", return_value=25):
            result = evolve.check_data_sufficiency()
        assert result["sufficient"] is True
        assert result["telemetry_empty"] is False
        assert result["backfill_recommended"] is False
