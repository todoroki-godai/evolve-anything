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
        # #486: 旧 /evolve-anything:backfill スキルは #215 で CLI 削除済みの幻なので
        # 案内文に含めてはならない。初回は hooks の観測蓄積 + /evolve-anything:evolve が正。
        assert "/evolve-anything:backfill" not in result["message"]
        assert "/evolve-anything:evolve" in result["message"]

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

    def test_no_new_observations_flags_lightweight(self, tmp_path):
        """過去データ十分だが新規観測 0 → no_new_observations=True で軽量モード誘導（#396）。"""
        import evolve
        usage_file = tmp_path / "usage.jsonl"
        lines = [
            f'{{"timestamp": "2026-01-01T00:00:0{i}Z", "session_id": "s{i}"}}'
            for i in range(25)
        ]
        usage_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
        with mock.patch.object(evolve, "DATA_DIR", tmp_path), \
             mock.patch.object(evolve, "count_new_sessions", return_value=0), \
             mock.patch.object(evolve, "count_new_observations", return_value=0):
            result = evolve.check_data_sufficiency()
        assert result["sufficient"] is True  # total>=20 でべき等性は保つ
        assert result["no_new_observations"] is True
        assert result["telemetry_empty"] is False  # 空ではない（過去データあり）
        assert "軽量モード" in result["message"]

    def test_new_observations_not_lightweight(self, tmp_path):
        """新規観測があれば no_new_observations=False（回帰ガード）。"""
        import evolve
        usage_file = tmp_path / "usage.jsonl"
        lines = [
            f'{{"timestamp": "2026-01-01T00:00:0{i}Z", "session_id": "s{i}"}}'
            for i in range(25)
        ]
        usage_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
        with mock.patch.object(evolve, "DATA_DIR", tmp_path), \
             mock.patch.object(evolve, "count_new_sessions", return_value=2), \
             mock.patch.object(evolve, "count_new_observations", return_value=8):
            result = evolve.check_data_sufficiency()
        assert result["no_new_observations"] is False
